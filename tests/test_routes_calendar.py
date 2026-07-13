"""Route tests for calendar → notes handoff (Phase 6 / audit C1). Require Postgres."""
import hashlib


def _mk_prepared(app, filename="2026-07-15 - Acme [cal-abc].md"):
    mid = hashlib.sha1(filename.encode()).hexdigest()[:16]
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO external_calendar_events (ics_uid, summary, dtstart)
                VALUES ('uid-abc', 'Budget review', '2026-07-15T18:00:00+00:00')
                RETURNING id
            """)
            ece_id = cur.fetchone()["id"]
            cur.execute("""
                INSERT INTO meetings (id, filename, canonical_group, status, calendar_event_id,
                                      dtstart, meeting_link)
                VALUES (%s, %s, 'Acme Corp', 'prepared', %s,
                        '2026-07-15T18:00:00+00:00', 'https://teams.example/join')
            """, (mid, filename, ece_id))
    return mid


def test_start_notes_writes_into_prepared_meeting_no_duplicate(db, client):
    app = db
    mid = _mk_prepared(app)

    resp = client.post("/api/notes/intake", json={
        "group": "Acme Corp",
        "topic": "Budget review",
        "date": "2026-07-15",
        "prepared_meeting_id": mid,
        "body": "Discussed the FY26 numbers.",
        "confirmed_items": [{"type": "ask", "text": "Please support the bill"}],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)

    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM meetings")
            assert cur.fetchone()["n"] == 1  # C1: no duplicate meeting minted
            cur.execute("SELECT id, status, body, calendar_event_id, meeting_link FROM meetings")
            row = cur.fetchone()
            assert row["id"] == mid                    # same row
            assert row["status"] == "complete"         # flipped to complete
            assert "FY26" in row["body"]               # notes written in
            assert row["calendar_event_id"] is not None  # calendar linkage preserved
            assert row["meeting_link"] == "https://teams.example/join"
            # The ask attached to the same meeting.
            cur.execute("SELECT meeting_id FROM asks")
            assert cur.fetchone()["meeting_id"] == mid
