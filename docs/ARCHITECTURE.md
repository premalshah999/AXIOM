# Architecture

How AXIOM / ApplyPilot is built today, and how it should be built. Part 1 describes the code as it is on `main` at
`69d921b` (2026-10-20), checked against the source, with file names so you can follow it. Part 2 is the target design
for the restructure, which is a separate, later step: nothing in this document moves a file.

Related documents: [KNOWLEDGE.md](KNOWLEDGE.md) (how a question is answered), [INFRA.md](INFRA.md) (image, compose,
configuration, worker settings), [SETUP.md](SETUP.md) (running your own copy), [API.md](API.md) (every route),
[OVERHAUL.md](OVERHAUL.md) (plan and status), [rea/](rea/) (per-ATS behaviour maps), [forensics/](forensics/)
(failure analysis).

---

# Part 1. The system today

## 1. What it is

A self-hosted engine that finds jobs, ranks them against one applicant's profile, and fills in and submits the
applications in a real browser, without asking the applicant questions it can answer from facts they already confirmed.
It is built around three promises:

- **Truthful.** An answer comes from a confirmed fact, a supported inference that is validated against the facts, or
  the applicant. A run is `confirmed` only with evidence of the employer's own acknowledgement.
- **No duplicates.** One writer per job, one employer at a time, submission reservations and uncertain-submission guards.
- **The applicant's facts are data, never code.** A friend's copy runs with their own profile and knowledge base.

**Terms used in the code.** "Owner" means two different things: the *installation owner* (the applicant who signed up:
`owner_account.py`, `owner_apply.py`) and an *application owner* (the task that holds one browser slot and one tab; the
documents say worker or slot). The rest:

| Term | Meaning |
|---|---|
| job, run | A posting (`jobs` row). One attempt to apply to it (`runs` row); a job can have many runs |
| draft | A filled, unsubmitted form kept (tab and checkpoint) for recovery |
| packet | JSON on a run: profile snapshot, pool, slot, pipeline, controller policy, recovery and escalation state |
| receipt | JSON on a run: the evidence that it was submitted (section 10) |
| reservation | The one durable claim to press Submit for a job (`reserve_commit`); it is what stops duplicates |
| review | A question a run is waiting on (`reviews` row), answered in the dashboard or on Telegram |
| fact, policy | A confirmed value about the applicant (`Profile.facts`, `kn_facts`); a standing decision (a `Profile` flag or `answer_rules`) |
| ATS, adapter | The employer's applicant-tracking platform; the deterministic driver for one (`adapters/`) |
| pool, slot, tier | The ATS-worker or specialist group; one browser owner; the strength level of a retry (`tiers.py`) |
| partition | The employer key that makes the queue run one job per employer at a time |
| outbox | Transactional delivery table for follow-ups (`application_outbox`: dispatch, report, repair, refill, notify, mail_resume, fact_resume, tailor) |

## 2. Processes and stores

```
   browser ── HTTPS/JSON ──►  app container: `jobpilot serve` (uvicorn → FastAPI, jobpilot/api.py)
   (React dashboard)           │
                               ├─ API + the built dashboard (jobpilot/static) + /healthz
   Telegram ◄── long poll ──   ├─ DBOS runtime (workers.py): queues applications_normal, applications_specialist,
   Gmail ◄── IMAP / OAuth ──   │    discovery, matching, classification, tailoring; one scheduler tick a minute
   LLM providers ◄── HTTPS ─   ├─ background tasks: outbox delivery, mailbox polling, Telegram bots, tab cleanup
                               └─ one BrowserEngine task per running application ── CDP ──► host Chrome :9224 (worker tabs)
                                                                                     └─ CDP ──► host Chrome :9225 (twin: tokens only)
   db container: PostgreSQL 17 ── application tables, feature tables (kn_ rs_ dc_), DBOS system tables
   volume applicant-data (/app/.data): resumes/*.pdf, runs/<run_id>/*, sessions/, mail-key, access-token
```

- **One server process owns everything**: the API, the scheduler and every browser owner. There is no separate worker
  process; a "worker" is a slot (a browser owner), not a process (`application_slots.py`). `Service` (`service.py`) is
  the application core that the API, the scheduler, Telegram and the CLI all call.
- **Two host Chromes**, started on the machine, never in the container. The worker Chrome (9224) holds one tab per
  running application. The twin (9225) is never attached by Playwright: it only mints hCaptcha tokens for platforms that
  distrust automated browsers (`captcha_twin.py`, iCIMS). Without `BROWSER_CDP_URL` the engine launches a headless
  Chromium inside the container (tests, demos, `jobpilot verify`).
- **Telegram is optional.** A main bot plus up to eight per-slot bots (`telegram.py`, `telegram_workers.py`) deliver
  questions, outcomes and screenshots and accept replies and instructions. It is a view onto the same `reviews` and `runs`
  rows, never an execution owner.
- **Mail is read-only.** Gmail by app password (IMAP) or OAuth (`gmail_imap.py`, `mail.py`). The app never sends mail or
  marks messages read. Codes and links go to the owning browser, never into a model prompt.
- **CLI** (`cli.py`): `serve`, `token`, `doctor`, `probe` (dry-run one URL), `trace`, `mcp` (read-only MCP over stdio),
  `verify` (synthetic end-to-end check), `login` (capture a session), `browser` and `twin-browser` (host Chromes).

## 3. Where each piece of state lives

| State | Where | Written by | Notes |
|---|---|---|---|
| Applicant profile: identity, contact, address, work and education history, `facts`, answer policies | `settings['profile']` (JSON, `schemas.Profile`) | setup guide, `PUT /api/profile`, Telegram, review answers | `extra="allow"`: a key this version does not know is kept. The server owns `reviewed_answers`, `approved_answers`, `forgotten_answers`; `PUT /api/profile` restores them |
| Normalized, versioned facts and confirmed answers | `kn_facts` (+ `kn_employers`, `kn_aliases`, `kn_versions`, `kn_events`, `kn_migrations`) | `features/knowledge` (`migration.sync` mirrors the profile on every lookup; native answers via the knowledge API) | Row ids `m:*` are migration mirrors; others are native and win. See [KNOWLEDGE.md](KNOWLEDGE.md) |
| Legacy reviewed answers | `settings['profile'].reviewed_answers` / `approved_answers` | `knowledge.py` (`save`, `forget`, `learned_answer`) | Second store, mirrored both ways with `kn_facts` (see section 14) |
| Jobs and discovery evidence | `jobs`, `job_listings`, `sources`, `dc_requisitions`, `dc_classifications`, `dc_source_health`, `dc_deferrals` | `discovery.py`, `jobboard.py`, `jobsearch.py`, `features/discovery` | `jobs.status` mirrors the latest run state, plus the pre-run states `new`, `ready`, `review`, `skipped`, `archived` |
| Runs | `runs` (`state`, `packet` JSON, `receipt` JSON), `events` (append-only trace), `reviews` (questions awaiting an answer) | `Service`, `BrowserEngine` | `packet` carries the profile snapshot, pool, slot, pipeline, controller policy, recovery and escalation state |
| Queue delivery | DBOS system tables, and `application_outbox` | `workers.py`, `outbox.py` | The `queued → running` compare-and-set on `runs` is the real idempotency guard; DBOS only delivers |
| Resumes | `resumes` (uploaded PDFs, files in the volume), `rs_masters`, `rs_artifacts` (tailored variants) | upload API, `features/resumes` | A run pins one artifact; a continuation cannot change its PDF |
| Employer accounts and mailboxes | `settings['employer_account:*']` (encrypted), `mailboxes` (encrypted) | `accounts.py`, `mail.py` | Vault key `DATA_DIR/mail-key`. Credentials never enter prompts, events or screenshots |
| Mail waits | `mail_rules`, `mail_challenges`, `mail_consumption`, `mail_oauth` | `mail.py`, `inbox.py` | A challenge is single-use and bound to sender, recipient, tenant and request time |
| Budgets and limits | `budgets` (daily spend and submissions); settings `application_budget`, `runner_tiers`, `application_pacing`, `unknown_resubmit_policy`… | `application_budget.py`, `tiers.py`, `application_pacing.py` | |
| Control and policy switches | settings `control` (`paused`, `auto_submit`), `job_board_settings`, `agent_settings`, `ats_focus`, `job_control:<job>`, `execution_v2_deployment_hold`… | API, Telegram, operators | |
| Run artifacts | `DATA_DIR/runs/<run_id>/` (`answers.json`, `final.png`, `final_outline.txt`, `steps/`) | `BrowserEngine`, `FormSession` | Read by `/api/runs/{id}`, the repair agent and the resubmit guard |
| Configuration and secrets | `.env` → `config.Settings` | operator | 101 settings; `.env.example` is the complete list |

The `settings` table is also the catch-all document store for per-job and per-run state (`job_origin:*`,
`failure_report:*`, `telegram_*`, `repair_*`, `draft_recovery:*`, `submission_guard:*`…). On a long-running installation it
holds tens of thousands of rows. That works, and it is a design smell addressed in Part 2.

## 4. From discovery to a queued run

```
 sources (rows) ─┐                                           ┌─ guards (section 4.3)
 public feeds ───┼─► discovery queue ─► jobs (new) ─► matching/classification ─► jobs.status = ready │ review │ skipped
 manual import ──┘   (workers.py)                      (LLM fit + facts, cached)      │
                                                                                      ▼
                       autopilot tick (autopilot.py) ── picks ready jobs by dispatch_priority ──► Service.queue
                                                                                      │
                          runs row (queued) ─► DBOS enqueue to applications_<pool>, partition key = employer
```

**4.1 Discovery.** `workers.scheduled_tick` runs every minute (a DBOS schedule). It finds due `sources` rows and the
public feed sweep (`features/discovery/feeds.sweep` every `interval_hours`) and enqueues them on the `discovery` queue,
which never occupies a browser slot. Sources scrape ATS boards (Greenhouse, Lever, Ashby, SmartRecruiters, Workday,
career pages: `jobboard.scrape_source`, `discovery.discover`), expand by web search (`jobsearch.py`) or ingest curated
new-grad lists. Each posting needs publication evidence for freshness and is deduplicated by canonical requisition
(`features/discovery/policy.py`, `dc_requisitions`). Target roles, excluded keywords and locations come from the profile
(`features/discovery/roles.py`).

**4.2 Ranking.** The `matching`/`classification` queues rank up to 24 jobs per model call (`discovery.rank_batch`) against
the profile, the confirmed facts and the uploaded resumes. Results are cached per (description, profile, policy)
version (`dc_classifications`). `ready` needs a fit at or above the profile's `minimum_fit`, an eligible verdict and a
resume; with `admit_by_title` a posting of one of the profile's own specific roles is admitted anyway. Tailoring
(`tailoring` queue, `features/resumes`) builds an evidence-only variant PDF before the run needs it.

**4.3 Admission.** Every route to a run goes through `Service.queue`, in this order: provider health; the profile's roles,
internship and seniority policy; the job's 24-hour token budget; archived and duplicate guards (archived submission
guard, aliases of the same requisition, active run, confirmed or uncertain submission); intake boundary (deployment
hold); the lifetime allowance; employer limits (three confirmed or pending per employer, an employer's own stated cap,
Ashby start gap, ATS focus); the shared mail sender; **eligibility** against the confirmed facts
(`eligibility.py`: a posting's explicit hiring restriction that contradicts a confirmed fact is refused); cooldowns and
site circuit breakers; the recovery authorization for retries; a resume and the provider keys; and, for a submission,
`control.auto_submit`. Refusals are `QueueBlocked` with an outcome (`deferred`, `protected`, `excluded`) and a retry time.
`autopilot.tick` calls the same function, so the automatic path has no looser rules than a click.

## 5. A run

`Service.queue` writes a `runs` row (`queued`) and enqueues `apply_workflow(run_id)` into `applications_<pool>`
partitioned by employer, so one employer runs at a time. `Service.execute` waits on the pool's semaphore, then
`_execute_owned` claims the run with a database compare-and-set (`queued → running`, or `submitting` for a verification-only
continuation) while checking, in one transaction: the lifetime allowance, the global and per-pool slot count, the
employer partition, pacing (`may_start`), and the mail sender lease. A slot is recorded in `runs.packet.worker_slot`.
The run then executes `BrowserEngine.run(job, run)` under a wall-clock budget set by runner tier, ATS and pool, and
`Service.complete(run_id, result, elapsed)` records the outcome.

Run states (`service.py`, `TERMINAL`, `ACTIVE`, `RETRYABLE`):

```
queued ─► running ─► submitting ─► confirmed                  evidence required (section 10)
   │         │            ├────────► submission_unknown        committed, outcome unverified; never resubmitted blind
   │         ├─► dry_run_passed                                mode = dry_run; never submits
   │         ├─► waiting_answer │ needs_review                 a required personal fact is missing → question → answer → requeue
   │         ├─► waiting_browser                               control or navigation failure, draft retained → recovery
   │         ├─► timed_out │ failed
   │         └─► closed │ already_applied │ cancelled │ paused │ skipped
```

`complete` also: refuses `confirmed` without a website confirmation page, a trusted employer email or the applicant's
word; turns a `submitting` run that ends any way except confirmed or an explicit rejection into `submission_unknown`; removes a job whose form asks for an SSN, ID or bank
number; closes a job whose employer hires only in places the applicant does not live; sets a cooldown after an
employer's spam refusal; releases the slot; and calls `recovery.schedule_recovery`.

**Recovery** (`recovery.py`, `tiers.py`). A `waiting_browser` or `timed_out` run with an unsubmitted draft is retried
after a delay, a bounded number of times, only for causes an agent can fix. After repeated failures, or one on a hard
platform, it moves to the specialist pool (section 6). Missing facts, mail waits, provider outages and uncertain
submissions are typed waits, not retries. `repair_agent.py` reads a failed run's evidence and applies a guarded data fix
(an answer override for one field, an instruction for the next attempt, or a question for the applicant).

### 5.1 One click on Apply, end to end

1. The dashboard calls `applyJob(id)` (`frontend/src/api.ts`) → `POST /api/jobs/{id}/apply` → `owner_apply.apply`.
2. `apply` marks the job `ready` with its resume (or the first uploaded one) when it was not, grants a fresh start
   allowance unless the job may already be submitted, and calls `owner_apply.start` → `Service.queue(job_id, "submit")`.
3. `_queue` runs the admission guards (section 4.3). A resume still being tailored answers *preparing*
   (`outbox` kind `tailor`); a temporary refusal is remembered in `settings['owner_apply:<job>']` and answered *waiting*, and
   `autopilot.tick` calls `owner_apply.due` to start it when admission allows. A permanent refusal is an error.
4. On success `_queue` writes the `runs` row (`queued`), sets `jobs.status = queued`, records a `queued` event and calls
   `workers.dispatch`, which enqueues `apply_workflow(run_id)` on `applications_<pool>` with the employer as partition key. If
   the enqueue fails the run is delivered through the outbox (kind `dispatch`); the run is never created twice.
5. DBOS calls `Service.execute`; `_execute_owned` claims the run (section 5) and starts `BrowserEngine.run`.
6. The engine fills and verifies the form (section 7) and returns a result dict; `Service.complete` records the state,
   receipt and reason, releases the slot, schedules recovery, creates `reviews` for missing facts, and enqueues outbox
   work: a failure `report`, the `repair` agent, a `refill` of the free slot, a `notify` for Telegram, and a `mail_resume` when
   an email wait was matched.
7. Later and out of band: `receipts.check_receipts` (the mail poll) confirms a `submission_unknown` run when the employer's
   acknowledgement arrives; an answer to a question (`POST /api/reviews/{id}` or Telegram) calls `Service.resolve_review`,
   which saves it as a fact and requeues the waiting run.

## 6. Worker pools

A worker is a browser slot. `NORMAL_WORKERS` (6) are the ATS workers, slots 1-6; `SPECIALIST_WORKERS` (2) are slots 7-8
(`config.Topology`, `pipeline_comparison.pool_slot_ids`). The pools are separate DBOS queues with their own
concurrency, so neither starves the other. Settings, thresholds and budgets are in [INFRA.md](INFRA.md) (Workers).

- **ATS classification.** `ats.detect(url)` maps a URL to one of 25 platforms when the job is created; `BrowserEngine.detect_ats`
  looks again at run time because redirects and embedded frames reveal the real platform.
- **ATS workers** take every new application and run that platform's own flow: the guided single-page flow for
  Greenhouse, Lever, Ashby, SmartRecruiters, Workable and BambooHR (`browser.AGENT_FIRST`); a deterministic adapter for
  Workday, Oracle, iCIMS, Taleo, SuccessFactors and Eightfold (`adapters.REGISTRY`); the generic multi-page adapter with a
  guided fallback for everything else.
- **Specialists** take a job only after an agent-fixable failure on an ATS worker (`specialist.py`,
  `recovery.SPECIALIST_CAUSES`). They continue the preserved unsubmitted checkpoint with a larger budget
  (`SPECIALIST_BUDGET_MULTIPLIER`) and must choose a different strategy from the failed one. They cannot consume the six
  regular slots or reset exhausted ordinary attempts.
- **Global rules** hold across both pools: one writer per job, one employer at a time, three applications per employer,
  Ashby start spacing, a shared mail sender per platform.

## 7. Inside a run: controller, browser, forms

`BrowserEngine` (`browser.py`) owns one application tab. `run()` (about 770 lines) opens or attaches the tab through
`desktop_cdp.py`, prepares the page (privacy and cookie handling, network journal), detects the platform, and then tries
controllers in this order, stopping at the first that produces a result:

1. **Workday authentication** (`workday.py`) for Workday jobs: account creation or sign-in with the encrypted store.
2. **Adapter** (`run_adapters`) for platforms in `adapters.REGISTRY` and unknown multi-page portals, unless the engine is
   `guided` or the controller policy is model-led. An adapter that does not recognize the page returns a fallback.
3. **Email step** (`handle_email`): verification codes and links through the owning browser.
4. **Model navigator** (`run_desktop_navigator`) when the controller policy is `reasoning_first` or `continuous_agent`
   (only while runner tiers are enabled; off in the original installation since 2026-10-14).
5. **Guided flow** (`guided_flow`): deterministic fill of observed fields with `FormSession`, page after page.
6. **Owned-page reasoning** (`run_agent`) after an adapter fallback or a recoverable control failure.

Whatever the controller, **every write goes through `FormSession`** (`forms.py`), and **every control is driven by
`widgets.py`**:

- **Form engine contract.** `scan()` evaluates `js/scan.js` in every application frame and returns fields and controls,
  each tagged with a `data-jp-id`; a field carries `label, type, required, options, value, valid, group, section, hint…`.
  Controls are referenced by these observed ids, never by model-generated selectors. `fill(field, value)` commits the value
  through the widget driver for that control kind and **reads it back**; a mismatch is an error, not a retry. `verify()`
  re-reads every field against the ledger. `click()` and `proof()` handle intermediate and final controls and the
  confirmation evidence; `rejection()` and `repair_validation()` act only on an explicit website refusal.
- **Widgets** (`widgets.py`, `widgets_ats.py`, `phone_fields.py`): native selects, React-select and MUI listboxes, Workday
  prompts, Oracle and SuccessFactors pickers, UI5 comboboxes, date parts, pills, phone groups, file choosers. Each driver
  commits an observed option and reads it back, and types like a person (`human_type`, `pointer_path`) because several
  platforms score pointer telemetry.
- **Adapter contract** (`adapters/base.py`). `Adapter.drive()` loops *observe → classify step → act → verify* with progress
  limits (`max_steps = 45`, repeated page signatures stop the loop). `classify()` yields a `Step`: `job_details`,
  `apply_method`, `email_entry`, `sign_in`, `create_account`, `verify_link`, `email_code`, `reset_password`, `consent`,
  `form`, `review`, `confirmation`, `already_applied`, `closed`, `blocked`, `unknown`. Each step has an `on_<step>` handler
  that a platform subclass overrides (button ids, labels, site locks, `handoff(url)` to another adapter). Typed waits are
  returned with `defer(reason, reviews, state)`. After the final click the adapter sets `committed` and only watches.
- **Challenges.** CAPTCHA tokens (`captcha.py`, official 2Captcha SDK in a cancellable subprocess; `captcha_visual.py` for
  same-IP image challenges; the twin Chrome for iCIMS), bot walls (`bot_wall.py`) and email challenges are bounded and
  never count as proof of submission.
- **Submission.** `reserve_commit` takes the one durable submission reservation; `finish` and `watch_submission` click
  Submit only on an armed page and then watch for proof, refusal, a challenge, or a bounded timeout. An exit after a
  commit is `submission_unknown`.

Platform behaviour (selectors, quirks, risks) is mapped per ATS in [rea/maps/](rea/maps/).

## 8. Answers: profile → knowledge → model

The full design is in [KNOWLEDGE.md](KNOWLEDGE.md). In one line: `Resolver.local` answers from profile fields, history rows,
policy facts and exact retrieval without a model; otherwise `retrieve_answer_context` builds an evidence packet from
`kn_facts`, the model returns an `AnswerBatch` citing evidence ids, and `Resolver._validate_answer` refuses an answer whose
citations do not exist, whose subject has no relevant fact, or whose polarity contradicts the facts. A required question with no
supporting fact becomes a `reviews` row and a Telegram or dashboard question; the answer is saved and reused.

Two properties matter for the architecture:

- **Policies are profile settings or facts.** `accept_all_application_terms`, `accept_required_marketing`,
  `decline_demographics` and `answer_rules`, `application_source`, `autonomous`, and facts such as
  `previously_employed_here` decide the standing choices. Section 12 lists the few policies still hard-coded.
- **The resolver is injected.** `FormSession` and the adapters receive a `Resolver`; the browser layer does not know how
  an answer is found.

## 9. Mail, accounts, CAPTCHA, models

- **Mail** (`mail.py`, `inbox.py`, `gmail_imap.py`, `email_browser.py`): `MailService` polls the mailbox; a *challenge*
  is opened by a run (code or link), matched to exactly one eligible message by authenticated sender, recipient, tenant and
  time window, consumed once, and delivered only to the owning browser. A wait longer than 30 seconds is parked as a durable
  request and the slot is released (`resume_mail_wait`).
- **Accounts** (`accounts.py`, `workday.py`): one shared login per applicant plus one record per employer origin, encrypted;
  `AccountFlow` discovers sign-up, sign-in and reset forms on unknown platforms. `email_alias.py` gives a platform a
  Gmail plus-address when it bound the base address to an unusable identity.
- **Models** (`models.py`, `model_admission.py`, `provider_health.py`, `agent_settings.py`): DeepSeek, MiMo and Claude Haiku
  behind one client with priority admission, per-call deadlines, daily budgets, health cooldowns and per-role selection
  (answers, navigator, discovery…). `jev.py` answers bounded pick-one questions first; `vision.py` is observation-only.
- **Telegram**: questions and outcomes out; replies, `/job` commands and scoped instructions in. A reply is validated like any
  other answer.

## 10. Receipts and confirmation

`Run.receipt` holds the evidence. A run may be `confirmed` only with one of:

| Evidence | Produced by | Notes |
|---|---|---|
| Website confirmation page | `FormSession.proof` → `receipt.type = explicit_confirmation_page`, `evidence_version = 2` with selector and status region bound to this job and run | `website_confirmed` |
| Authenticated employer email | `receipts.check_receipts` / `acknowledgement()` matching sender, recipient, employer and exact title to one attempted run | `email_confirmed`; a verification or activation message is never a receipt |
| The applicant's word | `POST /api/runs/{id}/reconcile` → `receipt.type = user_reconciled` | only for `submission_unknown` and `already_applied` |

`evidence_reconciliation` is `pending` for confirmations recorded by an older detector until they are re-verified;
pending runs are not counted. Nothing marks a run confirmed because a CAPTCHA was solved, an account was activated, a
talent community was joined or an upload succeeded.

## 11. Frontend

`frontend/` is one React + Vite single-page app, built inside the image into `jobpilot/static` and served by the API
(`GET /{path}` falls back to `index.html`). The shell (`main.tsx`) polls `/api/snapshot` and renders: Batch (`batch.tsx`),
Jobs and job detail (`jobs.tsx`, `jobdetail.tsx`), Ledger (`ledger.tsx`), Job board (`jobboard.tsx`), Setup
(`setup.tsx`, `autonomy.tsx`, `mail.tsx`), the first-run guide (`onboarding.tsx`), the question inbox (`questions.tsx`)
and the v2 feature screens (`features.tsx`, `editor.tsx`: knowledge, discovery, resumes). The session is an HttpOnly cookie;
browser code never sees the access token. The routes it calls, their problems and a cleaner route design are in [API.md](API.md).

## 12. Policies encoded in code (audit, 2026-10-20)

The rule: code holds general rules; an applicant's decisions are profile settings or knowledge facts. State of the audit:

**Already data-driven (fine).** Consent, marketing, demographic and source policies (`Profile` flags and `answer_rules`);
prior-employment, relatives, government, restriction, relocation, travel, availability, salary, work-authorization and
self-identification answers (facts, section 13); target roles, locations, excluded keywords, minimum fit, experience
tolerance and job priority (profile); the unknown-submission policy (setting `unknown_resubmit_policy`); company limit and
token cap (settings, `GET/PUT /api/limits`); ATS scope (`ats_focus`).

**Changed in this pass.**

| Was | Now | Migration |
|---|---|---|
| `DEFAULT_ROLES` (ML/AI roles) used when a profile had none | A profile with no target roles searches and matches nothing; the setup guide already requires a role | none |
| `TITLE_ADMIT_FAMILIES` fixed list behind `admit_by_title`, with software-only exclusion words | The profile's own target roles that name a discipline; generic-only roles ("Developer", "Graduate Engineer") never qualify; exclusion words that appear in the profile's own roles are not exclusions (`roles.admitted_by_title`) | none: the original applicant's seventeen roles reduce to exactly the old eleven families |
| `TIMEZONE` default `America/New_York` | `UTC`; `scripts/setup.sh` writes the computer's zone | the original installation's production `.env` already sets `TIMEZONE`; any installation that relied on the old default must set it |
| The original applicant's school, employer, city, county, metro region, date of birth and e-mail domain in tests, comments and `docs/rea` | One synthetic mapping, applied on `main` (`b49ee2a`, `7408a40`, `78416b3`); this pass removed the last two leftovers, a university e-mail domain in the alias test and a research department in the iCIMS history test | none |

**Still hard-coded (decisions for the owner of `answers.py` and the admission code).**

| Where | What it encodes | Proposed form |
|---|---|---|
| `answers.MAJOR_FALLBACK`, `forms.py` (major prompt fallback) | A major the catalog lacks falls back to *Computer Science* | `answer_rules.major_fallback` (default empty); the original profile sets "Computer Science" |
| `answers.DEGREE_FALLBACK`, `answers.SOURCE_FALLBACK`, `source_choice` | Equivalent degrees; a fixed order of "how did you hear" channels (LinkedIn, company site, …) | `answer_rules.source_order`; degree equivalents are general vocabulary and can stay |
| `answers.answer_sources`, `answer_quality.context` (under `profile.autonomous`) | "open to travel", answer "Negotiable" for salary, "the applicant is pursuing full-time early-career employment" | profile fields (`travel_willingness`, `employment_type_sought`, `salary_stance`), set by the setup guide |
| `answers.talent_pool_opt_out`, `policy_choice` (narrowest data-sharing option) | Talent-community and data-sharing opt-ins are always declined | follow `accept_required_marketing` / a `join_talent_communities` setting |
| `service._queue`, `jobboard.role_matches`, `features/discovery/feeds.keep` | Internships are excluded everywhere | profile `job_types` (default full-time) |
| `jobboard.BoardSettings.early_career_only = True`, `features/discovery/policy.SENIOR` | Early-career-only discovery by default | a setup-guide choice that sets the board setting |
| `admission.UNKNOWN_RESUBMIT["enabled"] = True` | A run in `submission_unknown` with no receipt is resubmitted up to twice after 30 minutes | default `False`, with a one-time migration that writes `enabled: true` for an installation that already has runs and no saved `enabled` (the original installation saves only `max_resubmits`, so it would otherwise lose the behaviour). See [forensics/FAILURE_ANALYSIS.md](forensics/FAILURE_ANALYSIS.md) (F7: duplicate submissions) |
| `service.complete` ("owner rule") | A job whose form asks for an SSN, national or bank number is removed | product rule; rename the comment and the reason text |

## 13. Knowledge facts and settings the resolver reads

Existing keys, so retrieval and the setup guide stay aligned. Facts live in `Profile.facts` and are mirrored as
`kn_facts` rows `kf:<key>`. `migration.FACT_CATEGORY` and the first matching rule of `questions.CATEGORY_RULES` pick the
category from the key's words: self-identification keys land in `demographics`, clearance keys in `clearance`,
authorization and sponsorship keys in `work_authorization`, a key such as `has_relatives_at_employer` in
`employer_relationship`, and a key no rule matches in `additional`.

| Group | Keys | Read by |
|---|---|---|
| Work authorization | `work_authorization_US` (`work_authorized_us`), `requires_future_sponsorship_US`, `requires_sponsorship`, `current_immigration_status_US`, `visa_status`, `us_citizen`, `citizenship`, `country_of_citizenship` | `answer_context.evaluate_eligibility`, `status_defined_answer`, `us_person`; `eligibility.py` |
| Relationships and background | `previously_employed_here`, `previously_employed_at_applying_company` (any `worked_at_*` fact), `has_relatives_at_employer`, `government_employment_history`, `government_official`, `has_non_compete`, `has_non_solicitation`, `has_employment_restrictions`, `criminal_conviction`, `ever_fired`, `asked_to_resign`, `involuntarily_discharged`, `referred_by_employee`, `excluded_from_federal_healthcare_programs`, `tax_credit_screening_no`, `needs_accommodation` | `Resolver.owner_policy_answer`, `_intent_answer` (`INTENTS`) |
| Self-identification | `gender`, `pronouns`, `gender_identity`, `sexual_orientation`, `transgender`, `hispanic_or_latino`, `race_preference` (ordered list of preferred options), `veteran`, `veteran_family`, `disability` | `Resolver.demographic_chain`, `confirmed_demographic` |
| Logistics and location | `willing_to_relocate`, `relocation_at_own_expense`, `requires_relocation_assistance`, `relocation_locations`, `relocation_preferences`, `willing_to_travel`, `willing_to_work_onsite`, `availability`, `notice_period`, `desired_salary`, `metro_region`, `state_of_residence`, `country_of_residence`, `over_18`, `valid_drivers_license`, `has_security_clearance` | `_intent_answer`, `_local`, `residence_outside` |
| Education | `school_catalog_other_first`, `attended_community_college` | `Resolver.school_answer`, `_intent_answer` |
| Profile flags (not facts) | `accept_all_application_terms`, `allow_application_consents`, `accept_required_marketing`, `approved_consents`, `decline_demographics`, `answer_rules`, `application_source`, `autonomous`, `allow_account_creation`, `admit_by_title`, `accept_experience_years`, `job_priority`, `target_roles`, `target_locations`, `excluded_keywords`, `minimum_fit`, `ats_email_aliases` | resolver, discovery, `Service.queue` |

An absent fact is **unknown**: the question goes to the applicant. No policy default is assumed for a person.

## 14. Dead code, duplicates and legacy paths

Method: every top-level function and class in `jobpilot/` was searched by name across `jobpilot/`, `tests/`, `scripts/`,
the frontend and the docs; every module's imports (including function-level imports) were collected.

**Removed in this pass (referenced nowhere, tests included).** `application_pacing.company_identity`,
`application_pacing.next_ashby_start`, `eligibility.backfill_candidates`, `features/discovery/policy.known_requisitions`,
`features/resumes/select.rendered_units`, and the imports they left unused.

**Kept on purpose (referenced only by tests, a shell, or a decorator).** `operations/cancel.py` and
`operations/reset.py` (documented operator tools), `features/_store.drop_tables` (maintainer rollback),
`dbos_sqlite.sqlite_subsecond_clock` (registered through a decorator), `sensitive_ids.scan_sensitive`,
`widgets.page_is_blank`, `widgets_ats.read_combo` (used by tests only), `features/knowledge/models.Provenance` (documents a
JSON shape; unused as a type, in the knowledge package that is under change).

**No module is unreferenced.** Every module is imported somewhere. The structure hides problems instead:
677 imports sit inside functions, mostly to dodge import cycles, so the static import graph shows none while the real
dependencies run in every direction (`answers` → `widgets`, `pipeline_comparison`, `execution_services`; `forms` → `answers`).

**Duplicate implementations.**

| Duplicate | Where | Direction |
|---|---|---|
| Two knowledge stores and two knowledge APIs | `knowledge.py` + `settings['profile'].reviewed_answers` + `/api/knowledge`, versus `features/knowledge` (`kn_facts`) + `/api/v2/knowledge` | one store (`kn_facts`), one API; `knowledge.py` shrinks to the matching helpers the resolver uses |
| Three discovery stacks | `discovery.py` (import, rank, per-ATS scrapers), `jobboard.py` + `jobsearch.py` (board sources, catalogs, web search), `features/discovery` (policy, feeds, readiness) | one `discovery` package; `SENIOR`/`ENTRY`/`INTERN`/`NEW_GRAD` are defined in more than one place |
| Five ways to start an application | `POST /api/jobs/{id}/apply`, `/api/jobs/{id}/queue`, `/api/jobs/batch-queue`, `/api/jobs/{id}/approve`, `/api/job-board/apply` | see [API.md](API.md) |
| Two ways to add a source | `POST /api/sources`, `POST /api/job-board/sources` | one route with a `managed` flag |
| Several controllers | guided flow, adapters, `run_desktop_navigator`, `run_agent`, `continuous_agent.py`, `control_agent.py` | `continuous_agent.py`, the `reasoning_first` navigator and `tiers.escalate_due` run only while runner tiers are enabled (off in the original installation since 2026-10-14); decide whether to keep them |
| Pipeline names | `pipeline_comparison.LEGACY/REASONING/AGENTIC/CURRENT` | retained drafts keep old names; new runs use `current`. Remove with the last retained draft |

**Candidates for a later cleanup (not provably unused).** `/api/preflight`, `/api/jobs/batch-queue`,
`/api/jobs/{id}/approve`, `/api/runs/{id}/cancel`, `/api/job-board/search` (no caller in the dashboard, tests or scripts;
see [API.md](API.md)).

## 15. Module map

Every module of `jobpilot/`, by area. Line counts are given for the modules over 1,000 lines.

| Area | Modules | Responsibility |
|---|---|---|
| **Entry points** | `cli.py`, `mcp_server.py`, `verify.py`, `demo.py` | Commands; read-only MCP over stdio; the synthetic end-to-end check and its fake employer |
| **HTTP** | `api.py` (1.1k), `owner_account.py`, `mail_api.py`, `jobboard_api.py`, `features/{knowledge,resumes,discovery}/router.py` | App factory, middleware, lifespan and the 109 routes ([API.md](API.md)) |
| **Core** | `config.py`, `db.py`, `schemas.py`, `network.py`, `runtime.py`, `dbos_sqlite.py`, `features/_store.py` | Settings; tables and sessions; `Profile` and the request models; the SSRF-safe fetch; version tags; feature-table helpers |
| **Application** | `service.py` (1.6k), `admission.py`, `application_budget.py`, `application_pacing.py`, `application_slots.py`, `execution_services.py`, `job_control.py`, `owner_apply.py`, `archive.py`, `outbox.py` | `Service` (queue, execute, complete, reconcile, reviews); admission guards and capacity; lifetime allowance; employer and ATS pacing; slot claims; resume pinning and tailoring requests; pause, resume and delete; one-click apply; archived history; transactional delivery |
| **Scheduling and recovery** | `workers.py`, `autopilot.py`, `recovery.py`, `tiers.py`, `pipeline_comparison.py`, `specialist.py`, `controller_policy.py`, `repair_agent.py`, `failure_reports.py`, `run_trace.py`, `provider_health.py` | DBOS workflows, queues and the minute tick; the unattended batch; bounded retries and specialist handoff; runner tiers and budgets; pools, slots and engine choice; the repair agent; failure summaries; per-run timing |
| **Discovery** | `discovery.py`, `jobboard.py`, `jobsearch.py`, `ats.py`, `eligibility.py`, `features/discovery/{classify,feeds,graduation,policy,postings,readiness,roles,tables}.py` | Import, ranking and per-ATS scrapers; board sources and catalogs; web-search expansion; platform detection; hiring restrictions versus facts; cached classification, curated feeds, freshness, dedupe, readiness, target-role matching |
| **Answers and knowledge** | `answers.py` (4.8k), `answer_context.py`, `answer_quality.py`, `answer_rules.py`, `knowledge.py`, `evidence_equivalence.py`, `inference_agent.py`, `sensitive_ids.py`, `letters.py`, `features/knowledge/*` | The resolver; question identity and eligibility propositions; the pre-submit review; owner-editable fallback chains; the legacy answer store; interpretation of conflicting facts; inference before asking; identifier detection; cover letters; the versioned store, retrieval, employer identity, writing |
| **Resumes** | `features/resumes/*` | Master resume, evidence inventory, tailoring, LaTeX editor and compiler |
| **Engine** | `browser.py` (4.4k), `browser_contracts.py`, `forms.py` (5.2k), `widgets.py` (2.7k), `widgets_ats.py`, `phone_fields.py`, `history_editor_guard.py`, `js/scan.js`, `desktop_cdp.py`, `privacy.py`, `playwright_fast.py`, `diagnostics.py`, `bot_wall.py` | `BrowserEngine`; shared observation types; `FormSession`; control drivers and per-platform pickers; phone groups; the DOM scanner; tab ownership on the host Chrome; cookie and geolocation handling; masked diagnostics; platform-wide bot walls |
| **Adapters** | `adapters/base.py` (1.8k), `adapters/{workday,oracle,icims,others}.py` (`icims.py` 1.5k), `workday.py` (1.2k) | The deterministic multi-page driver and one subclass per platform; Workday account setup |
| **Model-led controllers** | `continuous_agent.py`, `control_agent.py`, `vision.py` | Opt-in agent owner and bounded reasoning over one control (run only while runner tiers are enabled); optional observation of a masked screenshot |
| **CAPTCHA** | `captcha.py`, `captcha_client.py`, `captcha_observation.py`, `captcha_same_ip.py`, `captcha_twin.py`, `captcha_visual.py` | Token workflow and SDK client; read-only frame observation; same-IP image solving; twin-Chrome tokens |
| **Mail and accounts** | `mail.py` (1.9k), `mail_errors.py`, `inbox.py`, `gmail_imap.py`, `email_browser.py`, `email_alias.py`, `receipts.py`, `accounts.py` (1.6k) | Challenges, rules, extraction and the vault; sanitized errors; the adapter-facing view; read-only IMAP; code and link actions in the owning browser; plus-addresses; receipt matching; employer account store and flows |
| **Models** | `models.py`, `model_admission.py`, `agent_settings.py`, `jev.py` | LLM clients with budgets; process-wide request admission; role and helper switches; typed choice decisions |
| **Telegram** | `telegram.py` (1.7k), `telegram_workers.py` | Main bot and per-slot bots |
| **Operations** | `operations/{cancel,close_tabs,reset}.py` | Maintenance that is not part of application execution |
| **Frontend** | `frontend/src/*.tsx` | The dashboard (section 11); built into `jobpilot/static` |

---

# Part 2. Target design

This is the plan for the restructure ("Phase B"). It starts only after the other branches are merged, because it moves
files that all of them edit.

## 16. What is wrong with the structure

- A flat package of about 130 modules; four of them hold 17,000 lines (`forms.py` 5.2k, `answers.py` 4.8k, `browser.py`
  4.4k, `widgets.py` 2.7k). 30 of the 669 functions in the eleven largest modules are longer than 150 lines; the longest
  are `Resolver._local` (916), `BrowserEngine.run` (769), `run_desktop_navigator` (608), `FormSession.fill_current` (546),
  `FormSession._fill` (526) and `FormSession.proof` (471).
- No layering: 677 function-level imports hide cycles. A change in `widgets.py` can break answers, and the answer module
  imports queue-level code for a prompt string.
- Two knowledge stores, three discovery stacks, two source routes and five ways to start an application.
- `api.py` holds 54 route handlers, the middleware, the snapshot builder and SQL inside one 1,065-line function (`create_app`).
- The `settings` table is used as a document store for per-job state.
- No migration framework (`create_all` only), so a column change cannot ship.

## 17. Principles

1. **Layers, one direction.** A package imports only from layers below it. A test (`tests/test_layering.py`, an `ast` scan)
   fails on an upward import, and function-level imports of project modules are not allowed outside `api` wiring.
2. **Packages by capability.** Each package has a small public surface in its `__init__.py`; other packages import only that.
3. **Thin interfaces.** Route handlers validate and call a service function; no SQL, no business rules, no 150-line bodies.
4. **No personal facts or policies in code.** Policies are profile settings or knowledge facts; an installation-level default is a setting with a safe default.
5. **State in tables.** Per-job and per-run documents move out of `settings`; real migrations (Alembic) ship the first schema change.
6. **Every file is movable.** Moves are mechanical (`git mv` plus import rewrites) and tested one package at a time.

## 18. Layers and packages

```
L5 interfaces    api/  telegram/  cli  mcp_server  (frontend/)
L4 applications  applications/  (queue, lifecycle, pools, recovery, autopilot, scheduler, repair, owner actions)
L3 engine        browser/  forms/  widgets/  adapters/  captcha/  email_browser  workday
L2 answering     answers/  (resolver, intents, consent, demographics, history, choices, validate, prompts)
L1 services      knowledge/  resumes/  discovery/  mail/  accounts/  models/  eligibility
L0 core          config  db/ (tables, session)  schemas  settings_store  secrets  events  outbox  network  matching (text)
```

Rules that follow from it, with today's violations to fix first:

- `answers` must not import `widgets`, `pipeline_comparison` or `execution_services`. Move the matching helpers they use
  (`norm`, `best_option`, `placeholder_option`, `parse_date`, `money_range`, `US_STATES`, alias groups) to `core/matching.py`;
  move `worker_context` (a prompt string) to `answers/prompts.py`; pass `authoritative_packet` in.
- `forms` and `widgets` get the resolver and the profile injected; they import answer *patterns* (`DEMOGRAPHIC`, `DECLINE`)
  from `answers/patterns.py`, not the resolver.
- `applications` is the only layer that knows `Run` state transitions; the engine returns a result dict, `applications`
  persists it.
- `api` and `telegram` call `applications` and `knowledge` services; they never reach into `browser` or `forms`.

## 19. Target directory layout

```
jobpilot/
  core/            config.py  db.py (engine, session)  tables.py  schemas.py  settings_store.py  secrets.py
                   events.py  outbox.py  network.py  matching.py  runtime.py
  knowledge/       store.py  retrieval.py  questions.py  employers.py  migration.py  attestations.py  writing.py  tables.py
                   (absorbs knowledge.py; one store)
  resumes/         master.py  pipeline.py  tailor.py  editor.py  latex.py  compiler.py  evidence.py  tables.py
  discovery/       sources/ (greenhouse, lever, ashby, smartrecruiters, workday, career_page)  feeds.py  search.py
                   postings.py  classify.py  policy.py  roles.py  readiness.py  tables.py
                   (absorbs discovery.py, jobboard.py, jobsearch.py, features/discovery)
  mail/            service.py  rules.py  extract.py  imap.py  oauth.py  inbox.py  receipts.py
  accounts/        store.py  flow.py  alias.py  workday_auth.py
  models/          client.py  admission.py  health.py  settings.py  jev.py  vision.py
  eligibility.py   ats.py
  answers/         resolver.py (the cascade)  patterns.py  intents.py  contact.py  history.py  demographics.py  consent.py
                   choices.py (option matching, fallbacks)  propositions.py (answer_context)  validate.py  model_batch.py
                   prompts.py  quality.py  rules.py (answer_rules)  inference.py
  widgets/         core.py (options, popups)  click.py  dropdown.py  combobox.py  prompt.py (Workday)  date.py  phone.py
                   pills.py  upload.py  matching.py  ats/ (oracle.py  successfactors.py  ui5.py  icims.py  dayforce.py)
  forms/           session.py  scan.py (+ js/scan.js)  fill.py  options.py  uploads.py  phone.py  verify.py  submit.py
                   proof.py  rejection.py  network.py  confirmation.py
  adapters/        base/ (driver.py  steps.py  account.py  mail.py  consent.py)  workday/  oracle/  icims/  taleo/
                   successfactors/  eightfold/  generic/       (each: adapter.py, selectors, notes → docs/rea/maps)
  browser/         engine.py (BrowserEngine.run split into phases)  tab.py (CDP, desktop_cdp)  draft.py  flows/ (guided.py
                   adapter.py navigator.py agent.py)  submit.py  account.py  email.py  challenges.py  trace.py  observations.py
  captcha/         solver.py  client.py  visual.py  twin.py  same_ip.py  observation.py
  applications/    queue.py (Service.queue guards)  lifecycle.py (execute, complete, reconcile)  admission.py  pacing.py
                   budget.py  pools.py (pipeline_comparison, slots)  specialist.py  tiers.py  recovery.py  autopilot.py
                   scheduler.py (workers.py)  repair.py  owner_actions.py  job_control.py  archive.py
  telegram/        bot.py  workers.py  conversation.py  cards.py
  api/             app.py (create_app, lifespan)  middleware.py  auth.py  snapshot.py  schemas.py
                   routes/ jobs.py runs.py profile.py knowledge.py resumes.py discovery.py mail.py accounts.py settings.py
  operations/      cancel.py  close_tabs.py  reset.py  legacy_migrations.py
  cli.py  mcp_server.py
tests/             unit/  integration/ (API, DB)  browser/ (Chromium fixtures)  regression/ (one file per ATS incident)
```

## 20. Splitting the mega-modules

| Today | Target | How |
|---|---|---|
| `answers.py` (4.8k) | `answers/` | Tables (`CONTACT`, `NAMES`, `ADDRESS`, `WORK`, `EDUCATION`, the regexes, `INTENTS`) → `patterns.py` and `intents.py`. `Resolver._local` (916 lines) is a cascade of independent rules; each rule becomes a function `(resolver, field) -> Answer | None` in `contact.py`, `history.py`, `demographics.py`, `consent.py`, `choices.py`, `eligibility.py`, and `_local` becomes a list of them in order. `_resolve` and `jev_choices` → `model_batch.py`; `_validate_answer` (321) → `validate.py`; the model instructions (a 200-line string) → `prompts.py` |
| `forms.py` (5.2k) | `forms/` | `FormSession` keeps state and the contract (`session.py`). `scan` → `scan.py`; `fill_current` (546) and `_fill` (526) split by control kind into `fill.py` and `options.py`; `proof` (471) → `proof.py` with the confirmation helpers; `click` and submit-control classification → `submit.py`; `rejection` and `repair_validation` → `rejection.py`; `NetworkMonitor` → `network.py` |
| `browser.py` (4.4k) | `browser/` | `BrowserEngine.run` (769) becomes phases: `open_application`, `detect_platform`, `choose_controllers`, `finish`. `run_desktop_navigator` (608) and `run_agent` (249) → `flows/`; `_finish`, `watch_submission`, `reserve_commit` → `submit.py`; draft checkpoint and retention → `draft.py`; `handle_account*`, `handle_email`, `browser_challenge` → `account.py`, `email.py`, `challenges.py` |
| `widgets.py` (2.7k) + `widgets_ats.py` | `widgets/` | 83 functions by control family: matching helpers (`norm`, `best_option`, dates, money, phone) → `core/matching.py`; popups and options → `core.py`; clicking and typing → `click.py`; `dropdown`, `combobox`, `prompt` (226), `pills`, `date_parts`, `select`, `checkbox` one module each; Oracle, SuccessFactors, UI5 and iCIMS pickers → `ats/` |
| `service.py` (1.6k) | `applications/queue.py`, `lifecycle.py` | `_queue` (429 lines) becomes an ordered list of guard functions, each returning or raising `QueueBlocked`; `complete` (244) becomes `record_outcome` plus small policy functions (confirmation evidence, sensitive-id removal, ineligible residence, cooldown) |
| `api.py` (1.1k) | `api/` | `create_app` (middleware, lifespan, routers) and one router module per resource, as proposed in [API.md](API.md) |
| `mail.py` (1.9k), `accounts.py` (1.6k), `telegram.py` (1.7k), `adapters/base.py` (1.8k), `adapters/icims.py` (1.5k), `workday.py` (1.2k) | `mail/`, `accounts/`, `telegram/`, `adapters/*/` | Same method: one module per responsibility, long handlers (`MailService.poll_once` 320, `AccountFlow.handle` 431, `_handle_update` 379, `WorkdayAuth.run` 351) split by step |

## 21. Order of work and safeguards

1. **Freeze and measure.** Run the whole suite once on the merged `main`; record the result. Add `tests/test_layering.py` in
   report-only mode listing today's violations.
2. **Lowest layer first.** `core/` (config, db, schemas, matching) with the old import paths kept as one-line re-export modules.
3. **Services.** `knowledge/` (retire the legacy store behind the same functions), `discovery/`, `mail/`, `models/`.
4. **Answering, then the engine.** `answers/`, `widgets/`, `forms/`, `adapters/`, `browser/`.
5. **Applications and interfaces.** `applications/`, then `api/` (route changes follow [API.md](API.md)), `telegram/`.
6. **Remove the shims** once tests import the new paths; make `test_layering.py` blocking.

Each step is one commit series: move with `git mv`, rewrite imports mechanically, run the targeted tests for the package plus
the browser fixtures it owns, and compare `pytest --collect-only` counts before and after. Behaviour changes are never mixed into a move.
The production cut-over deploys the restructured image only after the full suite passes; there is no schema change in the restructure.
