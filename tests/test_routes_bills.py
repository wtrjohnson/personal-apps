"""Route tests for the unified bill story (Phase 9 / audit H4). Require Postgres."""


def test_intake_ask_links_to_bill(db, client):
    app = db
    resp = client.post("/api/notes/intake", json={
        "group": "Acme Corp",
        "date": "2026-07-15",
        "attendees": "Jane Smith",
        "confirmed_items": [{"type": "ask", "text": "Please support H.R. 5"}],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT bill_ref_id FROM asks")
            bref = cur.fetchone()["bill_ref_id"]
            assert bref is not None  # ask linked to a bill (Phase 9)
            cur.execute("SELECT bill_type, bill_number FROM bill_references WHERE id=%s", (bref,))
            r = cur.fetchone()
            assert r["bill_type"] == "HR" and r["bill_number"] == "5"


def test_bill_context_shows_meetings_and_asks(db, client):
    app = db
    client.post("/api/notes/intake", json={
        "group": "Acme Corp",
        "date": "2026-07-15",
        "attendees": "Jane Smith",
        "confirmed_items": [{"type": "ask", "text": "Please support H.R. 5"}],
    })
    # Track the same bill so its drawer context can join.
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tracked_bills (id, congress, bill_type, bill_number, relationship)
                VALUES ('t1', %s, 'HR', '5', 'sponsored')
            """, (app._current_congress(),))
    ctx = client.get("/api/tracked-bills/t1/context").get_json()
    assert ctx["ok"] is True
    assert "Acme Corp" in ctx["organizations"]
    assert any("support H.R. 5" in a["text"] for a in ctx["asks"])
    assert len(ctx["meetings"]) == 1
