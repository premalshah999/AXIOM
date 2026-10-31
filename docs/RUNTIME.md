# How AXIOM runs today

What the code on `main` does, with file names so you can follow it: terms, state, the path from a posting to a submitted
application, the engine contract, and how a run is confirmed. It was checked against the source on 2026-10-20 and later
edits have kept it close, but `main` wins where they differ. Three things have moved since: production is moving from
Docker to the native launcher ([NATIVE.md](NATIVE.md)), the model-led agent and runner-tier escalation are being deleted,
and the one process drawn in section 2 is what [ARCHITECTURE.md](ARCHITECTURE.md) replaces.

Related documents: [ARCHITECTURE.md](ARCHITECTURE.md) (the target design and how to get there), [KNOWLEDGE.md](KNOWLEDGE.md)
(how a question is answered), [API.md](API.md) (every route), [INFRA.md](INFRA.md) and [NATIVE.md](NATIVE.md) (running it),
[rea/](rea/) (per-ATS behaviour maps).

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
   browser ── HTTPS/JSON ──►  app container: `jobpilot serve` (uvicorn → FastAPI, jobpilot/api/)
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
  process; a "worker" is a slot (a browser owner), not a process (`application_slots.py`). `Service` (`service/`) is
  the application core that the API, the scheduler, Telegram and the CLI all call.
- **Two host Chromes**, started on the machine, never in the container. The worker Chrome (9224) holds one tab per
  running application. The twin (9225) is never attached by Playwright: it only mints hCaptcha tokens for platforms that
  distrust automated browsers (`captcha_twin.py`, iCIMS). Without `BROWSER_CDP_URL` the engine launches a headless
  Chromium inside the container (tests, demos, `jobpilot verify`).
- **Telegram is optional.** A main bot plus up to eight per-slot bots (`telegram.py`, `telegram_workers.py`) deliver
  questions, outcomes and screenshots and accept replies and instructions. It is a view onto the same `reviews` and `runs`
  rows, never an execution owner.
- **Mail is read-only.** Gmail by app password (IMAP) or OAuth (`gmail_imap.py`, `mail/`). The app never sends mail or
  marks messages read. Codes and links go to the owning browser, never into a model prompt.
- **CLI** (`cli.py`): `serve`, `token`, `doctor`, `probe` (dry-run one URL), `trace`,
  `verify` (synthetic end-to-end check), `login` (capture a session), `browser` and `twin-browser` (host Chromes).

## 3. Where each piece of state lives

| State | Where | Written by | Notes |
|---|---|---|---|
| Applicant profile: identity, contact, address, work and education history, `facts`, answer policies | `settings['profile']` (JSON, `schemas.Profile`) | setup guide, `PUT /api/profile`, Telegram, review answers | `extra="allow"`: a key this version does not know is kept. The server owns `reviewed_answers`, `approved_answers`, `forgotten_answers`; `PUT /api/profile` restores them |
| Normalized, versioned facts and confirmed answers | `kn_facts` (+ `kn_employers`, `kn_aliases`, `kn_versions`, `kn_events`, `kn_migrations`) | `features/knowledge` (`migration.sync` mirrors the profile on every lookup; native answers via the knowledge API) | Row ids `m:*` are migration mirrors; others are native and win. See [KNOWLEDGE.md](KNOWLEDGE.md) |
| Legacy reviewed answers | `settings['profile'].reviewed_answers` / `approved_answers` | `knowledge.py` (`save`, `forget`, `learned_answer`) | Second store, mirrored both ways with `kn_facts` |
| Jobs and discovery evidence | `jobs`, `job_listings`, `sources`, `dc_requisitions`, `dc_classifications`, `dc_source_health`, `dc_deferrals` | `discovery.py`, `jobboard.py`, `jobsearch.py`, `features/discovery` | `jobs.status` mirrors the latest run state, plus the pre-run states `new`, `ready`, `review`, `skipped`, `archived` |
| Runs | `runs` (`state`, `packet` JSON, `receipt` JSON), `events` (append-only trace), `reviews` (questions awaiting an answer) | `Service`, `BrowserEngine` | `packet` carries the profile snapshot, pool, slot, pipeline, controller policy, recovery and escalation state |
| Queue delivery | DBOS system tables, and `application_outbox` | `workers.py`, `outbox.py` | The `queued → running` compare-and-set on `runs` is the real idempotency guard; DBOS only delivers |
| Resumes | `resumes` (uploaded PDFs, files in the volume), `rs_masters`, `rs_artifacts` (tailored variants) | upload API, `features/resumes` | A run pins one artifact; a continuation cannot change its PDF |
| Employer accounts and mailboxes | `settings['employer_account:*']` (encrypted), `mailboxes` (encrypted) | `accounts/`, `mail/` | Vault key `DATA_DIR/mail-key`. Credentials never enter prompts, events or screenshots |
| Mail waits | `mail_rules`, `mail_challenges`, `mail_consumption`, `mail_oauth` | `mail/`, `inbox.py` | A challenge is single-use and bound to sender, recipient, tenant and request time |
| Budgets and limits | `budgets` (daily spend and submissions); settings `application_budget`, `runner_tiers`, `application_pacing`, `unknown_resubmit_policy`… | `application_budget.py`, `tiers.py`, `application_pacing.py` | |
| Control and policy switches | settings `control` (`paused`, `auto_submit`), `job_board_settings`, `agent_settings`, `ats_focus`, `job_control:<job>`, `reserved_employers`, `reserved_approval:<job>`, `execution_v2_deployment_hold`… | API, Telegram, operators | `reserved_employers` holds FAANG, MANGO and Big Four jobs at queue admission until the owner approves each (`reserved.py`) |
| Run artifacts | `DATA_DIR/runs/<run_id>/` (`answers.json`, `final.png`, `final_outline.txt`, `steps/`) | `BrowserEngine`, `FormSession` | Read by `/api/runs/{id}`, the repair agent and the resubmit guard |
| Configuration and secrets | `.env` → `config.Settings` | operator | 101 settings; `.env.example` is the complete list |

The `settings` table is also the catch-all document store for per-job and per-run state (`job_origin:*`,
`failure_report:*`, `telegram_*`, `repair_*`, `draft_recovery:*`, `submission_guard:*`…). On a long-running installation it
holds tens of thousands of rows. That works, and it is a design smell: [ARCHITECTURE.md](ARCHITECTURE.md) section 3.3.

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

Run states (`service/states.py`, `TERMINAL`, `ACTIVE`, `RETRYABLE`):

```
queued ─► running ─► submitting ─► confirmed                  evidence required (section 10)
   │         │            ├────────► submission_unknown        committed, outcome unverified; never resubmitted blind
   │         ├─► dry_run_passed                                mode = dry_run; never submits
   │         ├─► waiting_answer │ needs_review                 a required personal fact is missing → question → answer → requeue
   │         ├─► waiting_browser                               control or navigation failure, draft retained → recovery
   │         ├─► waiting_email                                 parked on a durable wait for an emailed code or link; the
   │         │                                                 matched email starts a continuation run. Not a failure
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
2. `apply` marks the job `ready` with its resume (or the first uploaded one) when it was not, resets a spent start
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
  `previously_employed_here` decide the standing choices.
- **The resolver is injected.** `FormSession` and the adapters receive a `Resolver`; the browser layer does not know how
  an answer is found.

## 9. Mail, accounts, CAPTCHA, models

- **Mail** (`mail/`, `inbox.py`, `gmail_imap.py`, `email_browser.py`): `MailService` polls the mailbox; a *challenge*
  is opened by a run (code or link), matched to exactly one eligible message by authenticated sender, recipient, tenant and
  time window, consumed once, and delivered only to the owning browser. A wait longer than 30 seconds is parked as a durable
  request and the slot is released (`resume_mail_wait`).
- **Accounts** (`accounts/`, `workday.py`): one shared login per applicant plus one record per employer origin, encrypted;
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

## 12. Knowledge facts and settings the resolver reads

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
