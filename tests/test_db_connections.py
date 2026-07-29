"""Connection lifecycle: one connection per request, with nested blocks isolated.
Require Postgres."""
import app as app_mod


def _count_connects(app, monkeypatch):
    """Wrap _connect so we can count real connects during a request."""
    calls = []
    real = app._connect

    def counting():
        calls.append(1)
        return real()

    monkeypatch.setattr(app, "_connect", counting)
    return calls


def test_stats_request_opens_one_connection(db, client, monkeypatch):
    """/api/stats alone used to open three: db_get_all_tasks, completions_per_day, and its
    own block. A home-page load cost seven to nine connects, each a fresh TLS handshake."""
    calls = _count_connects(db, monkeypatch)
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    assert len(calls) == 1, f"expected a single connection per request, got {len(calls)}"


def test_search_request_opens_one_connection(db, client, monkeypatch):
    """/api/search nested db_get_all_meetings inside an already-open cursor, so every
    keystroke in the global search overlay opened a second connection."""
    calls = _count_connects(db, monkeypatch)
    resp = client.get("/api/search?q=acme")
    assert resp.status_code == 200
    assert len(calls) == 1, f"expected a single connection per request, got {len(calls)}"


def test_request_writes_are_committed(db, client):
    """The commit moved to teardown; writes must still be durable after the response."""
    resp = client.post("/api/tasks/add", json={"text": "persisted task"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    with db.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM tasks WHERE text = 'persisted task'")
            assert cur.fetchone()["c"] == 1


def test_swallowed_nested_failure_does_not_poison_the_request(db, client, monkeypatch):
    """log_completion catches its own errors. Sharing one connection without savepoints
    would leave the transaction aborted and fail every later statement in the request."""
    def exploding_log(*a, **kw):
        try:
            with app_mod.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM table_that_does_not_exist")
        except Exception:
            pass  # mirrors log_completion's swallow

    with db.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (id, text, type, source_filename, section) "
                "VALUES ('t-poison','Toggle me','free','tasks.md','free')"
            )

    monkeypatch.setattr(app_mod, "log_completion", exploding_log)
    resp = client.post("/api/tasks/toggle", json={"id": "t-poison", "done": True})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    # The toggle itself must still have committed.
    with db.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT done FROM tasks WHERE id = 't-poison'")
            assert cur.fetchone()["done"] is True


def test_get_db_outside_a_request_still_commits(db):
    """init_db and the cron jobs call get_db with no request context."""
    with db.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (id, text, type, source_filename, section) "
                "VALUES ('t-nocontext','No context','free','tasks.md','free')"
            )
    with db.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM tasks WHERE id = 't-nocontext'")
            assert cur.fetchone()["c"] == 1
