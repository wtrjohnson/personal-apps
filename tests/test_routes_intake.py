"""Route tests for closing the intake loop (Phase 7 / audit H1, H2). Require Postgres."""


def _contact_id(app, name):
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM contacts WHERE lower(name)=lower(%s)", (name,))
            row = cur.fetchone()
            return row["id"] if row else None


def test_intake_links_people_to_asks_and_commitments(db, client):
    app = db
    resp = client.post("/api/notes/intake", json={
        "group": "Acme Corp",
        "topic": "Budget review",
        "date": "2026-07-15",
        "attendees": "Jane Smith",  # sole attendee -> default person for captured items
        "confirmed_items": [
            {"type": "ask", "text": "Please support the bill"},
            {"type": "commitment", "text": "I will send the memo"},
            {"type": "trigger", "text": "FU IF markup scheduled -> prep memo"},
            {"type": "person", "text": "Bob Aide"},
        ],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)

    jane = _contact_id(app, "Jane Smith")
    bob = _contact_id(app, "Bob Aide")
    assert jane and bob  # both @person callout and attendee became contacts (H2)

    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT contact_id FROM asks")
            assert cur.fetchone()["contact_id"] == jane          # H1: ask -> person
            cur.execute("SELECT contact_id FROM commitments")
            assert cur.fetchone()["contact_id"] == jane          # H1: commitment -> person
            cur.execute("SELECT contact_id FROM followup_triggers")
            assert cur.fetchone()["contact_id"] == jane          # trigger -> person
            # Both people are linked to the meeting.
            cur.execute("SELECT COUNT(*) AS n FROM meeting_contacts WHERE contact_id IN (%s,%s)", (jane, bob))
            assert cur.fetchone()["n"] == 2


def test_intake_at_name_wins_over_attendee(db, client):
    app = db
    resp = client.post("/api/notes/intake", json={
        "group": "Acme Corp",
        "date": "2026-07-15",
        "attendees": "Jane Smith, Carol Aide",  # ambiguous -> no attendee default
        "confirmed_items": [
            {"type": "ask", "text": "Follow up with @Carol on the letter"},
        ],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    carol = _contact_id(app, "Carol")
    assert carol is not None
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT contact_id FROM asks")
            assert cur.fetchone()["contact_id"] == carol


def test_intake_deadline_callout_sets_meeting_deadline(db, client):
    app = db
    resp = client.post("/api/notes/intake", json={
        "group": "Acme Corp",
        "date": "2026-07-15",
        "confirmed_items": [
            {"type": "deadline", "text": "budget memo due 2026-08-01"},
        ],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT deadline FROM meetings WHERE canonical_group='Acme Corp'")
            assert cur.fetchone()["deadline"] == "2026-08-01"
