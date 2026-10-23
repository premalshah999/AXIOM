# HTTP API

Every route the application serves, what it does, who may call it, and what is wrong with the design. The last part
proposes a cleaner route map. **Renames are still not done:** the dashboard calls these paths from about seventy places,
so they ship together with the frontend change (section 5). Phase B (2026-10-20) moved every handler into
`jobpilot/api/routes/` (section 7) and deleted the four routes nothing called (section 6).

How this was produced: the route table was read from the running application object (109 routes: 54 declared in
`jobpilot/api.py`, 8 in `owner_account.py`, 9 in `mail_api.py`, 7 in `jobboard_api.py`, 31 in the three `/api/v2`
routers), every handler was read, and each path was searched for in `frontend/src` and `tests/`. Four routes were
deleted afterwards: **105 remain** (the reference tables no longer list the four deleted ones).

Related: [ARCHITECTURE.md](ARCHITECTURE.md) (what the handlers call), [KNOWLEDGE.md](KNOWLEDGE.md), [SETUP.md](SETUP.md).

## 1. Conventions

**Base and format.** JSON over HTTP on the published port (default `127.0.0.1:8080`). The OpenAPI schema and `/docs` are
disabled (`openapi_url=None`). Everything outside `/api/` is a page: `/healthz`, `/oauth/gmail/callback`, `/demo/form/{nonce}`
and the dashboard (`GET /{path}` returns `index.html`, `/assets/*` the build).

**Authentication** (`api/middleware.py`, the `security` middleware). A request to `/api/*` needs one of:

- `Authorization: Bearer <access token>` (`APP_TOKEN`, or the generated `DATA_DIR/access-token`; print it with
  `jobpilot token`). Constant-time comparison. Used by scripts and the MCP server.
- the `jp_session` cookie (`api/sessions.py`): HttpOnly, `SameSite=Strict`, `Secure` when `SECURE_COOKIES`, issued by
  `POST /api/login` (token), `POST /api/account/login` (e-mail and password), sign-up and a password reset. The signed
  payload is `{s: "applicant", v: <session version>, k: <keep>}`; the signing key is the access token. A session ends
  after 24 hours without use, or 30 days with "Keep me signed in" (`keep: true`); a cookie in use is re-signed at most
  every ten minutes (sliding expiry). Raising the session version (`owner_sessions` setting) ends every older cookie:
  a password change (except the browser that changed it), a reset, `DELETE /api/account/sessions` and
  `jobpilot reset-password`. Cookies signed before 2026-10-22 hold the bare constant and work until the first version
  change. A cookie that no longer opens the API is answered "Your session ended. Sign in again."

Public (no credential): `POST /api/login`, `POST /api/logout`, `GET /api/account`, `POST /api/account/signup` (only on a
fresh installation), `POST /api/account/login`, `POST /api/account/reset`, and outside `/api/`: `/healthz`,
`/oauth/gmail/callback` (bound to a state value and a cookie), `/demo/form/{nonce}` (only with `ENABLE_DEMO`, and only
for a valid demo nonce), the dashboard files.

**Sign-in throttle.** One limiter (`api/sessions.Throttle`) counts failed credential checks per client address: the
token, the password, a recovery code, a reset, the current password in Setup. After ten in a minute every one of them is
429 with `Retry-After`; refusals say how many tries are left once five or fewer remain.

**Browser safety.** A request with an `Origin` header must equal `BASE_URL` or it is answered 403. `POST`, `PUT` and `PATCH`
must send `application/json` or `multipart/form-data` (415 otherwise). Responses under `/api/` carry
`Cache-Control: no-store`; all responses carry `X-Content-Type-Options`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`.
There is no CSRF token; the protection is the cookie policy, the Origin check and the JSON-only content types.

**Errors.** Body `{"detail": "<text>"}`. A `ValueError` anywhere in a handler is 400 (text up to 1,000 characters), a
`RuntimeError` is 409 (500 characters), `HTTPException` keeps its status, schema errors are FastAPI's 422 with a list
`detail`. `QueueBlocked` (the admission refusal) is a `ValueError`, so its `code`, `outcome` and `not_before` are lost in
the response except where a handler turns a deferral into 202.

**Pagination.** Only the `/api/v2` lists page: `page` (from 1), `size` (≤ 100), `q`; the response carries `items`,
`total`, `page`, `size`.

**Column "UI".** `✔` the dashboard calls it, `–` it does not. **Tests:** before Phase B only 20 of the 109 routes were
exercised through the HTTP test client; every router now has an HTTP test module (section 4.5).

## 2. Route reference

### 2.1 Health and sign-in

| Method | Path | Auth | Purpose | Request | Response | UI |
|---|---|---|---|---|---|---|
| GET | `/healthz` | public | Liveness; fails if the database does not answer | – | `{status:"ok", version}` | Docker |
| POST | `/api/login` | public, throttled | Exchange the access token for a session cookie | `{token, keep?}` | `{ok:true}` + cookie; 401 wrong token; 429 after 10 failures a minute | ✔ |
| POST | `/api/logout` | public | Clear this browser's cookie | – | `{ok:true}` | ✔ |
| GET | `/api/account` | public | Does an owner account exist; may one be created, and why not (`installation`: `fresh`, `has_data`, `has_account`); am I signed in | – | `{exists, signup_allowed, signed_in, installation, password_rules, [name, email, onboarding, password_changed_at, recovery_codes:{left,total,created_at}, session:{via,keep,idle_hours}]}` | ✔ |
| POST | `/api/account/signup` | public if the installation is fresh (no account, no profile identity, no jobs), else session | Create the owner account, seed the profile name and e-mail, generate ten recovery codes | `{name, email, password, keep?}` | `{ok, onboarding, recovery_codes}` (shown once; only hashes are stored) + cookie; 400 one sentence per problem; 409 exists; 403 not fresh | ✔ |
| POST | `/api/account/login` | public, throttled | E-mail and password sign-in | `{email, password, keep?}` | `{ok, onboarding}` + cookie; 401 unknown e-mail or wrong password; 409 no password set | ✔ |
| POST | `/api/account/reset` | public, throttled | Forgot password: a recovery code (each works once, never spent on a refused password) or the access token proves ownership; ends every session | `{method: recovery_code or access_token, secret, password, keep?}` | `{ok, email, onboarding, recovery_codes_left}` + cookie; 401 used or invalid code, wrong token; 409 no password set | ✔ |
| POST | `/api/account/password` | session, throttled | Change the owner password; ends every other session | `{current, new}` | `{ok, other_sessions_ended}` + a new cookie for this browser; 400 wrong current password | ✔ |
| POST | `/api/account/recovery-codes` | session, throttled | Replace the recovery codes (asks the password again) | `{password}` | `{codes, created_at, total}`; 400 wrong password | ✔ |
| DELETE | `/api/account/sessions` | session | Sign out everywhere: raise the session version (the access token keeps working) | – | `{ok:true}`, cookie cleared | ✔ |
| GET | `/api/onboarding` | session | Setup-guide progress | – | `{completed, step, ...}` | – |
| PUT | `/api/onboarding` | session | Save progress | `{step, completed}` | state | ✔ |

### 2.2 Dashboard state and settings

| Method | Path | Purpose | Request | Response | UI |
|---|---|---|---|---|---|
| GET | `/api/snapshot` | Everything the dashboard shows, in one poll (answered from a cache for `SNAPSHOT_CACHE_SECONDS`, refreshed by a background thread) | `?include_sources=true\|false` | `counts` (exact, over every job's latest run: Pipeline `groups`, latest-run `states` incl. `pending:waiting`, `pipeline`, `jobs`, `discovered`), `jobs` and `runs` (only the live rows: queued, applying, waiting for an email or an answer, open questions, the 20 latest outcomes; no description or packet), `job_controls`, `resumes`, `reviews` (open questions), `sources`, `profile` (without the server-owned answer stores), `control`, `application_resume_policy`, `telegram_health`, `telegram_workers`, `autopilot`, `autopilot_runtime`, `discovery_settings`, `scheduler_health`, `scheduler_heartbeat`, `engine_version`, `ats`, `stats`, `config` | ✔ every 6 s |
| GET | `/api/runtime-health` | Engine version, scheduler health, outbox backlog, worker capacity, model health, production target | – | `{engine_version, scheduler, heartbeat, outbox_pending, workers, application_workers, models, production_target}` | ✔ |
| GET | `/api/control` | Global pause and automatic-submission switches | – | `{paused, auto_submit}` | – (read from the snapshot) |
| PUT | `/api/control` | Set them; resumes queued runs when unpaused | `{paused, auto_submit}` | same | ✔ |
| GET | `/api/limits` | Per-job token cap and per-employer application limit | – | `{job_token_cap, company_limit}` | ✔ |
| PUT | `/api/limits` | Set them | `{job_token_cap: 50k–2M, company_limit: 1–25}` | same | ✔ |
| GET | `/api/agents` | Which model providers and helper switches are available (never keys) | – | summary | ✔ |
| PUT | `/api/agents` | Choose providers per role, switch helpers | `AgentSettings` | summary; 422 on a bad choice | ✔ |
| GET | `/api/successful-applications` | The ledger: one confirmed run per job | – | `{applications:[{job_id, run_id, title, company, url, applied_at, ats, website_confirmed, email_confirmed, existing_application}]}` | ✔ |
| GET | `/api/archive` | Archived failed applications | – | `{applications, log_bytes}` | ✔ |
| POST | `/api/archive/failed` | Archive all failed applications | – | summary | ✔ |
| GET | `/api/archive/{job_id}/log` | Download one archived run log | – | gzip JSON lines | ✔ (link) |

### 2.3 Profile, resumes, knowledge (applicant data)

| Method | Path | Purpose | Request | Response | UI |
|---|---|---|---|---|---|
| GET | `/api/profile` | The profile without the server-owned answer stores | – | `Profile` | – (snapshot) |
| PUT | `/api/profile` | Save the profile; keys this version does not know and the server-owned stores are kept | `Profile` | `{saved:true}` | ✔ |
| POST | `/api/profile/extract` | Propose structured history from an uploaded resume (nothing saved) | `?resume_id=` | `ProfileExtract` | ✔ |
| POST | `/api/profile/organize` | Turn uploaded evidence into saved knowledge notes | – | `{saved, ...}` | ✔ |
| GET | `/api/resumes` | Uploaded resumes without text | – | `[{id, name, roles, created_at}]` | – (snapshot) |
| POST | `/api/resumes` | Upload a PDF (≤ 5 MB, ≤ 20 pages, text-based, ≤ 10 variants) | multipart `file, name, roles` | resume record | ✔ |
| GET | `/api/resumes/{id}/download` | The stored PDF | – | `application/pdf` | ✔ (link) |
| DELETE | `/api/resumes/{id}` | Remove an unused variant (the file is kept for receipts) | – | `{deleted:true}`; 400 if an active run uses it | ✔ |
| GET | `/api/knowledge` | **Legacy** reviewed answers | `?q=` | `{entries}` (≤ 500) | ✔ |
| POST | `/api/knowledge` | **Legacy** save an answer | `KnowledgeInput` | entry | ✔ |
| DELETE | `/api/knowledge/{entry_id}` | **Legacy** forget an answer | – | `{deleted:true}` | ✔ |
| GET | `/api/v2/knowledge/facts` | Facts and confirmed answers (syncs the profile first) | `category, layer, scope, employer_id, q, page, size` | `{items, total, page, size, store_version}` | ✔ |
| POST | `/api/v2/knowledge/facts` | Save a confirmed answer (versioned) | `SaveAnswer` | `FactSaved`; 409 version conflict | – |
| POST | `/api/v2/knowledge/facts/{id}/retire` | Retire a fact | `{expected_version?}` | `{store_version}`; 409 | ✔ |
| GET | `/api/v2/knowledge/summary` | Counts per category and layer | – | `{categories, employers, store_version}` | ✔ |
| GET | `/api/v2/knowledge/employers` | Employer identities with aliases and memory counts | `q, page, size` | page | ✔ |
| GET | `/api/v2/knowledge/attestations` | Broad statements the applicant can confirm once | – | `{items}` | ✔ |
| POST | `/api/v2/knowledge/attestations/{id}` | Confirm one | `{statement}` | `{confirmed}`; 422 | ✔ |
| POST | `/api/v2/knowledge/retrieve` | Debug: the evidence packet for a question | `RetrieveInput` | `EvidencePacket` | – |
| POST | `/api/v2/knowledge/migrate` | Mirror the profile into `kn_facts` | `?dry_run=true` | report | – |
| GET | `/api/v2/resumes/master` | The master resume and its approval state | – | status | ✔ |
| PUT | `/api/v2/resumes/master` | Save the master document | `{doc, style}` | status | ✔ |
| POST | `/api/v2/resumes/master/import` | Build the master from an uploaded resume | – | status | ✔ |
| POST | `/api/v2/resumes/master/{id}/approve` | Approve it | `{accept_differences}` | status | ✔ |
| GET | `/api/v2/resumes/evidence` | The material tailoring may use (no contact values) | – | inventory | ✔ |
| POST | `/api/v2/resumes/prepare` | Tailor a resume for a job | `{job_id, source_resume_id?}` | artifact | ✔ |
| GET | `/api/v2/resumes/artifacts` | Tailored variants | `status, job_id, page, size` | page | ✔ |
| GET | `/api/v2/resumes/artifacts/{id}` | One variant | – | artifact | – |
| POST | `/api/v2/resumes/artifacts/{id}/cancel` | Cancel a preparation | – | artifact | – |
| GET | `/api/v2/resumes/artifacts/{id}/pdf` | The PDF | – | `application/pdf` | ✔ (link) |
| GET | `/api/v2/resumes/editor/document` | Editor document of an artifact or the master | `artifact_id, master` | doc and style | ✔ |
| GET | `/api/v2/resumes/editor/templates` | Style presets | – | list | ✔ |
| POST | `/api/v2/resumes/editor/render` | Document to LaTeX source | `EditorInput` | source | ✔ |
| POST | `/api/v2/resumes/editor/compile` | Compile a preview PDF | `EditorInput` | `application/pdf` | ✔ |
| POST | `/api/v2/resumes/editor/save` | Save an edited variant | `EditorInput` | artifact | ✔ |

### 2.4 Jobs, runs and questions

| Method | Path | Purpose | Request | Response | UI |
|---|---|---|---|---|---|
| POST | `/api/jobs` | Import a job by URL; rank and queue it if autonomous mode is on | `JobInput {url, title?, company?, location?, description?}` | `{job, created, [run \| notice, preparation]}` | ✔ |
| GET | `/api/jobs` | Jobs › Pipeline, one page: started or waiting applications, attention first (`pipeline.py`) | `?group=all\|active\|needs\|repair\|unconfirmed\|paused\|done\|stopped\|new&q&page&size≤100` | `{rows: [{job, run, attempts, control, request, reviews}], total, page, size, pages}` | ✔ |
| POST | `/api/jobs/apply-many` | The owner's Apply for up to 50 jobs, each through `owner_apply.apply` (fit filters skipped, every duplicate guard kept) | `{job_ids}` | `{results: [{job_id, outcome: queued\|waiting\|preparing\|already\|refused, state, message, code?, company, title}], summary}` | ✔ |
| GET | `/api/jobs/{id}` | One job, its latest run, its control state and a waiting Apply | – | `{job, latest_run, job_control, request}` | ✔ |
| POST | `/api/jobs/{id}/rank` | Rank one job against the profile | – | `{score, eligible, resume_id, reason, uncertainties}` | ✔ |
| POST | `/api/jobs/{id}/apply` | **One click**: approve if needed, queue, or remember a deferred start | – | `{job_id, state, message, [run_id, deferred, preparing]}` | ✔ |
| POST | `/api/jobs/{id}/queue` | Queue with explicit options (dry run or submit) | `QueueInput {mode, resume_id?, resume_artifact_id?, controller_policy?}` | run record; 202 `{preparation, message}` while a resume is prepared; 202 deferred start | ✔ |
| POST | `/api/jobs/{id}/pause` | Pause (saves the draft) | – | `{job_id, state, message}` | ✔ |
| POST | `/api/jobs/{id}/resume` | Resume or retry; a deferral becomes 202 | `QueueInput?` | same | ✔ |
| DELETE | `/api/jobs/{id}` | Remove from the active pipeline (history and submission guards are kept) | – | same | ✔ |
| GET | `/api/runs` | Every attempt of one job, newest first, without packets | `?job_id` | `{runs}` | ✔ |
| GET | `/api/runs/{id}` | A run, its events, the answers it used, whether a screenshot exists, the failure report | – | `{run, events, answers, screenshot, demo_receipt, failure_report}` | ✔ |
| GET | `/api/runs/{id}/screenshot` | The final page | – | `image/png` | ✔ |
| POST | `/api/runs/{id}/reconcile` | The applicant states whether an uncertain submission went through | `{submitted, evidence ≥ 5 chars}` | `{reconciled:true}` | ✔ |
| POST | `/api/runs/{id}/check-email` | Look for the employer's acknowledgement now | – | receipt-check result | ✔ |
| POST | `/api/runs/{id}/verify` | Resume a retained verification draft | – | result; 409 none retained | – (documented operator route) |
| POST | `/api/runs/{id}/cancel` | Cancel a *queued* run | – | `{cancelled:true}`; 400 otherwise | – |
| POST | `/api/reviews/{id}` | Answer a question a run is waiting on; saves the answer and resumes the run | `{answer}` | resume result | ✔ |

### 2.5 Sources, job board, autopilot

| Method | Path | Purpose | Request | Response | UI |
|---|---|---|---|---|---|
| POST | `/api/sources` | Add a source (URL checked as public) | `SourceInput {name, kind, url, cron, enabled, auto_queue}` | source | ✔ |
| PUT | `/api/sources/{id}` | Edit | `SourceInput` | `{saved:true}` | ✔ |
| DELETE | `/api/sources/{id}` | Delete (200 even when missing) | – | `{deleted:true}` | ✔ |
| POST | `/api/sources/{id}/discover` | Scan now | – | `{queued:true}` | ✔ |
| GET | `/api/job-board` | Board settings, managed sources, fresh jobs, search provider. With `page`: Jobs › Discovered, only jobs not applied to yet, filtered and paged here | `?include_sources&page&size&q&ats&level&fit=all\|eligible\|uncertain\|unranked\|below` | board snapshot (+ `total`, `fresh_total`, `fits`, `platforms`, `pages`, `selectable_ids` ≤500, `selectable_total` when paged) | ✔ |
| PUT | `/api/job-board/settings` | Board settings; turning on auto-apply starts a batch | `BoardSettings {enabled, interval_hours, early_career_only, web_search, auto_apply, batch_target, minimum_auto_fit, max_age_hours}` | `{saved, sources}` | ✔ |
| POST | `/api/job-board/sources` | Add a **managed** source | `SourceInput` | `{added, id}`; 409 if already a plain source | ✔ |
| POST | `/api/job-board/scrape` | Scan all managed sources now | – | `{queued_sources, total_sources}` | ✔ |
| POST | `/api/job-board/apply` | Rank and queue up to 50 listed jobs (the dashboard uses `POST /api/jobs/apply-many`) | `{job_ids}` | `{queued, skipped}` | – |
| POST | `/api/job-board/autopilot/start` | Start an unattended batch | `{target 1–500}` | start result and runtime | ✔ |
| GET | `/api/v2/discovery/status` | Pipeline readiness and the bottleneck (one answer per 20 s) | – | status | – |
| GET | `/api/v2/discovery/feeds` | Last public-feed sweep | – | state | ✔ |
| POST | `/api/v2/discovery/feeds/refresh` | Start a sweep (an in-process task) | – | `{state}` | ✔ |
| GET | `/api/v2/discovery/due` | Sources due now | – | list | – |
| GET | `/api/v2/discovery/ready` | Ready applications | `limit ≤ 200` | `{items}` | – |
| GET | `/api/v2/discovery/requisitions` | Canonical requisitions | `page, size` | page | – |
| GET | `/api/v2/discovery/sources/health` | Failing sources | – | `{items}` | – |

### 2.6 Mail, employer accounts, sessions, demo

| Method | Path | Purpose | Request | Response | UI |
|---|---|---|---|---|---|
| GET | `/api/mail` | Mailboxes, rules, open challenges | – | summary | ✔ |
| POST | `/api/mail/connect` | Start Google OAuth | – | `{authorization_url}` + binding cookie | ✔ |
| GET | `/oauth/gmail/callback` | OAuth return; redirects to `/?gmail=connected\|connection_failed` | `state, code` | 303 | browser |
| POST | `/api/mail/imap` | Connect Gmail with an app password (verified first, sealed in the vault) | `{email, app_password}` | mailbox | ✔ |
| POST | `/api/mail/{id}/check` | Test a mailbox | – | `{connected, email}` | ✔ |
| DELETE | `/api/mail/{id}` | Disconnect | – | result | ✔ |
| POST | `/api/mail-rules` | Allow an employer's sender domains and link origins | `RuleInput` | rule | ✔ |
| DELETE | `/api/mail-rules/{id}` | Remove (cancels its live challenges) | – | `{removed:true}` | ✔ |
| POST | `/api/mail-challenges/{id}/cancel` | Cancel a waiting code or link | – | `{cancelled:true}` | ✔ |
| POST | `/api/inbox/check` | Test the `.env`-configured IMAP login | – | `{connected, folder, host}` | ✔ |
| GET | `/api/accounts` | Employer account records in plain words (`accounts/overview.py`: `status` active, verification, password_unknown, reset_pending, locked, created, signing_in, registered or unknown, with `label`, `detail`, `attention`, `login_email`, `can_retry_verification`), needs-you first, and the shared-password policy problems; never a password or sealed value | – | `{accounts, password_problems}` | ✔ |
| PUT | `/api/accounts/{id}/password` | "I reset it myself": seal the owner's new password for that employer as state `password_reset` with `owner_set_at`; reset and rejection markers are cleared | `{password}` | `{ok, account}`; 404 unknown record | ✔ |
| POST | `/api/accounts/{id}/verify` | Restart the employer's retained verification draft (`Service.resume_mail_wait(restart_verification=True)`) | – | `{ok, run_id, state}`; 409 not waiting or nothing to restart | ✔ |
| DELETE | `/api/accounts/{id}` | Forget one record (`employer_account:*` only) | – | `{deleted:true}` | ✔ |
| POST | `/api/sessions` | Import a Playwright storage state for one employer host | multipart `url, file ≤ 2 MB` | `{saved, host}` | ✔ |
| POST | `/api/demo` | Queue the synthetic application (needs `ENABLE_DEMO` and workers) | `QueueInput` | run record | – (`jobpilot verify`) |
| GET, POST | `/demo/form/{nonce}` | The synthetic employer form and its receipt | form | HTML | – |
| GET | `/{path}` | The dashboard (`index.html`) | – | HTML | browser |

## 3. What the review found, summarized

| Kind | Count | Where |
|---|---|---|
| Duplicate routes | 10 groups | 4.1 |
| Routes with no dashboard caller | 15 when reviewed; 4 deleted, 11 kept | 4.2 |
| Inconsistencies | 9 | 4.3 |
| Security findings | 8 (none critical for a single-user installation bound to localhost) | 4.4 |
| Routes covered by an HTTP test | 20 of 109 when reviewed; see 4.5 | 4.5 |

## 4. Findings

### 4.1 Duplicates

| # | Duplicate | Routes | Recommendation |
|---|---|---|---|
| D1 | Five ways to start an application (two were deleted in Phase B: `/approve`, `/batch-queue`) | `POST /api/jobs/{id}/apply`, `/queue`, `/api/job-board/apply` | One service function behind two routes: `POST /api/jobs/{id}/apply` (optional body takes the `/queue` options) and `POST /api/jobs/apply` (`job_ids`). Delete `/approve` and `/batch-queue` |
| D2 | Two ways to add a source | `POST /api/sources`, `POST /api/job-board/sources` | One route with `managed: bool` |
| D3 | Board information in four places | `GET /api/job-board`, (`/api/job-board/search`, deleted), the snapshot's `discovery_settings`/`sources`, `GET /api/v2/discovery/status` and `/feeds` | One `GET /api/discovery` |
| D4 | Two knowledge stores and APIs | `/api/knowledge` (profile `reviewed_answers`) versus `/api/v2/knowledge/facts` (`kn_facts`). The dashboard uses both | `kn_facts` only; move the "saved answers" screen to v2, then delete v1 |
| D5 | Profile and resumes read twice | `GET /api/profile` and `GET /api/resumes` versus the same data inside `GET /api/snapshot` | Keep the snapshot for the poll; drop the standalone GETs the UI does not use |
| D6 | Two mailbox tests | `POST /api/mail/{id}/check`, `POST /api/inbox/check` | The `.env` mailbox should appear as a mailbox in `GET /api/mail`; one check route |
| D7 | Two sign-ins | `POST /api/login` (token), `POST /api/account/login` (password) with separate rate limiters | One `POST /api/session` with `{token}` or `{email, password}` and one limiter |
| D8 | Health in three places | `/healthz`, `/api/runtime-health`, the snapshot's `scheduler_*` and `engine_version` | Keep `/healthz` (public, minimal) and one authenticated detail route |
| D9 | Two Gmail connections | `POST /api/mail/connect` (OAuth), `POST /api/mail/imap` | One `POST /api/mail/mailboxes` with `kind` |
| D10 | Control state read twice | `GET /api/control` and the snapshot's `control` | Remove `GET /api/control` if the UI keeps reading the snapshot |

### 4.2 Routes the dashboard does not call

| Route | Also unused by tests? | Recommendation |
|---|---|---|
| `POST /api/runs/{id}/cancel` | yes | Keep and use: a queued run cannot be cancelled from the UI; or delete |
| `POST /api/runs/{id}/verify` | n/a (service-level tests) | Keep: documented operator route in DEVELOPER.md |
| `GET /api/v2/discovery/due`, `/ready`, `/requisitions`, `/sources/health` | yes | Keep as operator diagnostics under `/api/admin/discovery/…`, or build the screen they were made for |
| `POST /api/v2/knowledge/facts`, `/retrieve`, `/migrate` | yes | Keep as operator routes; `facts` is the write path the v1 screen should move to |
| `GET /api/v2/resumes/artifacts/{id}`, `POST …/{id}/cancel` | yes | Keep: needed once the UI cancels a preparation |
| `GET /api/onboarding`, `GET /api/profile`, `GET /api/resumes`, `GET /api/control` | partly | Read through the snapshot; delete or keep for scripts |

`GET /api/preflight`, `POST /api/jobs/{id}/approve`, `POST /api/jobs/batch-queue` and `GET /api/job-board/search` were also in this table and were deleted in Phase B (section 6). The ones above stay.

### 4.3 Inconsistencies

| # | Finding | Evidence | Fix |
|---|---|---|---|
| I1 | `/api/v2` exists without a v1, and the v2 routers behave differently (paged lists, 422 for `ValueError`, 409 for version conflicts) from the rest (400, no paging) | `features/*/router.py` versus `api.py` | One prefix `/api`; one error table (4.3 I3) |
| I2 | Naming mixes nouns and verbs and splits one resource over siblings: `/jobs/{id}/rank`, `/pause`, `/apply`; `/runs/{id}/check-email`; `/profile/organize`; `/sources/{id}/discover`; `/job-board/scrape`; `/archive/failed`; `/mail`, `/mail-rules`, `/mail-challenges`; `/account` versus `/accounts` (the owner versus employer records) | route table | Section 5 |
| I3 | Status codes: 400, 404, 409, 422 are used for the same class of problem. `HTTPException(404)` has no detail while `ValueError("Job not found")` is a 400; `DELETE /api/sources/{id}` answers 200 for a missing id while `DELETE /api/resumes/{id}` and `/api/accounts/{id}` answer 404; `QueueBlocked` loses `code`, `outcome` and `not_before`, so `frontend/src/api.ts: isPreparing` recognizes "preparing a tailored resume" by **regular expression on the message** | `api.py`, `admission.py` | `{detail, code, retry_at?}` with a stable `code`; the client switches on `code` |
| I4 | **GET with writes:** `GET /api/v2/knowledge/facts` and `/summary` call `migration.sync`, which writes `kn_facts` and recategorizes; `GET /api/snapshot` starts a refresh thread | `features/knowledge/router.py` | Sync on profile save or in a background task; GETs read only |
| I5 | Seven acknowledgement shapes: `{saved:true}`, `{deleted:true}`, `{removed:true}`, `{cancelled:true}`, `{queued:true}`, `{ok:true}`, `{reconciled:true}` | many | Return the resource, or `204` |
| I6 | The set of public account paths (`owner_account.PUBLIC_PATHS`) lives in a different module from the middleware that uses it; `POST /api/logout` needs a valid session, so a client with an expired one gets 401 | `api.py`, `owner_account.py` | One route table with an `auth` attribute. **2026-10-22:** sign-out is public |
| I7 | Lists: only v2 pages. `GET /api/knowledge` returns up to 500; the snapshot silently showed only the **newest 1,000 jobs and runs** | `api.py` `build_snapshot` | Done 2026-10-22: `GET /api/jobs` (paged Pipeline), `GET /api/runs?job_id`, paged `GET /api/job-board`; the snapshot keeps exact counters, live rows and settings |
| I8 | `features/discovery/router.py` assumes `min(6, workers)` application owners instead of the configured `NORMAL_WORKERS`, so readiness and backpressure ignore a different topology; it also defines its own `policy_for` | `owners = min(6, …)` | Use `config.topology` |
| I9 | `POST /api/v2/discovery/feeds/refresh` runs the sweep as an in-process task, while the scheduled sweep goes through the durable `discovery` queue; a restart drops the manual one | `router.py` versus `workers.enqueue_feed_if_due` | Enqueue on the durable queue |

### 4.4 Security

The model is a single owner on a private machine; findings are ranked for a self-hosted installation that may be shared
with friends and family behind a reverse proxy.

| # | Severity | Finding | Recommendation |
|---|---|---|---|
| S1 | Medium | `POST /api/account/signup` is **unauthenticated on a fresh installation**: whoever reaches a new instance first can claim it. Safe with the default bind (`127.0.0.1`), unsafe with `APP_BIND=0.0.0.0` | Require the access token for sign-up whenever the bind is not loopback; show the first-run page only after the token step |
| S2 | Medium | The session is a signed constant with no identifier: no server-side revocation, `POST /api/logout` clears the browser only, a changed password does not end other sessions, and the signing key is the bearer token itself | Random session ids stored server-side (revoked on logout and password change); a separate signing secret. **2026-10-22:** cookies carry a session version; a password change, a reset and "Sign out everywhere" end older cookies. Revoking one browser and a separate signing secret remain |
| S3 | Low–Medium | Two independent in-memory rate limiters (token login and account routes), per client address, reset on restart; behind a reverse proxy every visitor shares one address, so one attacker can lock the owner out | One shared limiter; use the forwarded address only from a configured trusted proxy; back-off. **2026-10-22:** one shared limiter of failures; the trusted-proxy address and back-off remain |
| S4 | Low | `POST /api/sessions` stores employer session cookies as **plaintext JSON files** (mode 0600) while account passwords are encrypted in the vault | Encrypt with the vault key |
| S5 | Low | Raw exception text reaches clients: `ValueError`/`RuntimeError` messages (≤ 1,000 characters) are returned verbatim | Typed errors with fixed messages; log the rest |
| S6 | Low | No CSRF token. Protection is `SameSite=Strict`, the `Origin` check (only when the header is present) and JSON-only content types | Acceptable; add a header check for non-browser clients if they are ever allowed cookies |
| S7 | Low | The access token never rotates, is printed by a command and stored in `access-token` (0600) | `POST /api/session/rotate` that regenerates it and ends sessions |
| S8 | Info | `ENABLE_DEMO` defaults to `true`: `/demo/form/{nonce}` is reachable without credentials (nonce-guarded) and `POST /api/demo` creates demo runs | Default `false` outside `jobpilot verify` and the first-run tour |

Good as built: every user-supplied URL (`/api/jobs`, `/api/sources`, `/api/job-board/sources`, `/api/sessions`) passes the
SSRF guard `network.public_url` (public addresses only, redirects revalidated, 4 MB response cap); uploads are bounded and
type-checked (PDF magic bytes, 5 MB, 20 pages; session JSON 2 MB, cookies limited to the host); passwords use scrypt and
are never returned; credentials and mail bodies never appear in responses; `Cache-Control: no-store` on every API response;
the Origin check and content-type rule; file downloads resolve through database ids, so there is no path traversal.

### 4.5 Test coverage

When this review was written, the HTTP test client reached only `GET /api/account`, `POST /api/account/{signup,login,password}`,
`GET|PUT /api/agents`, `GET|PUT /api/control`, `GET|PUT /api/limits`, `GET /api/mail`, `POST /api/mail/imap`,
`POST|DELETE /api/mail/{id}…`, `PUT /api/onboarding`, `GET|PUT /api/profile`, `POST /api/resumes`, `POST /api/jobs/{id}/rank`
and `GET /api/snapshot`: 20 of 109 routes.

Phase B added a contract test module for each router, one file per area (`tests/test_http_*.py`; shared rows in
`tests/support_http.py`): health, session, request security (sign-in, Origin, content type, headers, error mapping), jobs, runs,
the ledger and archive, profile and resumes, settings, sources, legacy knowledge and employer accounts and sessions, the demo
and dashboard fallback, mail rules and OAuth, the job board and the three `/api/v2` routers. A literal-path scan of the
tests finds 94 of the 105 remaining routes called over HTTP. Not yet called over HTTP:
`POST /api/v2/discovery/feeds/refresh` and the `/api/v2/resumes` write routes (`PUT master`, `master/import`, `master/{id}/approve`, `prepare`, `artifacts/{id}/cancel`, `artifacts/{id}/pdf`, `editor/document`, `editor/render`, `editor/compile`, `editor/save`), which need the LaTeX toolchain or a model. Add them before renaming anything: the tests are what makes the rename in section 5 safe.

## 5. Proposed route design

**Principles.**

1. One prefix, `/api`. The `v2` routers move to their resource names.
2. Collections are plural nouns; an action that is not create, read, update or delete is `POST /{resource}/{id}/{verb}`.
3. One way to do each thing (D1–D9). Operator and diagnostic routes live under `/api/admin/…`.
4. One error body, `{detail, code, retry_at?}`, and one status table: 400 invalid request, 401 not signed in, 403 not
   allowed, 404 unknown id, 409 conflict with the current state (version, already running), 422 schema error,
   429 throttled, 202 accepted but deferred. The client switches on `code`, never on message text.
5. GET is safe: no writes, no migration, no sync.
6. Every list pages the same way (`page`, `size`, `q`, `total`), and the dashboard's big poll shrinks to counters and
   settings while `GET /api/jobs` and `GET /api/runs` serve the tables.
7. Generate the OpenAPI schema in CI (`openapi_url` stays off in production), publish it as an artifact, and generate the
   frontend client types from it, so the contract is checked by the build.

**Route map** (current → proposed). Priority: **P1** fixes a duplicate, a dead route or a security finding; **P2** is naming.

| Current | Proposed | P |
|---|---|---|
| `POST /api/login`, `POST /api/account/login` | `POST /api/session` `{token}` or `{email, password}` | P1 (S3, D7) |
| `POST /api/logout` | `DELETE /api/session` (server-side revocation) | P1 (S2) |
| `GET /api/account`, `POST /api/account/signup`, `POST /api/account/password` | `GET /api/owner`, `POST /api/owner`, `PUT /api/owner/password` | P2 |
| `GET`, `PUT /api/onboarding` | `GET`, `PUT /api/owner/onboarding` | P2 |
| `GET /api/snapshot` | `GET /api/state` (counters, settings, open questions) | P1 (I7) |
| – | `GET /api/jobs?status&q&page&size`, `GET /api/runs?job_id&state&page&size`, `GET /api/reviews` | P1 (I7) |
| `GET /api/runtime-health` | `GET /api/health` (authenticated detail); `/healthz` unchanged | P2 |
| `GET`, `PUT` on `/api/control`, `/api/limits`, `/api/agents` | `GET`, `PUT` on `/api/settings/control`, `/settings/limits`, `/settings/agents` | P2 |
| `GET /api/job-board`, `/job-board/search`, `PUT /job-board/settings` | `GET /api/discovery`, `PUT /api/settings/discovery` | P1 (D3) |
| `POST /api/job-board/sources` | `POST /api/sources` with `managed` | P1 (D2) |
| `POST /api/job-board/scrape`, `POST /api/sources/{id}/discover` | `POST /api/scans`, `POST /api/sources/{id}/scan` | P2 |
| `POST /api/job-board/autopilot/start` | `POST /api/autopilot/batches` | P2 |
| `/api/v2/discovery/status`, `/feeds`, `/feeds/refresh` | `/api/discovery/status`, `/api/discovery/feed`, `POST /api/discovery/feed/sweeps` (durable queue, I9) | P1 |
| `/api/v2/discovery/due`, `/ready`, `/requisitions`, `/sources/health` | `/api/admin/discovery/…` | P2 |
| `POST /api/jobs/{id}/apply`, `/queue`, `/approve`, `POST /api/jobs/batch-queue`, `POST /api/job-board/apply` | `POST /api/jobs/{id}/apply` (optional options) and `POST /api/jobs/apply` (`job_ids`) | P1 (D1) |
| `POST /api/jobs/{id}/rank` | unchanged (or `/ranking`) | – |
| `POST /api/runs/{id}/check-email` | `POST /api/runs/{id}/receipt-check` | P2 |
| `POST /api/reviews/{id}` | `POST /api/reviews/{id}/answer` | P2 |
| `/api/knowledge` (v1) | removed; `/api/v2/knowledge/*` becomes `/api/knowledge/*` (`POST /facts/{id}/retire` stays, it carries `expected_version`; `migrate` and `retrieve` move to `/api/admin/knowledge/`) | P1 (D4) |
| `/api/v2/resumes/*` | `/api/resumes/masters`, `/artifacts`, `/editor`, `/evidence`, `/prepare` (drop `v2`); uploaded resumes stay at `/api/resumes` | P2 |
| `GET /api/resumes/{id}/download` | `GET /api/resumes/{id}/file` | P2 |
| `POST /api/mail/connect`, `POST /api/mail/imap`, `POST /api/inbox/check`, `/api/mail/{id}…` | `POST /api/mail/mailboxes` (`kind`), `POST /api/mail/mailboxes/{id}/check`, `DELETE /api/mail/mailboxes/{id}` (the `.env` mailbox listed too) | P1 (D6, D9) |
| `/api/mail-rules`, `/api/mail-challenges/{id}/cancel` | `/api/mail/rules`, `/api/mail/challenges/{id}/cancel` | P2 |
| `/api/accounts` | `/api/employer-accounts` (no clash with the owner's account) | P2 |
| `POST /api/sessions` | `POST /api/browser-sessions` (stored encrypted, S4) | P1 |
| `GET /api/preflight`, `POST /api/jobs/{id}/approve`, `POST /api/jobs/batch-queue`, `GET /api/job-board/search` | deleted (done, Phase B) | P1 |
| `/oauth/gmail/callback`, `/demo/form/{nonce}`, `/healthz`, `/{path}` | unchanged (external contracts) | – |

**How to get there without breaking the dashboard.**

1. Add the contract tests (section 4.5) against the current routes.
2. Introduce `frontend/src/client.ts`: one typed function per operation (`listJobs`, `applyToJob`, …). Today paths are
   string literals in components; after this step a route rename touches one file.
3. Add each new route as an alias that calls the existing handler; log hits on the old paths; switch the client; run for one
   release; delete the old routes. Behaviour changes (error bodies, sessions, rate limiting, S1–S4) ship separately from renames.
4. Generate the schema and the client types in CI.

## 6. Changes made (Phase B, 2026-10-20)

- **Four routes deleted**, after a search of `frontend/src`, `jobpilot`, `tests`, `scripts`, `DEVELOPER.md` and the operator
  helpers found no caller: `GET /api/preflight`, `POST /api/jobs/{id}/approve`, `POST /api/jobs/batch-queue`,
  `GET /api/job-board/search`. `Service.preflight_questions`, `Service.queue_batch` and the `Approval` and `BatchQueue`
  models went with them. 105 routes remain.
- **Kept although the dashboard does not call them** (uncertain or documented): `POST /api/runs/{id}/cancel`,
  `POST /api/runs/{id}/verify` (operator route), `GET /api/onboarding`, `GET /api/profile`, `GET /api/resumes`,
  `GET /api/control` (read through the snapshot, but scripts may use them), the `/api/v2/discovery` diagnostics
  (`due`, `ready`, `requisitions`, `sources/health`), `POST /api/v2/knowledge/{facts,retrieve,migrate}` and
  `GET /api/v2/resumes/artifacts/{id}` with `POST …/cancel`.
- **Handlers moved** into one router per resource, with no change to a path, method, auth rule, request or response
  (the OpenAPI paths, parameters and request bodies of the running application were compared before and after).
- **A latent import fix** came with the move: `GET /api/runtime-health` imports `host_health`, and the new HTTP test caught
  the relative import that moved with the handler.

## 7. Where each route lives

| Module (`jobpilot/api/routes/`) | Routes |
|---|---|
| `health.py` | `/healthz`, `/api/runtime-health` |
| `session.py` | `/api/login`, `/api/logout` |
| `owner.py` | `/api/account`, `/api/account/{signup,login,reset,password,recovery-codes,sessions}`, `/api/onboarding` (logic and models in `owner_account.py`; cookie and throttle in `api/sessions.py`) |
| `state.py` | `/api/snapshot` (builder and cache in `api/snapshot.py`), `/api/successful-applications` |
| `archive.py` | `/api/archive`, `/api/archive/failed`, `/api/archive/{job_id}/log` |
| `profile.py` | `/api/profile` (GET, PUT), `/api/profile/{organize,extract}` |
| `resumes.py` | `/api/resumes` (GET, POST), `/api/resumes/{id}/download`, `DELETE /api/resumes/{id}` |
| `knowledge.py` | `/api/knowledge` (legacy store) |
| `settings.py` | `/api/control`, `/api/limits`, `/api/agents` |
| `jobs.py` | `/api/jobs`, `/api/jobs/{id}` (GET, DELETE) and `/rank`, `/apply`, `/queue`, `/pause`, `/resume` |
| `runs.py` | `/api/runs/{id}` and `/screenshot`, `/reconcile`, `/check-email`, `/verify`, `/cancel`; `/api/reviews/{id}` |
| `sources.py` | `/api/sources`, `/api/sources/{id}`, `/api/sources/{id}/discover` |
| `job_board.py` | `/api/job-board` and `/settings`, `/sources`, `/scrape`, `/apply`, `/autopilot/start` |
| `mail.py` | `/api/mail…`, `/oauth/gmail/callback`, `/api/mail-rules…`, `/api/mail-challenges/{id}/cancel`, `/api/inbox/check` |
| `accounts.py` | `/api/accounts`, `/api/accounts/{id}`, `/api/accounts/{id}/{password,verify}`, `/api/sessions` (employer browser sessions) |
| `demo.py` | `/api/demo`, `/demo/form/{nonce}` |
| `frontend.py` | `GET /{path}` and the `/assets` mount. Registered last |
| `features/{knowledge,resumes,discovery}/router.py` | the 31 `/api/v2` routes (unchanged) |
