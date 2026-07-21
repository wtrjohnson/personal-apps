"""Committee-meeting schedule sync tests (require Postgres).

These cover the resumable-sweep + fetch-cache behaviour: an upcoming markup that sorts
below the per-run fetch budget must not be lost, and concluded meetings whose updateDate
keeps getting bumped must not keep consuming that budget.
"""
import datetime


def _seed_sponsored_bill(app, congress, btype, bnum):
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tracked_bills (id, congress, bill_type, bill_number, relationship) "
                "VALUES (%s,%s,%s,%s,'sponsored')",
                (f"{congress}-{btype}-{bnum}".lower(), congress, btype, bnum),
            )


def _install_fake_congress(app, monkeypatch, congress, meetings, details, call_log):
    """meetings: list of {eventId, updateDate} newest-first. details: eventId -> committeeMeeting."""
    list_path = f"committee-meeting/{congress}/house"

    def fake_get(path, params=None):
        call_log.append(path)
        if path == list_path:
            offset = (params or {}).get("offset", 0)
            return {"committeeMeetings": meetings if offset == 0 else []}
        eid = path.rsplit("/", 1)[-1]
        return {"committeeMeeting": details[eid]}

    monkeypatch.setattr(app, "_congress_api_get", fake_get)
    monkeypatch.setattr(app, "_current_congress", lambda: congress)
    monkeypatch.setattr(app, "_sync_house_floor", lambda cur, c, keys: (0, True))
    monkeypatch.setattr(app, "app_today", lambda: datetime.date(2026, 7, 21))


def _stored_markups(app):
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT bill_type, bill_number, event_type FROM bill_schedule_events")
            return cur.fetchall()


def _watermark(app):
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT schedule_last_synced FROM bill_sync_meta WHERE id = 1")
            row = cur.fetchone()
            return row["schedule_last_synced"] if row else None


def test_upcoming_markup_below_budget_survives_and_is_caught(db, monkeypatch):
    """A run that stops at the fetch cap before reaching the markup must not advance the
    watermark, and the next run must pick the markup up — without re-fetching the concluded
    meeting that consumed the first run's budget."""
    app = db
    congress = 119
    _seed_sponsored_bill(app, congress, "HR", "1234")

    # Newest-updated first: a concluded hearing (backfilled today) sorts above our markup.
    meetings = [
        {"eventId": "PAST1", "updateDate": "2026-07-21T09:00:00Z"},
        {"eventId": "FUT1", "updateDate": "2026-07-20T09:00:00Z"},
    ]
    details = {
        "PAST1": {"date": "2026-07-10T14:00:00Z", "type": "Hearing", "meetingStatus": "Scheduled",
                  "title": "A concluded hearing", "relatedItems": {"bills": []}},
        "FUT1": {"date": "2026-07-22T14:00:00Z", "type": "Markup", "meetingStatus": "Scheduled",
                 "title": "Markup of H.R. 1234",
                 "relatedItems": {"bills": {"bill": [
                     {"congress": congress, "type": "HR", "number": "1234"}]}}},
    }
    call_log = []
    _install_fake_congress(app, monkeypatch, congress, meetings, details, call_log)
    # Force an early stop after a single detail fetch.
    monkeypatch.setattr(app, "_COMMITTEE_DETAIL_CAP", 1)

    # Run 1: budget consumed by the concluded hearing; markup left unscanned.
    r1 = app._sync_bill_schedule()
    assert r1["committee_more"] is True
    assert _stored_markups(app) == []
    assert _watermark(app) is None, "watermark must not advance while the sweep is incomplete"

    # Run 2: concluded hearing is a cache hit (no re-fetch), so the budget reaches the markup.
    call_log.clear()
    r2 = app._sync_bill_schedule()
    assert f"committee-meeting/{congress}/house/PAST1" not in call_log, \
        "a meeting already resolved to a past date must not be re-fetched"
    rows = _stored_markups(app)
    assert len(rows) == 1
    assert (rows[0]["bill_type"], rows[0]["bill_number"], rows[0]["event_type"]) == ("HR", "1234", "Markup")
    assert r2["committee_more"] is False
    assert _watermark(app) is not None, "watermark advances once the sweep completes"


def test_past_meeting_rebump_is_not_refetched(db, monkeypatch):
    """Once a meeting is dated in the past, a later updateDate bump (post-hoc document
    posting) must not trigger another detail fetch."""
    app = db
    congress = 119
    meetings = [{"eventId": "PAST1", "updateDate": "2026-07-21T09:00:00Z"}]
    details = {"PAST1": {"date": "2026-07-10T14:00:00Z", "type": "Hearing",
                         "meetingStatus": "Scheduled", "title": "Concluded",
                         "relatedItems": {"bills": []}}}
    call_log = []
    _install_fake_congress(app, monkeypatch, congress, meetings, details, call_log)

    app._sync_bill_schedule()  # fetches + caches PAST1 as past
    # Its documents get posted → updateDate bumps.
    meetings[0]["updateDate"] = "2026-07-21T18:00:00Z"
    call_log.clear()
    app._sync_bill_schedule()
    assert f"committee-meeting/{congress}/house/PAST1" not in call_log
