"""Route tests for stable people & orgs (Phase 5 / audit H5, H7). Require Postgres."""


def _mk_meeting(app, mid="m-p5", group="Acme Corp"):
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meetings (id, filename, canonical_group) VALUES (%s,%s,%s)",
                (mid, f"{mid}.md", group))
    return mid


def test_upsert_matches_by_email_case_insensitive(db, client):
    r1 = client.post("/api/contacts", json={"name": "Jane", "email": "jane@x.com"}).get_json()
    r2 = client.post("/api/contacts", json={"name": "Jane Smith", "email": "JANE@x.com"}).get_json()
    assert r1["id"] == r2["id"]  # matched by lower(email), not a new row


def test_upsert_new_name_gets_uuid_not_hash(db, client):
    r = client.post("/api/contacts", json={"name": "Bob Unique"}).get_json()
    assert r["ok"] and len(r["id"]) == 16
    # A different person with a different name must not collide.
    r2 = client.post("/api/contacts", json={"name": "Carol Other"}).get_json()
    assert r2["id"] != r["id"]


def test_org_for_name_resolves_after_rename(db):
    app = db
    with app.get_db() as conn:
        with conn.cursor() as cur:
            oid = app._org_for_name(cur, "Acme Corp")
            # Rename the org (id stays the slug).
            cur.execute("UPDATE organizations SET name=%s WHERE id=%s", ("Acme Corporation", oid))
            again = app._org_for_name(cur, "Acme Corporation")
    assert again == oid  # resolved by name, no new slug forked (H7)


def test_merge_repoints_all_references(db, client):
    app = db
    mid = _mk_meeting(app)
    a = client.post("/api/contacts", json={"name": "Dup A", "phone": "555-1111"}).get_json()["id"]
    b = client.post("/api/contacts", json={"name": "Dup B", "email": "b@x.com"}).get_json()["id"]
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO meeting_contacts (meeting_id, contact_id) VALUES (%s,%s)", (mid, a))
            cur.execute("INSERT INTO asks (id, meeting_id, text, contact_id) VALUES (%s,%s,%s,%s)",
                        ("ask-1", mid, "please help", a))
            cur.execute("INSERT INTO entity_notes (entity_type, entity_id, body) VALUES ('contact',%s,%s)",
                        (a, "a note"))

    resp = client.post(f"/api/contacts/{a}/merge", json={"into_id": b})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM contacts WHERE id=%s", (a,))
            assert cur.fetchone() is None  # loser deleted
            cur.execute("SELECT contact_id FROM asks WHERE id='ask-1'")
            assert cur.fetchone()["contact_id"] == b
            cur.execute("SELECT contact_id FROM meeting_contacts WHERE meeting_id=%s", (mid,))
            assert cur.fetchone()["contact_id"] == b
            cur.execute("SELECT entity_id FROM entity_notes WHERE entity_type='contact'")
            assert cur.fetchone()["entity_id"] == b
            # Richest fields kept: winner gains the loser's phone.
            cur.execute("SELECT phone, email FROM contacts WHERE id=%s", (b,))
            row = cur.fetchone()
            assert row["phone"] == "555-1111" and row["email"] == "b@x.com"


def test_merge_rejects_self(db, client):
    a = client.post("/api/contacts", json={"name": "Solo"}).get_json()["id"]
    resp = client.post(f"/api/contacts/{a}/merge", json={"into_id": a})
    assert resp.status_code == 400
