"""Security-behaviour tests: CSRF origin check, login throttle, secret comparison,
and the error envelope's non-disclosure. Require Postgres."""
import app as app_mod


# ---- CSRF origin check (#9) ------------------------------------------------

def test_cross_site_post_is_blocked(db, client):
    """The API reads bodies with get_json(force=True), which ignores Content-Type, so a
    cross-site form POST was enough to drive any write endpoint on the session cookie."""
    resp = client.post(
        "/api/tasks/add",
        json={"text": "injected"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "Cross-site request blocked"
    with db.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM tasks")
            assert cur.fetchone()["c"] == 0, "blocked request must not have written"


def test_same_origin_post_is_allowed(db, client):
    resp = client.post(
        "/api/tasks/add",
        json={"text": "legitimate"},
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_post_without_origin_is_allowed(db, client):
    """curl and the Shortcuts app send no Origin and carry no cookie; a missing header is
    not evidence of a cross-site request."""
    resp = client.post("/api/tasks/add", json={"text": "from a script"})
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_cross_site_get_is_allowed(db, client):
    """Only state-changing methods are gated."""
    resp = client.get("/api/tasks", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 200


def test_key_auth_endpoints_are_exempt_from_origin_check(db, client, monkeypatch):
    """Key-authenticated endpoints have no ambient cookie authority to abuse."""
    monkeypatch.setattr(app_mod, "SHORTCUT_API_KEY", "k" * 32)
    resp = client.post(
        "/api/shortcut/add-task",
        json={"text": "from the phone"},
        headers={"X-API-Key": "k" * 32, "Origin": "https://shortcuts.example"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)


# ---- secret comparison (#8) ------------------------------------------------

def test_secrets_match_rejects_unset_expected():
    """An unset credential must never authenticate, even against an empty submission."""
    assert app_mod.secrets_match("", "") is False
    assert app_mod.secrets_match("anything", "") is False


def test_secrets_match_compares_correctly():
    assert app_mod.secrets_match("hunter2", "hunter2") is True
    assert app_mod.secrets_match("hunter3", "hunter2") is False


def test_login_rejects_when_no_password_configured(db, monkeypatch):
    monkeypatch.setattr(app_mod, "AUTH_PASSWORD", "")
    c = app_mod.app.test_client()
    resp = c.post("/login", data={"username": "admin", "password": ""})
    assert resp.status_code == 200  # re-renders the form, does not authenticate
    with c.session_transaction() as sess:
        assert "logged_in" not in sess


def test_login_throttles_repeated_failures(db, monkeypatch):
    monkeypatch.setattr(app_mod, "AUTH_USERNAME", "will")
    monkeypatch.setattr(app_mod, "AUTH_PASSWORD", "correct-horse")
    app_mod._LOGIN_ATTEMPTS.clear()
    c = app_mod.app.test_client()
    for _ in range(app_mod._LOGIN_MAX_ATTEMPTS):
        c.post("/login", data={"username": "will", "password": "wrong"})
    resp = c.post("/login", data={"username": "will", "password": "wrong"})
    assert resp.status_code == 429
    # Even the correct password is refused while the window is open.
    resp = c.post("/login", data={"username": "will", "password": "correct-horse"})
    assert resp.status_code == 429
    app_mod._LOGIN_ATTEMPTS.clear()


def test_successful_login_clears_the_throttle(db, monkeypatch):
    monkeypatch.setattr(app_mod, "AUTH_USERNAME", "will")
    monkeypatch.setattr(app_mod, "AUTH_PASSWORD", "correct-horse")
    app_mod._LOGIN_ATTEMPTS.clear()
    c = app_mod.app.test_client()
    c.post("/login", data={"username": "will", "password": "wrong"})
    resp = c.post("/login", data={"username": "will", "password": "correct-horse"})
    assert resp.status_code == 302
    with c.session_transaction() as sess:
        assert sess["logged_in"] is True
    app_mod._LOGIN_ATTEMPTS.clear()


# ---- error envelope non-disclosure (#6) ------------------------------------

def test_server_error_does_not_echo_exception_text(db, client, monkeypatch):
    """psycopg2 errors name tables, columns and constraints; they must not reach the
    browser. The response carries only a reference to find the entry in the logs."""
    def boom():
        raise RuntimeError("relation \"secret_internal_table\" does not exist")

    monkeypatch.setattr(app_mod, "db_query_tasks", lambda **kw: boom())
    resp = client.get("/api/tasks")
    assert resp.status_code == 500
    body = resp.get_data(as_text=True)
    assert "secret_internal_table" not in body
    assert "RuntimeError" not in body
    data = resp.get_json()
    assert data["ok"] is False and len(data["ref"]) == 8


# ---- upload ceiling (#7) ---------------------------------------------------

def test_oversized_body_is_rejected(db, client):
    """Intake and card-scan take base64 data URLs; the body size was unbounded."""
    limit = app_mod.app.config["MAX_CONTENT_LENGTH"]
    assert limit and limit > 0
    resp = client.post(
        "/api/notes/intake",
        data=b"x" * (limit + 1024),
        content_type="application/json",
    )
    assert resp.status_code == 413


# ---- session key resolution (#5) -------------------------------------------

def test_dev_secret_key_with_a_database_is_replaced():
    """A deployment must never serve sessions signed with the key published in this repo.
    Refusing to boot would close the hole but take the app down over a missing environment
    variable; substituting a random key keeps it up and the cookies unforgeable."""
    resolved = app_mod.resolve_secret_key(
        "postgresql://user:pw@localhost/db", app_mod._DEV_SECRET_KEY
    )
    assert resolved != app_mod._DEV_SECRET_KEY
    assert len(resolved) >= 32


def test_replacement_keys_are_not_predictable():
    a = app_mod.resolve_secret_key("postgresql://x/db", app_mod._DEV_SECRET_KEY)
    b = app_mod.resolve_secret_key("postgresql://x/db", app_mod._DEV_SECRET_KEY)
    assert a != b


def test_dev_secret_key_without_a_database_is_left_alone():
    """Local development has no database and no exposure; a stable key is more useful."""
    assert app_mod.resolve_secret_key("", app_mod._DEV_SECRET_KEY) == app_mod._DEV_SECRET_KEY


def test_a_configured_secret_key_is_used_verbatim():
    configured = "a" * 64
    assert app_mod.resolve_secret_key("postgresql://x/db", configured) == configured


def test_resolution_is_skippable_for_tests():
    assert app_mod.resolve_secret_key(
        "postgresql://x/db", app_mod._DEV_SECRET_KEY, skip=True
    ) == app_mod._DEV_SECRET_KEY
