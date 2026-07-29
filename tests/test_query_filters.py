"""SQL-resolved list filters must agree with the in-memory reference (#12).
Require Postgres."""
import itertools


def _seed(app, client):
    notes = [
        {"group": "Acme Corp", "topic": "Budget briefing", "date": "2026-07-10",
         "attendees": "Jane Smith; Bob Lee", "meeting_type": "briefing",
         "body": "Discussed the widget subsidy.",
         "action_items": "Draft the subsidy memo"},
        {"group": "Acme Corp", "topic": "Follow-up", "date": "2026-07-20",
         "attendees": "Jane Smith", "meeting_type": "1on1",
         "body": "Short check-in about timing."},
        {"group": "Beta Coalition", "topic": "Markup prep", "date": "2026-08-01",
         "attendees": "Carla Diaz", "meeting_type": "constituent",
         "purpose_val": "Casework",
         "body": "They asked about the widget rule.",
         "action_items": "Send the coalition letter"},
        {"group": "Gamma Group", "topic": "Intro call", "date": "2026-06-01",
         "attendees": "Dan Osei", "meeting_type": "other",
         "body": "Nothing notable."},
    ]
    for n in notes:
        r = client.post("/api/notes/intake", json=n)
        assert r.status_code == 200, r.get_data(as_text=True)


FILTERS = {
    "q": ["", "widget", "Jane", "Acme", "subsidy memo", "coalition letter", "zzz-no-match"],
    "group": ["", "Acme Corp", "Beta Coalition"],
    "purpose": ["", "Briefing", "1:1", "Casework"],
    "attendee": ["", "Jane Smith", "carla"],
    "date_from": ["", "2026-07-01"],
    "date_to": ["", "2026-07-31"],
    "has_open_tasks": [False, True],
}


def test_sql_filters_match_the_in_memory_reference(db, client):
    """Every predicate moved into SQL is checked against filter_meetings, which is retained
    as the reference implementation, across the cross product of filter values."""
    app = db
    _seed(app, client)
    all_meetings = app.db_get_all_meetings()

    keys = list(FILTERS)
    combos = itertools.product(*(FILTERS[k] for k in keys))
    checked = 0
    for combo in combos:
        kwargs = dict(zip(keys, combo))
        expected = [m.id for m in app.filter_meetings(all_meetings, **kwargs)]
        actual = [m.id for m in app.db_query_meetings(**kwargs)[0]]
        assert actual == expected, f"divergence for {kwargs}: {actual} != {expected}"
        checked += 1
    assert checked > 100, f"expected a broad sweep, only checked {checked}"


def test_meetings_pagination(db, client):
    app = db
    _seed(app, client)
    page, total = app.db_query_meetings(limit=2, offset=0)
    assert total == 4 and len(page) == 2
    page2, total2 = app.db_query_meetings(limit=2, offset=2)
    assert total2 == 4 and len(page2) == 2
    assert {m.id for m in page}.isdisjoint({m.id for m in page2})

    resp = client.get("/api/meetings?limit=2&offset=0").get_json()
    assert resp["total"] == 4 and resp["count"] == 2


def test_search_still_matches_across_fields(db, client):
    """The SQL free-text branch mirrors _matches_query field for field, including the
    EXISTS over task text."""
    _seed(db, client)
    for term, expected in [
        ("widget", 2),        # body of two notes
        ("subsidy memo", 1),  # action-item text only
        ("Carla", 1),         # attendees
        ("Markup prep", 1),   # topic
    ]:
        hits = client.get(f"/api/meetings?q={term}").get_json()
        assert hits["total"] == expected, f"{term!r} -> {hits['total']}, want {expected}"


# ---- task filters ----------------------------------------------------------

def _add(client, **kw):
    resp = client.post("/api/tasks/add", json=kw)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def test_task_filters_resolve_in_sql(db, client):
    import datetime as _dt
    today = _dt.date.today()
    _add(client, text="High priority thing", priority="high", group="Acme Corp")
    _add(client, text="Low priority thing", priority="low", group="Beta Coalition")
    _add(client, text="Overdue thing", deadline=(today - _dt.timedelta(days=2)).isoformat())
    _add(client, text="Future thing", deadline=(today + _dt.timedelta(days=9)).isoformat())

    def ids(qs=""):
        return {t["text"] for t in client.get("/api/tasks?" + qs).get_json()["tasks"]}

    assert ids("priority=high") == {"High priority thing"}
    assert ids("priority=low") == {"Low priority thing"}
    assert ids("overdue=1") == {"Overdue thing"}
    assert ids("q=priority") == {"High priority thing", "Low priority thing"}
    assert ids("q=Acme") == {"High priority thing"}, "q also matches the group name"
    assert ids(f"deadline={(today + _dt.timedelta(days=9)).isoformat()}") == {"Future thing"}
    assert len(ids()) == 4


def test_groups_in_scope_ignores_the_group_filter(db, client):
    """The group picker lists every group in the rest of the result set, so the filter it
    drives must be applied after that list is built."""
    _add(client, text="One", group="Acme Corp")
    _add(client, text="Two", group="Beta Coalition")
    data = client.get("/api/tasks?group=Acme Corp").get_json()
    assert [t["text"] for t in data["tasks"]] == ["One"]
    assert data["groups_in_scope"] == ["Acme Corp", "Beta Coalition"]


def test_snoozed_tasks_are_hidden_then_shown(db, client):
    import datetime as _dt
    _add(client, text="Snoozed thing")
    _add(client, text="Visible thing")
    later = (_dt.date.today() + _dt.timedelta(days=5)).isoformat()
    tid = [t["id"] for t in client.get("/api/tasks").get_json()["tasks"]
           if t["text"] == "Snoozed thing"][0]
    client.post("/api/tasks/snooze", json={"id": tid, "until": later})

    assert {t["text"] for t in client.get("/api/tasks").get_json()["tasks"]} == {"Visible thing"}
    assert {t["text"] for t in client.get("/api/tasks?snoozed=1").get_json()["tasks"]} == {"Snoozed thing"}


def test_tasks_pagination(db, client):
    for i in range(6):
        _add(client, text=f"Task {i}")
    data = client.get("/api/tasks?limit=2").get_json()
    assert data["total"] == 6 and data["count"] == 2
    rest = client.get("/api/tasks?limit=2&offset=2").get_json()
    assert rest["count"] == 2
    assert {t["id"] for t in data["tasks"]}.isdisjoint({t["id"] for t in rest["tasks"]})
