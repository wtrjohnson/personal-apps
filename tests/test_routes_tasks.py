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
