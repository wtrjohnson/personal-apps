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
CONGRESS_API_KEY = os.environ.get("CONGRESS_API_KEY", "")
# Rep. Blake Moore (R, UT-01). Overridable so the tracker can follow a different member.
CONGRESS_MEMBER_BIOGUIDE = os.environ.get("CONGRESS_MEMBER_BIOGUIDE", "M001213")

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


# --------------------------------------------------
# CONGRESS HELPERS
# --------------------------------------------------

def _current_congress(d: Optional[date_cls] = None) -> int:
    """Congress number for a date. A new Congress convenes Jan 3 of each odd year."""
    override = os.environ.get("CURRENT_CONGRESS")
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    d = d or date_cls.today()
    if d.year % 2 == 0:
        start = d.year - 1
    else:
        start = d.year if (d.month, d.day) >= (1, 3) else d.year - 2
    return (start - 1789) // 2 + 1


def _normalize_bill_type(s: Optional[str]) -> str:
    """'H.R.' -> 'HR', ' s ' -> 'S'. Strips dots/spaces, uppercases."""
    return re.sub(r"[^A-Za-z]", "", (s or "")).upper()


def _normalize_bill_number(s: Optional[str]) -> str:
    """Digits only, e.g. 'No. 1234' -> '1234'."""
    return re.sub(r"\D", "", (s or ""))


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
                        WHERE table_name='meetings' AND column_name='status'
                    ) THEN
                        ALTER TABLE meetings ADD COLUMN status TEXT DEFAULT 'complete';
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='meetings' AND column_name='dtstart'
                    ) THEN
                        ALTER TABLE meetings ADD COLUMN dtstart TIMESTAMPTZ;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='meetings' AND column_name='meeting_link'
                    ) THEN
                        ALTER TABLE meetings ADD COLUMN meeting_link TEXT;
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
                CREATE TABLE IF NOT EXISTS external_calendar_events (
                    id           SERIAL PRIMARY KEY,
                    user_id      TEXT NOT NULL DEFAULT 'default',
                    ics_uid      TEXT NOT NULL,
                    recurrence_id TEXT,
                    sequence     INTEGER NOT NULL DEFAULT 0,
                    method       TEXT,
                    status       TEXT,
                    summary      TEXT,
                    description  TEXT,
                    location     TEXT,
                    dtstart      TIMESTAMPTZ,
                    dtend        TIMESTAMPTZ,
                    organizer    TEXT,
                    attendees    JSONB DEFAULT '[]',
                    rrule        TEXT,
                    raw_ics      TEXT,
                    meeting_id   TEXT REFERENCES meetings(id) ON DELETE SET NULL,
                    created_at   TIMESTAMPTZ DEFAULT NOW(),
                    updated_at   TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (user_id, ics_uid, recurrence_id)
                );
                CREATE INDEX IF NOT EXISTS ece_uid ON external_calendar_events (ics_uid);
                CREATE INDEX IF NOT EXISTS ece_dtstart ON external_calendar_events (dtstart);
            """)
            # calendar_event_id FK can only be added after external_calendar_events exists
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='meetings' AND column_name='calendar_event_id'
                    ) THEN
                        ALTER TABLE meetings ADD COLUMN calendar_event_id INTEGER
                            REFERENCES external_calendar_events(id) ON DELETE SET NULL;
                    END IF;
                END $$;
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
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='tasks' AND column_name='contact_id'
                    ) THEN
                        ALTER TABLE tasks ADD COLUMN contact_id TEXT
                            REFERENCES contacts(id) ON DELETE SET NULL;
                    END IF;
                END $$;
            """)
            # Many-to-many people <-> organizations
            cur.execute("""
                CREATE TABLE IF NOT EXISTS contact_organizations (
                    contact_id      TEXT REFERENCES contacts(id) ON DELETE CASCADE,
                    organization_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
                    PRIMARY KEY (contact_id, organization_id)
                );
                CREATE INDEX IF NOT EXISTS contact_orgs_org ON contact_organizations (organization_id);
            """)
            # Standalone notes attached to an organization or a person
            cur.execute("""
                CREATE TABLE IF NOT EXISTS entity_notes (
                    id          SERIAL PRIMARY KEY,
                    entity_type TEXT NOT NULL,   -- 'organization' | 'contact'
                    entity_id   TEXT NOT NULL,
                    body        TEXT NOT NULL DEFAULT '',
                    created_at  TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS entity_notes_lookup
                    ON entity_notes (entity_type, entity_id);
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
            # Seed organizations from distinct task group_name + backfill task org links
            cur.execute("""
                INSERT INTO organizations (id, name, created_at, updated_at)
                SELECT DISTINCT
                    trim('-' FROM regexp_replace(lower(group_name), '[^a-z0-9]+', '-', 'g')) AS id,
                    group_name AS name,
                    NOW(), NOW()
                FROM tasks
                WHERE group_name IS NOT NULL AND group_name != ''
                ON CONFLICT (id) DO NOTHING
            """)
            cur.execute("""
                UPDATE tasks
                SET organization_id =
                    trim('-' FROM regexp_replace(lower(group_name), '[^a-z0-9]+', '-', 'g'))
                WHERE organization_id IS NULL
                  AND group_name IS NOT NULL AND group_name != ''
            """)
            # Backfill the people<->org join table from the legacy single-org column
            cur.execute("""
                INSERT INTO contact_organizations (contact_id, organization_id)
                SELECT id, organization_id FROM contacts
                WHERE organization_id IS NOT NULL
                ON CONFLICT DO NOTHING
            """)
            # Strip the legacy trailing "@group:..." tag that the add-task flow used to
            # append into free-task text (organization is a structured field now).
            cur.execute(r"""
                UPDATE tasks
                SET text = regexp_replace(text, '\s*@group:.*$', '')
                WHERE text LIKE '%@group:%'
            """)
            # ---- Bill Tracker (Congress.gov) ----
            # Tag note-flagged bills with a Congress so matches are exact across Congresses.
            cur.execute("ALTER TABLE bill_references ADD COLUMN IF NOT EXISTS congress INTEGER")
            cur.execute(
                "UPDATE bill_references SET congress = %s WHERE congress IS NULL",
                (_current_congress(),),
            )
            cur.execute("""
                CREATE INDEX IF NOT EXISTS bill_refs_cong_typenum
                    ON bill_references (congress, bill_type, bill_number);
                CREATE TABLE IF NOT EXISTS tracked_bills (
                    id                 TEXT PRIMARY KEY,    -- "{congress}-{type}-{number}" lower
                    congress           INTEGER,
                    bill_type          TEXT NOT NULL,       -- normalized upper, e.g. HR / S / HRES
                    bill_number        TEXT NOT NULL,
                    relationship       TEXT NOT NULL,       -- 'sponsored' | 'cosponsored'
                    title              TEXT,
                    introduced_date    DATE,
                    latest_action      TEXT,
                    latest_action_date DATE,
                    url                TEXT,
                    raw                JSONB DEFAULT '{}',
                    last_synced        TIMESTAMP DEFAULT NOW(),
                    created_at         TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS tracked_bills_cong_typenum
                    ON tracked_bills (congress, bill_type, bill_number);
                CREATE TABLE IF NOT EXISTS bill_match_flags (
                    id              TEXT PRIMARY KEY,
                    tracked_bill_id TEXT REFERENCES tracked_bills(id) ON DELETE CASCADE,
                    bill_ref_id     INTEGER REFERENCES bill_references(id) ON DELETE CASCADE,
                    status          TEXT NOT NULL DEFAULT 'new',  -- 'new'|'notified'|'dismissed'
                    noticed_at      TIMESTAMP DEFAULT NOW(),
                    resolved_at     TIMESTAMP,
                    UNIQUE (tracked_bill_id, bill_ref_id)
                );
                CREATE TABLE IF NOT EXISTS bill_sync_meta (
                    id          INTEGER PRIMARY KEY DEFAULT 1,
                    last_synced TIMESTAMP,
                    last_result JSONB
                );
            """)
            # "Will's Bills" — bills the user is personally working on. Kept out of the
            # sync upsert's SET list so the flag survives re-syncs.
            cur.execute("ALTER TABLE tracked_bills ADD COLUMN IF NOT EXISTS working_on BOOLEAN NOT NULL DEFAULT FALSE")


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
    _tasks_full: List[dict] = None  # [{id, text, type, done}] for editing

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
            "tasks_full": self._tasks_full or [],
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
    contact_id: Optional[str] = None

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
            "contact_id": self.contact_id,
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


def _contact_name_key(name: str) -> str:
    """Stable contact id derived from a name only (used for attendee-derived contacts)."""
    return hashlib.sha1(name.strip().lower().encode()).hexdigest()[:16]


def _org_for_name(cur, name: Optional[str]) -> Optional[str]:
    """Upsert an organization by display name and return its id. Returns None for blank
    or the 'intake' placeholder so callers don't create junk orgs."""
    name = (name or "").strip()
    if not name or name == "intake":
        return None
    oid = _org_slug(name)
    cur.execute("""
        INSERT INTO organizations (id, name, created_at, updated_at)
        VALUES (%s, %s, NOW(), NOW())
        ON CONFLICT (id) DO UPDATE SET updated_at = NOW()
    """, (oid, name))
    return oid


def _link_contact_org(cur, contact_id: Optional[str], org_id: Optional[str]) -> None:
    """Associate a person with an organization (many-to-many). No-op if either is missing."""
    if not contact_id or not org_id:
        return
    cur.execute("""
        INSERT INTO contact_organizations (contact_id, organization_id)
        VALUES (%s, %s) ON CONFLICT DO NOTHING
    """, (contact_id, org_id))


def _upsert_attendee_contacts(cur, mid: str, attendees_str: Optional[str],
                              org_id: Optional[str]) -> None:
    """Split a free-text attendees string and ensure each name is a People contact
    linked to this meeting. Uses ON CONFLICT DO NOTHING so re-saving a meeting never
    clobbers a contact the user has since enriched. organization_id is only set on the
    first insert and only when org_id refers to a real organization."""
    for raw in re.split(r"[;,]", attendees_str or ""):
        name = raw.strip()
        if not name:
            continue
        cid = _contact_name_key(name)
        cur.execute("""
            INSERT INTO contacts (id, name, organization_id, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (id) DO NOTHING
        """, (cid, name, org_id))
        cur.execute("""
            INSERT INTO meeting_contacts (meeting_id, contact_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
        """, (mid, cid))
        _link_contact_org(cur, cid, org_id)


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
        _tasks_full=[{"id": t["id"], "text": t["text"], "type": t["type"], "done": t["done"]} for t in task_rows],
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
        contact_id=row.get("contact_id"),
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
                SELECT id, bill_type, bill_number
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
            cur.execute("""
                SELECT m.id, m.topic, to_char(m.file_date,'YYYY-MM-DD') AS date,
                       m.attendees, m.canonical_group
                FROM meetings m WHERE m.organization_id = %s
                ORDER BY m.file_date DESC NULLS LAST
            """, (org_id,))
            meetings = [dict(r) for r in cur.fetchall()]
            # Return None only if there's truly no data for this org
            if not org_row and not meetings:
                return None
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
            # People linked to this org via the many-to-many join, the legacy single-org
            # column, OR by attending one of its meetings — unioned and de-duplicated.
            cur.execute("""
                SELECT ct.id, ct.name, ct.title, ct.company, ct.email, ct.phone, ct.card_image
                FROM contacts ct
                WHERE ct.id IN (
                    SELECT contact_id FROM contact_organizations WHERE organization_id = %s
                    UNION
                    SELECT id FROM contacts WHERE organization_id = %s
                    UNION
                    SELECT mc.contact_id FROM meeting_contacts mc
                    JOIN meetings m ON m.id = mc.meeting_id
                    WHERE m.organization_id = %s
                )
                ORDER BY ct.name
            """, (org_id, org_id, org_id))
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
            # Match tasks either by explicit organization_id or by their group_name
            # slugified to the same form used for organization ids (see seeding above).
            _group_slug = "trim('-' FROM regexp_replace(lower(t.group_name), '[^a-z0-9]+', '-', 'g'))"
            cur.execute(_TASKS_SELECT + f"""
                WHERE NOT t.done AND (t.organization_id = %s OR {_group_slug} = %s)
                ORDER BY
                  CASE WHEN t.deadline IS NULL THEN 1 ELSE 0 END,
                  t.deadline ASC,
                  t.created_at DESC
            """, (org_id, org_id))
            today_iso = date_cls.today().isoformat()
            open_tasks = [_task_row_to_task(dict(r), today_iso).as_dict()
                          for r in cur.fetchall()]
            cur.execute(_TASKS_SELECT + f"""
                WHERE t.done AND (t.organization_id = %s OR {_group_slug} = %s)
                ORDER BY t.created_at DESC
            """, (org_id, org_id))
            completed_tasks = [_task_row_to_task(dict(r), today_iso).as_dict()
                               for r in cur.fetchall()]
            entity_notes = _get_entity_notes(cur, "organization", org_id)
    # Derive org name from meetings if no org row exists
    fallback_name = meetings[0]["canonical_group"] if meetings else org_id
    return {
        "id": org_id,
        "name": org_row["name"] if org_row else fallback_name,
        "type": org_row["type"] if org_row else None,
        "notes": org_row["notes"] if org_row else None,
        "meetings": meetings,
        "asks": asks,
        "commitments": commitments_rows,
        "triggers": triggers,
        "contacts": contacts_rows,
        "bills": bills,
        "open_tasks": open_tasks,
        "completed_tasks": completed_tasks,
        "entity_notes": entity_notes,
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


@app.route("/api/meetings/<mid>", methods=["PUT"])
def api_meeting_update(mid: str):
    data = request.get_json(force=True, silent=True) or {}
    note_group = (data.get("group") or "").strip()
    note_topic = (data.get("topic") or "").strip()
    note_attendees = (data.get("attendees") or "").strip()
    note_deadline = (data.get("deadline") or "").strip()
    note_outcome = (data.get("outcome") or "").strip()
    if not note_group:
        return jsonify({"ok": False, "error": "Group required"}), 400
    canon = note_group  # user explicitly set this name, treat it as canonical directly
    org_id_new = _org_slug(note_group)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM meetings WHERE id = %s", (mid,))
            if not cur.fetchone():
                return jsonify({"ok": False, "error": "Not found"}), 404
            cur.execute("""
                INSERT INTO organizations (id, name, created_at, updated_at)
                VALUES (%s, %s, NOW(), NOW())
                ON CONFLICT (id) DO UPDATE SET updated_at = NOW()
            """, (org_id_new, note_group))
            cur.execute("""
                UPDATE meetings
                SET raw_group = %s, canonical_group = %s, topic = %s,
                    attendees = %s, deadline = %s, outcome = %s,
                    organization_id = %s
                WHERE id = %s
            """, (note_group, canon, note_topic, note_attendees,
                  note_deadline, note_outcome, org_id_new, mid))
            _upsert_attendee_contacts(cur, mid, note_attendees, org_id_new)
    return jsonify({"ok": True, "org_id": org_id_new})


@app.route("/api/groups/canonical")
def api_groups_canonical():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT canonical_group
                FROM meetings
                WHERE canonical_group IS NOT NULL AND canonical_group != ''
                ORDER BY canonical_group
            """)
            groups = [r["canonical_group"] for r in cur.fetchall()]
    return jsonify({"groups": groups})


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


@app.route("/api/people/<contact_id>", methods=["PUT"])
def api_person_update(contact_id):
    """Update an existing contact BY ID without recomputing the id, so enriching an
    attendee-derived contact (e.g. adding an email) keeps its meeting_contacts links."""
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM contacts WHERE id=%s", (contact_id,))
            if not cur.fetchone():
                return jsonify({"ok": False, "error": "Not found"}), 404
            cur.execute("""
                UPDATE contacts SET name=%s, company=%s, title=%s, email=%s, phone=%s,
                    card_image=COALESCE(%s, card_image), updated_at=NOW()
                WHERE id=%s
            """, (name,
                  (data.get("company") or "").strip(),
                  (data.get("title") or "").strip(),
                  (data.get("email") or "").strip().lower(),
                  (data.get("phone") or "").strip(),
                  data.get("card_image") or None,
                  contact_id))
    return jsonify({"ok": True, "id": contact_id})


@app.route("/api/people/<contact_id>", methods=["DELETE"])
@app.route("/api/contacts/<contact_id>", methods=["DELETE"])
def api_person_delete(contact_id):
    """Delete a contact. FK references (meeting_contacts, contact_organizations) cascade
    or null out automatically; entity notes are cleaned up explicitly."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM contacts WHERE id=%s", (contact_id,))
            if not cur.fetchone():
                return jsonify({"ok": False, "error": "Not found"}), 404
            cur.execute("DELETE FROM entity_notes WHERE entity_type='contact' AND entity_id=%s",
                        (contact_id,))
            cur.execute("DELETE FROM contacts WHERE id=%s", (contact_id,))
    return jsonify({"ok": True})


# --------------------------------------------------
# BUSINESS CARD SCANNING (Claude Vision → structured contact fields)
# --------------------------------------------------

def _card_media_type(data_url: str) -> str:
    if data_url.startswith("data:image/png"):
        return "image/png"
    if data_url.startswith("data:image/gif"):
        return "image/gif"
    if data_url.startswith("data:image/webp"):
        return "image/webp"
    return "image/jpeg"


@app.route("/api/contacts/scan", methods=["POST"])
def api_contacts_scan():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"ok": False, "error": "Card scanning not configured"}), 503

    data = request.get_json(force=True, silent=True) or {}
    raw_image = data.get("image", "")
    if not raw_image:
        return jsonify({"ok": False, "error": "image required"}), 400

    media_type = _card_media_type(raw_image)
    image_data = raw_image.split(",", 1)[1] if "," in raw_image else raw_image

    prompt = (
        "Extract the contact information from this business card. "
        "Return ONLY a JSON object with these exact keys (empty string if not found): "
        "name, company, title, email, phone. "
        'Example: {"name": "Jane Smith", "company": "Acme Corp", '
        '"title": "Director", "email": "jane@acme.com", "phone": "555-1234"}'
    )

    try:
        import anthropic as _anthropic
        import json as _json
        client = _anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        fields = _json.loads(raw.strip())
    except Exception as e:
        return jsonify({"ok": False, "error": f"Scan failed: {e}"}), 500

    return jsonify({
        "ok":      True,
        "name":    str(fields.get("name", "")),
        "company": str(fields.get("company", "")),
        "title":   str(fields.get("title", "")),
        "email":   str(fields.get("email", "")),
        "phone":   str(fields.get("phone", "")),
    })



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


@app.route("/api/bills/<int:bill_id>", methods=["PUT"])
def api_bill_update(bill_id: int):
    data = request.get_json(force=True, silent=True) or {}
    bill_type = (data.get("bill_type") or "").strip()
    bill_number = (data.get("bill_number") or "").strip()
    if not bill_number:
        return jsonify({"ok": False, "error": "bill_number required"}), 400
    congress = data.get("congress")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM bill_references WHERE id = %s", (bill_id,))
            if not cur.fetchone():
                return jsonify({"ok": False, "error": "Not found"}), 404
            if congress is None:
                cur.execute(
                    "UPDATE bill_references SET bill_type = %s, bill_number = %s WHERE id = %s",
                    (bill_type, bill_number, bill_id),
                )
            else:
                cur.execute(
                    "UPDATE bill_references SET bill_type = %s, bill_number = %s, congress = %s WHERE id = %s",
                    (bill_type, bill_number, int(congress), bill_id),
                )
    return jsonify({"ok": True})


# --------------------------------------------------
# BILL TRACKER (Congress.gov)
# --------------------------------------------------

_CHAMBER_SLUG = {
    "HR": "house-bill", "S": "senate-bill",
    "HRES": "house-resolution", "SRES": "senate-resolution",
    "HJRES": "house-joint-resolution", "SJRES": "senate-joint-resolution",
    "HCONRES": "house-concurrent-resolution", "SCONRES": "senate-concurrent-resolution",
}


def _ordinal(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _bill_human_url(congress: int, btype: str, number: str) -> str:
    slug = _CHAMBER_SLUG.get(btype)
    if not slug:
        return f"https://www.congress.gov/search?q={btype}{number}"
    return f"https://www.congress.gov/bill/{_ordinal(int(congress))}-congress/{slug}/{number}"


def _congress_api_get(path: str, params: Optional[dict] = None) -> dict:
    import urllib.request
    import urllib.parse
    import urllib.error
    qp = dict(params or {})
    qp.setdefault("format", "json")
    qp["api_key"] = CONGRESS_API_KEY
    url = f"https://api.congress.gov/v3/{path}?" + urllib.parse.urlencode(qp)
    # Congress.gov is behind Cloudflare, which blocks the default Python-urllib
    # User-Agent (HTTP 403, "error code: 1010"). Send a normal UA.
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; PersonalAppsBillTracker/1.0)",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"Congress.gov returned HTTP {e.code} for /{path}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Congress.gov: {e.reason}") from e


def _fetch_member_legislation(kind: str) -> List[dict]:
    """kind = 'sponsored' | 'cosponsored'. Pages the Congress.gov member endpoint."""
    key = "sponsoredLegislation" if kind == "sponsored" else "cosponsoredLegislation"
    path = f"member/{CONGRESS_MEMBER_BIOGUIDE}/{kind}-legislation"
    items: List[dict] = []
    offset, limit = 0, 250
    for _ in range(8):  # cap pages to bound serverless runtime (~2000 records)
        data = _congress_api_get(path, {"limit": limit, "offset": offset})
        batch = data.get(key) or []
        if not batch:
            break
        items.extend(batch)
        total = (data.get("pagination") or {}).get("count")
        offset += limit
        if total is not None and offset >= total:
            break
    return items


def _sync_congress_bills() -> dict:
    """Pull sponsored + cosponsored legislation, upsert, and recompute match flags."""
    def _d(s):  # empty string -> NULL date
        return s or None

    fetched = {
        "sponsored": _fetch_member_legislation("sponsored"),
        "cosponsored": _fetch_member_legislation("cosponsored"),
    }
    counts = {"sponsored": 0, "cosponsored": 0}
    with get_db() as conn:
        with conn.cursor() as cur:
            for kind, items in fetched.items():
                for it in items:
                    btype = _normalize_bill_type(it.get("type"))
                    bnum = _normalize_bill_number(it.get("number"))
                    cong = it.get("congress")
                    if not (btype and bnum and cong):
                        continue
                    latest = it.get("latestAction") or {}
                    cur.execute(
                        """
                        INSERT INTO tracked_bills
                            (id, congress, bill_type, bill_number, relationship, title,
                             introduced_date, latest_action, latest_action_date, url, raw, last_synced)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                        ON CONFLICT (id) DO UPDATE SET
                            relationship = EXCLUDED.relationship,
                            title = EXCLUDED.title,
                            introduced_date = EXCLUDED.introduced_date,
                            latest_action = EXCLUDED.latest_action,
                            latest_action_date = EXCLUDED.latest_action_date,
                            url = EXCLUDED.url,
                            raw = EXCLUDED.raw,
                            last_synced = NOW()
                        """,
                        (
                            f"{cong}-{btype.lower()}-{bnum}", cong, btype, bnum, kind,
                            it.get("title"), _d(it.get("introducedDate")),
                            latest.get("text"), _d(latest.get("actionDate")),
                            _bill_human_url(cong, btype, bnum), json.dumps(it),
                        ),
                    )
                    counts[kind] += 1

            # Recompute matches: exact on (congress, normalized type, normalized number).
            cur.execute(r"""
                SELECT tb.id AS tbid, br.id AS brid
                FROM tracked_bills tb
                JOIN bill_references br
                  ON br.congress = tb.congress
                 AND upper(regexp_replace(br.bill_type, '[^A-Za-z]', '', 'g')) = tb.bill_type
                 AND regexp_replace(br.bill_number, '\D', '', 'g') = tb.bill_number
            """)
            new_matches = 0
            for p in cur.fetchall():
                cur.execute(
                    "INSERT INTO bill_match_flags (id, tracked_bill_id, bill_ref_id) "
                    "VALUES (%s, %s, %s) ON CONFLICT (tracked_bill_id, bill_ref_id) DO NOTHING",
                    (uuid.uuid4().hex, p["tbid"], p["brid"]),
                )
                if cur.rowcount == 1:
                    new_matches += 1

            result = {
                "sponsored_count": counts["sponsored"],
                "cosponsored_count": counts["cosponsored"],
                "new_matches": new_matches,
            }
            cur.execute(
                "INSERT INTO bill_sync_meta (id, last_synced, last_result) "
                "VALUES (1, NOW(), %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET last_synced = NOW(), last_result = EXCLUDED.last_result",
                (json.dumps(result),),
            )
    return result


@app.route("/api/tracked-bills")
def api_tracked_bills():
    a = request.args
    rel = a.get("relationship", "all")
    q = (a.get("q") or "").strip()
    qlike = f"%{q}%"
    congress_arg = a.get("congress", "current")
    if congress_arg in ("", "all"):
        congress = None
    elif congress_arg == "current":
        congress = _current_congress()
    else:
        try:
            congress = int(congress_arg)
        except ValueError:
            congress = _current_congress()

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, congress, bill_type, bill_number, relationship, title,
                       to_char(introduced_date, 'YYYY-MM-DD') AS introduced_date,
                       latest_action,
                       to_char(latest_action_date, 'YYYY-MM-DD') AS latest_action_date,
                       url, working_on
                FROM tracked_bills
                WHERE (%(cong)s IS NULL OR congress = %(cong)s)
                  AND (%(rel)s = 'all' OR (%(rel)s = 'working' AND working_on)
                       OR relationship = %(rel)s)
                  AND (%(q)s = '' OR title ILIKE %(qlike)s
                       OR (bill_type || ' ' || bill_number) ILIKE %(qlike)s)
                ORDER BY introduced_date DESC NULLS LAST, bill_number
                """,
                {"cong": congress, "rel": rel, "q": q, "qlike": qlike},
            )
            bills = cur.fetchall()
            # Counts for the selected Congress, independent of relationship/search filters.
            cur.execute(
                """
                SELECT
                    count(*) AS all,
                    count(*) FILTER (WHERE relationship = 'sponsored')   AS sponsored,
                    count(*) FILTER (WHERE relationship = 'cosponsored') AS cosponsored,
                    count(*) FILTER (WHERE working_on)                   AS working
                FROM tracked_bills
                WHERE (%(cong)s IS NULL OR congress = %(cong)s)
                """,
                {"cong": congress},
            )
            counts = dict(cur.fetchone())
            cur.execute("SELECT DISTINCT congress FROM tracked_bills WHERE congress IS NOT NULL ORDER BY congress DESC")
            congresses = [r["congress"] for r in cur.fetchall()]
            cur.execute("SELECT last_synced FROM bill_sync_meta WHERE id = 1")
            meta = cur.fetchone()
            last_synced = meta["last_synced"] if meta else None
            cur.execute(
                "SELECT (%s::timestamp IS NULL OR %s::timestamp::date < CURRENT_DATE) AS needs",
                (last_synced, last_synced),
            )
            needs_sync = cur.fetchone()["needs"]

    return jsonify({
        "bills": [dict(b) for b in bills],
        "counts": counts,
        "congresses": congresses,
        "current_congress": _current_congress(),
        "last_synced": last_synced.isoformat() if last_synced else None,
        "needs_sync": bool(needs_sync),
        "configured": bool(CONGRESS_API_KEY),
    })


@app.route("/api/tracked-bills/sync", methods=["POST"])
def api_tracked_bills_sync():
    if not CONGRESS_API_KEY:
        return jsonify({"ok": False, "error": "Bill tracker not configured"}), 503
    try:
        result = _sync_congress_bills()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tracked-bills/<bill_id>/working", methods=["POST"])
def api_tracked_bill_working(bill_id: str):
    data = request.get_json(force=True, silent=True) or {}
    working = bool(data.get("working", True))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE tracked_bills SET working_on = %s WHERE id = %s", (working, bill_id))
            if cur.rowcount == 0:
                return jsonify({"ok": False, "error": "Not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/bill-matches")
def api_bill_matches():
    status = (request.args.get("status") or "").strip()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT f.id, f.status,
                       tb.congress, tb.bill_type, tb.bill_number, tb.title,
                       tb.relationship, tb.url, tb.latest_action,
                       br.id AS bill_ref_id,
                       m.id AS meeting_id,
                       COALESCE(NULLIF(m.topic, ''), m.filename) AS meeting_topic,
                       to_char(m.file_date, 'YYYY-MM-DD') AS meeting_date,
                       o.name AS meeting_org,
                       COALESCE(
                           json_agg(DISTINCT jsonb_build_object(
                               'contact_id', c.id, 'name', c.name, 'org', ao.name
                           )) FILTER (WHERE a.id IS NOT NULL),
                           '[]'
                       ) AS askers
                FROM bill_match_flags f
                JOIN tracked_bills tb ON tb.id = f.tracked_bill_id
                JOIN bill_references br ON br.id = f.bill_ref_id
                JOIN meetings m ON m.id = br.meeting_id
                LEFT JOIN organizations o ON o.id = m.organization_id
                LEFT JOIN asks a ON a.bill_ref_id = br.id
                LEFT JOIN contacts c ON c.id = a.contact_id
                LEFT JOIN organizations ao ON ao.id = a.organization_id
                WHERE (%(st)s = '' AND f.status <> 'dismissed')
                   OR (%(st)s <> '' AND f.status = %(st)s)
                GROUP BY f.id, tb.congress, tb.bill_type, tb.bill_number, tb.title,
                         tb.relationship, tb.url, tb.latest_action, br.id,
                         m.id, m.topic, m.filename, m.file_date, o.name
                ORDER BY f.noticed_at DESC
                """,
                {"st": status},
            )
            rows = cur.fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/bill-matches/<flag_id>/status", methods=["POST"])
def api_bill_match_status(flag_id: str):
    data = request.get_json(force=True, silent=True) or {}
    status = (data.get("status") or "").strip()
    if status not in ("new", "notified", "dismissed"):
        return jsonify({"ok": False, "error": "invalid status"}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            if status == "new":
                cur.execute(
                    "UPDATE bill_match_flags SET status = %s, resolved_at = NULL WHERE id = %s",
                    (status, flag_id),
                )
            else:
                cur.execute(
                    "UPDATE bill_match_flags SET status = %s, resolved_at = NOW() WHERE id = %s",
                    (status, flag_id),
                )
            if cur.rowcount == 0:
                return jsonify({"ok": False, "error": "Not found"}), 404
    return jsonify({"ok": True})


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


@app.route("/api/organizations/<org_id>", methods=["PUT"])
def api_organization_update(org_id):
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM organizations WHERE id=%s", (org_id,))
            if not cur.fetchone():
                return jsonify({"ok": False, "error": "Not found"}), 404
            cur.execute("""
                UPDATE organizations SET name=%s, type=%s, notes=%s, updated_at=NOW()
                WHERE id=%s
            """, (name, (data.get("type") or "").strip() or None,
                  (data.get("notes") or "").strip() or None, org_id))
    return jsonify({"ok": True, "id": org_id})


@app.route("/api/organizations/<org_id>", methods=["DELETE"])
def api_organization_delete(org_id):
    """Delete an organization. FK references null out automatically (ON DELETE SET NULL /
    CASCADE on the join table); entity notes are cleaned up explicitly."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM organizations WHERE id=%s", (org_id,))
            if not cur.fetchone():
                return jsonify({"ok": False, "error": "Not found"}), 404
            cur.execute("DELETE FROM entity_notes WHERE entity_type='organization' AND entity_id=%s",
                        (org_id,))
            cur.execute("DELETE FROM organizations WHERE id=%s", (org_id,))
    return jsonify({"ok": True})


@app.route("/api/search")
def api_search():
    """Unified search across people, meetings, notes, tasks, organizations and bills.
    Returns results grouped by category for the global search overlay."""
    q = (request.args.get("q") or "").strip()
    try:
        limit = min(int(request.args.get("limit", 8) or 8), 20)
    except ValueError:
        limit = 8
    if not q:
        return jsonify({"groups": []})
    like = f"%{q}%"
    groups = []
    with get_db() as conn:
        with conn.cursor() as cur:
            # People
            cur.execute("""
                SELECT id, name, company, title, email FROM contacts
                WHERE name ILIKE %s OR company ILIKE %s OR email ILIKE %s
                ORDER BY name LIMIT %s
            """, (like, like, like, limit))
            people = [{
                "type": "person", "id": r["id"], "title": r["name"] or "(no name)",
                "subtitle": " · ".join([x for x in (r.get("title"), r.get("company"), r.get("email")) if x]),
            } for r in cur.fetchall()]
            if people:
                groups.append({"type": "person", "label": "People", "items": people})

            # Meetings (reuse existing comprehensive match) + entity notes
            meetings = filter_meetings(db_get_all_meetings(), q=q)[:limit]
            mn_items = [{
                "type": "meeting", "id": m.id,
                "title": m.topic or m.canonical_group or m.filename or "(meeting)",
                "subtitle": " · ".join([x for x in (m.canonical_group, m.date) if x]),
            } for m in meetings]
            cur.execute("""
                SELECT entity_type, entity_id, body FROM entity_notes
                WHERE body ILIKE %s ORDER BY created_at DESC LIMIT %s
            """, (like, limit))
            for r in cur.fetchall():
                body = (r["body"] or "").strip().replace("\n", " ")
                mn_items.append({
                    "type": "note", "id": r["entity_id"], "entity_type": r["entity_type"],
                    "title": (body[:80] + ("…" if len(body) > 80 else "")) or "(note)",
                    "subtitle": "Note",
                })
            if mn_items:
                groups.append({"type": "meeting", "label": "Meetings & Notes", "items": mn_items})

            # Tasks (open)
            cur.execute("""
                SELECT id, text, group_name FROM tasks
                WHERE NOT done AND (text ILIKE %s OR group_name ILIKE %s)
                ORDER BY source_date DESC NULLS LAST LIMIT %s
            """, (like, like, limit))
            tasks = [{
                "type": "task", "id": r["id"], "title": r["text"],
                "subtitle": r.get("group_name") or "",
            } for r in cur.fetchall()]
            if tasks:
                groups.append({"type": "task", "label": "Tasks", "items": tasks})

            # Organizations
            cur.execute("""
                SELECT id, name, type FROM organizations
                WHERE name ILIKE %s OR type ILIKE %s
                ORDER BY name LIMIT %s
            """, (like, like, limit))
            orgs = [{
                "type": "org", "id": r["id"], "title": r["name"],
                "subtitle": r.get("type") or "",
            } for r in cur.fetchall()]
            if orgs:
                groups.append({"type": "org", "label": "Organizations", "items": orgs})

            # Bills (grouped by type + number)
            cur.execute("""
                SELECT br.bill_type, br.bill_number,
                       (array_agg(br.meeting_id ORDER BY br.created_at DESC))[1] AS meeting_id
                FROM bill_references br
                WHERE br.bill_type ILIKE %s OR br.bill_number ILIKE %s
                   OR (COALESCE(br.bill_type,'') || ' ' || COALESCE(br.bill_number,'')) ILIKE %s
                GROUP BY br.bill_type, br.bill_number
                ORDER BY max(br.created_at) DESC LIMIT %s
            """, (like, like, like, limit))
            bills = [{
                "type": "bill",
                "id": f"{r['bill_type'] or ''} {r['bill_number'] or ''}".strip(),
                "title": f"{r['bill_type'] or ''} {r['bill_number'] or ''}".strip(),
                "subtitle": "Bill", "meeting_id": r["meeting_id"],
            } for r in cur.fetchall()]
            if bills:
                groups.append({"type": "bill", "label": "Bills", "items": bills})

    return jsonify({"groups": groups})


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


@app.route("/api/scan-items/<int:item_id>", methods=["DELETE"])
def api_scan_item_delete(item_id):
    """Delete a callout (and the task it created, if any)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT task_id FROM meeting_scan_items WHERE id = %s", (item_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({"ok": False, "error": "Not found"}), 404
            if row["task_id"]:
                cur.execute("DELETE FROM tasks WHERE id = %s", (row["task_id"],))
            cur.execute("DELETE FROM meeting_scan_items WHERE id = %s", (item_id,))
    return jsonify({"ok": True})


_NOTE_TYPES = ("organization", "contact")

def _get_entity_notes(cur, entity_type, entity_id):
    cur.execute("""
        SELECT id, body, to_char(created_at,'YYYY-MM-DD') AS created_at
        FROM entity_notes WHERE entity_type=%s AND entity_id=%s
        ORDER BY created_at DESC, id DESC
    """, (entity_type, entity_id))
    return [dict(r) for r in cur.fetchall()]


@app.route("/api/entity-notes", methods=["POST"])
def api_entity_note_add():
    data = request.get_json(force=True, silent=True) or {}
    etype = (data.get("entity_type") or "").strip()
    eid = (data.get("entity_id") or "").strip()
    body = (data.get("body") or "").strip()
    if etype not in _NOTE_TYPES or not eid or not body:
        return jsonify({"ok": False, "error": "entity_type, entity_id, body required"}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO entity_notes (entity_type, entity_id, body)
                VALUES (%s, %s, %s) RETURNING id
            """, (etype, eid, body))
            nid = cur.fetchone()["id"]
    return jsonify({"ok": True, "id": nid})


@app.route("/api/entity-notes/<int:note_id>", methods=["DELETE"])
def api_entity_note_delete(note_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM entity_notes WHERE id=%s", (note_id,))
    return jsonify({"ok": True})


def _contact_org_list(cur, contact_id):
    """All organizations a person belongs to (via the join table or the legacy column)."""
    cur.execute("""
        SELECT o.id, o.name FROM organizations o
        WHERE o.id IN (
            SELECT organization_id FROM contact_organizations WHERE contact_id = %s
            UNION
            SELECT organization_id FROM contacts WHERE id = %s AND organization_id IS NOT NULL
        )
        ORDER BY o.name
    """, (contact_id, contact_id))
    return [{"id": r["id"], "name": r["name"]} for r in cur.fetchall()]


@app.route("/api/people")
def api_people():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ct.id, ct.name, ct.company, ct.title, ct.email, ct.phone,
                       ct.card_image,
                       COUNT(DISTINCT mc.meeting_id) AS meeting_count,
                       to_char(MAX(m.file_date), 'YYYY-MM-DD') AS last_seen
                FROM contacts ct
                LEFT JOIN meeting_contacts mc ON mc.contact_id = ct.id
                LEFT JOIN meetings m ON m.id = mc.meeting_id
                GROUP BY ct.id, ct.name, ct.company, ct.title, ct.email,
                         ct.phone, ct.card_image
                ORDER BY MAX(m.file_date) DESC NULLS LAST, ct.name
            """)
            rows = cur.fetchall()
            people = []
            for r in rows:
                orgs = _contact_org_list(cur, r["id"])
                people.append({
                    "id": r["id"], "name": r["name"], "company": r["company"],
                    "title": r["title"], "email": r["email"], "phone": r["phone"],
                    "card_image": r["card_image"],
                    "orgs": orgs,
                    "org_name": ", ".join(o["name"] for o in orgs) or None,
                    "meeting_count": r["meeting_count"], "last_seen": r["last_seen"],
                })
    return jsonify(people)


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
            cur.execute("""
                SELECT c.id, c.text, c.status,
                       to_char(c.created_at,'YYYY-MM-DD') AS created_at_str
                FROM commitments c WHERE c.contact_id = %s
                ORDER BY c.created_at DESC
            """, (contact_id,))
            commitments = [dict(r) for r in cur.fetchall()]
            today_iso = date_cls.today().isoformat()
            cur.execute(_TASKS_SELECT + """
                WHERE t.contact_id = %s
                ORDER BY t.done ASC,
                  CASE WHEN t.deadline IS NULL THEN 1 ELSE 0 END, t.deadline ASC,
                  t.created_at DESC
            """, (contact_id,))
            tasks = [_task_row_to_task(dict(r), today_iso).as_dict() for r in cur.fetchall()]
            orgs = _contact_org_list(cur, contact_id)
            entity_notes = _get_entity_notes(cur, "contact", contact_id)
    return jsonify({
        "id": row["id"], "name": row["name"], "company": row["company"],
        "title": row["title"], "email": row["email"], "phone": row["phone"],
        "card_image": row["card_image"], "organization_id": row["organization_id"],
        "orgs": orgs, "meetings": meetings, "asks": asks,
        "commitments": commitments, "tasks": tasks, "entity_notes": entity_notes,
    })


@app.route("/api/people/<contact_id>/organizations", methods=["POST"])
def api_person_add_org(contact_id):
    """Associate a person with an organization (by existing id or new name)."""
    data = request.get_json(force=True, silent=True) or {}
    org_id = (data.get("org_id") or "").strip() or None
    org_name = (data.get("name") or "").strip() or None
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM contacts WHERE id=%s", (contact_id,))
            if not cur.fetchone():
                return jsonify({"ok": False, "error": "Not found"}), 404
            if not org_id:
                org_id = _org_for_name(cur, org_name)
            if not org_id:
                return jsonify({"ok": False, "error": "org_id or name required"}), 400
            _link_contact_org(cur, contact_id, org_id)
    return jsonify({"ok": True, "org_id": org_id})


@app.route("/api/people/<contact_id>/organizations/<org_id>", methods=["DELETE"])
def api_person_remove_org(contact_id, org_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM contact_organizations WHERE contact_id=%s AND organization_id=%s",
                (contact_id, org_id))
            # Also clear the legacy single-org pointer if it matches.
            cur.execute(
                "UPDATE contacts SET organization_id=NULL WHERE id=%s AND organization_id=%s",
                (contact_id, org_id))
    return jsonify({"ok": True})


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


@app.route("/api/asks/<ask_id>", methods=["PUT"])
def api_ask_update(ask_id):
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400
    priority = (data.get("priority") or "normal").strip()
    if priority not in ("high", "normal", "low"):
        priority = "normal"
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM asks WHERE id=%s", (ask_id,))
            if not cur.fetchone():
                return jsonify({"ok": False, "error": "Not found"}), 404
            cur.execute("UPDATE asks SET text=%s, priority=%s WHERE id=%s",
                        (text, priority, ask_id))
    return jsonify({"ok": True, "id": ask_id})


@app.route("/api/asks/<ask_id>", methods=["DELETE"])
def api_ask_delete(ask_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM asks WHERE id=%s", (ask_id,))
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


@app.route("/api/commitments/<commitment_id>", methods=["PUT"])
def api_commitment_update(commitment_id):
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400
    due_date = (data.get("due_date") or "").strip() or None
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM commitments WHERE id=%s", (commitment_id,))
            if not cur.fetchone():
                return jsonify({"ok": False, "error": "Not found"}), 404
            cur.execute("UPDATE commitments SET text=%s, due_date=%s WHERE id=%s",
                        (text, due_date, commitment_id))
    return jsonify({"ok": True, "id": commitment_id})


@app.route("/api/commitments/<commitment_id>", methods=["DELETE"])
def api_commitment_delete(commitment_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM commitments WHERE id=%s", (commitment_id,))
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
            if smart_view == "commitments":
                return not d.get("done") and bool(d.get("commitment_id"))
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
    new_person = data.get("contact_id")  # None = don't change; "" = clear; id = assign person
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
                org_id = None
                if new_group is not None:
                    org_id = _org_for_name(cur, new_group)
                    sets.append("group_name = %s"); vals.append(new_group or None)
                    sets.append("organization_id = %s"); vals.append(org_id)
                person_id = (new_person or "").strip() or None if new_person is not None else None
                if new_person is not None:
                    sets.append("contact_id = %s"); vals.append(person_id)
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
                    f"UPDATE tasks SET {', '.join(sets)} WHERE id = %s RETURNING id, organization_id",
                    vals,
                )
                row = cur.fetchone()
                if row is None:
                    return jsonify({"ok": False, "error": "task not found"}), 404
                # Assigning a person ties them to the task's organization too.
                if person_id:
                    _link_contact_org(cur, person_id, row["organization_id"])
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
    add_person = (data.get("contact_id") or "").strip() or None
    add_estimate = data.get("estimate_minutes")
    add_recurrence = data.get("recurrence_rule")  # dict or None
    add_parent_id = (data.get("parent_id") or "").strip() or None
    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400

    # Organization + deadline are now structured fields, so keep the task text clean
    # (no more "@group:" / "due" tags appended into the text).
    full_text = text
    deadline, deadline_raw = extract_deadline(text, context_year=datetime.now().year)
    # If deadline was passed directly (already parsed client-side), prefer it
    if deadline_in and not deadline:
        deadline = deadline_in
        deadline_raw = deadline_in
    tid = str(uuid.uuid4())

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                org_id = _org_for_name(cur, group)
                cur.execute("""
                    INSERT INTO tasks
                        (id, text, type, done, backburner, source_filename, section,
                         group_name, source_date, deadline, deadline_raw, priority, contact,
                         estimate_minutes, recurrence_rule, parent_id,
                         organization_id, contact_id)
                    VALUES (%s, %s, 'free', FALSE, FALSE, 'tasks.md', 'free',
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    tid, full_text, group, date_cls.today(), deadline, deadline_raw,
                    add_priority, add_contact,
                    int(add_estimate) if add_estimate else None,
                    json.dumps(add_recurrence) if add_recurrence else None,
                    add_parent_id, org_id, add_person,
                ))
                # Assigning a person ties them to the task's organization too.
                _link_contact_org(cur, add_person, org_id)
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
            mid = summary.get("id")
            # Ensure org row exists and meeting is linked so brief lookup works
            if mid:
                post = frontmatter.loads(content)
                raw_group = (post.metadata or {}).get("group") or fname.split(" - ", 1)[-1].replace(".md", "")
                raw_group = str(raw_group).strip()
                if raw_group and raw_group != "intake":
                    org_id_imp = _org_slug(raw_group)
                    with get_db() as conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                INSERT INTO organizations (id, name, created_at, updated_at)
                                VALUES (%s, %s, NOW(), NOW())
                                ON CONFLICT (id) DO UPDATE SET updated_at = NOW()
                            """, (org_id_imp, raw_group))
                            cur.execute("""
                                UPDATE meetings SET organization_id = %s
                                WHERE id = %s AND organization_id IS NULL
                            """, (org_id_imp, mid))
            results.append({"filename": fname, "ok": True, "task_count": summary["tasks"]})
        except Exception as e:
            results.append({"filename": fname, "ok": False, "error": str(e)})
    ok_count = sum(1 for r in results if r.get("ok"))
    return jsonify({"ok": True, "processed": ok_count, "total": len(results), "results": results})


# --------------------------------------------------
# INTAKE (text notes + confirmed items → meeting note)
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
    if not any([note_body, action_items_raw, reminders_raw, has_new_items]):
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
    now = datetime.now()
    time_suffix = now.strftime("%H%M%S")
    filename = f"{note_date} - {safe_group} [{time_suffix}].md"

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

                    # Turn attendees into linked People contacts
                    _upsert_attendee_contacts(cur, mid_out, note_attendees, org_id_out)

                    # Bill references
                    if bill_items:
                        for bill in bill_items:
                            bt = (bill.get("billType") or "").strip()
                            bn = (bill.get("billNumber") or "").strip()
                            if bt and bn:
                                cur.execute(
                                    "INSERT INTO bill_references (meeting_id, bill_type, bill_number, congress)"
                                    " VALUES (%s, %s, %s, %s)",
                                    (mid_out, bt, bn, _current_congress())
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
# CALENDAR / ICS UPLOAD
# --------------------------------------------------

_TEAMS_RE = re.compile(r'https://teams\.microsoft\.com/l/meetup-join/[^\s<>"\']+')
_ZOOM_RE = re.compile(r'https://[\w.-]*zoom\.us/j/[^\s<>"\']+')


def parse_ics_content(raw_bytes: bytes) -> Optional[dict]:
    """Parse raw ICS bytes into a normalized event dict. Returns None on failure."""
    try:
        from icalendar import Calendar
    except ImportError:
        return None
    try:
        cal = Calendar.from_ical(raw_bytes)
    except Exception:
        return None

    cal_method = cal.get('METHOD')
    method = str(cal_method).upper() if cal_method else None

    for component in cal.walk():
        if component.name != 'VEVENT':
            continue
        uid = str(component.get('UID', '')).strip()
        if not uid:
            continue

        def _str(key: str) -> str:
            v = component.get(key)
            return str(v) if v is not None else ''

        def _dt_iso(key: str) -> Optional[str]:
            try:
                from datetime import timezone as _tz
                v = component.decoded(key, None)
                if v is None:
                    return None
                if isinstance(v, datetime):
                    if v.tzinfo:
                        return v.astimezone(_tz.utc).isoformat()
                    return v.isoformat()
                # date only — treat as UTC midnight
                return datetime(v.year, v.month, v.day, tzinfo=_tz.utc).isoformat()
            except Exception:
                return None

        organizer = _str('ORGANIZER')
        if organizer.lower().startswith('mailto:'):
            organizer = organizer[7:]

        attendees = []
        att_raw = component.get('ATTENDEE')
        if att_raw is not None:
            if not isinstance(att_raw, list):
                att_raw = [att_raw]
            for a in att_raw:
                addr = str(a)
                if addr.lower().startswith('mailto:'):
                    addr = addr[7:]
                cn = a.params.get('CN', addr) if hasattr(a, 'params') else addr
                attendees.append({'email': addr, 'name': str(cn)})

        rrule = None
        rr = component.get('RRULE')
        if rr is not None:
            try:
                rrule = rr.to_ical().decode('utf-8')
            except Exception:
                rrule = str(rr)

        recurrence_id = None
        rid = component.get('RECURRENCE-ID')
        if rid is not None:
            recurrence_id = _dt_iso('RECURRENCE-ID') or str(rid)

        return {
            'uid': uid,
            'sequence': int(component.get('SEQUENCE', 0) or 0),
            'method': method,
            'status': _str('STATUS').upper() or None,
            'summary': _str('SUMMARY'),
            'description': _str('DESCRIPTION'),
            'location': _str('LOCATION'),
            'dtstart': _dt_iso('DTSTART'),
            'dtend': _dt_iso('DTEND'),
            'organizer': organizer,
            'attendees': attendees,
            'rrule': rrule,
            'recurrence_id': recurrence_id,
            'teams_url': _str('X-MICROSOFT-SKYPETEAMSMEETINGURL'),
            'zoom_url': _str('X-ZOOM-JOIN-URL'),
        }
    return None


def extract_meeting_link(event: dict) -> Optional[str]:
    if event.get('teams_url'):
        return event['teams_url']
    if event.get('zoom_url'):
        return event['zoom_url']
    for field in ('location', 'description'):
        text = event.get(field, '') or ''
        m = _TEAMS_RE.search(text)
        if m:
            return m.group(0)
        m = _ZOOM_RE.search(text)
        if m:
            return m.group(0)
    return None


def _create_or_update_prepared_meeting(event: dict, ece_id: int) -> str:
    """Upsert a prepared meeting stub from a parsed ICS event. Returns meeting id."""
    uid = event['uid']
    recurrence_id = event.get('recurrence_id') or ''
    summary = event.get('summary') or 'Untitled Meeting'
    dtstart_str = event.get('dtstart') or ''

    try:
        dtstart_dt = datetime.fromisoformat(dtstart_str)
        date_str = dtstart_dt.date().isoformat()
    except Exception:
        date_str = date_cls.today().isoformat()

    uid_hash = hashlib.sha1(f"{uid}:{recurrence_id}".encode()).hexdigest()[:8]
    safe_summary = re.sub(r'[^a-zA-Z0-9_-]', '-', summary[:30])
    filename = f"{date_str} - {safe_summary} [cal-{uid_hash}].md"
    mid = hashlib.sha1(filename.encode()).hexdigest()[:16]

    _ATTENDEE_COLLAPSE_THRESHOLD = 8
    attendees_list = event.get('attendees') or []
    if len(attendees_list) <= _ATTENDEE_COLLAPSE_THRESHOLD:
        attendees_str = ', '.join(
            a.get('name') or a.get('email', '') for a in attendees_list
        )
    else:
        attendees_str = f"Large meeting ({len(attendees_list)} attendees)"
    meeting_link = extract_meeting_link(event)

    try:
        file_date: Optional[date_cls] = date_cls.fromisoformat(date_str)
    except Exception:
        file_date = None

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, status FROM meetings WHERE id = %s", (mid,))
            existing = cur.fetchone()

            if existing is None:
                cur.execute("""
                    INSERT INTO meetings
                        (id, filename, file_date, raw_group, canonical_group,
                         topic, purpose, outcome, deadline, attendees,
                         body, body_html, mtime,
                         status, calendar_event_id, dtstart, meeting_link)
                    VALUES (%s,%s,%s,'calendar','calendar',%s,%s,
                            '','', %s,'','',NULL,'prepared',%s,%s,%s)
                """, (
                    mid, filename, file_date,
                    summary, json.dumps([]), attendees_str,
                    ece_id, event.get('dtstart'), meeting_link,
                ))
                cur.execute(
                    "UPDATE external_calendar_events SET meeting_id = %s WHERE id = %s",
                    (mid, ece_id),
                )
            elif existing['status'] == 'prepared':
                cur.execute("""
                    UPDATE meetings SET
                        topic = %s, attendees = %s, dtstart = %s,
                        meeting_link = %s, calendar_event_id = %s
                    WHERE id = %s
                """, (summary, attendees_str, event.get('dtstart'), meeting_link, ece_id, mid))
                cur.execute(
                    "UPDATE external_calendar_events SET meeting_id = %s WHERE id = %s",
                    (mid, ece_id),
                )
    return mid


def _ingest_ics_bytes(raw_ics: bytes) -> dict:
    """Parse ICS bytes and upsert ECE + prepared meeting. Returns result dict."""
    event = parse_ics_content(raw_ics)
    if not event:
        return {"ok": False, "error": "Could not parse ICS content"}

    uid = event['uid']
    recurrence_id = event.get('recurrence_id') or None
    sequence = event.get('sequence', 0)
    method = event.get('method') or 'REQUEST'
    status = event.get('status') or 'CONFIRMED'
    is_cancelled = method == 'CANCEL' or status == 'CANCELLED'

    ece_id: Optional[int] = None

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, sequence, meeting_id FROM external_calendar_events
                WHERE user_id = 'default' AND ics_uid = %s
                  AND (recurrence_id IS NOT DISTINCT FROM %s)
            """, (uid, recurrence_id))
            existing_ece = cur.fetchone()

            if existing_ece:
                if sequence <= existing_ece['sequence'] and not is_cancelled:
                    return {"ok": True, "action": "skipped", "reason": "stale_sequence",
                            "meeting_id": existing_ece.get('meeting_id')}
                cur.execute("""
                    UPDATE external_calendar_events SET
                        sequence = %s, method = %s, status = %s,
                        summary = %s, description = %s, location = %s,
                        dtstart = %s, dtend = %s, organizer = %s,
                        attendees = %s, rrule = %s, raw_ics = %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (
                    sequence, method, status,
                    event.get('summary'), event.get('description'), event.get('location'),
                    event.get('dtstart'), event.get('dtend'), event.get('organizer'),
                    json.dumps(event.get('attendees', [])), event.get('rrule'),
                    raw_ics.decode('utf-8', errors='replace'),
                    existing_ece['id'],
                ))
                ece_id = existing_ece['id']
                linked_mid = existing_ece.get('meeting_id')
                if is_cancelled and linked_mid:
                    cur.execute("""
                        UPDATE meetings SET status = 'cancelled'
                        WHERE id = %s AND status = 'prepared'
                    """, (linked_mid,))
                action = "cancelled" if is_cancelled else "updated"
            else:
                if is_cancelled:
                    return {"ok": True, "action": "skipped", "reason": "cancel_for_unknown"}
                cur.execute("""
                    INSERT INTO external_calendar_events
                        (user_id, ics_uid, recurrence_id, sequence, method, status,
                         summary, description, location, dtstart, dtend, organizer,
                         attendees, rrule, raw_ics)
                    VALUES ('default',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (
                    uid, recurrence_id, sequence, method, status,
                    event.get('summary'), event.get('description'), event.get('location'),
                    event.get('dtstart'), event.get('dtend'), event.get('organizer'),
                    json.dumps(event.get('attendees', [])), event.get('rrule'),
                    raw_ics.decode('utf-8', errors='replace'),
                ))
                ece_id = cur.fetchone()['id']
                action = "created"

    mid = None
    if not is_cancelled and ece_id is not None:
        mid = _create_or_update_prepared_meeting(event, ece_id)

    return {"ok": True, "action": action, "meeting_id": mid, "ece_id": ece_id,
            "summary": event.get('summary'), "dtstart": event.get('dtstart')}


@app.route("/api/calendar/upload", methods=["POST"])
def api_calendar_upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400
    raw_ics = f.read()
    if not raw_ics:
        return jsonify({"ok": False, "error": "Empty file"}), 400
    try:
        result = _ingest_ics_bytes(raw_ics)
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/meetings/upcoming")
def api_meetings_upcoming():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT m.id, m.topic, m.attendees, m.dtstart, m.meeting_link,
                           m.status, m.calendar_event_id,
                           e.organizer, e.attendees AS cal_attendees, e.description
                    FROM meetings m
                    LEFT JOIN external_calendar_events e ON e.id = m.calendar_event_id
                    WHERE m.dtstart >= NOW() - INTERVAL '1 hour'
                      AND m.status IN ('prepared', 'in_progress')
                    ORDER BY m.dtstart ASC
                    LIMIT 20
                """)
                rows = [dict(r) for r in cur.fetchall()]
        return jsonify({"ok": True, "meetings": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/meetings/<mid>/metadata", methods=["POST"])
def api_meeting_metadata(mid: str):
    """Edit parsed metadata (title/date/attendees/link) of a prepared meeting."""
    data = request.get_json(force=True, silent=True) or {}
    topic = (data.get("topic") or "").strip()
    attendees = (data.get("attendees") or "").strip()
    meeting_link = (data.get("meeting_link") or "").strip() or None
    dtstart = (data.get("dtstart") or "").strip() or None
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Preserve the existing dtstart if none is provided, so a blank
                # date field can't silently drop the meeting from the upcoming card.
                cur.execute("""
                    UPDATE meetings
                    SET topic = %s, attendees = %s, meeting_link = %s,
                        dtstart = COALESCE(%s, dtstart)
                    WHERE id = %s
                    RETURNING id
                """, (topic, attendees, meeting_link, dtstart, mid))
                if not cur.fetchone():
                    return jsonify({"ok": False, "error": "Not found"}), 404
                cur.execute("SELECT organization_id FROM meetings WHERE id=%s", (mid,))
                row = cur.fetchone()
                meeting_org = row["organization_id"] if row else None
                _upsert_attendee_contacts(cur, mid, attendees, meeting_org)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/meetings/<mid>/status", methods=["POST"])
def api_meeting_status(mid: str):
    data = request.get_json(force=True, silent=True) or {}
    new_status = (data.get("status") or "").strip()
    allowed = {"prepared", "in_progress", "complete", "cancelled"}
    if new_status not in allowed:
        return jsonify({"ok": False, "error": f"status must be one of {allowed}"}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE meetings SET status = %s WHERE id = %s RETURNING id",
                    (new_status, mid),
                )
                if not cur.fetchone():
                    return jsonify({"ok": False, "error": "Not found"}), 404
        return jsonify({"ok": True, "status": new_status})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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
