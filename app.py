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
                    parent_id TEXT,
                    snoozed_until DATE DEFAULT NULL,
                    estimate_minutes INT DEFAULT NULL,
                    recurrence_rule JSONB DEFAULT NULL,
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
                CREATE INDEX IF NOT EXISTS tasks_parent_id     ON tasks (parent_id);
                CREATE INDEX IF NOT EXISTS tasks_snoozed_until ON tasks (snoozed_until);
                CREATE INDEX IF NOT EXISTS task_time_log_task  ON task_time_log (task_id);
                CREATE INDEX IF NOT EXISTS task_deps_task      ON task_dependencies (task_id);
                CREATE INDEX IF NOT EXISTS task_deps_depends   ON task_dependencies (depends_on_id);
            """)
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
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='meetings' AND column_name='canvas_image'
                    ) THEN
                        ALTER TABLE meetings ADD COLUMN canvas_image TEXT;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='tasks' AND column_name='callout_source'
                    ) THEN
                        ALTER TABLE tasks ADD COLUMN callout_source TEXT NULL;
                    END IF;
                    -- Add FK constraint on parent_id if column exists but constraint doesn't
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.table_constraints tc
                        JOIN information_schema.constraint_column_usage ccu
                            ON tc.constraint_name = ccu.constraint_name
                        WHERE tc.table_name = 'tasks'
                            AND tc.constraint_type = 'FOREIGN KEY'
                            AND ccu.column_name = 'parent_id'
                    ) THEN
                        BEGIN
                            ALTER TABLE tasks ADD CONSTRAINT tasks_parent_id_fk
                                FOREIGN KEY (parent_id) REFERENCES tasks(id) ON DELETE CASCADE;
                        EXCEPTION WHEN OTHERS THEN NULL;
                        END;
                    END IF;
                END $$;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    company TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    email TEXT DEFAULT '',
                    phone TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    card_image TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS meeting_contacts (
                    meeting_id TEXT REFERENCES meetings(id) ON DELETE CASCADE,
                    contact_id TEXT REFERENCES contacts(id) ON DELETE CASCADE,
                    PRIMARY KEY (meeting_id, contact_id)
                );
                CREATE TABLE IF NOT EXISTS bill_references (
                    id          SERIAL PRIMARY KEY,
                    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                    bill_type   TEXT NOT NULL DEFAULT '',
                    bill_number TEXT NOT NULL DEFAULT '',
                    created_at  TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS bill_refs_meeting ON bill_references (meeting_id);
                CREATE TABLE IF NOT EXISTS meeting_scan_items (
                    id           SERIAL PRIMARY KEY,
                    meeting_id   TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                    callout_type TEXT NOT NULL,
                    text         TEXT NOT NULL,
                    task_id      TEXT REFERENCES tasks(id) ON DELETE SET NULL,
                    accepted     BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at   TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS scan_items_meeting ON meeting_scan_items (meeting_id);
            """)
            # Phase 1: organizations, asks, commitments, followup_triggers
            cur.execute("""
                CREATE TABLE IF NOT EXISTS organizations (
                    id         TEXT PRIMARY KEY,
                    name       TEXT NOT NULL,
                    type       TEXT,
                    notes      TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS asks (
                    id              TEXT PRIMARY KEY,
                    meeting_id      TEXT REFERENCES meetings(id) ON DELETE CASCADE,
                    text            TEXT NOT NULL,
                    organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
                    contact_id      TEXT REFERENCES contacts(id) ON DELETE SET NULL,
                    bill_ref_id     INTEGER REFERENCES bill_references(id) ON DELETE SET NULL,
                    status          TEXT NOT NULL DEFAULT 'logged',
                    priority        TEXT NOT NULL DEFAULT 'normal',
                    task_id         TEXT REFERENCES tasks(id) ON DELETE SET NULL,
                    source_excerpt  TEXT,
                    created_at      TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS commitments (
                    id              TEXT PRIMARY KEY,
                    meeting_id      TEXT REFERENCES meetings(id) ON DELETE CASCADE,
                    text            TEXT NOT NULL,
                    contact_id      TEXT REFERENCES contacts(id) ON DELETE SET NULL,
                    organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
                    related_ask_id  TEXT REFERENCES asks(id) ON DELETE SET NULL,
                    due_date        DATE,
                    status          TEXT NOT NULL DEFAULT 'open',
                    task_id         TEXT REFERENCES tasks(id) ON DELETE SET NULL,
                    source_excerpt  TEXT,
                    created_at      TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS followup_triggers (
                    id              TEXT PRIMARY KEY,
                    meeting_id      TEXT REFERENCES meetings(id) ON DELETE CASCADE,
                    condition_text  TEXT NOT NULL,
                    action_text     TEXT NOT NULL,
                    bill_ref_id     INTEGER REFERENCES bill_references(id) ON DELETE SET NULL,
                    contact_id      TEXT REFERENCES contacts(id) ON DELETE SET NULL,
                    organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
                    status          TEXT NOT NULL DEFAULT 'watching',
                    created_at      TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS asks_meeting   ON asks (meeting_id);
                CREATE INDEX IF NOT EXISTS asks_org       ON asks (organization_id);
                CREATE INDEX IF NOT EXISTS commits_meeting ON commitments (meeting_id);
                CREATE INDEX IF NOT EXISTS triggers_meeting ON followup_triggers (meeting_id);
            """)
            # New columns linking existing tables to organizations/asks/commitments
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='contacts' AND column_name='organization_id'
                    ) THEN
                        ALTER TABLE contacts ADD COLUMN organization_id TEXT
                            REFERENCES organizations(id) ON DELETE SET NULL;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='meetings' AND column_name='organization_id'
                    ) THEN
                        ALTER TABLE meetings ADD COLUMN organization_id TEXT
                            REFERENCES organizations(id) ON DELETE SET NULL;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='tasks' AND column_name='ask_id'
                    ) THEN
                        ALTER TABLE tasks ADD COLUMN ask_id TEXT
                            REFERENCES asks(id) ON DELETE SET NULL;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='tasks' AND column_name='commitment_id'
                    ) THEN
                        ALTER TABLE tasks ADD COLUMN commitment_id TEXT
                            REFERENCES commitments(id) ON DELETE SET NULL;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='tasks' AND column_name='organization_id'
                    ) THEN
                        ALTER TABLE tasks ADD COLUMN organization_id TEXT
                            REFERENCES organizations(id) ON DELETE SET NULL;
                    END IF;
                END $$;
            """)
            # Seed organizations from existing canonical_groups and link meetings
            cur.execute("""
                INSERT INTO organizations (id, name, created_at, updated_at)
                SELECT DISTINCT
                    trim('-' FROM regexp_replace(lower(canonical_group), '[^a-z0-9]+', '-', 'g')) AS id,
                    canonical_group AS name,
                    NOW(), NOW()
                FROM meetings
                WHERE canonical_group IS NOT NULL AND canonical_group != ''
                ON CONFLICT (id) DO NOTHING
            """)
            cur.execute("""
                UPDATE meetings
                SET organization_id =
                    trim('-' FROM regexp_replace(lower(canonical_group), '[^a-z0-9]+', '-', 'g'))
                WHERE organization_id IS NULL
                  AND canonical_group IS NOT NULL AND canonical_group != ''
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
    canvas_image: Optional[str] = None

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
            "canvas_image": self.canvas_image,
            "contacts": getattr(self, "_contacts", []),
            "bill_references": getattr(self, "_bill_references", []),
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
    callout_source: Optional[str] = None  # 'task' | 'followup' | 'important' | None
    ask_id: Optional[str] = None
    commitment_id: Optional[str] = None
    organization_id: Optional[str] = None

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
            "callout_source": self.callout_source,
            "ask_id": self.ask_id,
            "commitment_id": self.commitment_id,
            "organization_id": self.organization_id,
        }


@dataclass
class Organization:
    id: str
    name: str
    type: Optional[str]
    notes: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Ask:
    id: str
    meeting_id: Optional[str]
    text: str
    organization_id: Optional[str]
    contact_id: Optional[str]
    bill_ref_id: Optional[int]
    status: str
    priority: str
    task_id: Optional[str]
    source_excerpt: Optional[str]
    created_at: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "meeting_id": self.meeting_id,
            "text": self.text,
            "organization_id": self.organization_id,
            "contact_id": self.contact_id,
            "bill_ref_id": self.bill_ref_id,
            "status": self.status,
            "priority": self.priority,
            "task_id": self.task_id,
            "source_excerpt": self.source_excerpt,
            "created_at": self.created_at,
        }


@dataclass
class Commitment:
    id: str
    meeting_id: Optional[str]
    text: str
    contact_id: Optional[str]
    organization_id: Optional[str]
    related_ask_id: Optional[str]
    due_date: Optional[str]
    status: str
    task_id: Optional[str]
    source_excerpt: Optional[str]
    created_at: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "meeting_id": self.meeting_id,
            "text": self.text,
            "contact_id": self.contact_id,
            "organization_id": self.organization_id,
            "related_ask_id": self.related_ask_id,
            "due_date": self.due_date,
            "status": self.status,
            "task_id": self.task_id,
            "source_excerpt": self.source_excerpt,
            "created_at": self.created_at,
        }


@dataclass
class FollowupTrigger:
    id: str
    meeting_id: Optional[str]
    condition_text: str
    action_text: str
    bill_ref_id: Optional[int]
    contact_id: Optional[str]
    organization_id: Optional[str]
    status: str
    created_at: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "meeting_id": self.meeting_id,
            "condition_text": self.condition_text,
            "action_text": self.action_text,
            "bill_ref_id": self.bill_ref_id,
            "contact_id": self.contact_id,
            "organization_id": self.organization_id,
            "status": self.status,
            "created_at": self.created_at,
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


def _org_slug(name: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '-', name.strip().lower())
    return s.strip('-')[:40] or 'org'


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
    body: str, filename: str, meeting_id: str, group: str, date_str: Optional[str],
    callout_source_map: Optional[Dict[str, str]] = None,
) -> List[dict]:
    lines = body.splitlines()
    tasks = []
    in_reminders = in_actions = False
    year = _year_from_date(date_str)
    source_map = callout_source_map or {}

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
            "callout_source": source_map.get(text),
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
        canvas_image=row.get("canvas_image"),
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
        callout_source=row.get("callout_source"),
        ask_id=row.get("ask_id"),
        commitment_id=row.get("commitment_id"),
        organization_id=row.get("organization_id"),
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
            cur.execute("""
                SELECT c.id, c.name, c.company, c.title, c.email, c.phone, c.card_image
                FROM contacts c
                JOIN meeting_contacts mc ON mc.contact_id = c.id
                WHERE mc.meeting_id = %s
                ORDER BY c.name
            """, (mid,))
            contact_rows = [dict(r) for r in cur.fetchall()]
            cur.execute("""
                SELECT bill_type, bill_number
                FROM bill_references
                WHERE meeting_id = %s
                ORDER BY id
            """, (mid,))
            bill_rows = [dict(r) for r in cur.fetchall()]
    m = _row_to_meeting(dict(row), [dict(t) for t in task_rows])
    m._contacts = contact_rows
    m._bill_references = bill_rows
    return m


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


def db_get_org_profile(org_id: str) -> Optional[dict]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM organizations WHERE id = %s", (org_id,))
            org_row = cur.fetchone()
            if not org_row:
                return None
            cur.execute("""
                SELECT m.id, m.topic, to_char(m.file_date,'YYYY-MM-DD') AS date,
                       m.attendees, m.canonical_group
                FROM meetings m WHERE m.organization_id = %s
                ORDER BY m.file_date DESC NULLS LAST
            """, (org_id,))
            meetings = [dict(r) for r in cur.fetchall()]
            cur.execute("""
                SELECT a.*, to_char(a.created_at,'YYYY-MM-DD') AS created_at_str
                FROM asks a WHERE a.organization_id = %s
                ORDER BY a.created_at DESC
            """, (org_id,))
            asks = [dict(r) for r in cur.fetchall()]
            cur.execute("""
                SELECT c.*, to_char(c.created_at,'YYYY-MM-DD') AS created_at_str
                FROM commitments c WHERE c.organization_id = %s
                ORDER BY c.created_at DESC
            """, (org_id,))
            commitments_rows = [dict(r) for r in cur.fetchall()]
            cur.execute("""
                SELECT ft.*, to_char(ft.created_at,'YYYY-MM-DD') AS created_at_str
                FROM followup_triggers ft WHERE ft.organization_id = %s
                ORDER BY ft.created_at DESC
            """, (org_id,))
            triggers = [dict(r) for r in cur.fetchall()]
            cur.execute("""
                SELECT ct.id, ct.name, ct.title, ct.company, ct.email, ct.phone, ct.card_image
                FROM contacts ct WHERE ct.organization_id = %s
                ORDER BY ct.name
            """, (org_id,))
            contacts_rows = [dict(r) for r in cur.fetchall()]
            cur.execute("""
                SELECT br.bill_type, br.bill_number,
                       to_char(MIN(br.created_at),'YYYY-MM-DD') AS first_seen
                FROM bill_references br
                JOIN meetings m ON br.meeting_id = m.id
                WHERE m.organization_id = %s
                GROUP BY br.bill_type, br.bill_number
                ORDER BY MIN(br.created_at) DESC
            """, (org_id,))
            bills = [dict(r) for r in cur.fetchall()]
            cur.execute(_TASKS_SELECT + """
                WHERE NOT t.done AND t.organization_id = %s
                ORDER BY
                  CASE WHEN t.deadline IS NULL THEN 1 ELSE 0 END,
                  t.deadline ASC,
                  t.created_at DESC
            """, (org_id,))
            today_iso = date_cls.today().isoformat()
            open_tasks = [_task_row_to_task(dict(r), today_iso).as_dict()
                          for r in cur.fetchall()]
    return {
        "id": org_row["id"],
        "name": org_row["name"],
        "type": org_row["type"],
        "notes": org_row["notes"],
        "meetings": meetings,
        "asks": asks,
        "commitments": commitments_rows,
        "triggers": triggers,
        "contacts": contacts_rows,
        "bills": bills,
        "open_tasks": open_tasks,
    }


def db_get_pre_meeting_brief(org_id: str) -> dict:
    profile = db_get_org_profile(org_id)
    if not profile:
        return {"exists": False}
    open_asks = [a for a in profile["asks"]
                 if a["status"] not in ("completed", "declined", "no_action")]
    open_commitments = [c for c in profile["commitments"]
                        if c["status"] in ("open", "needs_review", "task_created")]
    last_meeting = profile["meetings"][0] if profile["meetings"] else None
    return {
        "exists": True,
        "org_id": org_id,
        "org_name": profile["name"],
        "last_meeting": last_meeting,
        "meeting_count": len(profile["meetings"]),
        "open_asks": open_asks,
        "open_commitments": open_commitments,
        "bills": profile["bills"],
        "contacts": profile["contacts"],
        "triggers": [t for t in profile["triggers"] if t["status"] == "watching"],
    }


# --------------------------------------------------
# IMPORT (parse markdown → DB)
# --------------------------------------------------

def import_meeting_from_content(
    filename: str,
    content: str,
    canvas_image: Optional[str] = None,
    callout_source_map: Optional[Dict[str, str]] = None,
) -> dict:
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

    tasks = _extract_tasks_from_body(
        body_md, filename, mid, canon, date_str,
        callout_source_map=callout_source_map,
    )
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
            "callout_source": None,
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
            if canvas_image:
                cur.execute(
                    "UPDATE meetings SET canvas_image = %s WHERE id = %s",
                    (canvas_image, mid),
                )
            for t in tasks:
                cur.execute("""
                    INSERT INTO tasks
                        (id, text, type, done, meeting_id, source_filename,
                         section, group_name, source_date, deadline, deadline_raw, callout_source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO UPDATE SET
                        text=EXCLUDED.text, type=EXCLUDED.type,
                        meeting_id=EXCLUDED.meeting_id,
                        source_filename=EXCLUDED.source_filename,
                        section=EXCLUDED.section, group_name=EXCLUDED.group_name,
                        source_date=EXCLUDED.source_date,
                        deadline=EXCLUDED.deadline, deadline_raw=EXCLUDED.deadline_raw,
                        callout_source=COALESCE(EXCLUDED.callout_source, tasks.callout_source)
                """, (
                    t["id"], t["text"], t["type"], t["done"], t["meeting_id"],
                    t["source_filename"], t["section"], t["group_name"],
                    t["source_date"], t["deadline"], t["deadline_raw"],
                    t.get("callout_source"),
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


@app.route("/api/meetings/<mid>", methods=["DELETE"])
def api_meeting_delete(mid: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM meetings WHERE id = %s", (mid,))
            if cur.rowcount == 0:
                return jsonify({"ok": False, "error": "Not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/contacts", methods=["GET"])
def api_contacts_list():
    q = request.args.get("q", "").lower()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.*, COUNT(mc.meeting_id) AS meeting_count
                FROM contacts c
                LEFT JOIN meeting_contacts mc ON mc.contact_id = c.id
                GROUP BY c.id
                ORDER BY c.name
            """)
            rows = [dict(r) for r in cur.fetchall()]
    if q:
        rows = [r for r in rows if q in (r.get("name") or "").lower()
                or q in (r.get("company") or "").lower()
                or q in (r.get("email") or "").lower()]
    for r in rows:
        r.pop("card_image", None)
    return jsonify(rows)


@app.route("/api/contacts", methods=["POST"])
def api_contacts_upsert():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    company = (data.get("company") or "").strip()
    title = (data.get("title") or "").strip()
    phone = (data.get("phone") or "").strip()
    notes = (data.get("notes") or "").strip()
    card_image = data.get("card_image") or None
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    key = email if email else (name + company).lower()
    cid = hashlib.sha1(key.encode()).hexdigest()[:16]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO contacts (id, name, company, title, email, phone, notes, card_image, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (id) DO UPDATE SET
                    name=EXCLUDED.name, company=EXCLUDED.company, title=EXCLUDED.title,
                    email=EXCLUDED.email, phone=EXCLUDED.phone, notes=EXCLUDED.notes,
                    card_image=COALESCE(EXCLUDED.card_image, contacts.card_image),
                    updated_at=NOW()
            """, (cid, name, company, title, email, phone, notes, card_image))
    return jsonify({"ok": True, "id": cid, "name": name})


@app.route("/api/meetings/<mid>/contacts", methods=["POST"])
def api_meeting_link_contact(mid: str):
    data = request.get_json(force=True, silent=True) or {}
    cid = (data.get("contact_id") or "").strip()
    if not cid:
        return jsonify({"ok": False, "error": "contact_id required"}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meeting_contacts (meeting_id, contact_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (mid, cid)
            )
    return jsonify({"ok": True})


@app.route("/api/meetings/<mid>/contacts/<cid>", methods=["DELETE"])
def api_meeting_unlink_contact(mid: str, cid: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM meeting_contacts WHERE meeting_id=%s AND contact_id=%s",
                (mid, cid)
            )
    return jsonify({"ok": True})


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


@app.route("/api/bills")
def api_bills():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT br.bill_type, br.bill_number,
                       json_agg(json_build_object(
                           'meeting_id', m.id,
                           'topic', COALESCE(NULLIF(m.topic, ''), m.filename),
                           'date', to_char(m.file_date, 'YYYY-MM-DD')
                       ) ORDER BY br.created_at DESC) AS meetings,
                       max(br.created_at)::date AS last_seen
                FROM bill_references br
                JOIN meetings m ON br.meeting_id = m.id
                GROUP BY br.bill_type, br.bill_number
                ORDER BY max(br.created_at) DESC
            """)
            rows = cur.fetchall()
    return jsonify([{
        "bill_type": r["bill_type"],
        "bill_number": r["bill_number"],
        "meetings": r["meetings"],
        "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
    } for r in rows])


@app.route("/api/organizations")
def api_organizations():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT o.id, o.name, o.type, o.notes,
                    COUNT(DISTINCT m.id) AS meeting_count,
                    to_char(MAX(m.file_date), 'YYYY-MM-DD') AS last_meeting,
                    COUNT(DISTINCT a.id) FILTER (
                        WHERE a.status NOT IN ('completed','declined','no_action')) AS open_asks,
                    COUNT(DISTINCT c.id) FILTER (
                        WHERE c.status IN ('open','needs_review','task_created')) AS open_commitments,
                    COUNT(DISTINCT t.id) FILTER (
                        WHERE NOT t.done AND t.organization_id = o.id) AS open_tasks
                FROM organizations o
                LEFT JOIN meetings m ON m.organization_id = o.id
                LEFT JOIN asks a ON a.organization_id = o.id
                LEFT JOIN commitments c ON c.organization_id = o.id
                LEFT JOIN tasks t ON t.organization_id = o.id
                GROUP BY o.id, o.name, o.type, o.notes
                ORDER BY MAX(m.file_date) DESC NULLS LAST, o.name
            """)
            rows = cur.fetchall()
    return jsonify([{
        "id": r["id"], "name": r["name"], "type": r["type"],
        "meeting_count": r["meeting_count"],
        "last_meeting": r["last_meeting"],
        "open_asks": r["open_asks"],
        "open_commitments": r["open_commitments"],
        "open_tasks": r["open_tasks"],
    } for r in rows])


@app.route("/api/organizations/<org_id>")
def api_organization_detail(org_id):
    profile = db_get_org_profile(org_id)
    if not profile:
        return jsonify({"ok": False, "error": "Not found"}), 404
    return jsonify(profile)


@app.route("/api/organizations/<org_id>/brief")
def api_organization_brief(org_id):
    return jsonify(db_get_pre_meeting_brief(org_id))


@app.route("/api/scan-items")
def api_scan_items_for_day():
    """Return all callout scan items for a given date (defaults to today),
    grouped by meeting. Each item carries linked-task status if applicable."""
    date_str = (request.args.get("date") or "").strip()
    try:
        target = date_cls.fromisoformat(date_str) if date_str else date_cls.today()
    except ValueError:
        target = date_cls.today()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT si.id, si.meeting_id, si.callout_type, si.text,
                       si.task_id, si.accepted,
                       to_char(si.created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS created_at_str,
                       m.topic AS meeting_topic, m.canonical_group AS meeting_group,
                       m.organization_id, m.file_date,
                       t.done AS task_done, t.deadline AS task_deadline,
                       t.priority AS task_priority
                FROM meeting_scan_items si
                JOIN meetings m ON m.id = si.meeting_id
                LEFT JOIN tasks t ON t.id = si.task_id
                WHERE m.file_date = %s
                ORDER BY m.file_date DESC, si.created_at ASC
            """, (target,))
            rows = [dict(r) for r in cur.fetchall()]
    # Group by meeting
    meetings_by_id: Dict[str, dict] = {}
    for r in rows:
        mid = r["meeting_id"]
        if mid not in meetings_by_id:
            meetings_by_id[mid] = {
                "meeting_id": mid,
                "topic": r.get("meeting_topic"),
                "group": r.get("meeting_group"),
                "organization_id": r.get("organization_id"),
                "date": r["file_date"].isoformat() if r.get("file_date") else None,
                "items": [],
            }
        meetings_by_id[mid]["items"].append({
            "id": r["id"],
            "type": r["callout_type"],
            "text": r["text"],
            "task_id": r.get("task_id"),
            "accepted": r["accepted"],
            "task_done": r.get("task_done"),
            "task_deadline": r["task_deadline"].isoformat() if r.get("task_deadline") else None,
            "task_priority": r.get("task_priority"),
        })
    return jsonify({
        "date": target.isoformat(),
        "meetings": list(meetings_by_id.values()),
    })


@app.route("/api/scan-items/<int:item_id>/update", methods=["POST"])
def api_scan_item_update(item_id):
    """Edit a scan item's text / type / due-date after the fact.
    Propagates to the linked task (if any) so the user's task list stays in sync."""
    data = request.get_json(force=True, silent=True) or {}
    new_text = (data.get("text") or "").strip() or None
    new_type = (data.get("type") or "").strip() or None
    due_raw = data.get("due")
    new_due = None
    if due_raw:
        try:
            new_due = date_cls.fromisoformat(str(due_raw).strip())
        except ValueError:
            return jsonify({"ok": False, "error": "Invalid due date"}), 400
    accepted = data.get("accepted")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM meeting_scan_items WHERE id = %s", (item_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({"ok": False, "error": "Not found"}), 404
            sets, params = [], []
            if new_text is not None:
                sets.append("text = %s"); params.append(new_text)
            if new_type is not None:
                sets.append("callout_type = %s"); params.append(new_type)
            if accepted is not None:
                sets.append("accepted = %s"); params.append(bool(accepted))
            if sets:
                params.append(item_id)
                cur.execute(f"UPDATE meeting_scan_items SET {', '.join(sets)} WHERE id = %s", params)
            task_id = row["task_id"]
            if task_id:
                t_sets, t_params = [], []
                if new_text is not None:
                    t_sets.append("text = %s"); t_params.append(new_text)
                if new_due is not None:
                    t_sets.append("deadline = %s"); t_params.append(new_due)
                if t_sets:
                    t_params.append(task_id)
                    cur.execute(f"UPDATE tasks SET {', '.join(t_sets)} WHERE id = %s", t_params)
    return jsonify({"ok": True})


@app.route("/api/people")
def api_people():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ct.id, ct.name, ct.company, ct.title, ct.email, ct.phone,
                       ct.card_image, ct.organization_id,
                       o.name AS org_name,
                       COUNT(DISTINCT mc.meeting_id) AS meeting_count,
                       to_char(MAX(m.file_date), 'YYYY-MM-DD') AS last_seen
                FROM contacts ct
                LEFT JOIN organizations o ON ct.organization_id = o.id
                LEFT JOIN meeting_contacts mc ON mc.contact_id = ct.id
                LEFT JOIN meetings m ON m.id = mc.meeting_id
                GROUP BY ct.id, ct.name, ct.company, ct.title, ct.email,
                         ct.phone, ct.card_image, ct.organization_id, o.name
                ORDER BY MAX(m.file_date) DESC NULLS LAST, ct.name
            """)
            rows = cur.fetchall()
    return jsonify([{
        "id": r["id"], "name": r["name"], "company": r["company"],
        "title": r["title"], "email": r["email"], "phone": r["phone"],
        "card_image": r["card_image"], "organization_id": r["organization_id"],
        "org_name": r["org_name"],
        "meeting_count": r["meeting_count"], "last_seen": r["last_seen"],
    } for r in rows])


@app.route("/api/people/<contact_id>")
def api_person_detail(contact_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM contacts WHERE id = %s", (contact_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({"ok": False, "error": "Not found"}), 404
            cur.execute("""
                SELECT m.id, m.topic, to_char(m.file_date,'YYYY-MM-DD') AS date,
                       m.canonical_group, m.attendees
                FROM meetings m
                JOIN meeting_contacts mc ON mc.meeting_id = m.id
                WHERE mc.contact_id = %s
                ORDER BY m.file_date DESC NULLS LAST
            """, (contact_id,))
            meetings = [dict(r) for r in cur.fetchall()]
            cur.execute("""
                SELECT a.id, a.text, a.status, a.priority,
                       to_char(a.created_at,'YYYY-MM-DD') AS created_at_str
                FROM asks a WHERE a.contact_id = %s
                ORDER BY a.created_at DESC
            """, (contact_id,))
            asks = [dict(r) for r in cur.fetchall()]
    return jsonify({
        "id": row["id"], "name": row["name"], "company": row["company"],
        "title": row["title"], "email": row["email"], "phone": row["phone"],
        "card_image": row["card_image"], "organization_id": row["organization_id"],
        "meetings": meetings, "asks": asks,
    })


@app.route("/api/asks")
def api_asks():
    status = request.args.get("status")
    org_id = request.args.get("org_id")
    with get_db() as conn:
        with conn.cursor() as cur:
            conditions = []
            params: list = []
            if status:
                conditions.append("a.status = %s")
                params.append(status)
            if org_id:
                conditions.append("a.organization_id = %s")
                params.append(org_id)
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            cur.execute(f"""
                SELECT a.*, o.name AS org_name,
                       to_char(a.created_at,'YYYY-MM-DD') AS created_at_str,
                       to_char(m.file_date,'YYYY-MM-DD') AS meeting_date,
                       m.topic AS meeting_topic
                FROM asks a
                LEFT JOIN organizations o ON a.organization_id = o.id
                LEFT JOIN meetings m ON a.meeting_id = m.id
                {where}
                ORDER BY a.created_at DESC
            """, params)
            rows = cur.fetchall()
    return jsonify([{
        "id": r["id"], "text": r["text"], "status": r["status"],
        "priority": r["priority"], "meeting_id": r["meeting_id"],
        "organization_id": r["organization_id"], "org_name": r["org_name"],
        "contact_id": r["contact_id"], "bill_ref_id": r["bill_ref_id"],
        "task_id": r["task_id"], "source_excerpt": r["source_excerpt"],
        "created_at": r["created_at_str"],
        "meeting_date": r["meeting_date"], "meeting_topic": r["meeting_topic"],
    } for r in rows])


@app.route("/api/asks/<ask_id>/status", methods=["POST"])
def api_ask_status(ask_id):
    data = request.get_json(force=True, silent=True) or {}
    status = (data.get("status") or "").strip()
    valid = {"logged","needs_review","under_review","task_created","accepted",
             "declined","completed","no_action","notify_if_changes"}
    if status not in valid:
        return jsonify({"ok": False, "error": "Invalid status"}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE asks SET status = %s WHERE id = %s", (status, ask_id))
    return jsonify({"ok": True})


@app.route("/api/commitments")
def api_commitments():
    status = request.args.get("status")
    org_id = request.args.get("org_id")
    with get_db() as conn:
        with conn.cursor() as cur:
            conditions = []
            params: list = []
            if status:
                conditions.append("c.status = %s")
                params.append(status)
            if org_id:
                conditions.append("c.organization_id = %s")
                params.append(org_id)
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            cur.execute(f"""
                SELECT c.*, o.name AS org_name,
                       to_char(c.created_at,'YYYY-MM-DD') AS created_at_str,
                       to_char(c.due_date,'YYYY-MM-DD') AS due_date_str,
                       to_char(m.file_date,'YYYY-MM-DD') AS meeting_date,
                       m.topic AS meeting_topic
                FROM commitments c
                LEFT JOIN organizations o ON c.organization_id = o.id
                LEFT JOIN meetings m ON c.meeting_id = m.id
                {where}
                ORDER BY c.created_at DESC
            """, params)
            rows = cur.fetchall()
    return jsonify([{
        "id": r["id"], "text": r["text"], "status": r["status"],
        "meeting_id": r["meeting_id"], "organization_id": r["organization_id"],
        "org_name": r["org_name"], "contact_id": r["contact_id"],
        "related_ask_id": r["related_ask_id"], "due_date": r["due_date_str"],
        "task_id": r["task_id"], "source_excerpt": r["source_excerpt"],
        "created_at": r["created_at_str"],
        "meeting_date": r["meeting_date"], "meeting_topic": r["meeting_topic"],
    } for r in rows])


@app.route("/api/commitments/<commitment_id>/status", methods=["POST"])
def api_commitment_status(commitment_id):
    data = request.get_json(force=True, silent=True) or {}
    status = (data.get("status") or "").strip()
    valid = {"open","task_created","waiting","completed","closed_no_action","needs_review"}
    if status not in valid:
        return jsonify({"ok": False, "error": "Invalid status"}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE commitments SET status = %s WHERE id = %s",
                        (status, commitment_id))
    return jsonify({"ok": True})


@app.route("/api/commitments/<commitment_id>/create-task", methods=["POST"])
def api_commitment_create_task(commitment_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.*, o.name AS org_name,
                       to_char(m.file_date,'YYYY-MM-DD') AS meeting_date
                FROM commitments c
                LEFT JOIN organizations o ON c.organization_id = o.id
                LEFT JOIN meetings m ON c.meeting_id = m.id
                WHERE c.id = %s
            """, (commitment_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({"ok": False, "error": "Commitment not found"}), 404
            if row["task_id"]:
                return jsonify({"ok": True, "task_id": row["task_id"], "already_exists": True})
            tid = str(uuid.uuid4())
            org_ctx = f" (for {row['org_name']})" if row["org_name"] else ""
            mtg_ctx = f" — from meeting {row['meeting_date']}" if row["meeting_date"] else ""
            cur.execute("""
                INSERT INTO tasks
                    (id, text, type, done, backburner, meeting_id, source_filename,
                     section, group_name, source_date, priority, commitment_id, organization_id,
                     callout_source, created_at)
                VALUES (%s,%s,'action',FALSE,FALSE,%s,%s,'action_items',%s,%s,'normal',%s,%s,'commitment',NOW())
            """, (
                tid,
                row["text"] + org_ctx + mtg_ctx,
                row["meeting_id"],
                f"commitment-{commitment_id[:8]}",
                row["org_name"] or "",
                row["meeting_date"],
                commitment_id,
                row["organization_id"],
            ))
            cur.execute("UPDATE commitments SET status='task_created', task_id=%s WHERE id=%s",
                        (tid, commitment_id))
    return jsonify({"ok": True, "task_id": tid})


@app.route("/api/followup-triggers")
def api_followup_triggers():
    status = request.args.get("status", "watching")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ft.*, o.name AS org_name,
                       to_char(ft.created_at,'YYYY-MM-DD') AS created_at_str,
                       to_char(m.file_date,'YYYY-MM-DD') AS meeting_date
                FROM followup_triggers ft
                LEFT JOIN organizations o ON ft.organization_id = o.id
                LEFT JOIN meetings m ON ft.meeting_id = m.id
                WHERE ft.status = %s
                ORDER BY ft.created_at DESC
            """, (status,))
            rows = cur.fetchall()
    return jsonify([{
        "id": r["id"], "condition_text": r["condition_text"],
        "action_text": r["action_text"], "status": r["status"],
        "meeting_id": r["meeting_id"], "organization_id": r["organization_id"],
        "org_name": r["org_name"], "bill_ref_id": r["bill_ref_id"],
        "created_at": r["created_at_str"], "meeting_date": r["meeting_date"],
    } for r in rows])


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
                new_id = str(uuid.uuid4())
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
# CLAUDE VISION TRANSCRIPTION
# --------------------------------------------------

@app.route("/api/notes/transcribe", methods=["POST"])
def api_notes_transcribe():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"ok": False, "error": "Transcription not configured"}), 503

    data = request.get_json(force=True, silent=True) or {}
    image_data = data.get("image", "")
    if not image_data:
        return jsonify({"ok": False, "error": "image required"}), 400

    if "," in image_data:
        image_data = image_data.split(",", 1)[1]

    prompt = (
        "Transcribe all handwritten text from this image exactly as written. "
        "Preserve line breaks — output one line of text per line of handwriting. "
        "Return only the transcribed text, nothing else."
    )

    try:
        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        text = message.content[0].text.strip()
    except Exception as e:
        return jsonify({"ok": False, "error": f"Transcription failed: {e}"}), 500

    return jsonify({"ok": True, "text": text})


# --------------------------------------------------
# HANDWRITING INTAKE (canvas image + confirmed items → meeting note)
# --------------------------------------------------

@app.route("/api/notes/intake", methods=["POST"])
def api_notes_intake():
    data = request.get_json(force=True, silent=True) or {}

    note_group = (data.get("group") or "").strip() or "intake"
    note_topic = (data.get("topic") or "").strip()
    note_date = (data.get("date") or "").strip() or date_cls.today().isoformat()
    note_attendees = (data.get("attendees") or "").strip()
    canvas_image = data.get("canvas_image") or None
    meeting_type = (data.get("meeting_type") or "").strip()
    purpose_val = (data.get("purpose_val") or "").strip()

    # Build purpose list from meeting type and optional constituent purpose
    _MEETING_TYPE_LABELS = {
        "1on1": "1:1", "staff": "Staff Meeting", "legteam": "Leg. Team",
        "constituent": "Constituent", "briefing": "Briefing", "other": "Other",
    }
    note_purpose: list = []
    if meeting_type and meeting_type in _MEETING_TYPE_LABELS:
        note_purpose.append(_MEETING_TYPE_LABELS[meeting_type])
    if purpose_val:
        note_purpose.append(purpose_val)

    # confirmed_items from canvas scan: [{type, text, billType?, billNumber?}]
    confirmed_items: List[dict] = data.get("confirmed_items") or []
    bill_items = [i for i in confirmed_items if i.get("type") == "bill"]

    note_body = (data.get("body") or "").strip()
    action_items_raw = (data.get("action_items") or "").strip()
    reminders_raw = (data.get("reminders") or "").strip()

    # Map of task text -> originating callout source ('task' | 'followup' | 'important').
    # Stamped onto created tasks so the UI can show provenance badges and the user
    # can find what they originally wrote on the canvas.
    callout_source_map: Dict[str, str] = {}

    # Map of item text -> ISO due date set in the review queue ("YYYY-MM-DD").
    # Applied to created tasks / commitments after import.
    due_map: Dict[str, str] = {}
    for _item in confirmed_items:
        _due = (_item.get("due") or "").strip()
        _txt = (_item.get("text") or "").strip()
        if _txt and _due:
            try:
                date_cls.fromisoformat(_due)  # validate
                due_map[_txt] = _due
            except ValueError:
                pass

    # If canvas items provided, merge them into the body sections.
    # Routing: task + followup -> Action Items (type=action), important -> Reminders.
    if confirmed_items:
        task_texts     = [i["text"] for i in confirmed_items if i.get("type") == "task"     and i.get("text")]
        followup_texts = [i["text"] for i in confirmed_items if i.get("type") == "followup" and i.get("text")]
        important_texts = [i["text"] for i in confirmed_items if i.get("type") == "important" and i.get("text")]
        bill_refs      = [i["text"] for i in confirmed_items if i.get("type") == "bill"     and i.get("text")]
        person_refs    = [i["text"] for i in confirmed_items if i.get("type") == "person"   and i.get("text")]
        deadline_refs  = [i["text"] for i in confirmed_items if i.get("type") == "deadline" and i.get("text")]

        for t in task_texts:      callout_source_map[t] = "task"
        for t in followup_texts:  callout_source_map[t] = "followup"
        for t in important_texts: callout_source_map[t] = "important"

        existing_actions = [l.strip() for l in action_items_raw.splitlines() if l.strip()]
        existing_reminders = [l.strip() for l in reminders_raw.splitlines() if l.strip()]
        action_items_raw = "\n".join(existing_actions + task_texts + followup_texts)
        reminders_raw = "\n".join(existing_reminders + important_texts)

        # Append structured references as context in the body
        extras = []
        if bill_refs:
            extras.append("Bills referenced: " + ", ".join(bill_refs))
        if person_refs:
            extras.append("People: " + ", ".join(person_refs))
        if deadline_refs:
            extras.append("Deadlines noted: " + ", ".join(deadline_refs))
        if extras:
            note_body = (note_body + "\n\n" + "\n".join(extras)).strip()

    has_new_items = any(i.get("type") in ("ask", "commitment", "trigger")
                        for i in confirmed_items)
    if not any([note_body, action_items_raw, reminders_raw, canvas_image, has_new_items]):
        return jsonify({"ok": False, "error": "Nothing to save — add some notes or tasks"}), 400

    # Build body with sections the existing parser understands
    body_parts: List[str] = []
    if note_body:
        body_parts.append(note_body)

    action_lines = [l.strip() for l in action_items_raw.splitlines() if l.strip()]
    if action_lines:
        body_parts.append("\nAction Items:")
        body_parts.extend(f"- [ ] {line}" for line in action_lines)

    reminder_lines = [l.strip() for l in reminders_raw.splitlines() if l.strip()]
    if reminder_lines:
        body_parts.append("\nReminders/Important:")
        body_parts.extend(f"- [ ] {line}" for line in reminder_lines)

    body = "\n".join(body_parts)

    # YAML frontmatter
    safe_group = re.sub(r"[^a-zA-Z0-9_-]", "-", note_group)[:30]
    fm_parts = [f"group: {note_group}", f"date: {note_date}"]
    if note_topic:
        fm_parts.append(f"topic: {note_topic}")
    if note_attendees:
        fm_parts.append(f"attendees: {note_attendees}")
    if note_purpose:
        fm_parts.append("purpose: [" + ", ".join(f'"{p}"' for p in note_purpose) + "]")

    content = "---\n" + "\n".join(fm_parts) + "\n---\n\n" + body
    filename = f"{note_date} - {safe_group}.md"

    try:
        summary = import_meeting_from_content(
            filename, content,
            canvas_image=canvas_image,
            callout_source_map=callout_source_map or None,
        )
        mid_out = summary.get("id")
        created = {"actions": 0, "followups": 0, "reminders": 0,
                   "bills": 0, "asks": 0, "commitments": 0, "triggers": 0}

        if mid_out:
            with get_db() as conn:
                with conn.cursor() as cur:
                    # Resolve or create organization for this meeting
                    org_id_out: Optional[str] = None
                    if note_group and note_group != "intake":
                        org_id_out = _org_slug(note_group)
                        cur.execute("""
                            INSERT INTO organizations (id, name, created_at, updated_at)
                            VALUES (%s, %s, NOW(), NOW())
                            ON CONFLICT (id) DO UPDATE SET updated_at = NOW()
                        """, (org_id_out, note_group))
                        cur.execute("""
                            UPDATE meetings SET organization_id = %s WHERE id = %s
                        """, (org_id_out, mid_out))

                    # Bill references
                    if bill_items:
                        for bill in bill_items:
                            bt = (bill.get("billType") or "").strip()
                            bn = (bill.get("billNumber") or "").strip()
                            if bt and bn:
                                cur.execute(
                                    "INSERT INTO bill_references (meeting_id, bill_type, bill_number)"
                                    " VALUES (%s, %s, %s)",
                                    (mid_out, bt, bn)
                                )
                                created["bills"] += 1

                    # Asks, commitments, triggers — new types
                    ask_items_in    = [i for i in confirmed_items if i.get("type") == "ask"]
                    commit_items_in = [i for i in confirmed_items if i.get("type") == "commitment"]
                    trigger_items_in = [i for i in confirmed_items if i.get("type") == "trigger"]

                    for item in ask_items_in:
                        text = (item.get("text") or "").strip()
                        if not text:
                            continue
                        aid = _task_id(mid_out, "ask", text)
                        cur.execute("""
                            INSERT INTO asks
                                (id, meeting_id, text, organization_id, status, priority, source_excerpt)
                            VALUES (%s, %s, %s, %s, 'logged', 'normal', %s)
                            ON CONFLICT (id) DO NOTHING
                        """, (aid, mid_out, text, org_id_out, text))
                        cur.execute("""
                            INSERT INTO meeting_scan_items
                                (meeting_id, callout_type, text, task_id, accepted)
                            VALUES (%s, 'ask', %s, NULL, TRUE)
                        """, (mid_out, text))
                        created["asks"] += 1

                    for item in commit_items_in:
                        text = (item.get("text") or "").strip()
                        if not text:
                            continue
                        cid = _task_id(mid_out, "commitment", text)
                        c_due = due_map.get(text)
                        cur.execute("""
                            INSERT INTO commitments
                                (id, meeting_id, text, organization_id, status, source_excerpt, due_date)
                            VALUES (%s, %s, %s, %s, 'open', %s, %s)
                            ON CONFLICT (id) DO NOTHING
                        """, (cid, mid_out, text, org_id_out, text, c_due))
                        # Create a task for the commitment
                        task_tid = _task_id(mid_out, "commitment-task", text)
                        org_ctx = f" (for {note_group})" if note_group and note_group != "intake" else ""
                        cur.execute("""
                            INSERT INTO tasks
                                (id, text, type, done, backburner, meeting_id, source_filename,
                                 section, group_name, source_date, deadline, priority, commitment_id,
                                 organization_id, callout_source, created_at)
                            VALUES (%s,%s,'action',FALSE,FALSE,%s,%s,'action_items',%s,%s,%s,
                                    'normal',%s,%s,'commitment',NOW())
                            ON CONFLICT (id) DO NOTHING
                        """, (
                            task_tid, text + org_ctx, mid_out, filename,
                            note_group or "", note_date, c_due,
                            cid, org_id_out,
                        ))
                        cur.execute("""
                            UPDATE commitments SET status='task_created', task_id=%s WHERE id=%s
                        """, (task_tid, cid))
                        cur.execute("""
                            INSERT INTO meeting_scan_items
                                (meeting_id, callout_type, text, task_id, accepted)
                            VALUES (%s, 'commitment', %s, %s, TRUE)
                        """, (mid_out, text, task_tid))
                        created["commitments"] += 1

                    for item in trigger_items_in:
                        full_text = (item.get("text") or "").strip()
                        if not full_text:
                            continue
                        # Split on → or -> for condition/action
                        if "→" in full_text:
                            parts = full_text.split("→", 1)
                        elif "->" in full_text:
                            parts = full_text.split("->", 1)
                        else:
                            parts = [full_text, ""]
                        cond = parts[0].strip().lstrip("FU IF").lstrip("FU if").strip()
                        action = parts[1].strip() if len(parts) > 1 else ""
                        trig_id = _task_id(mid_out, "trigger", full_text)
                        cur.execute("""
                            INSERT INTO followup_triggers
                                (id, meeting_id, condition_text, action_text,
                                 organization_id, status)
                            VALUES (%s, %s, %s, %s, %s, 'watching')
                            ON CONFLICT (id) DO NOTHING
                        """, (trig_id, mid_out, cond or full_text, action, org_id_out))
                        cur.execute("""
                            INSERT INTO meeting_scan_items
                                (meeting_id, callout_type, text, task_id, accepted)
                            VALUES (%s, 'trigger', %s, NULL, TRUE)
                        """, (mid_out, full_text))
                        created["triggers"] += 1

                    # Audit rows + per-type counts for task/followup/important/deadline/person/bill
                    for item in confirmed_items:
                        ctype = item.get("type") or ""
                        if ctype in ("ask", "commitment", "trigger"):
                            continue  # already handled above
                        text = (item.get("text") or "").strip()
                        if not text:
                            continue
                        tid: Optional[str] = None
                        if ctype in ("task", "followup"):
                            tid = _task_id(filename, "action_items", text)
                            if ctype == "task":
                                created["actions"] += 1
                            else:
                                created["followups"] += 1
                        elif ctype == "important":
                            tid = _task_id(filename, "reminders", text)
                            created["reminders"] += 1
                        # deadline/person/bill: no task row; tid stays None.
                        cur.execute("""
                            INSERT INTO meeting_scan_items
                                (meeting_id, callout_type, text, task_id, accepted)
                            VALUES (%s, %s, %s, %s, TRUE)
                        """, (mid_out, ctype, text, tid))
                    # Stamp organization_id on tasks created for this meeting
                    if org_id_out:
                        cur.execute("""
                            UPDATE tasks SET organization_id = %s
                            WHERE meeting_id = %s AND organization_id IS NULL
                        """, (org_id_out, mid_out))

                    # Apply manually-picked due dates from the review queue to the
                    # tasks created by the markdown import (which only auto-extracts
                    # "due:"-style deadlines from text).
                    for _text, _due in due_map.items():
                        cur.execute("""
                            UPDATE tasks SET deadline = %s
                            WHERE meeting_id = %s AND text = %s AND deadline IS NULL
                        """, (_due, mid_out, _text))

        return jsonify({
            "ok": True,
            "meeting_id": mid_out,
            "filename": filename,
            "task_count": summary["tasks"],
            "created": created,
            "topic": note_topic,
            "group": note_group,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"Database error: {e}"}), 500


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
