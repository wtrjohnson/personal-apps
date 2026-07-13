"""Route tests for working lifecycles (Phase 8 / audit H3, C2b). Require Postgres."""


def test_trigger_full_lifecycle(db, client):
    app = db
    t = client.post("/api/followup-triggers", json={"condition_text": "bill moves", "action_text": "ping"}).get_json()
    tid = t["id"]
    assert client.post(f"/api/followup-triggers/{tid}", json={"status": "fired"}).status_code == 200
    assert client.post(f"/api/followup-triggers/{tid}", json={"status": "resolved"}).status_code == 200
    # Invalid status rejected.
    assert client.post(f"/api/followup-triggers/{tid}", json={"status": "bogus"}).status_code == 400
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM followup_triggers WHERE id=%s", (tid,))
            assert cur.fetchone()["status"] == "resolved"
    assert client.delete(f"/api/followup-triggers/{tid}").status_code == 200
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM followup_triggers WHERE id=%s", (tid,))
            assert cur.fetchone() is None


def test_ask_create_and_create_task(db, client):
    app = db
    a = client.post("/api/asks", json={"text": "support the bill", "priority": "high"}).get_json()
    aid = a["id"]
    r = client.post(f"/api/asks/{aid}/create-task").get_json()
    assert r["ok"] and r["task_id"]
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, task_id FROM asks WHERE id=%s", (aid,))
            row = cur.fetchone()
            assert row["status"] == "accepted" and row["task_id"] == r["task_id"]
            cur.execute("SELECT ask_id FROM tasks WHERE id=%s", (r["task_id"],))
            assert cur.fetchone()["ask_id"] == aid
    # Second call is idempotent.
    r2 = client.post(f"/api/asks/{aid}/create-task").get_json()
    assert r2.get("already_exists") is True


def test_ask_status_canonical_only(db, client):
    a = client.post("/api/asks", json={"text": "x"}).get_json()["id"]
    assert client.post(f"/api/asks/{a}/status", json={"status": "declined"}).status_code == 200
    assert client.post(f"/api/asks/{a}/status", json={"status": "completed"}).status_code == 400  # legacy value rejected


def test_commitment_create_and_status(db, client):
    c = client.post("/api/commitments", json={"text": "send memo", "due_date": "2026-08-01"}).get_json()["id"]
    assert client.post(f"/api/commitments/{c}/status", json={"status": "done"}).status_code == 200
    assert client.post(f"/api/commitments/{c}/status", json={"status": "task_created"}).status_code == 400


def test_status_migration_maps_legacy(db, client):
    app = db
    # Drop the constraints so we can seed legacy statuses, then re-run migrate to remap.
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE asks DROP CONSTRAINT IF EXISTS asks_status_check")
            cur.execute("ALTER TABLE commitments DROP CONSTRAINT IF EXISTS commitments_status_check")
            cur.execute("INSERT INTO asks (id, text, status) VALUES ('lg1','x','logged')")
            cur.execute("INSERT INTO commitments (id, text, status) VALUES ('lg2','y','waiting')")
    app.init_db()
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM asks WHERE id='lg1'")
            assert cur.fetchone()["status"] == "open"
            cur.execute("SELECT status FROM commitments WHERE id='lg2'")
            assert cur.fetchone()["status"] == "in_progress"
