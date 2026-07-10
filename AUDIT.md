# JOS — End-to-End Audit Report

**Date:** 2026-07-10
**Auditor:** Claude Code (automated code audit)
**Scope snapshot:** commit `a23e34a` (main), branch `claude/jos-audit-report-839zcq`

---

## 1. Executive Summary

JOS is a single-user Flask + PostgreSQL "chief-of-staff" dashboard deployed on Vercel: meeting-note intake and parsing, task management with urgency scoring, a relationships CRM (organizations / people / asks / commitments / follow-up triggers), calendar (ICS) ingestion, business-card scanning via the Anthropic API, and a Congress.gov bill tracker with scheduled sync via GitHub Actions.

**Strengths.** The codebase is in better shape than most personal projects of this size: every SQL statement is parameterized (no SQL injection found anywhere), the frontend escapes HTML consistently through a single `escapeHtml` helper, authentication fails closed when no password is configured, the bill sync is carefully time-budgeted for serverless limits with persisted error state, and the schema uses real foreign keys, indexes, and idempotent migrations. Recent work (stepped sync, single-bill refresh, error surfacing) shows good operational instincts.

**Critical risks.** Five findings warrant prompt attention:

1. **Forgeable sessions if `SECRET_KEY` is unset** — the hard-coded fallback `"dev-secret-change-in-production"` lets anyone mint a valid `logged_in` session cookie and bypass login entirely.
2. **Stored XSS through note bodies** — Markdown is rendered server-side without sanitization and injected into the DOM as raw `body_html`, so any imported `.md` file (the one externally-sourced input) can execute script in the authenticated app.
3. **Task-edit primary-key rewrite** — editing the text of a meeting-sourced task recomputes its `id`; any task with subtasks, dependencies, time logs, scan items, asks, or commitments attached will hit a foreign-key violation and return a 500, and history (completions) silently detaches for the rest.
4. **Timezone correctness** — all "today"/overdue/due-today/recurrence math uses server-local dates, which is UTC on Vercel; for a Mountain/Eastern-time user, tasks flip to overdue and "today" rolls over in the late afternoon.
5. **Verified text-corruption bug** in follow-up trigger intake — a misused `lstrip("FU IF")` strips leading `F/U/I/space` *characters*, so a condition like "Increase staff pay" is stored as "ncrease staff pay".

Overall verdict: **fit for continued single-user personal use once the High items are fixed; not production-ready for multi-user or externally exposed deployment** (no CSRF tokens, no rate limiting, no audit logging, no tests). Details and a prioritized remediation plan follow.

---

## 2. Scope & Methodology

### Components in scope

| Component | Files | Size |
|---|---|---|
| Backend (Flask app, all API routes, DB layer, sync jobs) | `app.py` | 5,224 lines |
| Database schema & in-app migrations | `schema.sql`, `init_db()` in `app.py` | 147 lines + ~490 lines of DDL |
| Frontend | `static/app.js`, `static/style.css`, `templates/index.html`, `templates/login.html` | 5,237 / 4,228 / 966 / 137 lines |
| Deployment & ops | `vercel.json`, `run.sh`, `.github/workflows/bill-sync.yml`, `requirements.txt` | — |
| External integrations | Congress.gov API, docs.house.gov feeds, Anthropic API (card scan), ICS ingestion | — |

Out of scope: the live Vercel deployment and production database (no access from this environment), the actual values of environment secrets, and the Vercel/Neon platform configuration itself.

### Methods used

- **Full manual code review** of `app.py` (100% of lines) and targeted review of `app.js`/`index.html` (rendering paths, every API call site, all `innerHTML` sinks).
- **Static validation**: `ast.parse` on `app.py` and `node --check` on `app.js` (both pass).
- **Functional testing of suspect logic** by executing extracted code paths (e.g., the `lstrip` trigger-parsing bug was reproduced and confirmed).
- **Data-integrity analysis**: traced every table's write paths, ID-derivation schemes, FK/cascade behavior, and UNION column alignment in the timeline queries.
- **Security assessment**: authentication/session flow, injection surfaces (SQL, XSS, header/URL), CSRF posture, secrets handling, dependency hygiene.
- **Performance profiling by inspection**: query patterns (N+1, full-table loads), serverless cold-start cost, connection usage, and time-budget analysis of the sync jobs against Vercel invocation limits.
- **Cross-reference audit**: every backend route matched against frontend call sites to find dead endpoints and missing UI affordances.

Limitations: no live database or deployment to test against, so findings about production behavior (e.g., cron timeouts) are reasoned from code and platform limits rather than observed.

---

## 3. Detailed Findings

### 3a. Architecture & Environment

**Overall shape.** A deliberate two-tier monolith: one Flask file exposing ~70 JSON routes, one vanilla-JS client file, PostgreSQL, deployed as a single Vercel serverless function. For a single user this is a reasonable architecture — no framework overhead, easy to deploy, easy to reason about at the route level.

**Findings:**

- **A1 (Medium) — Schema migration runs on every cold start.** `init_db()` executes at module import (`app.py:583-587`): ~20 DDL statements, several `DO $$` blocks, plus *data* migrations (org seeding, `@group:` tag stripping, congress backfill) on every cold start. This adds cold-start latency, can race when Vercel spins up concurrent instances (two instances running `CREATE TABLE`/`ALTER TABLE`/seed `UPDATE`s simultaneously), and failures are swallowed with a `print` — the app then serves requests against a possibly half-migrated schema. `schema.sql` also duplicates only part of what `init_db()` creates and has drifted from it (no `organizations`, `asks`, `commitments`, `tracked_bills`, etc.), so it is misleading as documentation.
- **A2 (Medium) — Cron "bills" job likely exceeds the serverless time limit.** `/api/cron/sync?job=bills` runs `_sync_congress_bills()` — up to 8 pages × 2 relationship kinds = 16 sequential Congress.gov calls plus per-page DB connections — in a single invocation (`app.py:2990-2995`). The code's own comments elsewhere say each request must stay "well under the ~10s function limit," and the interactive UI was specifically redesigned to sync page-by-page for this reason. The scheduled path didn't get the same treatment; the GitHub Actions `curl --max-time 120` will not help if Vercel kills the function at 10s. Mitigated by `last_error` being persisted, but the twice-daily sync can silently degrade to "never completes" as the bill count grows.
- **A3 (Low) — Legacy Vercel config; static assets served through the function.** `vercel.json` uses the deprecated `builds`/`routes` v2 format and routes `/(.*)` — including `/static/*` — through the Python function, forfeiting CDN caching for a 5,200-line JS file and a 32KB JPEG, and burning function invocations on assets.
- **A4 (Low) — Dev/prod parity gaps.** `run.sh` detects a venv but then installs with `pip --user`; `DATABASE_URL` defaults to `""`, producing a confusing psycopg2 error at first request rather than a clear startup failure.
- **A5 (Info) — Modularity.** `app.py` mixes seven concerns (auth, notes/tasks CRM, bill tracker, calendar, card scan, intake, cron) in one file; `app.js` mirrors this. It works, but is past the size where a split into blueprints/modules (e.g., `bills.py`, `calendar.py`, `crm.py`) would pay for itself in reviewability — several bugs below survived precisely because near-duplicate logic lives far apart in the same file.

### 3b. Code Quality & Maintainability

**Style & structure.** Python style is consistent and commented where it matters (sync budgets, ID derivation rationale). The frontend is disciplined vanilla JS with a single `state` object. That said:

- **B1 (High, correctness) — Task edit rewrites primary keys and breaks referential integrity.** `api_edit_task` (`app.py:4126-4170`) recomputes a meeting-sourced task's ID from its new text (`_task_id(filename, section, new_text)`) and issues `UPDATE tasks SET id = %s …`. Six tables reference `tasks.id` with no `ON UPDATE CASCADE` (`task_dependencies`, `task_time_log`, `tasks.parent_id`, `meeting_scan_items.task_id`, `asks.task_id`, `commitments.task_id`). Editing any task that has a subtask, dependency, time log, scan item, ask, or commitment link fails with an FK violation → HTTP 500. Even when the update succeeds, `completions` history (keyed by the old text-hash ID, no FK) silently detaches, and the meeting's stored markdown `body` still contains the *old* text — a later re-import of that file resurrects the old task alongside the edited one (duplicate). Recommendation: stop deriving IDs from content; keep the import-time content hash as a separate `natural_key` column and use it only for import upserts.
- **B2 (High, correctness — verified) — `lstrip` misuse corrupts trigger conditions.** `app.py:4751`: `parts[0].strip().lstrip("FU IF").lstrip("FU if")` treats the argument as a *character set*, not a prefix. Verified by execution: `"If Utah funding increases"` → `"tah funding increases"`; `"Increase staff pay"` → `"ncrease staff pay"`. Any follow-up trigger whose condition starts with F, U, I, or a space (after an intended "FU IF" prefix or not) is stored corrupted. Fix with an anchored regex, e.g. `re.sub(r'^\s*FU\s+IF\s+', '', text, flags=re.I)`.
- **B3 (Medium) — Dead code and dead endpoints.** Confirmed unreferenced by the frontend: `/api/reload` (a no-op relic that only counts meetings), `/api/groups` (superseded by `/api/organizations`), the `/api/asks` and `/api/commitments` list endpoints, `/api/followup-triggers`. In `api_stats`, `week_completions_this_week` (`app.py:4279-4283`) is computed and never used. `Meeting.mtime` is carried through the whole pipeline but always written as `NULL`. Dead code should be deleted; it actively misleads (see B4).
- **B4 (Medium) — UI copy contradicts the data it displays.** The home "deadlines strip" is built from **Mon–Fri of the current work week** (`app.py:4239-4250`) but the frontend labels it "N deadlines **in the next 7 days**" (`app.js:116-118`) — on a Thursday it mostly shows the past, and weekend deadlines never appear. Similarly, `completions_30d` actually contains a **7-day** sum (`app.py:4284-4285, 4348`) feeding a sparkline whose surrounding copy has drifted. These are exactly the "views could be more uniform and correctly functioning" issues the audit was asked to find.
- **B5 (Medium) — Missing UI affordance: dependencies can be added but never removed.** The backend has `/api/tasks/dependency/remove`, but no frontend code calls it (only `dependency/add` at `app.js:4845`). A mis-clicked blocker permanently deprioritizes a task (−50 urgency, "Waiting" smart view) with no way to undo from the UI.
- **B6 (Low) — Duplicated render paths.** The meeting body/detail markup is built twice (task drawer at `app.js:884-896`, meeting detail at `app.js:~1213-1320`) with subtle differences (canvas fullscreen click is wired in one, not the other). The four table renderers (`renderGroupsTable`, `renderOrgsTable`, `renderPeopleTable`, `renderBillsTable`) share structure but not code. The org/person timelines commendably *do* share one renderer — extend that pattern.
- **B7 (Low) — Inconsistent error-handling styles.** Roughly half the endpoints wrap everything in `try/except` returning `{"ok": false, "error": …}` with 500; the rest (e.g., `api_meetings`, `api_stats`, `api_organizations`) let exceptions escape as Flask HTML 500s that the frontend's `api()` helper can't parse. Pick one convention (an `@app.errorhandler(Exception)` returning JSON is the cheap fix).
- **B8 (Low) — Unhandled parse errors.** `api_tasks_search` does `int(request.args.get("limit", 10))` outside its try block (`app.py:4379`) — `?limit=abc` → 500. `api_bill_update` casts `int(congress)` similarly (`app.py:4120`).
- **B9 — Tests: none.** There is no test file, no CI check, no linter config anywhere in the repo. Given the deterministic-ID scheme, deadline parser, congress-number math, and ICS handling — all pure functions begging for unit tests — this is the single highest-leverage maintainability investment. Several bugs in this report (B2, D-series) would have been caught by first-pass unit tests.
- **B10 (Info) — Stray artifact.** `static/img/test` (1 byte) is junk and should be deleted.

### 3c. Data Integrity & Functional Accuracy

- **C1 (High) — All date logic runs in server-local time (UTC on Vercel).** `date_cls.today()` / `datetime.now()` are used for overdue computation (`app.py:1158`), due-today stats, deadline strips, smart views, recurrence spawning, intake dating, and completion logging. For a user in Utah/DC, "today" advances at 5–8 PM local: tasks show overdue the evening before they're due, the daily-plan prompt (`last_plan_date`, computed client-side in *local* time at `app.js:158`) disagrees with the server's day, and completions land on the wrong date. Fix by pinning a `APP_TIMEZONE` (e.g., `America/Denver`) and deriving "today" from it everywhere server-side.
- **C2 (Medium) — Contact identity fragmentation (three ID schemes for one human).** Attendee-derived contacts get `sha1(lowercased name)` (`app.py:913-915`); manually created contacts get `sha1(email)` or `sha1(name+company)` (`app.py:1876-1877`); enrichment via `PUT /api/people/<id>` keeps whatever ID exists. Meeting "Jane Smith" once as an attendee and once via card-scan (with email) produces two permanent contact rows whose meetings/asks/tasks histories never merge. Changing a contact's email through the *upsert* endpoint mints yet another row. There is no merge tool. Recommendation: random IDs + a case-insensitive unique index on email, an explicit match-by-name step during attendee ingestion, and a "merge contacts" action.
- **C3 (Medium) — Rescheduled calendar events create orphaned duplicate meetings.** Prepared-meeting IDs derive from `filename`, which embeds the event **date** (`app.py:4947-4950`). When an invite is updated to a new date (the most common calendar operation), the new filename yields a new meeting ID; the ECE row is re-pointed at the new meeting, and the old 'prepared' stub is stranded forever in the UI. The UID hash is already in the filename — the date shouldn't be part of the identity.
- **C4 (Medium) — ICS ingestion reads only the first VEVENT** (`app.py:4843-4915` returns inside the loop). A multi-event ICS (a forwarded series, or a file exported with several meetings) silently drops all but one. Related: the `UNIQUE (user_id, ics_uid, recurrence_id)` constraint does not deduplicate rows with `NULL` recurrence_id (Postgres treats NULLs as distinct), so the dedupe relies entirely on the pre-select — fine single-threaded, racy otherwise.
- **C5 (Medium) — Floor-schedule events vanish mid-week.** House floor events are stored with `event_date` = Monday of the week (`app.py:2549`), but `api_bill_schedule` filters `event_date >= CURRENT_DATE` (`app.py:2935`). From Tuesday onward, a bill still scheduled for floor action *this week* disappears from the Upcoming panel — the opposite of the feature's purpose. Filter floor-type events by "week containing today" instead.
- **C6 (Medium) — `deadline` is unvalidated free text in a TEXT column.** `deadline_direct` from the edit drawer is stored verbatim (`app.py:4119-4124`); anything non-ISO silently breaks the string-comparison overdue logic (`deadline < today`) and the smart-view range checks. Meanwhile `commitments.due_date` is a proper `DATE`. Validate with `date.fromisoformat` at the API edge and migrate the column to `DATE`.
- **C7 (Low) — Monthly recurrence clamps to day 28.** `_compute_next_recurrence` uses `min(dom, 28)` (`app.py:4010`), so "repeat on the 31st" quietly becomes the 28th. Also, the cycle detector for dependencies only catches direct A↔B pairs (`app.py:4411-4417`); A→B→C→A cycles are accepted and leave all three tasks permanently "blocked."
- **C8 (Low) — Legacy divergence in `shortcut_add_task`.** The Apple-Shortcut endpoint still appends `@group:`/`due` tags into the task *text* (`app.py:5192-5197`) — the exact format a cold-start migration strips back out (`app.py:489-493`) — and unlike `api_add_task` it never sets `organization_id`. Shortcut-created tasks look and link differently until the next cold start partially heals them.
- **C9 (Low) — Meeting-group edits bypass the alias map.** `api_meeting_update` treats the typed group as canonical for that one meeting and upserts an org, but never records the alias in `groups_map`; future imports of the same raw group re-create the old canonical name. Editing metadata of a calendar-prepared meeting also feeds the collapsed placeholder string `"Large meeting (12 attendees)"` into `_upsert_attendee_contacts`, creating a junk contact by that name.
- **C10 (Info) — `bill_references` are not normalized or deduplicated at write time** (`app.py:4672-4677`); normalization happens only in the match query. Consistent, but repeated references in one meeting produce duplicate rows and duplicate match flags are prevented only by the `(tracked_bill_id, bill_ref_id)` unique key.

### 3d. Security Assessment

**No SQL injection was found** — all queries use parameter binding; the two f-string interpolations into SQL (`_record_sync_error` field name, `_group_slug` fragment) interpolate only hard-coded internal strings. CORS is not enabled. Debug mode is off. The password check fails closed when `AUTH_PASSWORD` is empty. Those are real positives. The gaps:

- **S1 (High) — Hard-coded `SECRET_KEY` fallback** (`app.py:32`). If `SECRET_KEY` is ever unset/renamed in the Vercel env, sessions are signed with a public string from the repo; anyone can forge `{"logged_in": true}` and own the app (and with it, all constituent/contact PII). Fail hard at startup instead: refuse to boot without a real key, or derive-and-log a random one.
- **S2 (High) — Stored XSS via Markdown bodies.** Python-Markdown passes raw HTML through by default; `body_html` is generated at import (`app.py:1557-1559`) and injected unescaped in two places (`app.js:892`, `app.js:1308`). Any imported `.md` file — the one input that plausibly originates outside the user (notes shared by colleagues, exports from other tools) — can carry `<script>`/`<img onerror>` that runs authenticated, exfiltrates the session, or calls any of the app's destructive APIs. Sanitize server-side (e.g., `nh3`/`bleach` allow-list) at render time.
- **S3 (Medium) — No CSRF tokens.** All state-changing endpoints rely on the session cookie alone. Modern browsers' default-Lax SameSite blunts classic cross-site POSTs, but that is a browser default, not a control the app sets (`SESSION_COOKIE_SAMESITE` is unset). Set `SESSION_COOKIE_SAMESITE="Lax"` (or `Strict`) and `SESSION_COOKIE_SECURE=True` explicitly; for defense in depth, require a custom header (e.g., `X-Requested-With`) on mutating JSON routes — one line in a `before_request`.
- **S4 (Medium) — Login endpoint has no throttling and non-constant-time comparisons.** `password == AUTH_PASSWORD` (`app.py:611`), `X-API-Key != SHORTCUT_API_KEY` (`app.py:5182`), and the CRON secret check (`app.py:2985`) all use `==`. Use `hmac.compare_digest`, and add minimal lockout/backoff (even a per-IP sleep) — the login form is the only thing between the internet and the data.
- **S5 (Medium) — No security headers.** No CSP, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, or HSTS are set. A small `@app.after_request` adding these (CSP can be strict: same-origin + `data:` images for the canvas/card images) would also materially reduce the blast radius of S2.
- **S6 (Medium) — Dependency hygiene.** `requirements.txt` uses only `>=` floors and there is no lockfile, so every deploy resolves the latest of seven packages — silent supply-chain drift with no vulnerability gate. Pin exact versions and add `pip-audit`/Dependabot. (`anthropic>=0.25` is also a very stale floor for an API whose SDK moves quickly.)
- **S7 (Low) — Unbounded base64 image storage.** `card_image`/`canvas_image` data-URLs are accepted with no size or MIME validation and stored inline in TEXT columns; `/api/people` returns every contact's full card image in the list payload. Vercel's body cap (~4.5MB) is the only limit. Validate/limit server-side and drop `card_image` from list responses.
- **S8 (Low) — Side effects on GET.** `/api/cron/sync` accepts GET (`app.py:2976`); token-protected, but GETs get logged in more places (proxies, browser history) and prefetched. `api_key` also rides in Congress.gov query strings (their standard pattern, but worth knowing it appears in any egress logs).
- **S9 (Info) — Third-party data flows.** Business-card photos (names, emails, phones of third parties) are sent to the Anthropic API; raw ICS bodies (including private meeting descriptions) are stored verbatim in `raw_ics`. Both are acceptable for a personal tool but belong in a data inventory (see 3f).

### 3e. Performance & Reliability

- **P1 (Medium) — Full-corpus loads on hot paths.** `db_get_all_meetings()` pulls **every meeting including full `body` and `body_html`** plus all their tasks, then filters in Python. It backs `/api/meetings`, `/api/facets`, `/api/groups`, and — worst — `/api/search`, which runs on (debounced) keystrokes. At a few hundred meetings this is already hundreds of KB per request on a serverless function that also pays a fresh DB connection each call. Move search/filtering into SQL (`ILIKE`/`tsvector`) and select summary columns for lists.
- **P2 (Medium) — N+1 queries.** `api_people` runs `_contact_org_list` per contact (`app.py:3478-3480`); the org timeline runs two correlated subqueries per meeting row. Batch with joins/`json_agg`.
- **P3 (Medium) — Connection churn.** Each `get_db()` opens a brand-new psycopg2 connection; single requests routinely open several (e.g., import loops call `canonical_group()` per file, each with its own connection). On Vercel + Neon this is added latency per request and connection-limit pressure under concurrency. Use one connection per request (Flask `g`) and the pooled connection string.
- **P4 (Medium) — Cold-start weight.** Import-time `init_db()` (A1) plus the `anthropic` import means the first request after idle pays migration + heavy imports. Move migrations to a deploy step (or a guarded `?migrate=1` admin call) and keep the lazy `anthropic` import as-is.
- **P5 (Low) — Reliability of scheduled sync.** Good: per-job error persistence (`last_error`, `schedule_last_error`) and UI surfacing. Risky: A2 (timeout exposure), and GitHub cron itself can skip under load with nothing detecting a *missed* run (the `needs_sync` flag in the UI partially covers this — nice touch).
- **P6 (Low) — `_current_congress()` env override silently swallows bad values**, and `CURRENT_CONGRESS` must be remembered at each biennium if ever set; the date math itself is correct (verified for Jan-3 boundaries).

### 3f. Compliance & Audit Trails

**Honest scoping note:** the audit brief mentions ASIC and KYC/AML readiness. JOS is not a financial-services product and has no Australian nexus — it is a U.S. congressional-office productivity tool — so ASIC registration and KYC/AML obligations **do not apply**. The compliance surface that *does* apply:

- **F1 (Medium) — PII inventory & third-party disclosure.** The `contacts`, `entity_notes`, `asks`, and `raw_ics` tables hold third-party PII (constituents, lobbyists, colleagues: names, emails, phones, photographed business cards, meeting descriptions). Two third parties receive slices of it: Anthropic (card images) and the hosting/database provider. For a tool used in official congressional work, House rules on records handling and office data policies are the governing framework — worth a short written note in the repo: what's stored, where, who can access it, and how to purge it. There is currently **no data export or delete-all capability** and no retention policy.
- **F2 (Medium) — No audit trail for mutations.** Deletes are hard deletes everywhere (meetings cascade their tasks; orgs, contacts, asks, commitments, notes all `DELETE` with at most a `confirm()` dialog client-side). The only historical record is the `completions` table (task check-offs) — which itself detaches on task edits (B1). There is no record of *what* was deleted, when, or from where. A minimal `audit_log(ts, actor, action, entity_type, entity_id, payload)` insert in the dozen mutating endpoints — or soft-delete flags on the big four tables — would provide recoverability and accountability at trivial cost.
- **F3 (Low) — Operational logging is `print()` only**, into Vercel's short-retention function logs. Failed logins are not logged at all, so brute-force attempts (S4) are invisible. Log auth failures and destructive operations at minimum.
- **F4 (Info) — Congress.gov API terms** are respected in spirit (keyed access, modest rate, caching of details) — the detail cache and time-budgeted paging are good citizenship.

---

## 4. Recommendations & Remediation Plan

| # | Priority | Area | Finding | Recommended fix | Effort |
|---|----------|------|---------|-----------------|--------|
| 1 | **High** | Security | S1: `SECRET_KEY` insecure fallback | Refuse to start (or refuse logins) without a configured key | XS |
| 2 | **High** | Security | S2: stored XSS via `body_html` | Sanitize markdown output with `nh3`/`bleach` allow-list at import time; re-render existing rows | S |
| 3 | **High** | Data/Code | B1: task-ID rewrite on edit → FK 500s, detached history, re-import duplicates | Never mutate `tasks.id`; keep content hash as a separate import key | M |
| 4 | **High** | Data | C1: UTC "today" skews overdue/due-today/recurrence for a US user | Central `today()` helper using a configured `APP_TIMEZONE` | S |
| 5 | **High** | Data | B2: `lstrip("FU IF")` corrupts trigger text (verified) | Anchored regex prefix strip + backfill scan of stored `condition_text` | XS |
| 6 | **High** | Security | S4: no login throttling; non-constant-time secret compares | `hmac.compare_digest` everywhere; simple backoff on failed logins; log failures | S |
| 7 | **Medium** | Security | S3/S5: no CSRF hardening, no security headers, cookie flags unset | Set `SESSION_COOKIE_SECURE/SAMESITE`, add `after_request` headers + custom-header check on mutations | S |
| 8 | **Medium** | Architecture | A1: migrations on every cold start | Move `init_db()` behind an admin/deploy hook; make `schema.sql` authoritative | M |
| 9 | **Medium** | Reliability | A2: cron full bill sync vs ~10s function limit | Drive the cron through the same paged `?step=` protocol from the workflow (loop in Actions) | S |
| 10 | **Medium** | Data | C2: contact ID fragmentation, no merge | Random IDs + email unique index + merge endpoint/UI | M |
| 11 | **Medium** | Data | C3/C4: rescheduled invites orphan duplicates; only first VEVENT parsed | Derive meeting ID from UID hash only; iterate all VEVENTs | S |
| 12 | **Medium** | Data | C5: floor events hidden after Monday | Week-aware filter for `source='floor'` events | XS |
| 13 | **Medium** | Data | C6: free-text deadlines | Validate ISO at API edge; migrate `tasks.deadline` to `DATE` | M |
| 14 | **Medium** | Compliance | F2: no audit trail; hard deletes | Minimal `audit_log` table + inserts in mutating routes (or soft deletes) | M |
| 15 | **Medium** | Quality | B9: zero tests/CI | pytest for pure functions (deadline parser, `_current_congress`, recurrence, trigger parse, ICS) + a GitHub Actions check | M |
| 16 | **Medium** | Security | S6: unpinned dependencies | Pin exact versions; add `pip-audit`/Dependabot | XS |
| 17 | **Medium** | Perf | P1–P3: full-corpus loads, N+1s, connection churn | SQL-side search/filtering; `json_agg` for per-row lookups; per-request connection reuse | M–L |
| 18 | **Low** | UX/Code | B4: deadline strip shows work-week but says "next 7 days"; `completions_30d` is 7d | Align data with copy (true rolling 7 days incl. weekend) and rename keys | S |
| 19 | **Low** | UX | B5: dependencies un-removable in UI | Wire `dependency/remove` into the task drawer | S |
| 20 | **Low** | Code | B3/B10: dead endpoints, dead vars, stray `static/img/test` | Delete | XS |
| 21 | **Low** | Data | C7–C9: recurrence day-28 clamp, indirect dep cycles, shortcut `@group:` legacy, alias-map bypass, junk "Large meeting" contact | Individual small fixes | S each |
| 22 | **Low** | Compliance | F1: no PII note, export, or purge | Short DATA.md + a JSON export endpoint + delete-all | S |
| 23 | **Low** | Architecture | A3: legacy vercel.json; static via function | Modern `rewrites` config; let Vercel serve `/static` | XS |

(Effort: XS < 1h, S = hours, M = a day-ish, L = multi-day.)

**Suggested sequencing:** items 1–6 in one hardening pass (a single small PR, ~a day of work total, removes every High); items 7–9 next since they protect the same data; the data-integrity cluster (10–13) after that; testing (15) alongside any of it so fixes land with regression coverage.

---

## 5. Conclusion

JOS is a genuinely capable personal tool with a stronger-than-typical foundation: injection-safe data access, disciplined output escaping, thoughtful serverless budgeting, and an increasingly coherent CRM data model. The audit found **no SQL injection, no exposed unauthenticated data routes, and no evidence of structural unsoundness** in the schema.

It is **not production-grade software today**, and doesn't need to be for its current single-user mission — but five High findings (forgeable-session fallback, markdown XSS, task-ID rewrite breakage, UTC date skew, and the verified trigger-text corruption bug) are worth fixing promptly even for personal use, because they respectively risk total account compromise, script execution from imported files, 500s and silent history loss in the core task flow, wrong "overdue" states every evening, and quiet data corruption of saved intelligence.

If JOS is ever shared beyond one user — a second staffer, an intern, a public URL passed around the office — the Medium security items (CSRF hardening, headers, throttling, audit logging, dependency pinning) graduate to blocking requirements, and multi-tenancy would need real work (the schema already gestures at it with `user_id 'default'` in the calendar table, but nothing else is tenant-aware).

**Verdict: conditionally sound for continued personal production use; remediate the six High-priority items before adding users, more sensitive data, or any external exposure.**
