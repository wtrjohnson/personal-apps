"""Date / time pure-function tests (no DB)."""
from datetime import date, datetime, timezone

import app


# ---- app timezone helpers (C4) --------------------------------------------

def test_app_now_is_tz_aware():
    now = app.app_now()
    assert now.tzinfo is not None
    assert app.app_today() == now.date()


def test_app_tz_bad_zone_falls_back_to_denver(monkeypatch):
    monkeypatch.setattr(app, "APP_TIMEZONE", "Not/AZone")
    assert app._app_tz().key == "America/Denver"


def test_denver_evening_is_prior_utc_day(monkeypatch):
    # The C4 bug: a late-UTC instant is still the previous calendar day in Denver.
    monkeypatch.setattr(app, "APP_TIMEZONE", "America/Denver")
    tz = app._app_tz()
    utc_dt = datetime(2026, 7, 14, 2, 0, tzinfo=timezone.utc)  # 8 PM MDT on the 13th
    assert utc_dt.astimezone(tz).date().isoformat() == "2026-07-13"


# ---- _normalize_date -------------------------------------------------------

def test_normalize_iso():
    assert app._normalize_date("2025-06-15") == "2025-06-15"


def test_normalize_mdy_slash_two_digit_year():
    assert app._normalize_date("6/15/25") == "2025-06-15"


def test_normalize_md_uses_context_year():
    assert app._normalize_date("6/15", context_year=2024) == "2024-06-15"


def test_normalize_strips_trailing_punctuation():
    assert app._normalize_date("2025-06-15).") == "2025-06-15"


def test_normalize_invalid_returns_none():
    assert app._normalize_date("not a date") is None
    assert app._normalize_date("13/40/2025") is None  # Feb-31 style impossible date


# ---- extract_deadline ------------------------------------------------------

def test_extract_deadline_due_keyword():
    norm, raw = app.extract_deadline("Finish memo due 2025-06-15")
    assert norm == "2025-06-15"
    assert "due 2025-06-15" in raw


def test_extract_deadline_parenthetical():
    norm, _ = app.extract_deadline("Send letter (deadline 7/1/2026)")
    assert norm == "2026-07-01"


def test_extract_deadline_none():
    assert app.extract_deadline("no date here") == (None, None)


# ---- _current_congress (Jan-3 odd-year boundary) ---------------------------

def test_congress_boundary(monkeypatch):
    monkeypatch.delenv("CURRENT_CONGRESS", raising=False)
    # 118th Congress ran 2023-01-03 .. 2025-01-03; 119th begins 2025-01-03.
    assert app._current_congress(date(2025, 1, 2)) == 118
    assert app._current_congress(date(2025, 1, 3)) == 119
    assert app._current_congress(date(2024, 6, 1)) == 118
    assert app._current_congress(date(2023, 1, 3)) == 118


def test_congress_env_override(monkeypatch):
    monkeypatch.setenv("CURRENT_CONGRESS", "200")
    assert app._current_congress(date(2025, 1, 3)) == 200


# ---- _compute_next_recurrence ---------------------------------------------

def test_recurrence_daily():
    assert app._compute_next_recurrence({"type": "daily"}, date(2026, 7, 13)) == date(2026, 7, 14)


def test_recurrence_weekly_next_weekday():
    # Monday 2026-07-13 -> next Wednesday (dow=2) is 2026-07-15.
    got = app._compute_next_recurrence({"type": "weekly", "day_of_week": 2}, date(2026, 7, 13))
    assert got == date(2026, 7, 15)


def test_recurrence_weekly_same_weekday_rolls_forward():
    # Same weekday must land at least 7 days out, never today.
    got = app._compute_next_recurrence({"type": "weekly", "day_of_week": 0}, date(2026, 7, 13))
    assert got == date(2026, 7, 20)


def test_recurrence_monthly_clamps():
    got = app._compute_next_recurrence({"type": "monthly", "day_of_month": 31}, date(2026, 1, 15))
    assert got == date(2026, 2, 28)


def test_recurrence_unknown_returns_none():
    assert app._compute_next_recurrence({"type": "nope"}, date(2026, 7, 13)) is None
