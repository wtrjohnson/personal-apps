"""Route tests for stable task identity (Phase 2 / audit C3, M15). Require a Postgres."""


MEETING_BODY = """---
group: Acme Corp
date: 2026-07-15
topic: Budget review
---

Action Items:
- [ ] Draft the appropriations letter
- [ ] Circle back with the whip office
"""


def _import(app, filename="2026-07-15 - Acme Corp.md", body=MEETING_BODY):
    return app.import_meeting_from_content(filename, body)


def _task_by_text(app, text):
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tasks WHERE text = %s", (text,))
            return cur.fetchone()


def test_edit_keeps_stable_id_with_fk_reference(db, client):
    app = db
    _import(app)
    original = _task_by_text(app, "Draft the appropriations letter")
    assert original is not None
    tid = original["id"]
    assert original["import_key"] == tid  # backfill: meeting-sourced id == import_key

    # Add a subtask (FK: parent_id) and a dependency so an id rewrite would 500.
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (id, text, type, source_filename, parent_id) "
                "VALUES ('sub1', 'sub', 'free', 'tasks.md', %s)", (tid,))

    resp = client.post("/api/tasks/edit", json={
        "id": tid,
        "old_text": "Draft the appropriations letter",
        "new_text": "Draft the FY26 appropriations letter",
        "section": "action_items",
        "source_filename": "2026-07-15 - Acme Corp.md",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)

    edited = _task_by_text(app, "Draft the FY26 appropriations letter")
    assert edited is not None
    assert edited["id"] == tid            # id never rewritten (C3)
    assert edited["import_locked"] is True
    # Subtask still points at the (unchanged) parent id.
    sub = _task_by_text(app, "sub")
    assert sub["parent_id"] == tid


def test_reimport_does_not_revert_edited_task(db, client):
    app = db
    _import(app)
    tid = _task_by_text(app, "Draft the appropriations letter")["id"]
    client.post("/api/tasks/edit", json={
        "id": tid,
        "new_text": "EDITED TEXT",
        "section": "action_items",
        "source_filename": "2026-07-15 - Acme Corp.md",
    })
    # Re-importing the same file must NOT overwrite the user's edit (M15).
    _import(app)
    assert _task_by_text(app, "EDITED TEXT") is not None
    assert _task_by_text(app, "Draft the appropriations letter") is None


def test_deleted_task_stays_deleted_after_reimport(db, client):
    app = db
    _import(app)
    tid = _task_by_text(app, "Circle back with the whip office")["id"]
    resp = client.post("/api/tasks/delete", json={"id": tid})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _task_by_text(app, "Circle back with the whip office") is None
    # Tombstone recorded; re-import must not resurrect it.
    _import(app)
    assert _task_by_text(app, "Circle back with the whip office") is None


def test_untouched_task_still_follows_file(db, client):
    app = db
    _import(app)
    # A task nobody edited should update when the source file's text changes.
    body2 = MEETING_BODY.replace(
        "Circle back with the whip office", "Circle back with leadership"
    )
    _import(app, body=body2)
    assert _task_by_text(app, "Circle back with leadership") is not None


def _seed_recurring(app, tid="rec1", rule=None):
    import json as _json
    rule = rule or {"type": "daily", "interval": 7}
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (id, text, type, source_filename, section, deadline, "
                " recurrence_rule) "
                "VALUES (%s, 'Weekly check-in', 'free', 'tasks.md', 'free', '2026-07-15', %s)",
                (tid, _json.dumps(rule)),
            )
    return tid


def _instances(app):
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, done, deadline, recurrence_parent_id FROM tasks "
                        "WHERE recurrence_parent_id IS NOT NULL")
            return cur.fetchall()


def test_recompleting_a_recurring_task_does_not_fork_the_series(db, client):
    """Regression for #2: completing spawned an instance, un-completing left it orphaned,
    and completing again spawned another — a misclick permanently forked the series."""
    app = db
    tid = _seed_recurring(app)

    client.post("/api/tasks/toggle", json={"id": tid, "done": True})
    assert len(_instances(app)) == 1

    client.post("/api/tasks/toggle", json={"id": tid, "done": False})
    assert _instances(app) == [], "un-completing must withdraw the spawned instance"

    client.post("/api/tasks/toggle", json={"id": tid, "done": True})
    assert len(_instances(app)) == 1, "re-completing must not create a second instance"

    # And completing repeatedly without an intervening undo is also idempotent.
    client.post("/api/tasks/toggle", json={"id": tid, "done": True})
    assert len(_instances(app)) == 1


def test_undo_preserves_an_instance_the_user_already_completed(db, client):
    """Undoing a checkbox must not delete work the user has actually done."""
    app = db
    tid = _seed_recurring(app)
    client.post("/api/tasks/toggle", json={"id": tid, "done": True})
    spawned = _instances(app)[0]["id"]
    client.post("/api/tasks/toggle", json={"id": spawned, "done": True})

    client.post("/api/tasks/toggle", json={"id": tid, "done": False})
    remaining = [r["id"] for r in _instances(app)]
    assert spawned in remaining, "a completed instance must survive undoing its parent"


def test_weekly_pct_ignores_backlog_outside_the_week(db, client):
    """Regression for #14: pct_complete divided a 7-day completion count by the entire
    open backlog, so the home ring could only drift toward zero as the backlog grew.
    Tasks with no deadline, or one well in the future, are not 'due this week'."""
    app = db
    with app.get_db() as conn:
        with conn.cursor() as cur:
            # One task due today, completed. Plus a large undated backlog.
            cur.execute(
                "INSERT INTO tasks (id, text, type, source_filename, section, deadline, done) "
                "VALUES ('t-due','Due today','free','tasks.md','free',%s,TRUE)",
                (app.app_today().isoformat(),),
            )
            for i in range(40):
                cur.execute(
                    "INSERT INTO tasks (id, text, type, source_filename, section) "
                    "VALUES (%s,'Someday backlog','free','tasks.md','free')",
                    (f"t-backlog-{i}",),
                )
            cur.execute(
                "INSERT INTO completions (task_id, task_text, done, completed_date) "
                "VALUES ('t-due','Due today',TRUE,%s)",
                (app.app_today(),),
            )

    s = client.get("/api/stats").get_json()
    assert s["completions_this_week"] == 1
    assert s["week_outstanding"] == 0, "undated backlog is not due this week"
    assert s["pct_complete"] == 100, (
        f"expected 100% (1 of 1 due this week), got {s['pct_complete']}% — "
        "the denominator is counting the whole backlog again"
    )
