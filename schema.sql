-- Notes Dashboard — PostgreSQL schema
-- Run this once against your Vercel Postgres database. The app no longer runs DDL on
-- cold start (audit A1); after each deploy that changes the schema, apply migrations by
-- POSTing to /api/admin/migrate with the CRON_SECRET bearer token (this file mirrors what
-- init_db() applies).

CREATE TABLE IF NOT EXISTS meetings (
    id               TEXT PRIMARY KEY,
    filename         TEXT NOT NULL,
    file_date        DATE,
    raw_group        TEXT    DEFAULT '',
    canonical_group  TEXT    DEFAULT '',
    topic            TEXT    DEFAULT '',
    purpose          JSONB   DEFAULT '[]',
    outcome          TEXT    DEFAULT '',
    deadline         TEXT    DEFAULT '',
    attendees        TEXT    DEFAULT '',
    body             TEXT    DEFAULT '',
    body_html        TEXT    DEFAULT '',
    mtime            DOUBLE PRECISION,
    created_at       TIMESTAMP DEFAULT NOW()
);

-- All tasks: meeting action items, reminders, and free-form tasks.
-- meeting_id is NULL for free tasks (source_filename = 'tasks.md').
CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    text             TEXT NOT NULL,
    type             TEXT NOT NULL CHECK (type IN ('action', 'reminder', 'free')),
    done             BOOLEAN   DEFAULT FALSE,
    backburner       BOOLEAN   DEFAULT FALSE,
    meeting_id       TEXT      REFERENCES meetings(id) ON DELETE CASCADE,
    source_filename  TEXT      NOT NULL DEFAULT '',
    section          TEXT      NOT NULL DEFAULT '',
    group_name       TEXT,
    source_date      DATE,
    deadline         TEXT,
    deadline_raw     TEXT,
    priority         TEXT      DEFAULT 'normal' CHECK (priority IN ('high', 'normal', 'low')),
    contact          TEXT      DEFAULT NULL,
    created_at       TIMESTAMP DEFAULT NOW()
);

-- Migration: add columns to existing databases
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS priority TEXT DEFAULT 'normal'
    CHECK (priority IN ('high', 'normal', 'low'));
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS contact TEXT DEFAULT NULL;
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS parent_id TEXT REFERENCES tasks(id) ON DELETE CASCADE;
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS snoozed_until DATE DEFAULT NULL;
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS estimate_minutes INT DEFAULT NULL;
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS recurrence_rule JSONB DEFAULT NULL;
-- Stable task identity (audit C3/M15): import_key holds the content hash used only by the
-- .md import upsert so tasks.id can stay immutable; import_locked pins user-edited text
-- against re-import reversion.
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS import_key TEXT;
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS import_locked BOOLEAN DEFAULT FALSE;
UPDATE tasks SET import_key = id
  WHERE import_key IS NULL AND source_filename NOT IN ('', 'tasks.md');
CREATE UNIQUE INDEX IF NOT EXISTS tasks_import_key_uniq
  ON tasks (import_key) WHERE import_key IS NOT NULL;

-- Recurrence instance identity: which completion spawned a recurring instance. The partial
-- unique index makes the spawn idempotent, so re-completing a recurring task cannot fork
-- the series into duplicate future instances.
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS recurrence_parent_id TEXT REFERENCES tasks(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX IF NOT EXISTS tasks_recurrence_parent_uniq
  ON tasks (recurrence_parent_id) WHERE recurrence_parent_id IS NOT NULL;

-- Tombstones for user-deleted meeting-sourced tasks (so re-import can't resurrect them).
CREATE TABLE IF NOT EXISTS import_tombstones (
    import_key       TEXT PRIMARY KEY,
    deleted_at       TIMESTAMP DEFAULT NOW()
);

ALTER TABLE meetings
  ADD COLUMN IF NOT EXISTS canvas_image TEXT;

-- Group alias map: raw group name → canonical display name.
-- raw_name is stored lowercase-trimmed to match lookup behaviour.
CREATE TABLE IF NOT EXISTS groups_map (
    raw_name         TEXT PRIMARY KEY,
    canonical_name   TEXT NOT NULL
);

-- Timestamped log of task completions for the 30-day sparkline.
CREATE TABLE IF NOT EXISTS completions (
    id               SERIAL PRIMARY KEY,
    task_id          TEXT NOT NULL,
    task_text        TEXT,
    section          TEXT,
    source_filename  TEXT,
    done             BOOLEAN   DEFAULT TRUE,
    completed_date   DATE      DEFAULT CURRENT_DATE,
    completed_at     TIMESTAMP DEFAULT NOW()
);

-- Task dependency graph: task_id is blocked until depends_on_id is done
CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_id   TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, depends_on_id)
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS tasks_meeting_id    ON tasks (meeting_id);
CREATE INDEX IF NOT EXISTS tasks_done          ON tasks (done);
CREATE INDEX IF NOT EXISTS tasks_parent_id     ON tasks (parent_id);
CREATE INDEX IF NOT EXISTS tasks_snoozed_until ON tasks (snoozed_until);
CREATE INDEX IF NOT EXISTS meetings_file_date  ON meetings (file_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS completions_date    ON completions (completed_date);
CREATE INDEX IF NOT EXISTS task_deps_task      ON task_dependencies (task_id);
CREATE INDEX IF NOT EXISTS task_deps_depends   ON task_dependencies (depends_on_id);

-- Legislative bill references captured during note intake
CREATE TABLE IF NOT EXISTS bill_references (
    id           SERIAL PRIMARY KEY,
    meeting_id   TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    bill_type    TEXT NOT NULL DEFAULT '',
    bill_number  TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS bill_refs_meeting ON bill_references (meeting_id);

-- Calendar events ingested via forwarded Outlook invites (ICS parsing)
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

ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'complete',
    ADD COLUMN IF NOT EXISTS calendar_event_id INTEGER REFERENCES external_calendar_events(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS dtstart TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS meeting_link TEXT;
