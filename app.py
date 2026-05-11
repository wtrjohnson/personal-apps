#!/usr/bin/env python3
"""Notes Dashboard — Vercel + PostgreSQL edition."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import frontmatter
import markdown as md_lib
import psycopg2
import psycopg2.extras
from flask import (
    Flask, abort, jsonify, redirect, render_template,
    request, session, url_for,
)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
AUTH_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin")
AUTH_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
SHORTCUT_API_KEY = os.environ.get("SHORTCUT_API_KEY", "")

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = SECRET_KEY


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

@contextmanager
def get_db() -> Generator:
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            # Step 1: base tables (no new columns here — added via migration below)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS meetings (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_date DATE,
                    raw_group TEXT DEFAULT '',
                    canonical_group TEXT DEFAULT '',
                    topic TEXT DEFAULT '',
                    purpose JSONB DEFAULT '[]',
                    outcome TEXT DEFAULT '',
                    deadline TEXT DEFAULT '',
                    attendees TEXT DEFAULT '',
                    body TEXT DEFAULT '',
                    body_html TEXT DEFAULT '',
                    mtime DOUBLE PRECISION,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    type TEXT NOT NULL,
                    done BOOLEAN DEFAULT FALSE,
                    backburner BOOLEAN DEFAULT FALSE,
                    meeting_id TEXT REFERENCES meetings(id) ON DELETE CASCADE,
                    source_filename TEXT NOT NULL DEFAULT '',
                    section TEXT NOT NULL DEFAULT '',
                    group_name TEXT,
                    source_date DATE,
                    deadline TEXT,
                    deadline_raw TEXT,
                    priority TEXT DEFAULT 'normal',
                    contact TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS groups_map (
                    raw_name TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS completions (
                    id SERIAL PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    task_text TEXT,
                    section TEXT,
                    source_filename TEXT,
                    done BOOLEAN DEFAULT TRUE,
                    completed_date DATE DEFAULT CURRENT_DATE,
                    completed_at TIMESTAMP DEFAULT NOW()
                );
            """)
            # Step 2: column migrations — must run before creating indexes on new columns
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='tasks' AND column_name='priority'
                    ) THEN
                        ALTER TABLE tasks ADD COLUMN priority TEXT DEFAULT 'normal'
                            CHECK (priority IN ('high','normal','low'));
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='tasks' AND column_name='contact'
                    ) THEN
                        ALTER TABLE tasks ADD COLUMN contact TEXT DEFAULT NULL;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='tasks' AND column_name='parent_id'
                    ) THEN
                        ALTER TABLE tasks ADD COLUMN parent_id TEXT REFERENCES tasks(id) ON DELETE CASCADE;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='tasks' AND column_name='snoozed_until'
                    ) THEN
                        ALTER TABLE tasks ADD COLUMN snoozed_until DATE DEFAULT NULL;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='tasks' AND column_name='estimate_minutes'
                    ) THEN
                        ALTER TABLE tasks ADD COLUMN estimate_minutes INT DEFAULT NULL;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='tasks' AND column_name='recurrence_rule'
                    ) THEN
                        ALTER TABLE tasks ADD COLUMN recurrence_rule JSONB DEFAULT NULL;
                    END IF;
                END $$;
            """)
            # Step 3: new tables and indexes (after columns exist)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS task_time_log (
                    id SERIAL PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    minutes_spent INT NOT NULL,
                    logged_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS task_dependencies (
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    depends_on_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    PRIMARY KEY (task_id, depends_on_id)
                );
                CREATE INDEX IF NOT EXISTS tasks_meeting_id    ON tasks (meeting_id);
                CREATE INDEX IF NOT EXISTS tasks_done          ON tasks (done);
                CREATE INDEX IF NOT EXISTS tasks_parent_id     ON tasks (parent_id);
                CREATE INDEX IF NOT EXISTS tasks_snoozed_until ON tasks (snoozed_until);
                CREATE INDEX IF NOT EXISTS meetings_file_date  ON meetings (file_date DESC NULLS LAST);
                CREATE INDEX IF NOT EXISTS completions_date    ON completions (completed_date);
                CREATE INDEX IF NOT EXISTS task_time_log_task  ON task_time_log (task_id);
                CREATE INDEX IF NOT EXISTS task_deps_task      ON task_dependencies (task_id);
                CREATE INDEX IF NOT EXISTS task_deps_depends   ON task_dependencies (depends_on_id);
            """)


if DATABASE_URL:
    try:
        init_db()
    except Exception as _e:
        print(f"[db] init warning: {_e}")


# --------------------------------------------------
# AUTH
# --------------------------------------------------

@app.before_request
def require_login() -> Optional[Any]:
    if request.path.startswith("/static/"):
        return None
    if request.endpoint in ("login", "logout", "shortcut_add_task"):
        return None
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == AUTH_USERNAME and password == AUTH_PASSWORD and AUTH_PASSWORD:
            session["logged_in"] = True
            session.permanent = True
            return redirect(url_for("home"))
        error = "Invalid credentials."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------
# DATA MODEL
# --------------------------------------------------

@dataclass
class Meeting:
    id: str
    filename: str
    date: Optional[str]
    raw_group: str
    canonical_group: str
    topic: str
    purpose: List[str]
    outcome: str
    deadline: str
    attendees: str
    action_items_open: List[str]
    action_items_done: List[str]
    reminders_open: List[str]
    reminders_done: List[str]
    body: str
    body_html: str
    mtime: Optional[float]

    def summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "date": self.date,
            "raw_group": self.raw_group,
            "group": self.canonical_group,
            "topic": self.topic,
            "purpose": self.purpose,
            "outcome": self.outcome,
            "deadline": self.deadline,
            "attendees": self.attendees,
            "open_action_items_count": len(self.action_items_open),
            "open_reminders_count": len(self.reminders_open),
        }

    def full(self) -> Dict[str, Any]:
        return {
            **self.summary(),
            "action_items_open": self.action_items_open,
            "action_items_done": self.action_items_done,
            "reminders_open": self.reminders_open,
            "reminders_done": self.reminders_done,
            "body": self.body,
            "body_html": self.body_html,
        }


@dataclass
class Task:
    id: str
    text: str
    type: str                   # "action" | "reminder" | "free"
    done: bool
    backburner: bool
    priority: str              # "high" | "normal" | "low"
    contact: Optional[str]
    source_filename: str
    section: str
    meeting_id: Optional[str]
    group: Optional[str]
    source_date: Optional[str]
    deadline: Optional[str]
    deadline_raw: Optional[str]
    overdue: bool
    snoozed_until: Optional[str]
    estimate_minutes: Optional[int]
    recurrence_rule: Optional[dict]
    parent_id: Optional[str]
    subtask_count: int          # computed via subquery
    has_blockers: bool          # computed via EXISTS subquery

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "type": self.type,
            "done": self.done,
            "backburner": self.backburner,
            "priority": self.priority,
            "contact": self.contact,
            "source_filename": self.source_filename,
            "section": self.section,
            "meeting_id": self.meeting_id,
            "group": self.group,
            "source_date": self.source_date,
            "deadline": self.deadline,
            "deadline_raw": self.deadline_raw,
            "overdue": self.overdue,
            "snoozed_until": self.snoozed_until,
            "estimate_minutes": self.estimate_minutes,
            "recurrence_rule": self.recurrence_rule,
            "parent_id": self.parent_id,
            "subtask_count": self.subtask_count,
            "has_blockers": self.has_blockers,
        }


# --------------------------------------------------
# DEADLINE PARSING
# --------------------------------------------------

_DATE_WORD = r"(?:deadline|due|by)"

DEADLINE_PATTERNS = [
    re.compile(
        rf"{_DATE_WORD}\s*:?\s*(\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\(\s*{_DATE_WORD}\s+(\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?)\s*\)",
        re.IGNORECASE,
    ),
]


def _normalize_date(raw: str, context_year: Optional[int] = None) -> Optional[str]:
    raw = raw.strip().rstrip(").,;:")
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", raw)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})(?:[-/](\d{2,4}))?", raw)
        if not m:
            return None
        mo, d, y_raw = m.group(1), m.group(2), m.group(3)
        mo, d = int(mo), int(d)
        y = int(y_raw) if y_raw else (context_year or datetime.now().year)
        if y < 100:
            y += 2000
    try:
        return date_cls(y, mo, d).isoformat()
    except Exception:
        return None


def extract_deadline(
    text: str, context_year: Optional[int] = None
) -> Tuple[Optional[str], Optional[str]]:
    for pat in DEADLINE_PATTERNS:
        m = pat.search(text)
        if m:
            normalized = _normalize_date(m.group(1), context_year=context_year)
            if normalized:
                return normalized, m.group(0)
    return None, None


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def _task_id(*parts: str) -> str:
    blob = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]


def _year_from_date(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = re.match(r"(\d{4})", s)
    return int(m.group(1)) if m else None


_URGENCY_KW: list = [
    (re.compile(r"\b(?:urgent|urgently)\b",            re.IGNORECASE), 100),
    (re.compile(r"\basap\b|a\.s\.a\.p",                re.IGNORECASE), 100),
    (re.compile(r"\b(?:critical|blocker|blocking)\b",  re.IGNORECASE), 80),
    (re.compile(r"\bimmediately\b|\bright away\b",      re.IGNORECASE), 70),
    (re.compile(r"\bp[01]\b",                          re.IGNORECASE), 80),
    (re.compile(r"\b(?:high priority|top priority)\b", re.IGNORECASE), 80),
    (re.compile(r"\b(?:must do|must complete)\b",      re.IGNORECASE), 50),
    (re.compile(r"\b(?:mandatory|required)\b",         re.IGNORECASE), 40),
    (re.compile(r"\beod\b|\bend of day\b",             re.IGNORECASE), 30),
]


def _urgency_score(task: dict) -> int:
    today = date_cls.today()
    # Snoozed tasks are deprioritized to the bottom
    snoozed = task.get("snoozed_until")
    if snoozed:
        try:
            if date_cls.fromisoformat(snoozed) > today:
                return -999
        except (ValueError, TypeError):
            pass
    score = 0
    p = task.get("priority", "normal")
    if p == "high":  score += 300
    elif p == "low": score -= 100
    if task.get("overdue"):
        score += 200
        try: score += (today - date_cls.fromisoformat(task["deadline"])).days * 5
        except (ValueError, TypeError, KeyError): pass
    elif task.get("deadline") and not task.get("done"):
        try:
            d = (date_cls.fromisoformat(task["deadline"]) - today).days
            if   d <= 1:  score += 150
            elif d <= 3:  score += 100
            elif d <= 7:  score += 50
            elif d <= 14: score += 20
        except (ValueError, TypeError): pass
    if task.get("source_date"):
        try: score += min(40, (today - date_cls.fromisoformat(task["source_date"])).days // 7 * 5)
        except (ValueError, TypeError): pass
    t = task.get("type", "")
    if t == "action":   score += 10
    elif t == "reminder": score += 5
    # Quick-win bonus for short tasks
    est = task.get("estimate_minutes")
    if est is not None and est <= 30:
        score += 15
    # Blocked tasks are less actionable
    if task.get("has_blockers"):
        score -= 50
    kw = 0
    for pat, pts in _URGENCY_KW:
        if pat.search(task.get("text", "")): kw += pts
    score += min(150, kw)
    return score


def canonical_group(raw_group: str) -> str:
    if not raw_group:
        return "Unknown"
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT canonical_name FROM groups_map WHERE raw_name = %s",
                (raw_group.strip().lower(),),
            )
            row = cur.fetchone()
            return row["canonical_name"] if row else raw_group.strip()


# --------------------------------------------------
# MEETING / TASK DB HELPERS
# --------------------------------------------------

SECTION_HEADER_RE = {
    "reminders": re.compile(r"^\s*\*{0,2}Reminders/Important:\*{0,2}\s*$", re.IGNORECASE),
    "action_items": re.compile(r"^\s*\*{0,2}Action Items:\*{0,2}\s*$", re.IGNORECASE),
}
ANY_SECTION_RE = re.compile(r"^\s*\*{0,2}([A-Za-z0-9 /\.\-]+):\*{0,2}\s*$")
TASK_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<bullet>[-*+])\s*\[(?P<state>[ xX])\](?P<rest>\s*.+?)\s*$"
)
FREE_GROUP_RE = re.compile(
    r"@group:\s*(.+?)(?=\s+@\w+:|\s+(?:due|deadline|by)\b|\s*$)",
    re.IGNORECASE,
)


def _extract_tasks_from_body(
    body: str, filename: str, meeting_id: str, group: str, date_str: Optional[str]
) -> List[dict]:
    lines = body.splitlines()
    tasks = []
    in_reminders = in_actions = False
    year = _year_from_date(date_str)

    for line in lines:
        if SECTION_HEADER_RE["reminders"].match(line):
            in_reminders, in_actions = True, False
            continue
        if SECTION_HEADER_RE["action_items"].match(line):
            in_actions, in_reminders = True, False
            continue
        if (in_reminders or in_actions) and ANY_SECTION_RE.match(line):
            in_reminders = in_actions = False

        m = TASK_LINE_RE.match(line)
        if not m or (not in_reminders and not in_actions):
            continue

        text = m.group("rest").strip()
        done = m.group("state").strip().lower() == "x"
        type_ = "reminder" if in_reminders else "action"
        section = "reminders" if in_reminders else "action_items"
        deadline, deadline_raw = extract_deadline(text, context_year=year)
        tid = _task_id(filename, section, text)

        tasks.append({
            "id": tid, "text": text, "type": type_, "done": done,
            "meeting_id": meeting_id, "source_filename": filename,
            "section": section, "group_name": group,
            "source_date": date_str, "deadline": deadline, "deadline_raw": deadline_raw,
        })
    return tasks


def _date_from_filename(filename: str) -> Optional[str]:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", filename)
    return m.group(1) if m else None


def _row_to_meeting(row: dict, task_rows: List[dict]) -> Meeting:
    date_val = row["file_date"]
    date_str = date_val.isoformat() if date_val else None
    purpose = row["purpose"] if isinstance(row["purpose"], list) else (row["purpose"] or [])
    return Meeting(
        id=row["id"],
        filename=row["filename"],
        date=date_str,
        raw_group=row["raw_group"] or "",
        canonical_group=row["canonical_group"] or "",
        topic=row["topic"] or "",
        purpose=purpose,
        outcome=row["outcome"] or "",
        deadline=row["deadline"] or "",
        attendees=row["attendees"] or "",
        action_items_open=[t["text"] for t in task_rows if t["type"] == "action" and not t["done"]],
        action_items_done=[t["text"] for t in task_rows if t["type"] == "action" and t["done"]],
        reminders_open=[t["text"] for t in task_rows if t["type"] == "reminder" and not t["done"]],
        reminders_done=[t["text"] for t in task_rows if t["type"] == "reminder" and t["done"]],
        body=row["body"] or "",
        body_html=row["body_html"] or "",
        mtime=row["mtime"],
    )


def _task_row_to_task(row: dict, today: str) -> Task:
    source_date = row["source_date"]
    source_date_str = source_date.isoformat() if source_date else None
    snoozed_raw = row.get("snoozed_until")
    snoozed_str = snoozed_raw.isoformat() if snoozed_raw else None
    done = row["done"]
    deadline = row["deadline"]
    return Task(
        id=row["id"],
        text=row["text"],
        type=row["type"],
        done=done,
        backburner=row["backburner"],
        priority=row.get("priority") or "normal",
        contact=row.get("contact") or None,
        source_filename=row["source_filename"] or "",
        section=row["section"] or "",
        meeting_id=row["meeting_id"],
        group=row["group_name"],
        source_date=source_date_str,
        deadline=deadline,
        deadline_raw=row["deadline_raw"],
        overdue=bool(deadline and not done and deadline < today),
        snoozed_until=snoozed_str,
        estimate_minutes=row.get("estimate_minutes"),
        recurrence_rule=row.get("recurrence_rule"),
        parent_id=row.get("parent_id"),
        subtask_count=int(row.get("subtask_count") or 0),
        has_blockers=bool(row.get("has_blockers")),
    )


def db_get_all_meetings() -> List[Meeting]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM meetings ORDER BY file_date DESC NULLS LAST")
            meeting_rows = cur.fetchall()
            if not meeting_rows:
                return []
            meeting_ids = [r["id"] for r in meeting_rows]
            cur.execute(
                "SELECT * FROM tasks WHERE meeting_id = ANY(%s)",
                (meeting_ids,),
            )
            task_rows = cur.fetchall()
    tasks_by_meeting: Dict[str, List[dict]] = {}
    for t in task_rows:
        tasks_by_meeting.setdefault(t["meeting_id"], []).append(dict(t))
    return [_row_to_meeting(dict(r), tasks_by_meeting.get(r["id"], [])) for r in meeting_rows]


def db_get_meeting(mid: str) -> Optional[Meeting]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM meetings WHERE id = %s", (mid,))
            row = cur.fetchone()
            if not row:
                return None
            cur.execute("SELECT * FROM tasks WHERE meeting_id = %s", (mid,))
            task_rows = cur.fetchall()
    return _row_to_meeting(dict(row), [dict(t) for t in task_rows])


_TASKS_SELECT = """
    SELECT
        t.*,
        (SELECT COUNT(*) FROM tasks sub WHERE sub.parent_id = t.id AND NOT sub.done) AS subtask_count,
        EXISTS(
            SELECT 1 FROM task_dependencies d
            JOIN tasks dep ON dep.id = d.depends_on_id
            WHERE d.task_id = t.id AND NOT dep.done
        ) AS has_blockers
    FROM tasks t
"""


def db_get_all_tasks(include_done: bool = False) -> List[Task]:
    today = date_cls.today().isoformat()
    with get_db() as conn:
        with conn.cursor() as cur:
            if include_done:
                cur.execute(_TASKS_SELECT + "ORDER BY t.created_at")
            else:
                cur.execute(_TASKS_SELECT + "WHERE NOT t.done ORDER BY t.created_at")
            rows = cur.fetchall()
    return [_task_row_to_task(dict(r), today) for r in rows]


# --------------------------------------------------
# IMPORT (parse markdown → DB)
# --------------------------------------------------

def import_meeting_from_content(filename: str, content: str) -> dict:
    """Parse and upsert a meeting from raw markdown content."""
    post = frontmatter.loads(content)
    meta = post.metadata or {}

    def s(key: str, default: str = "") -> str:
        val = meta.get(key, default)
        return str(val).strip() if val is not None else default

    def sl(key: str) -> List[str]:
        val = meta.get(key, []) or []
        if isinstance(val, str):
            return [val.strip()] if val.strip() else []
        return [str(x).strip() for x in val if str(x).strip()]

    raw_group = s("group") or filename.split(" - ", 1)[-1].replace(".md", "")
    date_str = s("date") or _date_from_filename(filename)
    canon = canonical_group(raw_group)
    body_md = post.content or ""
    body_html = md_lib.markdown(
        body_md, extensions=["fenced_code", "tables", "sane_lists", "nl2br"]
    )
    mid = hashlib.sha1(filename.encode("utf-8")).hexdigest()[:16]

    file_date = None
    if date_str:
        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            pass

    tasks = _extract_tasks_from_body(body_md, filename, mid, canon, date_str)
    seen_texts = {t["text"] for t in tasks}
    year = _year_from_date(date_str)

    # Also ingest tasks stored directly in YAML front matter (legacy format)
    for text, done_, type_, section_ in [
        *[(t, False, "action", "action_items") for t in sl("action_items_open")],
        *[(t, True,  "action", "action_items") for t in sl("action_items_done")],
        *[(t, False, "reminder", "reminders")  for t in sl("reminders_open")],
        *[(t, True,  "reminder", "reminders")  for t in sl("reminders_done")],
    ]:
        if text in seen_texts:
            continue
        deadline, deadline_raw = extract_deadline(text, context_year=year)
        tasks.append({
            "id": _task_id(filename, section_, text),
            "text": text, "type": type_, "done": done_,
            "meeting_id": mid, "source_filename": filename, "section": section_,
            "group_name": canon, "source_date": date_str,
            "deadline": deadline, "deadline_raw": deadline_raw,
        })

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO meetings
                    (id, filename, file_date, raw_group, canonical_group,
                     topic, purpose, outcome, deadline, attendees, body, body_html, mtime)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET
                    filename=EXCLUDED.filename, file_date=EXCLUDED.file_date,
                    raw_group=EXCLUDED.raw_group, canonical_group=EXCLUDED.canonical_group,
                    topic=EXCLUDED.topic, purpose=EXCLUDED.purpose,
                    outcome=EXCLUDED.outcome, deadline=EXCLUDED.deadline,
                    attendees=EXCLUDED.attendees, body=EXCLUDED.body,
                    body_html=EXCLUDED.body_html, mtime=EXCLUDED.mtime
            """, (
                mid, filename, file_date, raw_group, canon,
                s("topic"), json.dumps(sl("purpose")), s("outcome"),
                s("deadline"), s("attendees"), body_md, body_html, None,
            ))
            for t in tasks:
                cur.execute("""
                    INSERT INTO tasks
                        (id, text, type, done, meeting_id, source_filename,
                         section, group_name, source_date, deadline, deadline_raw)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO UPDATE SET
                        text=EXCLUDED.text, type=EXCLUDED.type,
                        meeting_id=EXCLUDED.meeting_id,
                        source_filename=EXCLUDED.source_filename,
                        section=EXCLUDED.section, group_name=EXCLUDED.group_name,
                        source_date=EXCLUDED.source_date,
                        deadline=EXCLUDED.deadline, deadline_raw=EXCLUDED.deadline_raw
                """, (
                    t["id"], t["text"], t["type"], t["done"], t["meeting_id"],
                    t["source_filename"], t["section"], t["group_name"],
                    t["source_date"], t["deadline"], t["deadline_raw"],
                ))

    return {"id": mid, "filename": filename, "tasks": len(tasks)}


# --------------------------------------------------
# MEETING FILTERING
# --------------------------------------------------

def _date_in_range(
    d: Optional[str], start: Optional[str], end: Optional[str]
) -> bool:
    if not start and not end:
        return True
    if not d:
        return False
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return False
    if start:
        try:
            if dt < datetime.strptime(start, "%Y-%m-%d").date():
                return False
        except Exception:
            pass
    if end:
        try:
            if dt > datetime.strptime(end, "%Y-%m-%d").date():
                return False
        except Exception:
            pass
    return True


def _matches_query(m: Meeting, q: str) -> bool:
    if not q:
        return True
    q = q.lower()
    return any(q in (h or "").lower() for h in (
        m.raw_group, m.canonical_group, m.topic, m.outcome, m.deadline,
        m.attendees, m.filename, m.body, " ".join(m.purpose),
        " ".join(m.action_items_open + m.action_items_done),
        " ".join(m.reminders_open + m.reminders_done),
    ))


def filter_meetings(
    meetings: List[Meeting],
    *,
    q: str = "",
    group: str = "",
    purpose: str = "",
    attendee: str = "",
    date_from: str = "",
    date_to: str = "",
    has_open_tasks: bool = False,
) -> List[Meeting]:
    out = []
    for m in meetings:
        if group and m.canonical_group != group:
            continue
        if purpose and purpose not in m.purpose:
            continue
        if attendee and attendee.lower() not in (m.attendees or "").lower():
            continue
        if not _date_in_range(m.date, date_from or None, date_to or None):
            continue
        if has_open_tasks and not (m.action_items_open or m.reminders_open):
            continue
        if not _matches_query(m, q):
            continue
        out.append(m)
    out.sort(key=lambda x: (x.date or ""), reverse=True)
    return out


# --------------------------------------------------
# COMPLETIONS LOG
# --------------------------------------------------

def log_completion(
    task_id: str, text: str, section: str, filename: str, done: bool
) -> None:
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO completions
                        (task_id, task_text, section, source_filename, done, completed_date)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (task_id, text[:200], section, filename, done, date_cls.today()))
    except Exception as e:
        print(f"[completions] log error: {e}")


def completions_per_day(days: int = 30) -> List[Dict[str, Any]]:
    today = date_cls.today()
    window = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    buckets: Dict[str, int] = {d.isoformat(): 0 for d in window}
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT completed_date, done FROM completions WHERE completed_date >= %s",
                    (window[0],),
                )
                for row in cur.fetchall():
                    d = row["completed_date"].isoformat()
                    if d in buckets:
                        buckets[d] += 1 if row["done"] else -1
    except Exception as e:
        print(f"[completions] load error: {e}")
    return [{"date": d, "count": max(0, n)} for d, n in buckets.items()]


# --------------------------------------------------
# ROUTES
# --------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/meetings")
def api_meetings():
    a = request.args
    results = filter_meetings(
        db_get_all_meetings(),
        q=a.get("q", ""), group=a.get("group", ""), purpose=a.get("purpose", ""),
        attendee=a.get("attendee", ""), date_from=a.get("date_from", ""),
        date_to=a.get("date_to", ""),
        has_open_tasks=a.get("has_open_tasks", "").lower() in ("1", "true", "yes"),
    )
    return jsonify({"count": len(results), "meetings": [m.summary() for m in results]})


@app.route("/api/meetings/<mid>")
def api_meeting(mid: str):
    m = db_get_meeting(mid)
    if not m:
        abort(404)
    return jsonify(m.full())


@app.route("/api/groups")
def api_groups():
    by_group: Dict[str, List[Meeting]] = {}
    for m in db_get_all_meetings():
        by_group.setdefault(m.canonical_group, []).append(m)
    out = []
    for group, grp_meetings in by_group.items():
        dates = [m.date for m in grp_meetings if m.date]
        out.append({
            "group": group,
            "meeting_count": len(grp_meetings),
            "last_contact": max(dates) if dates else None,
            "open_action_items": sum(len(m.action_items_open) for m in grp_meetings),
            "open_reminders": sum(len(m.reminders_open) for m in grp_meetings),
            "raw_variants": sorted(
                {m.raw_group for m in grp_meetings if m.raw_group != group},
                key=str.casefold,
            ),
        })
    out.sort(key=lambda x: (x["last_contact"] or ""), reverse=True)
    return jsonify(out)


@app.route("/api/facets")
def api_facets():
    meetings = db_get_all_meetings()
    groups: set = set()
    purposes: set = set()
    attendees: set = set()
    raw_groups_seen: Dict[str, str] = {}

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT raw_name FROM groups_map")
            aliased_raws = {r["raw_name"] for r in cur.fetchall()}

    for m in meetings:
        groups.add(m.canonical_group)
        raw_groups_seen.setdefault(m.raw_group, m.canonical_group)
        for p in m.purpose:
            purposes.add(p)
        if m.attendees:
            for a in re.split(r"[;,]", m.attendees):
                a = a.strip()
                if a:
                    attendees.add(a)

    unaliased = [
        raw for raw, canon in raw_groups_seen.items()
        if raw.strip().lower() not in aliased_raws and raw == canon
    ]
    return jsonify({
        "groups": sorted(groups, key=str.casefold),
        "purposes": sorted(purposes, key=str.casefold),
        "attendees": sorted(attendees, key=str.casefold),
        "unaliased_raw_groups": sorted(unaliased, key=str.casefold),
    })


@app.route("/api/reload", methods=["POST"])
def api_reload():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM meetings")
            count = cur.fetchone()["c"]
    return jsonify({"ok": True, "count": count})


# ---- Tasks ----

@app.route("/api/tasks")
def api_tasks():
    a = request.args
    status = a.get("status", "open")
    type_filter = a.get("type", "")
    group_filter = a.get("group", "")
    overdue_only = a.get("overdue", "").lower() in ("1", "true", "yes")
    q = a.get("q", "").lower()
    priority_filter = a.get("priority", "")
    snoozed_filter = a.get("snoozed", "0")  # "0"=hide snoozed, "1"=only snoozed
    show_subtasks = a.get("show_subtasks", "0").lower() not in ("0", "false", "no")
    parent_id_filter = a.get("parent_id", "")
    smart_view = a.get("smart_view", "")

    today_iso = date_cls.today().isoformat()
    tomorrow_iso = (date_cls.today() + timedelta(days=1)).isoformat()
    week_out_iso = (date_cls.today() + timedelta(days=7)).isoformat()
    neglect_cutoff = (date_cls.today() - timedelta(days=14)).isoformat()

    tasks = db_get_all_tasks(include_done=(status == "done" or smart_view != ""))

    def _is_snoozed(t: Task) -> bool:
        if not t.snoozed_until:
            return False
        try:
            return date_cls.fromisoformat(t.snoozed_until) > date_cls.today()
        except (ValueError, TypeError):
            return False

    def passes_status(t: Task) -> bool:
        if status == "open":       return not t.done and not t.backburner
        if status == "done":       return t.done and not t.backburner
        if status == "backburner": return t.backburner
        return True

    pre_group = []
    for t in tasks:
        # Snooze filtering
        if snoozed_filter == "1":
            if not _is_snoozed(t): continue
        else:
            if _is_snoozed(t): continue

        # parent_id filter (return only subtasks of a specific task)
        if parent_id_filter:
            if t.parent_id != parent_id_filter: continue
        elif not show_subtasks:
            if t.parent_id: continue  # hide subtasks from main list

        if not passes_status(t): continue
        if type_filter and t.type != type_filter: continue
        if overdue_only and not t.overdue: continue
        if q and q not in t.text.lower() and q not in (t.group or "").lower(): continue
        if priority_filter and t.priority != priority_filter: continue
        pre_group.append(t)

    groups_in_scope = sorted({t.group for t in pre_group if t.group}, key=str.casefold)

    out = []
    for t in pre_group:
        if group_filter and (t.group or "") != group_filter: continue
        d = t.as_dict()
        d["urgency_score"] = _urgency_score(d)
        out.append(d)

    # Smart view filtering (applied after urgency scoring)
    if smart_view:
        def _smart_filter(d: dict) -> bool:
            if smart_view == "today":
                return (
                    (d.get("deadline") == today_iso)
                    or (d["urgency_score"] >= 200 and not d.get("done"))
                )
            if smart_view == "upcoming":
                dl = d.get("deadline") or ""
                return (
                    not d.get("done")
                    and tomorrow_iso <= dl <= week_out_iso
                )
            if smart_view == "neglected":
                return (
                    not d.get("done")
                    and not d.get("backburner")
                    and d.get("priority") in ("high", "normal")
                    and (d.get("source_date") or "") <= neglect_cutoff
                )
            if smart_view == "quick_wins":
                est = d.get("estimate_minutes")
                return (
                    not d.get("done")
                    and est is not None
                    and est <= 30
                    and d["urgency_score"] >= 50
                )
            if smart_view == "waiting":
                return not d.get("done") and bool(d.get("has_blockers"))
            return True
        out = [d for d in out if _smart_filter(d)]

    out.sort(key=lambda t: -t["urgency_score"])
    return jsonify({"count": len(out), "tasks": out, "groups_in_scope": groups_in_scope})


@app.route("/api/tasks/backburner", methods=["POST"])
def api_backburner_task():
    data = request.get_json(force=True, silent=True) or {}
    task_id = (data.get("id") or "").strip()
    on = bool(data.get("backburner", True))
    if not task_id:
        return jsonify({"ok": False, "error": "id required"}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE tasks SET backburner = %s WHERE id = %s", (on, task_id))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tasks/priority", methods=["POST"])
def api_set_priority():
    data = request.get_json(force=True, silent=True) or {}
    task_id = (data.get("id") or "").strip()
    priority = (data.get("priority") or "").strip().lower()
    if not task_id:
        return jsonify({"ok": False, "error": "id required"}), 400
    if priority not in ("high", "normal", "low"):
        return jsonify({"ok": False, "error": "priority must be high, normal, or low"}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET priority = %s WHERE id = %s RETURNING id",
                    (priority, task_id),
                )
                if cur.fetchone() is None:
                    return jsonify({"ok": False, "error": "task not found"}), 404
        return jsonify({"ok": True, "priority": priority})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _compute_next_recurrence(rule: dict, today: date_cls) -> Optional[date_cls]:
    rtype = rule.get("type")
    if rtype == "daily":
        return today + timedelta(days=int(rule.get("interval", 1)))
    if rtype == "weekly":
        interval = int(rule.get("interval", 1))
        dow = int(rule.get("day_of_week", today.weekday()))
        # Find next occurrence of that weekday (at least 1 day from now)
        days_ahead = (dow - today.weekday()) % 7 or 7
        return today + timedelta(days=days_ahead + (interval - 1) * 7)
    if rtype == "monthly":
        dom = int(rule.get("day_of_month", today.day))
        # Next month, same day
        month = today.month + 1 if today.month < 12 else 1
        year = today.year if today.month < 12 else today.year + 1
        try:
            return date_cls(year, month, min(dom, 28))
        except Exception:
            return date_cls(year, month, 28)
    if rtype == "after_completion":
        return today + timedelta(days=int(rule.get("days", 7)))
    return None


@app.route("/api/tasks/toggle", methods=["POST"])
def api_toggle_task():
    data = request.get_json(force=True, silent=True) or {}
    task_id = (data.get("id") or "").strip()
    text = (data.get("text") or "").strip()
    section = data.get("section", "")
    filename = data.get("source_filename", "")
    done = bool(data.get("done", True))

    # Derive task ID from fields when not provided directly (backward compat)
    if not task_id:
        if not text or not filename:
            return jsonify({"ok": False, "error": "id or (source_filename + text) required"}), 400
        task_id = _task_id(filename, section, text)

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET done = %s WHERE id = %s RETURNING id, text, type, "
                    "group_name, priority, contact, estimate_minutes, recurrence_rule, "
                    "source_filename, section",
                    (done, task_id),
                )
                row = cur.fetchone()
                if row is None:
                    return jsonify({"ok": False, "error": "task not found"}), 404

                # Spawn next recurrence instance when marking done
                if done and row["recurrence_rule"]:
                    rule = row["recurrence_rule"]
                    next_date = _compute_next_recurrence(rule, date_cls.today())
                    if next_date:
                        new_id = str(uuid.uuid4())
                        next_iso = next_date.isoformat()
                        cur.execute("""
                            INSERT INTO tasks
                                (id, text, type, done, backburner, source_filename, section,
                                 group_name, source_date, deadline, priority, contact,
                                 estimate_minutes, recurrence_rule)
                            VALUES (%s,%s,%s,FALSE,FALSE,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """, (
                            new_id, row["text"], row["type"],
                            row["source_filename"], row["section"], row["group_name"],
                            next_iso, next_iso, row["priority"], row["contact"],
                            row["estimate_minutes"], json.dumps(rule),
                        ))

        log_completion(task_id, text or (row["text"] if row else ""), section, filename, done)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tasks/delete", methods=["POST"])
def api_delete_task():
    data = request.get_json(force=True, silent=True) or {}
    task_id = (data.get("id") or "").strip()
    text = (data.get("text") or "").strip()
    section = data.get("section", "")
    filename = data.get("source_filename", "")

    if not task_id:
        if not text or not filename:
            return jsonify({"ok": False, "error": "id or (source_filename + text) required"}), 400
        task_id = _task_id(filename, section, text)

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM tasks WHERE id = %s RETURNING id", (task_id,))
                if cur.fetchone() is None:
                    return jsonify({"ok": False, "error": "task not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tasks/edit", methods=["POST"])
def api_edit_task():
    data = request.get_json(force=True, silent=True) or {}
    task_id = (data.get("id") or "").strip()
    old_text = (data.get("old_text") or "").strip()
    new_text = (data.get("new_text") or "").strip()
    section = data.get("section", "")
    filename = data.get("source_filename", "")

    if not new_text:
        return jsonify({"ok": False, "error": "new_text required"}), 400
    if not task_id:
        if not old_text or not filename:
            return jsonify({"ok": False, "error": "id or (source_filename + old_text) required"}), 400
        task_id = _task_id(filename, section, old_text)

    new_priority = (data.get("priority") or "").strip().lower()
    if new_priority not in ("high", "normal", "low"):
        new_priority = None
    new_group = (data.get("group") or "").strip() or None
    new_contact = data.get("contact")  # None means "don't change"; "" means clear it
    # deadline_direct from picker overrides text-extracted deadline
    deadline_direct = (data.get("deadline_direct") or "").strip() or None
    new_estimate = data.get("estimate_minutes")  # int or None
    new_recurrence = data.get("recurrence_rule")  # dict or None (False = don't change)

    if deadline_direct:
        new_deadline, new_deadline_raw = deadline_direct, deadline_direct
    else:
        new_deadline, new_deadline_raw = extract_deadline(new_text)

    # Only recompute deterministic ID for meeting-sourced tasks; free tasks keep UUID
    is_free = (filename == "tasks.md" or not filename)
    new_id = task_id if is_free else (_task_id(filename, section, new_text) if filename and section else task_id)

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                sets = ["id = %s", "text = %s", "deadline = %s", "deadline_raw = %s"]
                vals = [new_id, new_text, new_deadline, new_deadline_raw]
                if new_priority:
                    sets.append("priority = %s"); vals.append(new_priority)
                if new_group is not None:
                    sets.append("group_name = %s"); vals.append(new_group or None)
                if new_contact is not None:
                    sets.append("contact = %s"); vals.append((new_contact or "").strip() or None)
                if new_estimate is not None:
                    sets.append("estimate_minutes = %s")
                    vals.append(int(new_estimate) if new_estimate else None)
                if new_recurrence is not False:  # explicit None clears it, dict sets it
                    if new_recurrence is None:
                        sets.append("recurrence_rule = %s"); vals.append(None)
                    elif isinstance(new_recurrence, dict):
                        sets.append("recurrence_rule = %s"); vals.append(json.dumps(new_recurrence))
                vals.append(task_id)
                cur.execute(
                    f"UPDATE tasks SET {', '.join(sets)} WHERE id = %s RETURNING id",
                    vals,
                )
                if cur.fetchone() is None:
                    return jsonify({"ok": False, "error": "task not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tasks/add", methods=["POST"])
def api_add_task():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    group = (data.get("group") or "").strip() or None
    deadline_in = (data.get("deadline") or "").strip() or None
    add_priority = (data.get("priority") or "normal").strip().lower()
    if add_priority not in ("high", "normal", "low"):
        add_priority = "normal"
    add_contact = (data.get("contact") or "").strip() or None
    add_estimate = data.get("estimate_minutes")
    add_recurrence = data.get("recurrence_rule")  # dict or None
    add_parent_id = (data.get("parent_id") or "").strip() or None
    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400

    tags = []
    if group:
        tags.append(f"@group:{group}")
    if deadline_in:
        tags.append(f"due {deadline_in}")
    full_text = text + ((" " + " ".join(tags)) if tags else "")

    deadline, deadline_raw = extract_deadline(full_text, context_year=datetime.now().year)
    # If deadline was passed directly (already parsed client-side), prefer it
    if deadline_in and not deadline:
        deadline = deadline_in
        deadline_raw = deadline_in
    tid = str(uuid.uuid4())

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO tasks
                        (id, text, type, done, backburner, source_filename, section,
                         group_name, source_date, deadline, deadline_raw, priority, contact,
                         estimate_minutes, recurrence_rule, parent_id)
                    VALUES (%s, %s, 'free', FALSE, FALSE, 'tasks.md', 'free',
                            %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    tid, full_text, group, date_cls.today(), deadline, deadline_raw,
                    add_priority, add_contact,
                    int(add_estimate) if add_estimate else None,
                    json.dumps(add_recurrence) if add_recurrence else None,
                    add_parent_id,
                ))
        return jsonify({"ok": True, "text": full_text, "id": tid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/stats")
def api_stats():
    today = date_cls.today()
    today_iso = today.isoformat()

    all_tasks = db_get_all_tasks(include_done=True)
    open_tasks = [t for t in all_tasks if not t.done and not t.backburner]
    done_tasks = [t for t in all_tasks if t.done and not t.backburner]

    overdue_open = [t for t in open_tasks if t.overdue]
    due_today = [t for t in open_tasks if t.deadline == today_iso]

    # Deadlines strip: Mon–Fri of the current work week
    monday = today - timedelta(days=today.weekday())
    work_week = [monday + timedelta(days=i) for i in range(5)]  # Mon=0 … Fri=4
    deadlines_by_day = []
    for d in work_week:
        iso = d.isoformat()
        deadlines_by_day.append({
            "date": iso,
            "day": d.day,
            "dow": d.strftime("%a").upper(),
            "is_today": iso == today_iso,
            "count": sum(1 for t in open_tasks if t.deadline == iso),
        })

    def days_overdue(t: Task) -> int:
        try:
            return (today - datetime.strptime(t.deadline, "%Y-%m-%d").date()).days
        except Exception:
            return 0

    overdue_top = [{
        "id": t.id, "text": t.text, "group": t.group,
        "deadline": t.deadline, "days_overdue": days_overdue(t),
    } for t in sorted(overdue_open, key=days_overdue, reverse=True)[:5]]

    due_today_top = [
        {"id": t.id, "text": t.text, "group": t.group}
        for t in due_today[:2]
    ]

    group_counts: Dict[str, int] = {}
    for t in open_tasks:
        if t.group:
            group_counts[t.group] = group_counts.get(t.group, 0) + 1
    by_group = sorted(
        [{"group": g, "count": c} for g, c in group_counts.items()],
        key=lambda x: x["count"], reverse=True,
    )[:8]

    # Weekly completion %: tasks completed this week / (completed this week + currently open)
    week_start = monday.isoformat()
    week_completions_this_week = sum(
        1 for t in all_tasks
        if t.done and not t.backburner
        and (t.source_date or "") >= week_start  # proxy for "worked on this week"
    )
    per_day = completions_per_day(days=7)
    week_done_count = sum(x["count"] for x in per_day)
    week_total = week_done_count + len(open_tasks)
    pct_complete = round((week_done_count / week_total) * 100) if week_total else 0

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT m.id, m.canonical_group, m.topic, m.file_date,
                    (SELECT COUNT(*) FROM tasks WHERE meeting_id = m.id
                        AND type = 'action' AND NOT done) AS open_actions,
                    (SELECT COUNT(*) FROM tasks WHERE meeting_id = m.id
                        AND type = 'reminder' AND NOT done) AS open_reminders
                FROM meetings m
                WHERE m.file_date IS NOT NULL
                ORDER BY m.file_date DESC
                LIMIT 1
            """)
            recent_row = cur.fetchone()
            cur.execute("SELECT COUNT(*) AS c FROM meetings")
            total_meetings = cur.fetchone()["c"]

    recent = None
    if recent_row:
        recent = {
            "id": recent_row["id"],
            "group": recent_row["canonical_group"],
            "topic": recent_row["topic"],
            "date": recent_row["file_date"].isoformat() if recent_row["file_date"] else None,
            "open_actions": recent_row["open_actions"],
            "open_reminders": recent_row["open_reminders"],
        }

    # Top 3 non-snoozed open tasks by urgency for primary focus panel
    scoreable = [t for t in open_tasks if not (
        t.snoozed_until and date_cls.fromisoformat(t.snoozed_until) > today
        if t.snoozed_until else False
    )]
    top3_scored = sorted(
        [(t, _urgency_score(t.as_dict())) for t in scoreable],
        key=lambda x: -x[1],
    )[:3]
    top_urgency = [
        {
            "id": t.id, "text": t.text, "group": t.group,
            "urgency_score": score, "deadline": t.deadline,
            "priority": t.priority, "estimate_minutes": t.estimate_minutes,
        }
        for t, score in top3_scored
    ]

    return jsonify({
        "today": today_iso,
        "open_count": len(open_tasks),
        "overdue_count": len(overdue_open),
        "due_today_count": len(due_today),
        "due_today_top": due_today_top,
        "done_count": len(done_tasks),
        "total_tasks": len(all_tasks),
        "pct_complete": pct_complete,
        "deadlines": deadlines_by_day,
        "overdue_top": overdue_top,
        "by_group": by_group,
        "completions_per_day": per_day,
        "completions_30d": week_done_count,
        "recent_meeting": recent,
        "meetings_total": total_meetings,
        "top_urgency": top_urgency,
    })


@app.route("/api/tasks/snooze", methods=["POST"])
def api_snooze_task():
    data = request.get_json(force=True, silent=True) or {}
    task_id = (data.get("id") or "").strip()
    until = (data.get("until") or "").strip() or None  # ISO date or null to un-snooze
    if not task_id:
        return jsonify({"ok": False, "error": "id required"}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET snoozed_until = %s WHERE id = %s RETURNING id",
                    (until, task_id),
                )
                if cur.fetchone() is None:
                    return jsonify({"ok": False, "error": "task not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tasks/time-log", methods=["POST"])
def api_time_log():
    data = request.get_json(force=True, silent=True) or {}
    task_id = (data.get("task_id") or "").strip()
    minutes = data.get("minutes_spent")
    if not task_id or minutes is None:
        return jsonify({"ok": False, "error": "task_id and minutes_spent required"}), 400
    try:
        minutes = int(minutes)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO task_time_log (task_id, minutes_spent) VALUES (%s, %s)",
                    (task_id, minutes),
                )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tasks/time-estimate-hint")
def api_time_estimate_hint():
    group = request.args.get("group", "").strip()
    if not group:
        return jsonify({"ok": True, "suggested_minutes": None, "sample_count": 0})
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ROUND(AVG(tl.minutes_spent)) AS avg_mins, COUNT(*) AS cnt
                    FROM task_time_log tl
                    JOIN tasks t ON t.id = tl.task_id
                    WHERE t.group_name = %s
                """, (group,))
                row = cur.fetchone()
        if row and row["cnt"] > 0:
            return jsonify({"ok": True, "suggested_minutes": int(row["avg_mins"] or 0), "sample_count": int(row["cnt"])})
        return jsonify({"ok": True, "suggested_minutes": None, "sample_count": 0})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tasks/search")
def api_tasks_search():
    q = request.args.get("q", "").strip().lower()
    limit = min(int(request.args.get("limit", 10)), 20)
    if not q:
        return jsonify({"tasks": []})
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, text, group_name, deadline
                    FROM tasks
                    WHERE NOT done AND LOWER(text) LIKE %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (f"%{q}%", limit))
                rows = cur.fetchall()
        results = [{"id": r["id"], "text": r["text"], "group": r["group_name"], "deadline": r["deadline"]} for r in rows]
        return jsonify({"tasks": results})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tasks/dependency/add", methods=["POST"])
def api_dependency_add():
    data = request.get_json(force=True, silent=True) or {}
    task_id = (data.get("task_id") or "").strip()
    depends_on_id = (data.get("depends_on_id") or "").strip()
    if not task_id or not depends_on_id:
        return jsonify({"ok": False, "error": "task_id and depends_on_id required"}), 400
    if task_id == depends_on_id:
        return jsonify({"ok": False, "error": "task cannot depend on itself"}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Check direct circular dependency
                cur.execute(
                    "SELECT 1 FROM task_dependencies WHERE task_id = %s AND depends_on_id = %s",
                    (depends_on_id, task_id),
                )
                if cur.fetchone():
                    return jsonify({"ok": False, "error": "circular dependency"}), 400
                cur.execute(
                    "INSERT INTO task_dependencies (task_id, depends_on_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (task_id, depends_on_id),
                )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tasks/dependency/remove", methods=["POST"])
def api_dependency_remove():
    data = request.get_json(force=True, silent=True) or {}
    task_id = (data.get("task_id") or "").strip()
    depends_on_id = (data.get("depends_on_id") or "").strip()
    if not task_id or not depends_on_id:
        return jsonify({"ok": False, "error": "task_id and depends_on_id required"}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM task_dependencies WHERE task_id = %s AND depends_on_id = %s",
                    (task_id, depends_on_id),
                )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tasks/add-subtask", methods=["POST"])
def api_add_subtask():
    data = request.get_json(force=True, silent=True) or {}
    parent_id = (data.get("parent_id") or "").strip()
    text = (data.get("text") or "").strip()
    priority = (data.get("priority") or "normal").strip()
    if not parent_id or not text:
        return jsonify({"ok": False, "error": "parent_id and text required"}), 400
    if priority not in ("high", "normal", "low"):
        priority = "normal"
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Validate parent exists and has no parent itself (one level only)
                cur.execute("SELECT parent_id FROM tasks WHERE id = %s", (parent_id,))
                row = cur.fetchone()
                if not row:
                    return jsonify({"ok": False, "error": "Parent task not found"}), 404
                if row["parent_id"]:
                    return jsonify({"ok": False, "error": "Cannot nest subtasks more than one level"}), 400
                new_id = str(uuid4())
                cur.execute(
                    """INSERT INTO tasks (id, text, type, done, source_filename, section, priority, parent_id)
                       VALUES (%s, %s, 'free', FALSE, 'tasks.md', '', %s, %s)""",
                    (new_id, text, priority, parent_id),
                )
        return jsonify({"ok": True, "id": new_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/import", methods=["POST"])
def api_import_notes():
    uploaded = request.files.getlist("files")
    if not uploaded:
        return jsonify({"ok": False, "error": "No files uploaded"}), 400
    results = []
    for f in uploaded:
        fname = Path(f.filename).name if f.filename else ""
        if not fname.endswith(".md"):
            results.append({"filename": fname, "ok": False, "error": "Not a .md file"})
            continue
        try:
            content = f.read().decode("utf-8")
            summary = import_meeting_from_content(fname, content)
            results.append({"filename": fname, "ok": True, "task_count": summary["tasks"]})
        except Exception as e:
            results.append({"filename": fname, "ok": False, "error": str(e)})
    ok_count = sum(1 for r in results if r.get("ok"))
    return jsonify({"ok": True, "processed": ok_count, "total": len(results), "results": results})


# --------------------------------------------------
# SHORTCUT ENDPOINT (API key auth, no session required)
# --------------------------------------------------

@app.route("/api/shortcut/add-task", methods=["POST"])
def shortcut_add_task():
    if not SHORTCUT_API_KEY:
        return jsonify({"ok": False, "error": "Shortcut API not configured"}), 503
    if request.headers.get("X-API-Key", "") != SHORTCUT_API_KEY:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    group = (data.get("group") or "").strip() or None
    deadline_in = (data.get("deadline") or "").strip() or None
    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400

    tags = []
    if group:
        tags.append(f"@group:{group}")
    if deadline_in:
        tags.append(f"due {deadline_in}")
    full_text = text + ((" " + " ".join(tags)) if tags else "")

    deadline, deadline_raw = extract_deadline(full_text, context_year=datetime.now().year)
    tid = str(uuid.uuid4())

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO tasks
                        (id, text, type, done, backburner, source_filename, section,
                         group_name, source_date, deadline, deadline_raw)
                    VALUES (%s, %s, 'free', FALSE, FALSE, 'tasks.md', 'free',
                            %s, %s, %s, %s)
                """, (tid, full_text, group, date_cls.today(), deadline, deadline_raw))
        return jsonify({"ok": True, "text": full_text})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# --------------------------------------------------
# ENTRY
# --------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", "5050"))
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
