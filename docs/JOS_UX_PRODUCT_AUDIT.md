# JOS — UX & Product Audit

**Date:** 2026-07-10
**Scope snapshot:** commit `a23e34a` (main)
**Companion documents:** `AUDIT.md` (security/reliability audit), `docs/JOS_UX_IMPLEMENTATION_PLAN.md` (phased plan)

This audit treats JOS as what it is: a personal operating system for one congressional staffer. Every finding is tied to code. The methodology was a full read of `app.py` (5,224 lines — all routes, schema, sync jobs), `static/app.js` (5,237 lines — all views, modals, and event wiring), `templates/index.html`, `templates/login.html`, `schema.sql`, `vercel.json`, `.github/workflows/bill-sync.yml`, and the structure/tokens of `static/style.css`, plus execution-testing of suspect logic paths and cross-referencing every backend route against its frontend call sites.

---

## 1. Executive Summary

**Overall state.** JOS has an unusually strong *concept* and an increasingly strong *capture pipeline*: the four-phase intake modal (type → pre-meeting brief → live notes with callout markers → review queue) is genuinely well-designed, and the entity model underneath (meetings, tasks, people, organizations, asks, commitments, triggers, bills) is the right model for the job. The bill tracker's match feed ("bills we were asked about that Blake has now acted on") is the kind of cross-entity intelligence that makes a tool feel smart.

**The systemic problem is that capture outruns connection.** The intake pipeline creates rich, typed records — and then most of them dead-end:

- Follow-up **triggers** are created, corrupted by a parsing bug on the way in, listed by no screen, and have no way to ever be resolved. The feature is write-only.
- **Asks** carry a 9-state lifecycle in the database but the UI exposes exactly one transition (✓ complete). Asks are almost never linked to a *person* because intake only links them to the organization, so the "Asks Raised" section of every person page is effectively always empty (`app.py:4689-4694` sets `organization_id` but never `contact_id`; person page reads `asks WHERE contact_id = %s`, `app.py:3509-3515`).
- `@name` **person callouts** are rendered in the review queue and then discarded — no contact is created or linked (`app.py:4577` collects them only into a body-text line "People: …").
- Starting notes from an **upcoming calendar meeting** silently creates a *second, unlinked* meeting record: the frontend sends `prepared_meeting_id` (`app.js:3191`) but the backend never reads it (`prepared_meeting_id` appears nowhere in `app.py`), so the calendar stub is orphaned and its notes live in a duplicate.
- The two **bill surfaces** — "Bills" under Relationships (what people asked about) and the "Bill Tracker" tab (what Blake sponsors) — are connected in the database (`bill_match_flags`) but barely in the UI: the bill drawer never shows which meetings/orgs/people raised a bill, and the referenced-bills table never shows tracker status.

**Why it feels fragmented.** Three root causes recur through every finding below:

1. **No canonical component for repeated actions.** There are five different date-input patterns, four ways to pick a person or organization (datalist, custom dropdown, `prompt()`, free text), two parallel "who is this for" fields on tasks (`contact` free-text vs `contact_id`), and two vocabularies for the same concept ("group" vs "organization"). Each was built for its screen, not for the system.
2. **Content-derived identity.** Task IDs are hashes of their text, contact IDs are hashes of names or emails, meeting IDs are hashes of filenames that embed dates. Renaming anything either breaks references (task edit → FK violation, `app.py:4130-4161`), creates duplicates (contacts, rescheduled calendar meetings), or silently detaches history.
3. **Dual sources of truth.** Tasks live both in the `tasks` table and in the meeting's markdown body; org membership lives in both `contacts.organization_id` and `contact_organizations`; org identity lives in both `canonical_group` strings and `organization_id` FKs. Every dual-truth pair has at least one path where the copies diverge.

**Recommended direction.** Don't rewrite. The Flask + vanilla-JS architecture is fine for one user, and the entity model is right. The path to "coherent and premium" is: (1) fix the ~10 confirmed defects; (2) establish stable IDs and one source of truth per fact; (3) close the loop on every captured record — everything created by intake must be visible, actionable, and resolvable somewhere; (4) consolidate every repeated interaction onto one canonical component; (5) then polish. The highest-leverage single change is making the intake pipeline finish what it starts — linking people, wiring the calendar stub, giving asks/commitments/triggers a working lifecycle — because that turns the app's flagship flow into a closed loop instead of a scatter gun.

---

## 2. Current Product Map

### 2.1 Entities

| Entity | Table | Identity scheme | Notes |
|---|---|---|---|
| Meeting | `meetings` | `sha1(filename)[:16]` — filename embeds date + group/summary | Statuses: `prepared`, `in_progress`, `complete`, `cancelled` |
| Task | `tasks` | Meeting-sourced: `sha1(filename+section+text)[:16]`; free: UUID | Types `action`/`reminder`/`free`; done/backburner/snoozed; priority, deadline (TEXT), estimate, recurrence, parent/subtask, dependencies |
| Person | `contacts` | Attendee-derived: `sha1(name)`; manual: `sha1(email)` or `sha1(name+company)` | Three ID schemes for one human |
| Organization | `organizations` | Name slug (`_org_slug`, `app.py:908`) | Rename keeps old slug id |
| Ask | `asks` | `sha1(meeting_id+'ask'+text)` | 9 statuses; links org, contact, bill_ref, task |
| Commitment | `commitments` | `sha1(meeting_id+'commitment'+text)` | 6 statuses; due_date DATE; task link |
| Follow-up trigger | `followup_triggers` | `sha1(meeting_id+'trigger'+text)` | condition/action; status `watching` only in practice |
| Callout (scan item) | `meeting_scan_items` | SERIAL | Provenance of intake extractions; optional task link |
| Bill reference | `bill_references` | SERIAL | Free-text type/number + congress; per meeting |
| Tracked bill | `tracked_bills` | `{congress}-{type}-{number}` | Synced from Congress.gov; `working_on` flag; cached `detail` |
| Bill match | `bill_match_flags` / `bill_match_notifications` | UUID / SERIAL | Tracked bill ∩ referenced bill; notified entities |
| Schedule event | `bill_schedule_events` | Composite string | Committee hearings + floor weeks |
| Calendar event | `external_calendar_events` | SERIAL + `(uid, recurrence_id)` unique | Raw ICS retained |
| Entity note | `entity_notes` | SERIAL | Attached to org or contact |
| Completion | `completions` | SERIAL | Append-only check-off log |
| Group alias | `groups_map` | raw name | **No write path exists** — read-only vestige |
| Time log | `task_time_log` | SERIAL | **No API route reads or writes it** — dead table |

### 2.2 Screens

| Screen | Location | Contents |
|---|---|---|
| Home | `#view-home` | Hero stats, primary-focus panel, deadlines strip, completion ring, sparkline, upcoming meetings, today's-callouts card |
| Tasks | `#view-tasks` | Three stacked "papers" (Active / Backburner / Done), filter bar, keyboard nav |
| Relationships | `#view-groups` | 3-column slide layout: Orgs/People/Bills sub-tabs → org record (timeline + rail) or meeting list → meeting detail |
| Bill Tracker | `#view-bills` | Schedule panel, match feed, relationship sub-tabs, bill table, bill drawer |
| Modals/overlays (16) | — | NL add-task, edit task, intake (4 phases), import, ICS meeting edit, meeting edit, subtask, blocker, snooze, daily plan, focus mode, command palette, search overlay, today's callouts, canvas fullscreen, bill drawer |

### 2.3 Entity CRUD matrix

Where each entity can be **C**reated, **V**iewed, **E**dited, **L**inked, **D**one/resolved, **X** deleted:

| Entity | Create | View | Edit | Link | Resolve | Delete |
|---|---|---|---|---|---|---|
| Meeting | Intake, .md import, ICS upload | Rel. detail, task drawer | Meeting-edit modal (metadata only), ICS-edit modal | Contacts picker | Status buttons (ICS only) | Detail ✕ |
| Task | NL modal, intake callouts, commitment→task, subtask modal, Apple Shortcut | Tasks papers, drawer, org rail, person page, focus mode, daily plan | Edit modal, inline callout edit in meeting detail, scan-item edit | Blocker modal (add only), person/org via edit modal | Checkbox everywhere, focus mode, context menu | Context menu |
| Person | Attendees (implicit), card scan, +Add person, contact picker, `ensurePersonId` | People table expand, meeting attendee card, org rail | Person editor (2 places, shared) | org via `prompt()`, meeting via picker | — | Person editor |
| Organization | Meeting save (implicit slug upsert), task group (implicit), person + org | Orgs table, org record | Org edit panel | — | — | Org record |
| Ask | Intake `~~` only | Org rail, person page (empty in practice), timeline | `prompt()` ×2 | — | ✓ button (→completed) | Org rail 🗑 |
| Commitment | Intake `>>>` only | Org rail, person page, timeline | `prompt()` ×2 | — | +Task button; no complete control | Org rail 🗑 |
| Trigger | Intake `FU IF` only | Org/person timeline only | **None** | — | **None** | **None** |
| Callout | Intake save | Today's-callouts modal, home card | Inline text/type/due | task (implicit) | — | 🗑 (deletes task too) |
| Bill reference | Intake bill callout | Bills sub-tab, meeting detail pills | Inline pill edit | — | — | **None** |
| Tracked bill | Sync | Tracker table, drawer, schedule | ★ Will's Bills (context menu) | — | — | **None** (sync-owned) |
| Bill match | Sync (auto) | Match feed | — | — | Notified/Dismiss/Undo | — |
| Calendar event | ICS upload | Upcoming card | ICS-edit modal | — | Start Notes (broken link) | **None** |
| Entity note | Org/person pages | Same + timeline | **None** (delete + retype) | — | — | ✕ |

The blank and bold cells are the product's shape problem in one table: several entity types can be created but never resolved, and several can be seen but never acted on.

### 2.4 Primary workflows

1. **Capture a meeting** — intake modal (4 phases) or .md import or ICS upload.
2. **Process what came out of meetings** — Today's Callouts modal; org record rail.
3. **Work the task list** — Tasks screen, focus mode, daily plan.
4. **Prepare for a meeting** — pre-meeting brief inside intake phase 1 (`/api/organizations/<slug>/brief`).
5. **Review a relationship** — org record (timeline + rail) or person expansion.
6. **Track legislation** — Bill Tracker (sync, matches, schedule, drawer).

---

## 3. Findings by Severity

Legend: each finding lists severity, evidence, impact, root cause, fix, complexity (S/M/L). Confirmed defects are marked **[DEFECT]**; design gaps are unmarked; risks that need live testing are marked **[RISK]**.

### Critical

---

**C1. Starting notes from a calendar meeting creates a duplicate, unlinked meeting record [DEFECT]**
*Feature: calendar → intake handoff.*
**Current:** "Start Notes" on an upcoming meeting sets the stub to `in_progress` and opens intake with a hidden `prepared_meeting_id` (`app.js:227-242`, `314-333`). Intake POSTs it (`app.js:3191`), but `api_notes_intake` never reads it — the string `prepared_meeting_id` does not occur in `app.py`. The save creates a brand-new meeting (`filename = "{date} - {group} [{HHMMSS}].md"`, `app.py:4635`), then the frontend separately marks the *stub* complete (`app.js:3217-3225`).
**Impact:** Every calendar-driven meeting produces two meeting rows: an empty "complete" stub (still linked to the calendar event, organizer, join link, dtstart) and a notes record with none of that. Timelines, attendee links, and the `calendar_event_id` chain split across the two. The user sees duplicate meetings in the Relationships list.
**Root cause:** Backend contract never implemented; frontend papered over it with a status flip.
**Fix:** In `api_notes_intake`, when `prepared_meeting_id` is present, write the parsed body/tasks/metadata into *that* meeting row (upsert by that id, keep `calendar_event_id`, `dtstart`, `meeting_link`, set `status='complete'`) instead of minting a new filename. Complexity: **M**.

---

**C2. Follow-up triggers are write-only and their text is corrupted at creation [DEFECT]**
*Feature: `FU IF` triggers.*
**Current:** (a) `parts[0].strip().lstrip("FU IF")` strips leading F/U/I/space *characters*, verified by execution: "Increase staff pay" → "ncrease staff pay" (`app.py:4751`). (b) After creation, triggers appear only as timeline dots; the `/api/followup-triggers` list endpoint has zero frontend callers, there is no endpoint to change a trigger's status, no UI to edit or delete one, and the org rail omits them (rail sections at `app.js:1792-1800` cover asks/commitments/tasks/bills/people/notes only).
**Impact:** A core promise of the intake language ("follow up when X happens") is unusable: the condition text is mangled and the trigger can never be seen in a list, fired, or retired. The `watching` count even feeds the pre-meeting brief (`app.py:1525`), surfacing stale items forever.
**Root cause:** Feature shipped as capture-only; parsing bug hid in the double-`lstrip`.
**Fix:** Anchored regex strip; add `PUT /api/followup-triggers/<id>` (status/text) and `DELETE`; render a "Watching" section on the org rail and a global "Waiting on" view; auto-suggest firing a trigger when its linked bill gets a match (data already exists in `bill_match_flags`). Complexity: **M**.

---

**C3. Editing a meeting-sourced task rewrites its primary key and 500s when anything references it [DEFECT]**
*Feature: task editing (modal and inline callout edit).*
**Current:** `api_edit_task` recomputes `id = sha1(filename+section+new_text)` and runs `UPDATE tasks SET id=…` (`app.py:4130-4161`). Six FKs reference `tasks.id` with no `ON UPDATE CASCADE` (`task_dependencies`, `task_time_log`, `parent_id`, `meeting_scan_items.task_id`, `asks.task_id`, `commitments.task_id`).
**Impact:** Editing the text of any task that has a subtask, blocker, scan item, ask or commitment link fails with an FK violation → generic 500 alert. When it succeeds, the completions history detaches and the meeting body still holds the old text, so a re-import resurrects the old task as a duplicate.
**Root cause:** Content-derived identity used as a primary key.
**Fix:** Never mutate `tasks.id`; keep the content hash in a separate `import_key` column used only by the import upsert. Complexity: **M** (small code change + data-compatible).

---

**C4. All "today" logic runs in UTC — on both server and client [DEFECT]**
*Feature: overdue flags, due-today, deadlines strip, daily plan, intake date default, completions dates.*
**Current:** Server uses `date.today()`/`datetime.now()` throughout (`app.py:1158`, `4227-4229`, `4527` etc.) — UTC on Vercel. The client *also* uses UTC via `new Date().toISOString().slice(0,10)` for the daily-plan gate (`app.js:157`), intake date default (`app.js:2979`), snooze quick-picks (`app.js:4139`, `4689`), and Today's Callouts (`app.js:2690`).
**Impact:** From ~5–6 PM Mountain time, tasks flip overdue a day early, evening meeting notes are stamped with tomorrow's date, "Today's callouts" shows tomorrow (i.e., nothing), and the daily-plan modal re-triggers at the wrong boundary. For a tool whose core question is "what needs attention *today*", this is a correctness failure for several hours of every working day.
**Root cause:** No timezone concept anywhere.
**Fix:** Server: one `today()` helper honoring `APP_TIMEZONE` (default `America/Denver`). Client: one `localToday()` helper using local date parts (`getFullYear()/getMonth()/getDate()`), replacing every `toISOString().slice(0,10)`. Complexity: **S**.

---

### High

---

**H1. Asks and commitments never link to people, hollowing out the person page [DEFECT-adjacent gap]**
*Feature: intake → asks/commitments; person hub.*
**Current:** Intake inserts asks/commitments with `organization_id` only (`app.py:4689-4694`, `4708-4713`); `contact_id` is never populated anywhere in the app. The person detail queries asks/commitments by `contact_id` (`app.py:3509-3522`), and the person timeline does the same.
**Impact:** "What did this person ask me for?" and "What did I promise this person?" — the two questions a person page exists to answer — always come back empty. The bill-match "askers" list (`app.py:3028-3030`) is likewise permanently empty of names.
**Fix:** In the intake review queue, let ask/commitment items carry an optional person (defaulting to a single detected `@name` or a single-attendee meeting); persist `contact_id`. Backfill is impossible (data never captured), so ship the link forward. Complexity: **M**.

---

**H2. `@person` and `due:` callouts are captured, displayed, and then thrown away**
*Feature: intake callout language.*
**Current:** `person` items become a body-text line "People: Jane" (`app.py:4593-4594`) and an audit row; no contact is created or linked to the meeting (contrast with attendees, which do get contacts via `_upsert_attendee_contacts`). `deadline` items become "Deadlines noted: …" text only.
**Impact:** The intake legend promises "@name — Person: someone mentioned", but mentioning someone has no structural effect. Users learn the markers lie, which erodes trust in the whole callout language.
**Fix:** Person callouts → `ensure contact` + `meeting_contacts` link (same path as attendees, `app.py:943-963`); deadline callouts → offer a date in the review queue that attaches to the nearest task item or the meeting `deadline` field. Complexity: **S–M**.

---

**H3. Ask lifecycle is 9 states in the database, 1.5 states in the UI**
*Feature: asks.*
**Current:** Valid statuses: `logged, needs_review, under_review, task_created, accepted, declined, completed, no_action, notify_if_changes` (`app.py:3622-3623`). The UI exposes: a ✓ button hard-coded to `completed` (`app.js:1721`, `1870-1877`) and `prompt()`-based text/priority editing (`app.js:1905-1918`). There is no "create task from ask" even though `asks.task_id` exists and commitments have exactly that flow (`app.py:3737-3775`).
**Impact:** The recorded reality can't reflect actual outcomes ("declined", "referred", "watching"), and asks can't drive work. The status vocabulary is aspiration, not behavior.
**Fix:** Reduce to a working set (`open → in_review → accepted | declined | done | no_action`), render a status select (same component as commitments), add `POST /api/asks/<id>/create-task` mirroring the commitment one. Complexity: **M**.

---

**H4. Two bill worlds, one thin thread**
*Feature: bill references vs tracked bills.*
**Current:** Relationships→Bills lists `bill_references` grouped by type+number (no congress → cross-congress collisions, `app.py:2077-2096`); the Bill Tracker lists `tracked_bills`. They meet only in the match feed. The bill drawer (`app.js:4049-4130`) shows Congress.gov data but *nothing from the user's own world* — no "raised in these 3 meetings by these orgs", no linked asks, no notes. The referenced-bills table header promises an "Organizations" column that the renderer never fills — 4 `<th>` vs 3 `<td>` (`templates/index.html:274-282` vs `app.js:3371-3380`) **[DEFECT]**.
**Impact:** Answering "who cares about H.R. 1234 and what's its status?" requires manually cross-referencing two screens.
**Fix:** One bill identity (congress+type+number). The drawer gains a "In your meetings" section (query `bill_references` + `asks` by normalized key); the references table gains real org data and a tracker-status column; searching a bill opens the drawer. Complexity: **M**.

---

**H5. Contact identity: three hashing schemes, guaranteed duplicates, no merge**
*Feature: people.*
**Current:** `sha1(name)` for attendee-derived (`app.py:915`), `sha1(email)` or `sha1(name+company)` for manual/card-scan (`app.py:1876-1877`), preserved-id for edits. Card scan then `POST /api/contacts` (`app.js:2955-2966`) can silently *merge into* an unrelated contact that happens to share the key, or duplicate an attendee contact for the same human. No duplicate detection is shown at scan time; no merge tool exists.
**Impact:** The People table accumulates near-duplicates ("Jane Smith" the attendee and "Jane Smith" the card scan), splitting meeting history, tasks, and notes across them.
**Fix:** Random IDs for new contacts; unique-by-email guard; at card-scan save and person create, run a name/email similarity check and offer "Use existing Jane Smith?"; add a merge endpoint (repoint meeting_contacts, tasks.contact_id, asks, commitments, entity_notes, contact_organizations; delete loser). Complexity: **L**.

---

**H6. Clicking a day on the home "Upcoming deadlines" strip filters tasks by text, not by deadline — result: an empty list [DEFECT]**
*Feature: home → tasks navigation.*
**Current:** The click handler stuffs the ISO date into the global search box and refreshes (`app.js:4628-4634`); `api_tasks` matches `q` against task text and group only (`app.py:3902`).
**Impact:** The most natural drill-down on the dashboard ("what's due Thursday?") reliably lands on "No tasks." A companion inconsistency: the strip renders Mon–Fri of the *current* week (`app.py:4239-4250`) while its subtitle says "in the next 7 days" (`app.js:116-118`), so weekend/next-week deadlines are invisible and on Friday the strip is mostly the past.
**Fix:** Add a real `deadline=YYYY-MM-DD` filter param to `/api/tasks` and a visible filter chip; make the strip a true rolling 7 days. Complexity: **S**.

---

**H7. Renaming an organization strands its history under the old slug**
*Feature: org identity.*
**Current:** Org ids are name slugs. `api_organization_update` changes `name` but not `id` (`app.py:3164-3180`). Meeting saves and task-group edits upsert orgs by slugging the *current* name (`_org_for_name`, `app.py:918-930`). Meanwhile `db_get_org_profile`/timeline match tasks by `organization_id` **or** by slugified `group_name` (`app.py:1309-1311`).
**Impact:** Rename "Acme Corp" → "Acme Corporation": the org keeps id `acme-corp`, but the next meeting typed as "Acme Corporation" creates a *second* org `acme-corporation`. History splits; the org table shows two rows for one relationship. The dual task-matching (`organization_id` OR group-slug) exists precisely to paper over this class of drift.
**Fix:** Keep slugs as an initial-id convenience but resolve org by name case-insensitively before creating (`_org_for_name` should `SELECT … WHERE lower(name)=lower(%s)` first); longer term, random ids + unique name index. Complexity: **M**.

---

**H8. Changing a callout's type in Today's Callouts changes a label, not reality**
*Feature: post-hoc callout review.*
**Current:** The type `<select>` lets the user switch task ↔ ask ↔ commitment ↔ trigger (`app.js:2727-2731`), but `api_scan_item_update` only rewrites `meeting_scan_items.callout_type` and, at most, the linked task's text/deadline (`app.py:3350-3392`). Reclassifying a "task" as an "ask" leaves the task in the task list and creates no ask.
**Impact:** The control invites a correction it does not perform — the UI suggests integration the backend doesn't support (the exact anti-pattern this audit was asked to find).
**Fix:** Make type changes transactional conversions: task→ask deletes/retires the task and inserts an ask (and vice versa), reusing the same creation code paths intake uses. Or restrict the select to the label-only trio (task/followup/important) until conversion exists. Complexity: **M**.

---

### Medium

---

**M1. Two vocabularies for one concept: "group" vs "organization."** The tasks filter is labeled "Organization" with "All organizations" in HTML (`templates/index.html:159-160`) but `renderTasks` overwrites the options with "All groups" (`app.js:507`). Task chips say `group`; the API says `group_name`; the CRM says organization. Terminology should be **Organization** everywhere user-facing (it is the CRM's word), with `group` retained only as legacy column names. **S**

**M2. Two parallel "person" fields on every task.** `contact` (free-text phone/email, `app.py` tasks column) and `contact_id` (a real person) coexist in the edit and NL modals as separate inputs ("Person (optional)" and "Phone / email (optional)", `templates/index.html:443-487`). Nothing reconciles them. Fold the free-text field into the person record (a person has phone/email) and keep a single Person selector. **M**

**M3. Five date-input patterns.** Month/day/year triple `<select>`s (edit + NL modals), native `type=date` (intake, snooze, callout due, today's-callouts), free text with placeholder "e.g. 2025-06-15" (meeting edit deadline, `templates/index.html:922`), `datetime-local` (ICS edit), and quick-pick buttons that only some instances have. The triple-select is the worst of the five (three tabs to set one date, allows Feb 31). Standardize on native `type=date` + quick-pick chips. **S–M**

**M4. Four person/org selector patterns.** Datalist-by-name (edit/NL modals — silently creates a new contact on typo via `ensurePersonId`, `app.js:3352-3363`), custom search dropdown (meeting contact picker, `app.js:1419-1519`), `prompt()` (person→org link, `app.js:2334-2342`), and free-text org inputs that implicitly create orgs. One `EntityPicker` (search + "create new" row, like the contact picker) should serve all sites. **M**

**M5. `prompt()`/`confirm()`/`alert()` as the editing and feedback layer.** Ask/commitment edits use two chained `prompt()`s (`app.js:1905-1941`); ten destructive actions use bare `confirm()`; ~15 failure paths use `alert()`, while task completion has a proper undo toast (`app.js:608-641`). The `api()` helper throws `"path → 500"` and drops the server's JSON error detail (`app.js:36-40`). Standardize on: inline editors or the edit-modal pattern; toast with undo for destructive actions where feasible; a toast/error component that surfaces `data.error`. **M**

**M6. The meeting list shows date + org but not topic** (`app.js:1198-1210`) — you cannot tell two meetings with the same org apart without clicking. Add topic as the primary line. Also "All Meetings" is reachable only inside Relationships; meetings as a concept have no list of their own with topics/status. **S**

**M7. Smart views exist only in the API.** `smart_view=today|upcoming|neglected|quick_wins|waiting|commitments` (`app.py:3916-3949`) is a genuinely good prioritization feature — used only by the daily-plan modal. The Tasks screen offers none of them; "Waiting" (blocked) and "Commitments" views would directly answer "what am I waiting for?" and "what did I promise?". Expose as filter chips above the Active paper. **S**

**M8. Home dashboard fails silently.** `renderHome` catches the stats fetch error and returns (`app.js:80`), leaving "–" placeholders with no retry. Same pattern in `loadUpcomingMeetings` (hides the card on any error, `app.js:243-245`). Add a visible error state with retry. **S**

**M9. Daily plan ordering is theater.** Up/down reordering writes `state.dailyPlanOrder` (`app.js:4769`) which is never persisted nor read again; the order affects only the immediate focus-mode run. Either persist the plan for the day (it's the natural "my plan" artifact) or drop the reorder buttons. **S–M**

**M10. Dead/vestigial surfaces confuse the model.** `groups_map` aliasing has no write path (`app.py` — only reads at `1037-1042`, `3813-3814`) yet `api_facets` still computes `unaliased_raw_groups` that no UI consumes; `task_time_log` has no routes; `/api/reload`, `/api/groups`, `/api/asks` (list), `/api/commitments` (list), `/api/followup-triggers` have no callers; the edit modal ships an `#edit-m-estimate-hint` element that is never populated (`templates/index.html:492`). Delete or wire up. **S**

**M11. Intake presets hard-code personal data in source.** `_intakeTypePresets` maps 1:1 → group "Rebekah" (`app.js:3022`). Charming, but it means meeting types are unconfigurable and the codebase embeds a person's name. Move presets to data (an `app_settings` table or localStorage). **S**

**M12. Bill references dedupe/normalize nowhere on write.** Intake inserts `bill_references` verbatim ("H.R." vs "HR" both stored; duplicates allowed per meeting, `app.py:4667-4677`); grouping in `/api/bills` ignores congress. Normalize on write (reuse `_normalize_bill_type/number`) and add congress to the grouping key. **S**

**M13. Search result for a bill goes to the wrong place.** `navigateToSearchResult` for bills just opens the Relationships bills sub-tab (`app.js:3527-3529`) — it doesn't open that bill or even scroll to it, despite carrying `meeting_id`. Route to the bill drawer (once H4 unifies identity). **S**

**M14. The prepared-meeting metadata editor plants junk contacts.** Editing attendees on a large calendar meeting feeds the collapsed placeholder "Large meeting (12 attendees)" through `_upsert_attendee_contacts` (`app.py:5147` + `app.js` attendee string from `app.py:4959`), creating a contact named "Large meeting (12 attendees)". Guard the placeholder pattern. **S**

**M15. Task ↔ meeting body dual truth.** Tasks extracted from a meeting body live independently in `tasks`, but the body markdown keeps its own checkbox lines; editing/checking tasks never updates the body, and re-importing the same file resurrects old text (upsert by content-hash id, `app.py:1619-1638`). Declare the `tasks` table the source of truth: meeting detail should render open items from `tasks` (it already does) and the body as narrative; re-import should not resurrect tasks whose scan items/tasks were edited or deleted (track deletions via `import_key`). **M**

---

### Low

**L1.** Escape-key handling is spread across four separate `keydown` listeners with different capture phases (`app.js:699-701`, `4911-4913`, `4920-4922`, `5132-5150`); the bill drawer closes on Escape even when a modal above it should win. Centralize a modal stack. **S**

**L2.** The completion ring is labeled "% Completed (this week)" but `pct_complete` mixes a rolling-7-day completion count with *currently open* tasks (`app.py:4284-4287`) — the number isn't a week completion rate. Rename or recompute. **S**

**L3.** `state.paperOrder` z-stack vs the phone segmented control: two mechanisms, one concept; peek-edges are unlabeled for screen readers. **S**

**L4.** Toast container renders one toast at a time (`innerHTML =`), so a second completion within 6s destroys the first's undo. Queue them. **S**

**L5.** `_clientExtractDeadline`'s "next week" maps to this Friday (`app.js:990`) — wrong by most people's intuition. **XS**

**L6.** The NL modal auto-advances to step 2 after 700ms of typing pause (`app.js:4660-4667`) — typists get yanked mid-thought; the Enter-to-advance already covers intent. Consider removing auto-advance. **XS**

**L7.** `filter_meetings` string-sorts dates with `(x.date or "")` putting undated meetings *first* in descending order (empty string sorts last in reverse=True? — `""` < any date, so with `reverse=True` undated go last; fine — but `api_groups` `last_contact` max() on mixed None handled by filter). Verify undated-meeting ordering intent. **XS [RISK]**

**L8.** Dark mode exists as one `@media (prefers-color-scheme: dark)` block at `style.css:3621` over a light-token system plus "dark/legacy" tokens (`--night`, `--paper`…) that few selectors use — theme drift risk; several components (e.g., login page) are dark-only by their own inline palette. **M**

**L9.** `_TASKS_SELECT` runs two correlated subqueries per task row (`app.py:1222-1232`) and `/api/tasks` is called three times per refresh (open/backburner/done, `app.js:528-532`). Fine at current scale; consolidate to one call with client-side partition when convenient. **S**

**L10.** `switchTab("groups")` on search navigation calls `_activateGroupsSubtab` but `switchTab` may also reset org state mid-flight; the depth-transition `setTimeout(…, 300)` dance in `selectOrg`/`selectMeeting` (`app.js:1687-1697`, `3406-3418`) races with rapid navigation (guards exist but the pattern is fragile). **M [RISK]**

---

## 4. Integration Gap Matrix

Ratings: ✅ fully integrated · 🟡 partial · 🗄 data exists, UI weak · 🎭 UI suggests it, data weak · ❌ not integrated · — n/a

| From \ To | Meeting | Task | Person | Org | Ask | Commit | Trigger | Cal event | Card | Bill |
|---|---|---|---|---|---|---|---|---|---|---|
| **Meeting** | — | ✅ extraction + drawer | 🟡 attendees→contacts; `@name` dropped (H2) | ✅ auto-linked | 🟡 created, org-only | 🟡 created, org-only | 🟡 created, then orphaned (C2) | 🎭 stub duplicated (C1) | ✅ scan-in-intake links | ✅ references |
| **Task** | ✅ drawer shows source | — | 🗄 `contact_id` set only via edit modal; no person nav from chip | 🟡 chip shows name, not clickable | 🗄 `ask_id` column, never written | ✅ commitment→task + badge | ❌ | — | — | ❌ task can't reference a bill |
| **Person** | ✅ meeting history | ✅ tasks listed | — | ✅ chips + M2M | 🎭 section always empty (H1) | 🎭 same (H1) | 🗄 timeline only | ❌ cal attendees never matched to contacts | ✅ scan into editor | 🗄 bill-notified timeline events only |
| **Org** | ✅ timeline | ✅ rail + slug match (H7 fragility) | ✅ rail cards | — | ✅ rail | ✅ rail | 🗄 timeline only, no rail section (C2) | ❌ | — | ✅ bills rail |
| **Ask** | ✅ back-ref | 🗄 `task_id` exists, no create-task flow (H3) | 🎭 `contact_id` never set (H1) | ✅ | — | 🗄 `related_ask_id` never set | ❌ | — | — | 🗄 `bill_ref_id` never set by intake |
| **Commitment** | ✅ | ✅ create-task | 🎭 (H1) | ✅ | 🗄 | — | ❌ | — | — | ❌ |
| **Trigger** | ✅ | ❌ | 🗄 | ✅ | ❌ | ❌ | — | — | — | 🗄 `bill_ref_id` column, never set, never checked by sync |
| **Cal event** | 🎭 (C1) | — | ❌ | ❌ no org inference from organizer/attendees | — | — | — | — | — | — |
| **Card scan** | ✅ links contact to meeting | — | 🟡 creates contact; no dupe check (H5) | 🎭 company field ≠ org link | — | — | — | — | — | — |
| **Bill (referenced)** | ✅ pills + sub-tab | ❌ | 🗄 askers query exists, empty (H1) | 🗄 org column unrendered (H4) | 🗄 `asks.bill_ref_id` unused | — | 🗄 | — | — | 🟡 match feed only |
| **Bill (tracked)** | 🗄 matches exist; drawer shows nothing of yours (H4) | ❌ no "create task from bill action" | 🗄 notified timeline | 🗄 | 🗄 | — | 🗄 should auto-fire triggers | — | — | — |

**Priority future states** (the 🎭 and high-value 🗄 cells): intake links asks/commitments/triggers to people and bill refs; the bill drawer shows meetings/orgs/asks that raised the bill; trigger auto-fire on bill matches; calendar organizer/attendee → contact matching; task chips navigate to their person/org.

---

## 5. Consistency Audit

Canonical patterns recommended for each repeated action (keep = ✓, refactor = ↻, remove = ✗):

| Action | Variations found | Canonical pattern |
|---|---|---|
| **Pick a date** | ①MDY selects (edit modal `index.html:451-476`; NL modal `app.js:1066-1076`) ②`type=date` (intake/snooze/callouts) ③free text (meeting edit deadline) ④`datetime-local` (ICS) ⑤quick chips (some) | ✓② + ⑤ everywhere a date is set; ↻①③ to it; ④ stays for datetime. One `dateField()` helper renders it. |
| **Pick a person** | ①datalist by name w/ silent create (`app.js:774`, `1162`) ②contact-picker dropdown w/ explicit "+ Create" (`app.js:1461-1483`) ③`prompt()` (org add) | ✓② as shared `EntityPicker`; ↻①③. Silent creation on typo (①) is a data-integrity hazard. |
| **Pick an org** | ①free-text w/ datalist, implicit create (task edit/NL/intake/meeting edit) ②`prompt()` (person page) | Same `EntityPicker` with explicit create row. |
| **Edit a task** | ①full modal (all fields) ②inline callout edit in meeting detail (text only → different endpoint semantics, `app.js:1340-1375`) ③scan-item edit (text/due propagate to task, `app.py:3350`) | ✓① as the single editor; ↻②③ to open it (or constrain them to text-only via one shared endpoint call). Three editors with three field subsets currently update the same row. |
| **Complete something** | task checkbox (list/drawer/subtask/focus/home) ✓ consistent; ask ✓ button (hard-coded status); commitment: **no complete control** | One `StatusControl` for ask/commitment/trigger with per-entity vocabularies; task checkbox stays. |
| **Edit ask/commitment** | chained `prompt()`s | ↻ small inline form or the shared edit-modal shell. ✗ prompt(). |
| **Delete** | `confirm()` with 6 different copy styles; scan-item delete also deletes the task (stated only in code comment) | One `confirmAction()` with consequence text ("Also deletes 1 linked task") and, where cheap, undo-toast instead of confirm. |
| **Create a meeting** | intake (4-phase), .md import, ICS upload | ✓ all three, but all must converge on the same meeting row semantics (C1) and the same org/contact side effects (import currently skips `_upsert_attendee_contacts` — meetings imported from .md never link attendee contacts; intake and meeting-edit do). ↻ import to call the same post-save linker. |
| **Save feedback** | intake: inline result banner; import: per-file rows; tasks: silent refresh; person editor: silent + list reload; errors: `alert()` | One toast system: success toasts with entity link ("Saved — View note"), error toasts with server detail. |
| **Search** | global overlay (entities), command palette (commands+tasks), bills search box, contact picker, blocker search | ✓ overlay + palette merged into one Cmd-K surface (they overlap ~60%); field-level searches stay. |
| **Sub-tabs** | `.groups-subtab` class reused by Relationships and Bill Tracker with scoping workaround (`app.js:4938-4949`) | One tab component; distinct class names. |
| **Timeline** | org + person share `_renderTimelineEvents` ✓ | Extend to bills ("your history with this bill"). Model example of the right pattern. |
| **Terminology** | group/organization; note/meeting; callout/scan item; backburner/snoozed; "Will's Bills"/working_on | Organization; Meeting (with Notes as its body); Callout; keep Backburner (parking) + Snooze (defer-until) but document the difference in UI copy. |

---

## 6. Workflow Audits

### 6.1 Process meeting notes (flagship)

- **Goal:** capture a meeting and its consequences in one pass.
- **Current steps:** open intake (dock/`w`) → pick type → (phase 1 form: org/date/topic/attendees; brief appears on blur) → Start Meeting → type notes with markers → Finalize → review queue (edit text/type/due, delete) → End Meeting → result banner → optionally "View note".
- **Pain points:** person/deadline callouts vanish (H2); asks/commitments lose the person (H1); trigger text corrupted (C2); calendar handoff duplicates the meeting (C1); the review queue can't assign a person or org per item; after save the button relabels to "End Meeting" (`app.js:3256`) even though the meeting *was* ended — label churn across phases ("Finalize Notes" → "End Meeting" → "Done" → "End Meeting") reads as state confusion; UTC date default stamps evening meetings tomorrow (C4).
- **Proposed:** same phases, with the review queue as the single完 point: each item gets person/org pickers pre-filled from context, deadline chips, and correct type conversions; save writes into the prepared meeting when one exists; result banner offers "View note" and "Review callouts".
- **Automation:** pre-fill attendees→people links; auto-link a detected bill to the matching tracked bill; auto-suggest trigger creation from asks with "notify_if_changes".
- **Backend:** C1 fix, H1/H2 links, C2 regex; **Frontend:** review-queue pickers.
- **Benefit:** the flagship flow becomes trustworthy — everything typed with a marker demonstrably lands somewhere findable.

### 6.2 Prepare for a meeting

- **Current:** brief appears only *inside intake phase 1* after typing the org and blurring the field (`app.js:5042-5072`); or navigate Relationships → org → read rail/timeline (3–4 clicks). `/api/organizations/<id>/brief` is otherwise unused.
- **Pain:** the brief is invisible until you're already writing; upcoming calendar meetings don't show a brief at all despite knowing the attendees/organizer.
- **Proposed:** "Prep" button on each upcoming-meeting card → the same brief (last met, open asks, commitments owed, watching triggers, bills raised) in a drawer; keep the intake inline brief.
- **Changes:** infer org for calendar events (organizer domain / attendee match) — new small backend inference + a drawer reusing brief markup. **Benefit:** the single highest-value CRM moment (walking in knowing what's owed) becomes one click.

### 6.3 Create and edit tasks

- **Current create:** NL modal — type sentence → auto-parse (700ms) or Enter → confirm fields → Add. 2 screensteps, good defaults. **Current edit:** context-menu → modal (9 fields) or inline in meeting detail.
- **Pain:** MDY selects (3 interactions per date); duplicate person/contact fields (M2); editing meeting tasks can 500 (C3); no way to open the person/org from a task chip; "urgent" keyword parsing exists in two implementations (server `_URGENCY_KW`, `app.py:973-983`; client `_URGENCY_KW_RE`, `app.js:397`) that can disagree.
- **Proposed:** date field swap; one Person picker; chips clickable to hubs; id-stable edits.

### 6.4 Record an ask / commitment (outside a meeting)

- **Current:** impossible. Both are creatable **only** through intake markers. Remembering "Acme asked me for X" a day later means writing a fake meeting note.
- **Proposed:** "+ Ask" / "+ Commitment" on org and person pages (backend inserts exist; needs two small POST endpoints — currently only status/PUT/DELETE exist). **Benefit:** the CRM stops depending on perfect in-meeting capture.

### 6.5 Review the day (Today's Callouts)

- **Current:** home card summary → modal, day navigation, edit text/type/due, delete. Good bones.
- **Pain:** type-switch is cosmetic (H8); UTC "today" (C4); items show no person/org assignment; no "looks good, clear all" acknowledgment, so the card never empties.
- **Proposed:** make it the app's **Inbox**: every intake-created record (task/ask/commitment/trigger) appears once for confirmation with real conversion controls and per-item person pickers; acknowledging clears the badge.

### 6.6 Track a bill

- **Current:** Tracker tab auto-syncs daily, match feed with notified/dismiss (good loop!), drawer with cached detail, ★ Will's Bills, schedule panel.
- **Pain:** drawer omits user context (H4); no way to spawn a task from a bill event ("markup Thursday — prep memo"); floor events vanish mid-week (see `AUDIT.md` C5); match feed names no people (H1).
- **Proposed:** drawer "In your world" section; "Create task" on schedule rows and matches; fix floor-week filter.

### 6.7 Import a business card

- **Current:** during intake phase 3 or from a person editor: photo → Claude scan → editable fields → save → chip. 4 steps, good.
- **Pain:** no duplicate detection (H5); company field doesn't create/link an org; card contact isn't linked to the meeting's org.
- **Proposed:** post-scan dupe check ("Matches existing Jane Smith — use?"); company → org link via `_org_for_name` + `_link_contact_org` (both exist).

### 6.8 Weekly review

- **Current:** does not exist. Done paper + completions sparkline are the only retrospectives; "neglected" smart view is API-only.
- **Proposed (later):** a lightweight review screen: neglected tasks, stale watching triggers, open commitments past due, orgs not contacted in N weeks (all queryable from existing data).

---

## 7. Recommended Information Architecture

### Navigation (dock, unchanged shell)

1. **Home** — today's state and entry points (as now, corrected data).
2. **Tasks** — papers + smart-view chips (Today / Upcoming / Waiting / Commitments / Neglected).
3. **Relationships** — Organizations · People · Bills-in-meetings (as now, with real hub pages).
4. **Bills** — tracker, schedule, matches (as now, cross-linked).
5. **Inbox** (promoted Today's Callouts) — review/confirm everything capture created; badge = unacknowledged count. Lives as the 5th dock item or stays a home card with a real acknowledgment model.

### Page hierarchy and purposes

- **Home**: answer "what requires my attention?" — focus panel (top urgency), true 7-day deadlines, upcoming meetings with **Prep**, inbox summary. Remove or fix the ring/sparkline labels (L2).
- **Org record** (already the best page in the app): timeline + rail; add **Watching (triggers)** section, "+ Ask/+ Commitment", and make the header show "last met / owes us / we owe" as stat chips (partially present).
- **Person record**: same structure as org (it nearly is, via `renderPersonInto`); becomes genuinely useful once H1 links asks/commitments to people.
- **Meeting detail**: keep; add its asks/commitments/triggers created from this meeting (query by `meeting_id` — data exists, UI omits them entirely today), so a meeting page shows *all* its consequences, not just tasks.
- **Bill drawer**: Congress.gov data + "In your meetings/asks" + actions.
- **Related-records pattern**: every hub uses the same rail sections (Open asks / Commitments / Tasks / Watching / Bills / People / Notes) and the same timeline component — the org page already demonstrates the pattern; replicate, don't reinvent.

### Global surfaces

- Merge the **search overlay** and **command palette** into one Cmd-K surface (both are keyboard launchers over overlapping corpora; two muscle memories for one need).
- Task/entity **chips are links**: group chip → org, person chip → person, bill pill → drawer, meeting source → detail. Today only some are.

---

## 8. Design-System Recommendations

The CSS already has a real token system (`style.css:5-81`: neutrals, accent, status colors, radii, shadows) — the foundation is there; the gaps are component-level:

| Standard | Recommendation |
|---|---|
| Page layout | Keep card grid (home) and 3-column slide (relationships). Document the depth-state machine (`updateRelDepth`) — it's clever but fragile (L10). |
| Headers | One `.record-header` pattern: title, type chip, stat chips, Edit/Delete right-aligned (org page is the model; meeting detail differs slightly — align them). |
| Detail views | Hub = header + timeline + rail (org model). Rail sections via one `railSection()` helper (exists at `app.js:1790` — promote it to shared and use for person/meeting too). |
| Lists/tables | One `.groups` table style exists — good; fix header/body column parity (H4); every row gets a hover affordance and the whole row is the click target (already true). Empty states: one `emptyState(icon, text, action?)` helper replacing 8 ad-hoc inline styles (`app.js:1195`, `1647`, `2079`, `3368`…). |
| Forms | One field renderer (`_personEditorFields`'s `f()` at `app.js:2100` is the seed); labels above inputs; native date fields; explicit create in pickers. |
| Drawers/modals | Two shells exist (`.drawer`, `.modal`) — fine. Centralize open/close + Escape in a modal-stack manager (L1); all backdrops close on click except intake (documented iPad exception, `app.js:4980` — keep). |
| Entity selectors | `EntityPicker` component (search, results, "+ Create", keyboard nav) used for person, org, task-blocker (blocker modal already implements 80% of it, `app.js:4373-4401`). |
| Status controls | `StatusControl(entity, statuses)` pill-select with per-status colors (`.entity-status.status-*` classes already exist in CSS). |
| Task controls | Checkbox + chips row is consistent — keep. Make chips interactive links. |
| Dates | `dateField(value, {quickPicks})` and `fmtDate()`/`fmtDay()` (currently 4 local copies: `app.js:2005`, `3690`, `4052`, `2755`). |
| Badges | Consolidate `.chip`, `.badge`, `.pill`, `.count-badge`, `.chip-mini`, `.bill-pill`, `.callout-badge` (7 families) into a documented 3-tier set: chip (metadata), badge (count), status pill. |
| Notifications | One toast queue (success/error/undo), replacing `alert()` and the single-slot toast (L4). |
| Loading | Skeleton rows for tables/timeline instead of "Loading…" text; button-level spinners for sync (bill sync already does progressive status text well — `app.js:3921-3960` is the model). |
| Errors | Error state = message + retry button, never a hidden card (M8); `api()` surfaces server `error` field. |
| Destructive | `confirmAction()` with consequence line; undo-toast where reversible. |
| Responsive | 11 media queries exist; the segmented control / peek-edge duality (L3) should collapse to one mechanism driven by viewport. |

Since there's no build step, "components" = shared JS render helpers + CSS classes in one `ui.js` section — the codebase already does this implicitly (`_renderTimelineEvents`, `renderPersonInto`, `railSection`); the recommendation is to finish the job deliberately.

---

## 9. Reliability & Correctness Findings

**Confirmed defects** (all verified in code; several executed): C1 (prepared_meeting_id ignored), C2 (`lstrip` corruption — executed), C3 (task-id rewrite FK breakage), C4 (UTC on both tiers), H6 (deadline-strip filter mismatch), H4 (bills table 4-header/3-cell mismatch), H8 (cosmetic type switch), M14 (junk contact from placeholder), plus from `AUDIT.md`: floor events hidden mid-week, rescheduled-ICS duplicate meetings, single-VEVENT parsing, `SECRET_KEY` fallback, unsanitized `body_html`.

**Architectural risks** (plausible, need live verification): cron `job=bills` full sync vs the ~10s serverless limit; concurrent cold-start `init_db()` races; depth-transition animation races (L10); `api_contacts_upsert` silently merging distinct people who share a name+company hash.

Concrete recommendations (detail in the implementation plan):

- **Validation:** ISO-date validation at every deadline/due input server-side; `deadline` column → `DATE`; reject unknown statuses centrally (asks/commitments/triggers share a validator).
- **Transactions:** intake's post-save block (`app.py:4648-4806`) already runs in one connection — good; commitment→task and scan-item conversions must stay single-transaction; contact merge must be transactional.
- **Constraints:** unique index on `lower(contacts.email)` (where non-empty); `bill_references` unique `(meeting_id, congress, bill_type, bill_number)` after normalization; CHECK constraints for ask/commitment/trigger statuses.
- **Idempotency:** intake is content-hash idempotent (good); .md re-import must respect tombstones for user-deleted tasks; ICS ingestion needs multi-VEVENT + UID-only meeting identity.
- **Deduplication:** contact dupe detection at create/scan; org resolve-by-name before slug-create (H7).
- **Error reporting:** JSON error envelope on all routes (`@app.errorhandler`), surfaced by the `api()` helper and toast; log failed logins and destructive ops server-side.
- **Tests:** pure-function pytest first (`extract_deadline`, `_current_congress`, `_normalize_*`, `_compute_next_recurrence`, trigger parsing, `_extract_callouts` port, `parse_ics_content`); then route tests against a temp Postgres for intake→entity creation (the highest-defect area); a GitHub Actions check.
- **External integrations:** Congress.gov calls already time-budgeted and error-persisted (good); make the cron path use the paged protocol; card-scan errors already degrade to manual entry (good).
- **Serverless:** move `init_db()` out of import path; single connection per request; keep the paged-sync pattern as the template for any long work.

---

## 10. Prioritized Roadmap

### Tier 1 — Immediate fixes (days; high confidence, no design decisions)

| # | Item | Files | Complexity | Risk | Impact |
|---|---|---|---|---|---|
| 1.1 | Trigger `lstrip` → anchored regex (C2a) | `app.py:4751` | XS | none | stops data corruption |
| 1.2 | Timezone helpers server+client (C4) | `app.py` (one helper, ~20 call sites), `app.js` (~8 sites) | S | low | correct "today" |
| 1.3 | Wire `prepared_meeting_id` into intake save (C1) | `app.py` intake route | M | low | ends duplicate meetings |
| 1.4 | Stop rewriting `tasks.id` on edit (C3) | `app.py:4126-4170` | S–M | low | ends edit 500s/history loss |
| 1.5 | Deadline-strip → real `deadline` filter; rolling 7 days (H6) | `app.py` api_tasks + stats, `app.js` | S | none | dashboard drill-down works |
| 1.6 | Bills table column parity + normalized/deduped refs (H4a, M12) | `index.html`, `app.js:3365`, `app.py` | S | none | broken table fixed |
| 1.7 | Restrict callout type-switch to label-safe types until conversion exists (H8 stopgap) | `app.js:2598` | XS | none | stops lying UI |
| 1.8 | Delete dead endpoints/tables/UI stubs (M10) | `app.py`, `schema.sql`, `index.html` | S | low | smaller surface |
| 1.9 | Placeholder-attendee guard (M14) | `app.py` | XS | none | no junk contacts |
| 1.10 | "Organization" terminology + label fix (M1) | `app.js:507`, copy pass | XS | none | one vocabulary |

### Tier 2 — Foundation (canonical behavior; 1–2 weeks)

| # | Item | Outcome | Complexity | Depends on |
|---|---|---|---|---|
| 2.1 | JSON error envelope + toast system (M5, M8) | every failure visible, every success confirmed | M | — |
| 2.2 | `dateField()` everywhere (M3) | one date interaction | S | — |
| 2.3 | `EntityPicker` for person/org (M4); kill `prompt()` | one selection interaction; no silent creates | M | 2.1 |
| 2.4 | Stable IDs: contacts random-id + email unique; org resolve-by-name (H5a, H7) | renames stop forking history | M | — |
| 2.5 | Merge-contacts endpoint + UI (H5b) | duplicate people curable | M–L | 2.4 |
| 2.6 | Deadline column → DATE + validation | trustworthy overdue logic | M | 1.2 |
| 2.7 | pytest + CI for parsers and intake | regressions caught | M | — |
| 2.8 | Single-fetch tasks + shared `fmtDate`/`emptyState`/`railSection` helpers (L9, §8) | less duplication | S | — |

### Tier 3 — Integration (the coherence payoff; 2–4 weeks)

| # | Item | Outcome | Complexity | Depends on |
|---|---|---|---|---|
| 3.1 | Intake review queue assigns person/org per item; asks/commitments get `contact_id`; `@name` creates contacts; `due:` attaches dates (H1, H2) | capture pipeline closes the loop | M–L | 2.3 |
| 3.2 | Trigger lifecycle: endpoints, org-rail "Watching" section, global Waiting view, auto-fire on bill match (C2b) | triggers become a real feature | M | 1.1 |
| 3.3 | Ask lifecycle: working status set, StatusControl, ask→task (H3) | asks drive work | M | 2.1 |
| 3.4 | "+ Ask / + Commitment" on org & person pages (§6.4) | capture without a meeting | S | 3.3 |
| 3.5 | Bill unification: drawer "In your world", tracker status in references table, search→drawer (H4, M13) | one bill story | M | 1.6 |
| 3.6 | Calendar: multi-VEVENT, UID-only identity, organizer/attendee→contact/org inference, Prep button (§6.2) | calendar joins the CRM | M–L | 1.3 |
| 3.7 | Meeting detail shows its asks/commitments/triggers (§7) | meetings show all consequences | S | — |
| 3.8 | Real callout conversions (task↔ask↔commitment) (H8 full) | Inbox edits are real | M | 3.3 |
| 3.9 | Card scan dupe detection + company→org link (§6.7) | clean people data | M | 2.4, 2.5 |

### Tier 4 — Premium experience

| # | Item | Outcome | Complexity |
|---|---|---|---|
| 4.1 | Inbox model for callouts (acknowledge, badge, per-item confirm) | calm daily ritual | M |
| 4.2 | Smart-view chips on Tasks (M7) | prioritization visible | S |
| 4.3 | Merge search + command palette; interactive chips everywhere | fast navigation | M |
| 4.4 | Undo framework (delete task/ask/commitment/note via undo-toast) | confidence | M |
| 4.5 | Skeleton loading, empty-state component, modal-stack Escape (L1) | polish | S–M |
| 4.6 | Persisted daily plan (M9); weekly-review screen (§6.8) | ritual value | M |
| 4.7 | Meeting list shows topic; meetings status chips (M6) | scannable lists | S |
| 4.8 | Configurable intake presets (M11); dark-mode completion (L8) | maturity | S–M |

Suggested order: Tier 1 as one PR-sized batch each; 2.1→2.3 before any Tier 3 UI work; 3.1 is the centerpiece and should land before 4.1 renames Today's Callouts to Inbox.

---

## 11. Top 10 Recommendations (ranked)

1. **Close the intake loop** (3.1 + 1.3 + 1.1): everything captured must land somewhere findable, linked to the right person and meeting. This single cluster converts the app's flagship flow from "capture and hope" to "capture and trust" — the definition of the product feeling intelligent.
2. **Fix time (1.2):** a "what needs attention today" tool that is wrong every evening cannot feel premium no matter how it looks.
3. **Give triggers, asks, and commitments working lifecycles (3.2, 3.3):** these three tables are the CRM's memory of obligations; today two of three cannot even be resolved. This directly answers "What did I promise? What am I waiting for?"
4. **Stable identity for tasks, people, orgs (1.4, 2.4, 2.5):** stops the silent history-splitting that makes users doubt the data ("Can I trust it's reflected everywhere?" — currently, no).
5. **One error/success surface (2.1):** silent failures (home card, timeline fetches) and `alert()`s are the biggest day-to-day trust leak; a toast + retry pattern is cheap and transforms perceived reliability.
6. **Unify the two bill worlds (3.5):** the data for "who asked about this bill and what's happening to it" already exists in one database; showing it in one drawer is the app's best "wow" for its actual job.
7. **Canonical pickers and date fields (2.2, 2.3):** the same action doing the same thing everywhere is the core of feeling coherent; these two components cover ~80% of the current variation.
8. **Make the person page true (H1 via 3.1, plus 3.9):** people are the atoms of congressional work; a person page that actually lists their asks, promises, and meetings turns JOS from a notes app into a relationship system.
9. **Expose smart views + Prep button (4.2, 3.6):** two features that already exist in the backend (`smart_view`, `/brief`) delivering daily visible value for a few hours of UI work — the best effort-to-impact ratio in the codebase.
10. **Inbox ritual (4.1):** a single place that fills during the day and is emptied by review gives the product a daily rhythm — the difference between a database and an operating system.

---

*Uncertainties flagged: L7 (undated-meeting ordering intent), L10 (animation-race severity), cron-timeout behavior (needs a production log check), and whether `contact` free-text on tasks carries data worth migrating before removal (M2) — check row counts before dropping.*
