# JOS — UX Implementation Plan

Companion to `docs/JOS_UX_PRODUCT_AUDIT.md` (finding IDs like C1/H3/M5 refer to that document). Phases are sized to be individually reviewable PRs. Each phase establishes **one canonical behavior** rather than adding a parallel path; where multiple implementations of the same action exist, the phase names which one survives.

Guiding constraints:

- No framework migration. Shared "components" are exported render/wire helpers in `static/app.js` (or a new `static/ui.js` loaded before it) plus existing CSS classes.
- Every phase leaves the app deployable. Database changes are additive `IF NOT EXISTS` migrations consistent with the current `init_db()` style (until Phase 2 moves migration out of the request path — after that, via the migration hook).
- Every phase adds or extends tests for what it touches. Test scaffold arrives in Phase 0.

---

## Phase 0 — Test scaffold & CI

**Objective:** make every later phase verifiable.
**User problem:** regressions ship invisibly; several audited defects (C2, H6) would have been caught by first-pass tests.
**Scope:** pytest + a GitHub Actions workflow running tests and `ruff`. Unit tests only (no DB): `extract_deadline`, `_normalize_date`, `_normalize_bill_type/number`, `_current_congress` (Jan-3 boundaries), `_compute_next_recurrence`, `_org_slug`, `_parse_floor_weeks_feed` (fixture XML), `parse_ics_content` (fixture ICS), and a ported-to-Python spec of `_extractCallouts` marker parsing (documents the callout grammar).
**Database:** none. **Backend:** extract pure functions into module scope if needed (no behavior change). **Frontend:** none.
**Shared components:** `tests/` package, `.github/workflows/ci.yml`.
**Migration concerns:** none.
**Testing requirements:** the suite itself; CI green.
**Acceptance criteria:** `pytest` runs locally and in CI; at least 25 assertions over the functions above; the trigger-parsing test **fails** (demonstrating C2) and is marked `xfail` pending Phase 1.
**Files:** `tests/test_parsers.py`, `tests/test_dates.py`, `tests/fixtures/*`, `.github/workflows/ci.yml`, `requirements-dev.txt`.
**Depends on:** nothing.

---

## Phase 1 — Correctness batch (audit Tier 1)

**Objective:** eliminate the confirmed small defects in one reviewable sweep.
**User problem:** corrupted trigger text, wrong "today" every evening, dead-end dashboard drill-down, broken bills table, junk contacts, lying type-switch.
**Scope (exact):**

1. **C2a** — replace `lstrip("FU IF")` with `re.sub(r'^\s*FU\s+IF\s+', '', text, flags=re.I)` (`app.py:4751`); un-xfail the Phase 0 test.
2. **C4** — add `APP_TIMEZONE` env (default `America/Denver`); one `app_today()` / `app_now()` helper; replace every `date_cls.today()` / `datetime.now()` used for user-facing day logic (`app.py:1158, 3864-3867, 4227-4250, 4527, 5199` and the rest — grep-driven). Client: one `localToday()` in `app.js` replacing all `new Date().toISOString().slice(0,10)` call sites (`app.js:157, 2690, 2979, 4139, 4287, 4689, 5016-5026`).
3. **H6** — `deadline` query param on `/api/tasks` (exact-match ISO); deadlines strip click sets a visible filter (reuse the filter bar, not the search box); `api_stats` deadlines window becomes a rolling 7 days including weekends; subtitle copy stays truthful.
4. **H4a/M12** — bills sub-tab: render the Organizations column (extend `/api/bills` with `array_agg(DISTINCT o.name)`), fix `colspan`; normalize `bill_type`/`bill_number` on insert in intake; add congress to the `/api/bills` GROUP BY.
5. **H8 stopgap** — in both review queues, restrict the type `<select>` to `task | followup | important` (`_SWITCHABLE_CALLOUT_TYPES`, `app.js:2598`) until Phase 8 implements real conversion.
6. **M14** — skip `_upsert_attendee_contacts` for strings matching `^Large meeting \(\d+ attendees\)$`.
7. **M1** — "All groups" → "All organizations" (`app.js:507`); sweep visible "group" copy to "organization" (leave API/DB names).
8. **M10** — delete `/api/reload`, `/api/groups`, unused `#edit-m-estimate-hint`; drop `task_time_log` and `groups_map` writes from `schema.sql`/`init_db` docs (keep tables in DB; removal of tables is deferred to the migration phase). Remove `unaliased_raw_groups` computation.

**Database:** none (SQL text changes only).
**Shared components:** `app_today()` (server), `localToday()` (client).
**Migration concerns:** none.
**Testing:** unit tests for `app_today` TZ math and the new deadline filter; manual: evening-clock check with `TZ=UTC` server and Denver expectation.
**Acceptance criteria:** trigger text stored verbatim minus the marker; at 7 PM Denver the dashboard/deadline strip/callouts all describe the Denver day; clicking a strip day lists exactly the tasks due that day; bills table has 4 aligned columns showing orgs; no console/500 from removed endpoints.
**Files:** `app.py`, `static/app.js`, `templates/index.html`, `schema.sql`, tests.
**Depends on:** Phase 0.

---

## Phase 2 — Stable identity for tasks; migrations out of the request path

**Objective:** task edits never rewrite primary keys; schema management becomes deliberate.
**User problem:** editing a task with subtasks/blockers/links → 500; history silently detaches (C3); cold starts run migrations (AUDIT A1).
**Scope:** add `tasks.import_key TEXT` (backfilled = current id for meeting-sourced tasks); `import_meeting_from_content` upserts by `import_key` instead of id; `api_edit_task` drops the `id = %s` set entirely (id is immutable); re-import respects a `deleted_import_keys` tombstone table so user-deleted meeting tasks stay deleted (M15). Move `init_db()` behind `POST /api/admin/migrate` (CRON_SECRET-authed) + document running it on deploy; keep a cheap `SELECT 1` connection check at cold start.
**Database:** `ALTER TABLE tasks ADD COLUMN import_key TEXT; UPDATE tasks SET import_key = id WHERE source_filename <> 'tasks.md'; CREATE INDEX ...; CREATE TABLE import_tombstones (import_key TEXT PRIMARY KEY, deleted_at TIMESTAMP)`. Delete-task endpoint inserts a tombstone when the task has an `import_key`.
**Backend:** `app.py` import/edit/delete routes; migrate route.
**Frontend:** none (behavior-transparent).
**Migration concerns:** run migrate once right after deploy; verify no meeting-task ids change thereafter.
**Testing:** route test: create meeting → add subtask to its task → edit task text → expect 200 and stable id; re-import same file → text follows the file only when task untouched, deleted task stays gone.
**Acceptance criteria:** editing any task never 500s on FK; completions history survives edits; re-import doesn't resurrect deleted/edited tasks; cold start does no DDL.
**Files:** `app.py`, `schema.sql`, `.github/workflows/` (optional deploy hook note), tests.
**Depends on:** Phase 1 (test scaffold, tz helper in tests).

---

## Phase 3 — Error/success surface: JSON envelope + toast system

**Objective:** one canonical feedback channel; no silent failures, no `alert()`.
**User problem:** failures vanish (home card) or appear as raw `alert("… → 500")`; success is often unconfirmed (M5, M8).
**Scope:** backend `@app.errorhandler(Exception)` returning `{"ok": false, "error": str}` (500) and a small `def fail(msg, code)` helper; remove per-route try/except boilerplate where it duplicates this. Frontend: `api()` parses error bodies and throws `Error(data.error)`; new `toast(msg, {type, undo, linkLabel, onLink})` queue component replacing the single-slot container (`app.js:606-641`) and all `alert()` calls; `renderHome`/`loadUpcomingMeetings`/timeline fetches render an inline error + Retry instead of hiding.
**Database:** none.
**Shared components:** `toast()`, `emptyState()`, `errorState(retryFn)` in a new `static/ui.js`; CSS additions.
**Migration concerns:** none.
**Testing:** JS is untested today — add a minimal node-based unit test for `ui.js` pure renderers if cheap, else manual matrix (each mutating action: success toast; forced 500: error toast with server message).
**Acceptance criteria:** zero `alert(` in `app.js`; killing the network shows retryable error states on Home, timelines, tables; server error strings appear verbatim in toasts.
**Files:** `app.py`, `static/app.js`, `static/ui.js` (new), `static/style.css`, `templates/index.html` (script tag).
**Depends on:** Phase 1.

---

## Phase 4 — Canonical date field and entity pickers

**Objective:** one way to pick a date; one way to pick a person or organization.
**User problem:** five date patterns, four picker patterns, `prompt()` editing, silent contact creation on typos (M3, M4).
**Scope:**

- `dateField(prefix, value, {quickPicks})` in `ui.js`: native `type=date` + Today/This week/Next week chips. Replace the MDY triple-selects in the edit modal (`templates/index.html:451-476`) and NL step-2 (`app.js:1066-1076`); replace the free-text meeting-edit deadline; `getDeadlineValue()` reads the one field.
- `entityPicker({type: 'person'|'org', onSelect, allowCreate})`: search dropdown with explicit "+ Create" row — promote the existing contact-picker (`app.js:1419-1519`) to shared, add org mode backed by `/api/organizations` (client-filtered) . Replace: edit-modal person datalist, NL person datalist, edit/NL org datalists, person-page "+ org" `prompt()` (`app.js:2334`), intake org field keeps datalist for speed but gains the same create semantics. `ensurePersonId` is deleted; creation only happens through the picker's explicit create row.
- Ask/commitment editing: replace chained `prompt()`s (`app.js:1905-1941`) with a small inline edit form (text input, status/priority select, dateField for due) inside the org rail row.

**Database:** none. **Backend:** none (all read endpoints exist).
**Migration concerns:** none.
**Testing:** manual matrix across all 9 replaced sites; unit test for `dateField` value round-trip.
**Acceptance criteria:** no `<select id$="-dl-month">` remains; no `prompt(` in `app.js`; typing an unknown person name cannot create a contact without clicking "+ Create"; every date is set in ≤2 interactions.
**Files:** `static/ui.js`, `static/app.js`, `templates/index.html`, `static/style.css`.
**Depends on:** Phase 3 (toast for save feedback).

---

## Phase 5 — Stable people & organizations (identity, dedupe, merge)

**Objective:** one human = one contact; renames never fork history.
**User problem:** three contact ID schemes create duplicates with split history; org rename strands data under the old slug (H5, H7).
**Scope:**

- New contacts get `uuid4().hex[:16]` ids (all creation paths: `_upsert_attendee_contacts` keeps name-hash **lookup** for idempotent attendee re-saves but falls back to name-match against existing contacts first; `api_contacts_upsert` stops deriving id from email — it matches by email (case-insensitive) then by exact name+company, else inserts a new uuid row).
- `CREATE UNIQUE INDEX contacts_email_unique ON contacts (lower(email)) WHERE email <> ''`.
- `_org_for_name`: resolve `WHERE lower(name) = lower(%s)` before slug-insert; slug stays as the id for *new* orgs only.
- `POST /api/contacts/<id>/merge {into_id}`: transactional repoint of `meeting_contacts`, `contact_organizations`, `tasks.contact_id`, `asks.contact_id`, `commitments.contact_id`, `followup_triggers.contact_id`, `entity_notes(entity_type='contact')`, `bill_match_notifications`; keep richest field values; delete loser.
- UI: person page "Merge into…" (entityPicker); card-scan save and person create run a similarity check (`GET /api/contacts?q=`) and offer "Use existing" before insert (§6.7).

**Database:** unique index; no id rewrites of existing rows (legacy hash ids remain valid — they're opaque).
**Migration concerns:** email-unique index may fail on existing dupes — migration first reports dupes, user merges via new tool, then index applies (two-step deploy).
**Testing:** route tests for merge (all seven tables repointed atomically), upsert-by-email matching, attendee re-save idempotency.
**Acceptance criteria:** scanning a card for an existing person offers the match; merging two contacts leaves one row with unified history everywhere; renaming an org then saving a meeting under the new name attaches to the *same* org.
**Files:** `app.py`, `static/app.js`, `static/ui.js`, `schema.sql`, tests.
**Depends on:** Phases 3, 4.

---

## Phase 6 — Calendar joins the system

**Objective:** one meeting row per real meeting; calendar context flows into notes and prep.
**User problem:** Start Notes duplicates the meeting (C1); rescheduled invites orphan stubs; multi-event ICS drops events; attendees never match contacts (§6.2, AUDIT C3/C4).
**Scope:**

- **C1 fix:** `api_notes_intake` accepts `prepared_meeting_id`; when present and the row exists with status `prepared|in_progress`, write parsed content into that row (keep id, `calendar_event_id`, `dtstart`, `meeting_link`; update filename-independent fields; set `status='complete'`). Frontend drops its separate status flip.
- Meeting identity for calendar stubs: filename/id derive from UID hash only (drop the date from `_create_or_update_prepared_meeting`'s filename identity), so reschedules update in place.
- `parse_ics_content` iterates all VEVENTs; upload response reports per-event results.
- Organizer/attendee inference: on ingest, match attendee emails against `contacts.email` and link via `meeting_contacts`; surface unmatched ones in the ICS-edit modal later (not auto-created).
- **Prep button** on upcoming-meeting cards → drawer rendering `/api/organizations/<id>/brief` when the stub has an org (org inferred from matched contacts' primary org or set manually in the ICS-edit modal via entityPicker).

**Database:** none new.
**Migration concerns:** existing orphaned stubs: one-time cleanup query (delete `status='complete'` meetings with empty body and a `calendar_event_id` whose event maps to another meeting) — run manually with a dry-run report first.
**Testing:** ICS fixtures (single, multi-event, reschedule, cancel); route test: upload → start → intake save → exactly one meeting row with calendar linkage.
**Acceptance criteria:** calendar meeting → notes = one record carrying organizer/link/dtstart + notes/tasks; rescheduling moves, not duplicates; Prep shows the brief in one click.
**Files:** `app.py`, `static/app.js`, `templates/index.html`, tests/fixtures.
**Depends on:** Phases 2, 4 (picker), 5 (contact matching).

---

## Phase 7 — Close the intake loop (people, deadlines, links)

**Objective:** every callout lands as a linked, findable record.
**User problem:** `@name` and `due:` discarded; asks/commitments have no person; person pages empty (H1, H2).
**Scope:**

- Review queue items gain optional **person** and (rare) **org** assignment via entityPicker; ask/commitment/task/trigger items default person = single `@name` in the same line → else single meeting attendee → else unset.
- Backend intake: `person` callouts → ensure contact (Phase 5 rules) + `meeting_contacts` link; ask/commitment/trigger inserts persist `contact_id`; `due` on ask items → (asks have no due column — attach to the created task when converted; for commitments use `due_date` as today); `deadline` callouts offer a date in the queue that writes the meeting `deadline` field or the adjacent task's deadline.
- Task creation from intake stamps `contact_id` when assigned.

**Database:** none (columns exist).
**Migration concerns:** none (forward-only; historical asks stay org-only).
**Testing:** intake route tests: `~~ ask @Jane` → ask row with contact_id = Jane, Jane linked to meeting; person page lists it.
**Acceptance criteria:** after one intake with `@Jane`, `~~`, `>>>`, `FU IF` lines: Jane exists and is linked; her person page shows the ask and commitment; org rail and timelines show all four records with correct text.
**Files:** `app.py`, `static/app.js`, tests.
**Depends on:** Phases 4, 5.

---

## Phase 8 — Working lifecycles: asks, commitments, triggers, conversions

**Objective:** every obligation type can be created anywhere relevant, progressed, and resolved.
**User problem:** triggers are write-only; ask statuses unusable; commitments can't be completed; type-switch is cosmetic (C2b, H3, H8, §6.4).
**Scope:**

- **Statuses (canonical sets):** ask `open | in_review | accepted | declined | done | no_action` (migrate legacy values: logged→open, needs_review/under_review→in_review, task_created→accepted, completed→done, notify_if_changes→open + auto-created trigger); commitment `open | in_progress | done | dropped` (task_created→in_progress, waiting→in_progress, completed→done, closed_no_action→dropped, needs_review→open); trigger `watching | fired | resolved | dismissed`. CHECK constraints added.
- `StatusControl` component (pill select) used in org rail, person page, meeting detail.
- Endpoints: `POST /api/asks` and `/api/commitments` (standalone create with org/person/meeting optional); `POST /api/asks/<id>/create-task` (mirror of commitment version); `PUT/POST /api/followup-triggers/<id>` (status, text) + `DELETE`; "+ Ask / + Commitment / + Trigger" buttons on org and person pages.
- Org rail gains a **Watching** section (triggers); global Tasks screen "Waiting" smart-view chip includes fired triggers (or a dedicated list — keep simple: rail + a Watching group in Inbox).
- **Real conversions** for callouts/records: `POST /api/scan-items/<id>/convert {to}` performs transactional task↔ask↔commitment conversion (create target with same text/links, retire source task or mark source record superseded, repoint scan item); re-enable full type-switch lists in both review UIs.
- **Trigger auto-fire:** `_recompute_bill_matches` also flips linked triggers (`bill_ref_id` join) to `fired` and surfaces them in the match feed/Inbox.

**Database:** status CHECKs + data migration UPDATEs; nothing structural.
**Migration concerns:** legacy status mapping is one-way — run in the migrate hook with a logged count per mapping.
**Testing:** conversion transactional tests (no orphan task, no double record); status-mapping migration test on fixture rows; ask→task parity with commitment→task.
**Acceptance criteria:** a trigger can be watched, fired (manually or by bill match), and resolved from the UI; an ask can be declined; a commitment can be completed; converting an Inbox "task" to an "ask" removes it from the task list and adds it to the org's asks.
**Files:** `app.py`, `static/app.js`, `static/ui.js`, `schema.sql`, tests.
**Depends on:** Phases 3, 4, 7 (person links make lifecycles meaningful).

---

## Phase 9 — One bill story

**Objective:** a single bill surface answering "status + who cares + what next".
**User problem:** referenced bills and tracked bills barely meet; drawer shows no user context (H4, M13).
**Scope:** bill drawer gains "In your world": meetings that referenced it (normalized key join on `bill_references`), orgs/people from those meetings and from asks (`asks.bill_ref_id` now populated when an ask line contains a bill token — small intake addition), and its match/notification history; referenced-bills table gains a tracker-status column (sponsored/cosponsored/★/untracked) via the same normalized join; global search bill results open the drawer (tracked) or the referencing meeting (untracked); "Create task" action on schedule rows and match cards (pre-filled text, org, deadline = event date).
**Database:** none.
**Testing:** normalized-join unit tests (H.R./HR, cross-congress); route test for drawer context payload.
**Acceptance criteria:** from the tracker drawer you can see and click every meeting/org that raised the bill; from the references table you can see Blake's relationship to each bill; a markup event becomes a dated task in two clicks.
**Files:** `app.py`, `static/app.js`, tests.
**Depends on:** Phases 7, 8 (ask links).

---

## Phase 10 — Inbox (evolved Today's Callouts) + smart views

**Objective:** a daily review ritual with a terminal state; prioritization features surfaced.
**User problem:** captured items need one place to confirm; the callouts card never empties; smart views are API-only (M7, §6.5, 4.1, 4.2).
**Scope:** rename Today's Callouts → **Inbox**; add `meeting_scan_items.acknowledged_at`; per-item and per-meeting "Looks right ✓" sets it; the home card badge counts unacknowledged items; Inbox rows use the Phase 8 conversion + Phase 4 pickers. Tasks screen gains smart-view chips (Today / Upcoming / Waiting / Commitments / Neglected) driving the existing `smart_view` param; chips reflect counts.
**Database:** one column.
**Testing:** acknowledgment round-trip; smart-view chip ↔ API param mapping test.
**Acceptance criteria:** processing the Inbox to zero clears the home badge until new captures; each smart view chip shows the same tasks the API returns; "Waiting" answers "what am I blocked/waiting on" including fired triggers.
**Files:** `app.py`, `static/app.js`, `templates/index.html`, `schema.sql`.
**Depends on:** Phase 8.

---

## Phase 11 — Navigation & polish batch

**Objective:** premium feel: fast movement, visible state, no dead ends.
**Scope (each item small, batched for review):**

- Merge search overlay + command palette into one Cmd-K surface (union of both result sets; `/` focuses it too).
- Interactive chips: task group chip → org record; person chip → person; bill pill → drawer; source line → meeting (some exist; make uniform).
- Meeting list rows show topic + status chip (M6); undated ordering decision (L7).
- Modal-stack manager for Escape/backdrop (L1); toast queue already from Phase 3.
- Skeleton loaders for tables/timelines; shared `emptyState()` everywhere (removes 8 inline variants).
- Undo-toast for deletes of tasks/asks/commitments/notes (soft-hold: perform delete after toast timeout, or restore via re-insert payload).
- Persist daily plan for the day (`localStorage` order applied to focus mode; "today's plan" chip on Tasks) or remove reorder (decide with user — see open questions).
- Configurable intake presets (settings blob in DB or localStorage) removing the hard-coded "Rebekah" (M11).
- Copy pass: ring label (L2), strip subtitle, phase button labels in intake ("Finalize Notes" → "Review items", post-save "Done").

**Acceptance criteria:** every entity reference in the UI is a working link; Escape always closes exactly the top layer; deleting anything offers undo for ~6s; no hard-coded personal presets in source.
**Files:** `static/app.js`, `static/ui.js`, `templates/index.html`, `static/style.css`, `app.py` (settings endpoint if DB-backed).
**Depends on:** Phases 3, 4, 8, 9, 10.

---

## Phase ordering & dependency graph

```
P0 ─ P1 ─ P2 ─┬────────────── P6 ─┐
        P3 ─ P4 ─ P5 ─┬─ P6      │
                      └─ P7 ─ P8 ─┼─ P9 ─ P11
                                  └─ P10 ─ P11
```

Practical sequence: **0 → 1 → 2 → 3 → 4 → 5 → 7 → 8 → 6 → 9 → 10 → 11** (Phase 6 can slide earlier/later; it only hard-requires 2, 4, 5).

## Consolidation principles applied

Every phase replaces divergent implementations with one canonical path:

| Repeated action | Canonical implementation after this plan |
|---|---|
| Setting a date | `dateField()` (P4) |
| Picking/creating person or org | `entityPicker()` (P4/P5) — creation is always explicit |
| Task create/edit/complete | existing endpoints, immutable ids (P2), one editor modal |
| Ask/commitment/trigger lifecycle | `StatusControl` + one status vocabulary + create-task parity (P8) |
| Record conversion | one `convert` endpoint, transactional (P8) |
| Meeting creation | three intakes (typed, .md, ICS) converging on one meeting-row semantic + one post-save linker (P6/P7) |
| Feedback | one toast queue + JSON error envelope (P3) |
| "Today" | `app_today()` / `localToday()` (P1) |
| Bill identity | normalized (congress, type, number) join everywhere (P9) |

## Open product questions (need a decision, not code)

1. **Backburner vs snooze:** keep both parking mechanisms, or fold backburner into "snoozed indefinitely"? (Plan assumes keep both, with clearer copy.)
2. **Daily plan:** invest (persist the plan, show it on Tasks) or simplify (drop reordering, keep the morning modal as a launcher)? Phase 11 has both paths.
3. **Ask vs commitment vs trigger vocabulary:** the proposed canonical statuses (P8) rename several existing states — confirm the working set matches how you actually think about outcomes (esp. `no_action` vs `declined`).
4. **`tasks.contact` free-text field:** migrate its values into person records and drop the field, or keep as a scratch "phone/email" note? (Check row count first.)
5. **Inbox as 5th dock item vs home card:** dock item gives the ritual a place; home card keeps the dock minimal.
6. **Intake presets:** which meeting types/values do you want configurable (and should 1:1 still auto-fill a person)?
