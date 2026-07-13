"""Parser pure-function tests (no DB): bills, orgs, triggers, ICS, floor weeks,
and a Python port of the client callout grammar."""
import re
from datetime import date, timedelta
from pathlib import Path

import app

FIXTURES = Path(__file__).parent / "fixtures"


# ---- bill normalization ----------------------------------------------------

def test_normalize_bill_type():
    assert app._normalize_bill_type("H.R.") == "HR"
    assert app._normalize_bill_type(" s ") == "S"
    assert app._normalize_bill_type("H. Res.") == "HRES"


def test_normalize_bill_number():
    assert app._normalize_bill_number("No. 1234") == "1234"
    assert app._normalize_bill_number("HR-5") == "5"
    assert app._normalize_bill_number("") == ""


# ---- org slug --------------------------------------------------------------

def test_org_slug():
    assert app._org_slug("Acme Corp") == "acme-corp"
    assert app._org_slug("  Multiple   Spaces!! ") == "multiple-spaces"
    assert app._org_slug("") == "org"


# ---- trigger parsing (C2) --------------------------------------------------

def test_trigger_split_condition_action():
    cond, action = app._parse_trigger_text("FU IF bill moves -> email the team")
    assert cond == "bill moves"
    assert action == "email the team"


def test_trigger_arrow_unicode():
    cond, action = app._parse_trigger_text("FU IF markup scheduled → prep memo")
    assert cond == "markup scheduled"
    assert action == "prep memo"


def test_trigger_does_not_eat_leading_letters():
    # C2 regression: the condition begins with 'I' (and contains F/U). A char-set
    # lstrip('FU IF') would mangle it to 'ncrease staff pay'; the anchored regex must not.
    cond, _ = app._parse_trigger_text("Increase staff pay → ping me")
    assert cond == "Increase staff pay"


def test_trigger_strips_marker_when_present():
    cond, action = app._parse_trigger_text("FU IF Increase staff pay → ping me")
    assert cond == "Increase staff pay"
    assert action == "ping me"


# ---- floor weeks feed ------------------------------------------------------

def _build_floor_feed(dates):
    entries = "".join(
        f'<entry><title>Week of {d.isoformat()}</title>'
        f'<link rel="alternate" href="https://docs.house.gov/floor/?date={d.isoformat()}"/>'
        f'<content type="html">x</content></entry>'
        for d in dates
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">' + entries + '</feed>'
    ).encode("utf-8")


def test_floor_weeks_dedupes_and_drops_stale():
    today = app.app_today()
    this_monday = today - timedelta(days=today.weekday())
    next_monday = this_monday + timedelta(days=7)
    stale = date(2015, 1, 5)
    # this_monday appears twice (Update 1/2) -> collapses to one.
    raw = _build_floor_feed([this_monday, this_monday, next_monday, stale])
    weeks = app._parse_floor_weeks_feed(raw)
    got_dates = [d for d, _ in weeks]
    assert got_dates == [this_monday, next_monday]
    # ymd string is the folder key.
    assert weeks[0][1] == this_monday.strftime("%Y%m%d")


def test_floor_weeks_fixture_parses():
    raw = FIXTURES.joinpath("floor_weeks.xml").read_bytes()
    weeks = app._parse_floor_weeks_feed(raw)
    assert isinstance(weeks, list)  # date-filtered count is time-dependent; structure only


# ---- ICS parsing -----------------------------------------------------------

def test_parse_ics_single_event():
    raw = FIXTURES.joinpath("single_event.ics").read_bytes()
    ev = app.parse_ics_content(raw)
    assert ev is not None
    assert ev["summary"] == "Budget review with Acme Corp"
    assert ev["organizer"] == "jane@acme.example"
    emails = {a["email"] for a in ev["attendees"]}
    assert emails == {"bob@example.com", "carol@example.com"}
    # America/New_York 14:00 on 2026-07-15 is 18:00 UTC (EDT, -4).
    assert ev["dtstart"].startswith("2026-07-15T18:00:00")


def test_parse_ics_garbage_returns_none():
    assert app.parse_ics_content(b"not an ics file") is None


# ---- callout grammar port (documents the client _extractCallouts spec) -----

def _extract_callouts(text):
    """Python port of static/app.js `_extractCallouts` (line-marker subset, no bills).

    Documents the intake callout grammar so the server and client stay in agreement.
    """
    items = []

    def _has_due(s):
        return bool(re.search(r"\bdue[:\s]", s, re.I) or re.search(r"\bdeadline[:\s]", s, re.I))

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^(\[.*?\]|□|☐)\s*", line):
            t = re.sub(r"^(\[.*?\]|□|☐)\s*", "", line).strip()
            if t:
                items.append({"type": "task", "text": t})
                if _has_due(t):
                    items.append({"type": "deadline", "text": t})
        elif re.match(r"^!+\s*\S", line):
            t = re.sub(r"^!+\s*", "", line).strip()
            if t:
                items.append({"type": "important", "text": t})
                if _has_due(t):
                    items.append({"type": "deadline", "text": t})
        elif re.match(r"^\?+\s*\S", line):
            t = re.sub(r"^\?+\s*", "", line).strip()
            if t:
                items.append({"type": "followup", "text": t})
                if _has_due(t):
                    items.append({"type": "deadline", "text": t})
        elif re.match(r"^~~\s*\S", line) or re.match(r"^ASK\s+", line, re.I):
            t = re.sub(r"^ASK\s+", "", re.sub(r"^~~\s*", "", line), flags=re.I).strip()
            if t:
                items.append({"type": "ask", "text": t})
        elif re.match(r"^>>>\s*\S", line) or re.match(r"^COMMIT\s+", line, re.I):
            t = re.sub(r"^COMMIT\s+", "", re.sub(r"^>>>\s*", "", line), flags=re.I).strip()
            if t:
                items.append({"type": "commitment", "text": t})
        elif re.match(r"^FU\s+IF\s+", line, re.I):
            t = re.sub(r"^FU\s+IF\s+", "", line, flags=re.I).strip()
            if t:
                items.append({"type": "trigger", "text": t})
        elif _has_due(line):
            items.append({"type": "deadline", "text": line})
        elif re.match(r"^@([A-Za-z]\w*)", line):
            items.append({"type": "person", "text": re.sub(r"^@", "", line).strip()})
    return items


def test_callout_grammar_marker_types():
    text = "\n".join([
        "[ ] draft the letter",
        "! urgent flag",
        "? follow up on vote",
        "~~ Acme asked for a meeting",
        ">>> I will send the report",
        "FU IF bill moves → email team",
        "@Jane",
        "circle back due 2026-08-01",
    ])
    items = _extract_callouts(text)
    types = [i["type"] for i in items]
    assert types == [
        "task", "important", "followup", "ask", "commitment",
        "trigger", "person", "deadline",
    ]
    assert items[3]["text"] == "Acme asked for a meeting"
    assert items[5]["text"] == "bill moves → email team"
    assert items[6]["text"] == "Jane"


def test_callout_task_with_due_emits_deadline():
    items = _extract_callouts("[ ] file report due 2026-09-01")
    assert [i["type"] for i in items] == ["task", "deadline"]
