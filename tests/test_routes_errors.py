"""Route tests for the JSON error envelope (Phase 3 / audit M5)."""


def test_api_error_returns_json_envelope(db, client):
    # Editing a nonexistent task returns a structured error, not opaque HTML.
    resp = client.post("/api/tasks/edit", json={
        "id": "does-not-exist", "new_text": "x",
    })
    assert resp.status_code == 404
    data = resp.get_json()
    assert data["ok"] is False
    assert "error" in data


def test_fail_helper_shape(app_db):
    with app_db.app.test_request_context():
        resp, code = app_db.fail("nope", 422)
        assert code == 422
        assert resp.get_json() == {"ok": False, "error": "nope"}
