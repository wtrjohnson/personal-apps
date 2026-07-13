"""Route tests for the Inbox acknowledgment model (Phase 10). Require Postgres."""


def test_inbox_ack_clears_count(db, client):
    app = db
    client.post("/api/notes/intake", json={
        "group": "Acme", "date": app.app_today().isoformat(),
        "confirmed_items": [{"type": "ask", "text": "do X"}, {"type": "task", "text": "do Y"}],
    })
    assert client.get("/api/scan-items/inbox-count").get_json()["count"] == 2
    mid = client.get("/api/scan-items").get_json()["meetings"][0]["meeting_id"]
    assert client.post(f"/api/scan-items/ack-meeting/{mid}", json={}).status_code == 200
    assert client.get("/api/scan-items/inbox-count").get_json()["count"] == 0
    data = client.get("/api/scan-items").get_json()
    assert all(i["acknowledged"] for m in data["meetings"] for i in m["items"])


def test_inbox_single_item_ack(db, client):
    app = db
    client.post("/api/notes/intake", json={
        "group": "Acme", "date": app.app_today().isoformat(),
        "confirmed_items": [{"type": "task", "text": "one"}, {"type": "task", "text": "two"}],
    })
    items = [i for m in client.get("/api/scan-items").get_json()["meetings"] for i in m["items"]]
    assert client.post(f"/api/scan-items/{items[0]['id']}/ack", json={}).status_code == 200
    assert client.get("/api/scan-items/inbox-count").get_json()["count"] == 1
