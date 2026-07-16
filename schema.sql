-- Notes Dashboard — PostgreSQL schema
-- Run this once against your Vercel Postgres database. The app no longer runs DDL on
-- cold start (audit A1); after each deploy that changes the schema, apply migrations by
-- POSTing to /api/admin/migrate with the CRON_SECRET bearer token (this file mirrors what
-- init_db() applies, in the same order).

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
    parent_id        TEXT,
    snoozed_until    DATE      DEFAULT NULL,
    estimate_minutes INT       DEFAULT NULL,
    recurrence_rule  JSONB     DEFAULT NULL,
    import_key       TEXT,
    import_locked    BOOLEAN   DEFAULT FALSE,
    created_at       TIMESTAMP DEFAULT NOW()
);

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

-- Tombstones for meeting-sourced tasks the user deleted, so re-importing the same
-- .md file never resurrects them (audit M15).
CREATE TABLE IF NOT EXISTS import_tombstones (
    import_key       TEXT PRIMARY KEY,
    deleted_at       TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tasks_parent_id     ON tasks (parent_id);
CREATE INDEX IF NOT EXISTS tasks_snoozed_until ON tasks (snoozed_until);
CREATE INDEX IF NOT EXISTS task_deps_task      ON task_dependencies (task_id);
CREATE INDEX IF NOT EXISTS task_deps_depends   ON task_dependencies (depends_on_id);

-- Stable task identity (audit C3/M15): import_key holds the content hash used only by the
-- .md import upsert so tasks.id can stay immutable; import_locked pins user-edited text
-- against re-import reversion. Backfill = current id for meeting-sourced rows (whose id
-- already IS that content hash).
UPDATE tasks SET import_key = id
  WHERE import_key IS NULL AND source_filename NOT IN ('', 'tasks.md');
CREATE UNIQUE INDEX IF NOT EXISTS tasks_import_key_uniq
  ON tasks (import_key) WHERE import_key IS NOT NULL;

-- meetings.canvas_image / status / dtstart / meeting_link, tasks.callout_source, and the
-- tasks.parent_id FK constraint are added defensively in init_db() via a DO $$ block that
-- checks information_schema before altering — the ADD COLUMN IF NOT EXISTS forms below are
-- equivalent for a fresh apply.
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS canvas_image TEXT;
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'complete';
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS dtstart TIMESTAMPTZ;
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS meeting_link TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS callout_source TEXT NULL;
DO $$ BEGIN
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

-- People/contacts (exposed in the UI as "People").
CREATE TABLE IF NOT EXISTS contacts (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL DEFAULT '',
    company          TEXT DEFAULT '',
    title            TEXT DEFAULT '',
    email            TEXT DEFAULT '',
    phone            TEXT DEFAULT '',
    notes            TEXT DEFAULT '',
    card_image       TEXT,
    created_at       TIMESTAMP DEFAULT NOW(),
    updated_at       TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS meeting_contacts (
    meeting_id       TEXT REFERENCES meetings(id) ON DELETE CASCADE,
    contact_id       TEXT REFERENCES contacts(id) ON DELETE CASCADE,
    PRIMARY KEY (meeting_id, contact_id)
);

-- Legislative bill references captured during note intake.
CREATE TABLE IF NOT EXISTS bill_references (
    id           SERIAL PRIMARY KEY,
    meeting_id   TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    bill_type    TEXT NOT NULL DEFAULT '',
    bill_number  TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS bill_refs_meeting ON bill_references (meeting_id);

-- "Inbox" — auto-detected callouts (action/ask/commitment/trigger candidates) scanned out
-- of meeting notes, each optionally linked to a spawned task, with an accept/ack workflow.
CREATE TABLE IF NOT EXISTS meeting_scan_items (
    id           SERIAL PRIMARY KEY,
    meeting_id   TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    callout_type TEXT NOT NULL,
    text         TEXT NOT NULL,
    task_id      TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    accepted     BOOLEAN NOT NULL DEFAULT TRUE,
    acknowledged_at TIMESTAMP,
    created_at   TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS scan_items_meeting ON meeting_scan_items (meeting_id);

-- Calendar events ingested via forwarded Outlook invites (ICS parsing).
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

-- meetings.calendar_event_id can only be added after external_calendar_events exists.
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS calendar_event_id INTEGER
    REFERENCES external_calendar_events(id) ON DELETE SET NULL;

-- ---- Organizations / Asks / Commitments / Follow-up triggers (advocacy workflow) ----

CREATE TABLE IF NOT EXISTS organizations (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    type       TEXT,
    notes      TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Something requested of an org/contact, tied to a meeting and optionally a bill; can
-- spawn a linked task.
CREATE TABLE IF NOT EXISTS asks (
    id              TEXT PRIMARY KEY,
    meeting_id      TEXT REFERENCES meetings(id) ON DELETE CASCADE,
    text            TEXT NOT NULL,
    organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
    contact_id      TEXT REFERENCES contacts(id) ON DELETE SET NULL,
    bill_ref_id     INTEGER REFERENCES bill_references(id) ON DELETE SET NULL,
    status          TEXT NOT NULL DEFAULT 'open',
    priority        TEXT NOT NULL DEFAULT 'normal',
    task_id         TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    source_excerpt  TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Something promised in a meeting, with a due date; can spawn a linked task.
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

-- "If X happens, do Y" watch conditions, optionally tied to a tracked bill.
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

CREATE INDEX IF NOT EXISTS asks_meeting     ON asks (meeting_id);
CREATE INDEX IF NOT EXISTS asks_org         ON asks (organization_id);
CREATE INDEX IF NOT EXISTS commits_meeting  ON commitments (meeting_id);
CREATE INDEX IF NOT EXISTS triggers_meeting ON followup_triggers (meeting_id);

-- New columns linking existing tables to organizations/asks/commitments.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS organization_id TEXT
    REFERENCES organizations(id) ON DELETE SET NULL;
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS organization_id TEXT
    REFERENCES organizations(id) ON DELETE SET NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS ask_id TEXT
    REFERENCES asks(id) ON DELETE SET NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS commitment_id TEXT
    REFERENCES commitments(id) ON DELETE SET NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS organization_id TEXT
    REFERENCES organizations(id) ON DELETE SET NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS contact_id TEXT
    REFERENCES contacts(id) ON DELETE SET NULL;

-- Many-to-many people <-> organizations.
CREATE TABLE IF NOT EXISTS contact_organizations (
    contact_id      TEXT REFERENCES contacts(id) ON DELETE CASCADE,
    organization_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
    PRIMARY KEY (contact_id, organization_id)
);

CREATE INDEX IF NOT EXISTS contact_orgs_org ON contact_organizations (organization_id);

-- Standalone notes attached to an organization or a person.
CREATE TABLE IF NOT EXISTS entity_notes (
    id          SERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,   -- 'organization' | 'contact'
    entity_id   TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS entity_notes_lookup ON entity_notes (entity_type, entity_id);

-- Seed organizations from historical canonical_group / task group_name values and backfill
-- the FK/join-table links, so pre-existing meetings/tasks/contacts point at a real
-- organizations row instead of a free-text group name.
INSERT INTO organizations (id, name, created_at, updated_at)
SELECT DISTINCT
    trim('-' FROM regexp_replace(lower(canonical_group), '[^a-z0-9]+', '-', 'g')) AS id,
    canonical_group AS name,
    NOW(), NOW()
FROM meetings
WHERE canonical_group IS NOT NULL AND canonical_group != ''
ON CONFLICT (id) DO NOTHING;

UPDATE meetings
SET organization_id =
    trim('-' FROM regexp_replace(lower(canonical_group), '[^a-z0-9]+', '-', 'g'))
WHERE organization_id IS NULL
  AND canonical_group IS NOT NULL AND canonical_group != '';

INSERT INTO organizations (id, name, created_at, updated_at)
SELECT DISTINCT
    trim('-' FROM regexp_replace(lower(group_name), '[^a-z0-9]+', '-', 'g')) AS id,
    group_name AS name,
    NOW(), NOW()
FROM tasks
WHERE group_name IS NOT NULL AND group_name != ''
ON CONFLICT (id) DO NOTHING;

UPDATE tasks
SET organization_id =
    trim('-' FROM regexp_replace(lower(group_name), '[^a-z0-9]+', '-', 'g'))
WHERE organization_id IS NULL
  AND group_name IS NOT NULL AND group_name != '';

INSERT INTO contact_organizations (contact_id, organization_id)
SELECT id, organization_id FROM contacts
WHERE organization_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- Strip the legacy trailing "@group:..." tag the add-task flow used to append into
-- free-task text (organization is a structured field now).
UPDATE tasks
SET text = regexp_replace(text, '\s*@group:.*$', '')
WHERE text LIKE '%@group:%';

-- ---- Bill Tracker (Congress.gov) ----

-- Tag note-flagged bills with a Congress so matches are exact across Congresses.
ALTER TABLE bill_references ADD COLUMN IF NOT EXISTS congress INTEGER;

CREATE INDEX IF NOT EXISTS bill_refs_cong_typenum
    ON bill_references (congress, bill_type, bill_number);

-- Bills the user sponsors/cosponsors, synced from Congress.gov.
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
    created_at         TIMESTAMP DEFAULT NOW(),
    -- "Will's Bills" flag, kept out of the sync upsert's SET list so it survives re-syncs.
    working_on         BOOLEAN NOT NULL DEFAULT FALSE,
    -- On-demand detail enrichment (actions/cosponsors/committees/summary/text) cached so
    -- the drawer doesn't re-hit Congress.gov on every open.
    detail             JSONB,
    detail_synced      TIMESTAMP
);

CREATE INDEX IF NOT EXISTS tracked_bills_cong_typenum
    ON tracked_bills (congress, bill_type, bill_number);

-- Flags when a note-mentioned bill matches a tracked bill.
CREATE TABLE IF NOT EXISTS bill_match_flags (
    id              TEXT PRIMARY KEY,
    tracked_bill_id TEXT REFERENCES tracked_bills(id) ON DELETE CASCADE,
    bill_ref_id     INTEGER REFERENCES bill_references(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'new',  -- 'new'|'notified'|'dismissed'
    noticed_at      TIMESTAMP DEFAULT NOW(),
    resolved_at     TIMESTAMP,
    UNIQUE (tracked_bill_id, bill_ref_id)
);

-- Which orgs/contacts were (or should be) told about a bill match.
CREATE TABLE IF NOT EXISTS bill_match_notifications (
    id          SERIAL PRIMARY KEY,
    flag_id     TEXT REFERENCES bill_match_flags(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,           -- 'organization' | 'contact'
    entity_id   TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE (flag_id, entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS bill_match_notifs_entity
    ON bill_match_notifications (entity_type, entity_id);

-- Single-row sync status/error tracker for both bill and schedule syncs.
CREATE TABLE IF NOT EXISTS bill_sync_meta (
    id          INTEGER PRIMARY KEY DEFAULT 1,
    last_synced TIMESTAMP,
    last_result JSONB,
    schedule_last_synced TIMESTAMP,
    last_error TEXT,
    schedule_last_result JSONB,
    schedule_last_error TEXT
);

-- Upcoming committee hearings/markups + House-floor consideration for tracked bills.
CREATE TABLE IF NOT EXISTS bill_schedule_events (
    id             TEXT PRIMARY KEY,   -- 'cm-{eventId}-{TYPE}{NUM}' | 'floor-{YYYYMMDD}-{TYPE}{NUM}'
    source         TEXT NOT NULL,      -- 'committee' | 'floor'
    congress       INTEGER,
    bill_type      TEXT NOT NULL,      -- normalized upper
    bill_number    TEXT NOT NULL,
    chamber        TEXT,
    event_type     TEXT,               -- 'Hearing'|'Markup'|'Meeting'|'Floor'
    status         TEXT,               -- 'Scheduled'|'Canceled'|'Postponed'|'Rescheduled'
    event_date     TIMESTAMP,          -- committee meeting datetime; floor = week-of Monday
    title          TEXT,
    committee_name TEXT,
    location       TEXT,
    url            TEXT,
    raw            JSONB DEFAULT '{}',
    last_seen      TIMESTAMP DEFAULT NOW(),
    created_at     TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS bill_sched_typenum ON bill_schedule_events (congress, bill_type, bill_number);
CREATE INDEX IF NOT EXISTS bill_sched_date ON bill_schedule_events (event_date);

-- Fold legacy tasks.contact free-text into the linked contact's phone field when empty
-- (audit M2), so the redundant per-task field can retire from the UI without losing data.
-- The column itself is left in place as a harmless vestige.
UPDATE contacts c SET phone = sub.contact, updated_at = NOW()
FROM (
    SELECT DISTINCT ON (contact_id) contact_id, contact
    FROM tasks
    WHERE contact_id IS NOT NULL AND COALESCE(contact, '') <> ''
    ORDER BY contact_id, created_at DESC
) sub
WHERE c.id = sub.contact_id AND COALESCE(c.phone, '') = '';

-- Unique email index (audit H5). Best-effort: if duplicate emails still exist, merge the
-- dupes first (POST /api/contacts/<id>/merge), then re-run this statement.
CREATE UNIQUE INDEX IF NOT EXISTS contacts_email_unique
    ON contacts (lower(email)) WHERE email <> '';

-- Canonical status vocabularies (audit H3/C2b): remap legacy values, then apply CHECK
-- constraints (see ASK_STATUSES / COMMITMENT_STATUSES / TRIGGER_STATUSES in app.py).
ALTER TABLE asks ALTER COLUMN status SET DEFAULT 'open';
UPDATE asks SET status = CASE status
    WHEN 'logged' THEN 'open' WHEN 'needs_review' THEN 'in_review'
    WHEN 'under_review' THEN 'in_review' WHEN 'task_created' THEN 'accepted'
    WHEN 'completed' THEN 'done' WHEN 'notify_if_changes' THEN 'open'
    ELSE status END
WHERE status NOT IN ('open','in_review','accepted','declined','done','no_action');
UPDATE commitments SET status = CASE status
    WHEN 'task_created' THEN 'in_progress' WHEN 'waiting' THEN 'in_progress'
    WHEN 'completed' THEN 'done' WHEN 'closed_no_action' THEN 'dropped'
    WHEN 'needs_review' THEN 'open'
    ELSE status END
WHERE status NOT IN ('open','in_progress','done','dropped');

ALTER TABLE asks DROP CONSTRAINT IF EXISTS asks_status_check;
ALTER TABLE asks ADD CONSTRAINT asks_status_check
    CHECK (status IN ('open','in_review','accepted','declined','done','no_action'));
ALTER TABLE commitments DROP CONSTRAINT IF EXISTS commitments_status_check;
ALTER TABLE commitments ADD CONSTRAINT commitments_status_check
    CHECK (status IN ('open','in_progress','done','dropped'));
ALTER TABLE followup_triggers DROP CONSTRAINT IF EXISTS followup_triggers_status_check;
ALTER TABLE followup_triggers ADD CONSTRAINT followup_triggers_status_check
    CHECK (status IN ('watching','fired','resolved','dismissed'));

-- Tracks when a "watching" trigger was last checked against bill/schedule updates, so
-- the trigger-evaluation heuristic never reconsiders the same update twice.
-- last_match_at/last_match_reason hold the current suggest-and-confirm candidate (cleared
-- whenever the trigger's status is next updated via the API).
ALTER TABLE followup_triggers ADD COLUMN IF NOT EXISTS checked_at TIMESTAMP;
ALTER TABLE followup_triggers ADD COLUMN IF NOT EXISTS last_match_at TIMESTAMP;
ALTER TABLE followup_triggers ADD COLUMN IF NOT EXISTS last_match_reason TEXT;

-- Timestamps the transition into a terminal status, so asks/commitments can be plotted as
-- opened-vs-closed over time instead of only a closed-as-of-now count.
ALTER TABLE asks ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP;
ALTER TABLE commitments ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP;

-- Indexes for common query patterns (core tables).
CREATE INDEX IF NOT EXISTS tasks_meeting_id    ON tasks (meeting_id);
CREATE INDEX IF NOT EXISTS tasks_done          ON tasks (done);
CREATE INDEX IF NOT EXISTS meetings_file_date  ON meetings (file_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS completions_date    ON completions (completed_date);
