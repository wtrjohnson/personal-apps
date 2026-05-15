-- Notes Dashboard — PostgreSQL schema
-- Run this once against your Vercel Postgres database, or let the app
-- create the tables automatically on first cold start (init_db()).

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

-- Timestamped log of actual time spent on tasks (for learning estimates)
CREATE TABLE IF NOT EXISTS task_time_log (
    id              SERIAL PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    minutes_spent   INT NOT NULL,
    logged_at       TIMESTAMP DEFAULT NOW()
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
CREATE INDEX IF NOT EXISTS task_time_log_task  ON task_time_log (task_id);
CREATE INDEX IF NOT EXISTS task_deps_task      ON task_dependencies (task_id);
CREATE INDEX IF NOT EXISTS task_deps_depends   ON task_dependencies (depends_on_id);
