"""Meeting list/detail projection. Require Postgres."""

CANVAS = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


def _make_meeting(app, client):
    resp = client.post("/api/notes/intake", json={
        "group": "Acme Corp",
        "topic": "Quarterly check-in",
        "date": "2026-07-15",
        "body": "We discussed the widget subsidy at length.",
        "canvas_image": CANVAS,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM meetings")
            return cur.fetchone()["id"]


def test_meeting_detail_still_returns_canvas_and_html(db, client):
    """The list projection drops body_html and canvas_image; the detail fetch must not."""
    mid = _make_meeting(db, client)
    detail = client.get(f"/api/meetings/{mid}").get_json()
    assert detail["canvas_image"] == CANVAS
    assert "<p>" in detail["body_html"]
    assert "widget subsidy" in detail["body"]


def test_meeting_list_search_still_matches_note_body(db, client):
    """`body` stays in the list projection because free-text matching reads it."""
    _make_meeting(db, client)
    hits = client.get("/api/meetings?q=widget subsidy").get_json()
    assert hits["count"] == 1
    miss = client.get("/api/meetings?q=nothing-like-this").get_json()
    assert miss["count"] == 0


def test_meeting_list_does_not_read_canvas_images(db, client, monkeypatch):
    """Regression for #10: SELECT * pulled a base64 data URL per meeting out of the
    database on every list and every global-search keystroke, only to discard it."""
    import psycopg2
    import psycopg2.extras

    _make_meeting(db, client)
    statements = []

    class RecordingCursor(psycopg2.extras.RealDictCursor):
        def execute(self, sql, args=None):
            statements.append(str(sql))
            return super().execute(sql, args)

    monkeypatch.setattr(
        db, "_connect",
        lambda: psycopg2.connect(db.DATABASE_URL, cursor_factory=RecordingCursor),
    )
    assert client.get("/api/meetings").status_code == 200

    # The row-fetching selects, not the COUNT(*) used for pagination totals.
    meeting_selects = [
        s for s in statements
        if "FROM meetings" in s
        and s.lstrip().upper().startswith("SELECT")
        and "COUNT(" not in s.upper()
    ]
    assert meeting_selects, "expected a select against meetings"
    for sql in meeting_selects:
        assert "canvas_image" not in sql, "the meetings list is reading canvas_image again"
        assert "body_html" not in sql, "the meetings list is reading body_html again"
        assert "SELECT *" not in " ".join(sql.split()), "back to SELECT *"
