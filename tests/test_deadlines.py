"""tasks.deadline as a real DATE column (#13) and the crash it fixes (#18).
Require Postgres."""
import app as app_mod


def test_scan_items_with_a_task_deadline_does_not_crash(db, client):
    """Regression for #18: /api/scan-items called .isoformat() on t.deadline, which was a
    TEXT column, so the Inbox panel 500'd as soon as any callout task had a due date."""
    resp = client.post("/api/notes/intake", json={
        "group": "Acme Corp", "date": "2026-07-15",
        "confirmed_items": [
            {"type": "task", "text": "Send the letter", "due": "2026-08-01"},
        ],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)

    items = client.get("/api/scan-items?date=2026-07-15")
    assert items.status_code == 200, items.get_data(as_text=True)
    data = items.get_json()
    all_items = [i for m in data["meetings"] for i in m["items"]]
    assert any(i["task_deadline"] == "2026-08-01" for i in all_items), all_items


def test_deadline_column_is_a_date(db):
    with db.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'tasks' AND column_name = 'deadline'
            """)
            assert cur.fetchone()["data_type"] == "date"


def test_non_iso_deadline_input_is_rejected_not_stored(db, client):
    """A DATE column turns an unvalidated string into a DataError. Validate at the edge,
    and keep what the user typed in deadline_raw."""
    resp = client.post("/api/tasks/add", json={
        "text": "Follow up with the committee", "deadline": "whenever",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    with db.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT deadline, deadline_raw FROM tasks")
            row = cur.fetchone()
    assert row["deadline"] is None
    assert row["deadline_raw"] == "whenever"


def test_valid_deadline_input_is_stored(db, client):
    resp = client.post("/api/tasks/add", json={
        "text": "File the report", "deadline": "2026-09-30",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    tasks = client.get("/api/tasks").get_json()["tasks"]
    assert tasks[0]["deadline"] == "2026-09-30", "API still speaks ISO strings"


def test_as_iso_date_validation():
    assert app_mod.as_iso_date("2026-08-01") == "2026-08-01"
    assert app_mod.as_iso_date(" 2026-08-01 ") == "2026-08-01"
    assert app_mod.as_iso_date("") is None
    assert app_mod.as_iso_date(None) is None
    assert app_mod.as_iso_date("next Friday") is None
    assert app_mod.as_iso_date("2026-02-30") is None, "a real calendar check, not a regex"


def test_overdue_and_sorting_still_work_across_the_boundary(db, client):
    """The API contract is unchanged: ISO strings out, overdue computed against today."""
    import datetime as _dt
    past = (_dt.date.today() - _dt.timedelta(days=3)).isoformat()
    future = (_dt.date.today() + _dt.timedelta(days=30)).isoformat()
    client.post("/api/tasks/add", json={"text": "Overdue thing", "deadline": past})
    client.post("/api/tasks/add", json={"text": "Later thing", "deadline": future})

    tasks = client.get("/api/tasks").get_json()["tasks"]
    by_text = {t["text"]: t for t in tasks}
    assert by_text["Overdue thing"]["overdue"] is True
    assert by_text["Later thing"]["overdue"] is False
    # Urgency ordering still puts the overdue task first.
    assert tasks[0]["text"] == "Overdue thing"


def test_org_timeline_renders_task_deadlines(db, client):
    """The timeline unions deadline into a text column alongside NULLs; the cast has to
    keep that union well-typed."""
    client.post("/api/notes/intake", json={
        "group": "Acme Corp", "date": "2026-07-15",
        "action_items": "Send the letter due 2026-08-01",
    })
    orgs = client.get("/api/organizations").get_json()
    if isinstance(orgs, dict):
        orgs = orgs.get("organizations", [])
    assert orgs, "intake should have created an organization"
    org_id = orgs[0]["id"]
    resp = client.get(f"/api/organizations/{org_id}/timeline")
    assert resp.status_code == 200, resp.get_data(as_text=True)
