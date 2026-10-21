# ApplyPilot developer guide

## Run and maintain

Use Python 3.12, Node 22 and Docker Compose; the exact versions are pinned in `.python-version`, `.nvmrc` and the Dockerfile. `make up` (`scripts/setup.sh`) creates `.env` from `.env.example` with a generated database password, builds the one image, starts it with PostgreSQL and prints the address. Add provider keys to `.env` privately (it is created with permissions 0600). Never commit secrets or applicant data. Install steps for other people are in [docs/SETUP.md](docs/SETUP.md); the image, settings audit, worker topology and cut-over notes are in [docs/INFRA.md](docs/INFRA.md).

```sh
uv sync --frozen
make frontend      # npm ci && npm run build, into jobpilot/static, for local development
make up            # build the image and start the app
docker compose up -d --build --no-deps app   # later releases: rebuilds the changed layers (code and dashboard) only
```

The dashboard is at http://127.0.0.1:8080. `docker compose exec app jobpilot token` prints the private access token; view it locally. `GET /healthz` is the unauthenticated health check. The running code identifier is returned by `/api/runtime-health` and `jobpilot.runtime.engine_version()`.

First run and sign-in (`owner_account.py`, `frontend/src/onboarding.tsx`): an installation with no account, no profile identity and no jobs offers sign-up; any other installation accepts only the access token until its owner adds an email and password in Setup → System. Sign-up opens the setup guide: resume upload and prefill, contact, work authorization, roles, screening questions, self-identification, permissions (including automatic submission, which is off on a fresh install) and review. `scripts/demo_instance.sh` starts a throwaway instance (own SQLite database, no workers) for showing that flow without touching production.

## Repository layout

- `jobpilot/`: queue, owners, shared browser/form controls, answers, accounts and mail.
- `jobpilot/adapters/`: ATS-specific steps using those shared services.
- `jobpilot/features/`: knowledge, discovery, resume and other feature modules.
- `jobpilot/operations/`: data-preserving installation maintenance.
- `frontend/src/`: dashboard source; `jobpilot/static/` is generated build output.
- `tests/`: focused regressions and synthetic shared fixtures.
- `scripts/demo_instance.sh`: a throwaway local instance for showing sign-up and the setup guide.
- `docs/`: `ARCHITECTURE.md` (system design and target layout), `API.md` (every route), `KNOWLEDGE.md` (answers), `SETUP.md` and `INFRA.md`, the per-ATS maps in `rea/`, third-party notices and licenses (copied into the image) and Telegram worker notes.

Applicant state belongs in the existing private storage and Docker volumes, never source fixtures or documentation. This file is the maintained guide. Earlier versions are branches, not folders: `v1-oct04` (commit `825c1b6`, the October 4 engine) and `v2-oct15` (the code before the October 15 cleanup).

## Application pipeline

`service/` (`Service`; `queueing.py` holds the admission guards) owns canonical queue admission and submission reservations. Queue only through `POST /api/jobs/{id}/queue`. `workers.py` owns durable execution; `outbox.py` delivers background continuations. `application_slots.py` limits browser owners to `NORMAL_WORKERS` + `SPECIALIST_WORKERS` (6 + 2) and releases slots during saved fact/email waits.

`browser.py` coordinates one writer per application. Desktop owners reuse a healthy automation connection without repeating HTTP endpoint discovery; a new connection gets one bounded discovery retry. Owners share one automation connection per runtime event loop; each retains its own page and lease. A frozen saved tab does not trigger a fresh all-page attachment for every new owner. ATS adapters and model navigation use `forms.py` observations and verified widget actions. `answers.py` resolves supported profile facts, reviewed knowledge and a model answer batch. Required unknown personal facts remain questions. The demographic decline policy recognizes the offered label Undeclared without inferring race. Embedded job-alert frequency fields are excluded from application questions, and alert signup is refused. General job-search/catalog controls are refused during an owned application; the controller must save the profile and return to the original posting. The complete-employment attestation carries all compact confirmed employer rows under one citable reference. Answer calls include a compact confirmed applicant context plus scoped knowledge. Editable pickers search before declaring a value unavailable. Institution catalogs follow the current applicant instruction: prefer offered Other and skip optional institutions when Other is absent; free-text institutions retain the actual school. A sibling provider failure is reported after supported answers commit and conditional fields are rescanned; saved progress survives it. `answer_quality.py` checks objective saved facts locally. Its model second opinion is advisory and cannot overwrite answers or reopen completed pages. Page checks do not buy audit calls; final advice is cached across reloads. Supported factual repairs get two bounded attempts, then retain a non-retryable draft. Optional audit failures do not block the owner; actual field validation remains independent. A final submit requires the durable reservation and an armed page. Intermediate click permission cannot authorize a button that changed to Submit, including buttons inside shadow DOM.

`privacy.py` owns geolocation denial and policy-authorized cookie dismissal. Forms, adapters and Workday authentication use the same native pointer/notice-dismissal path. A saved application source absent from the observed catalog enters control recovery; it is not a new personal-fact question.

Oracle picker readback may rematch a replaced input only to the same field and frame; the new display must still commit the supported choice. A failed radio cannot acknowledge an unrelated agreement. An empty Oracle jobs shell can return once to the exact original posting in the same candidate site before submission; actual fields, confirmation, verification and armed owners prevent this recovery. Previous checkpoints remain in run history, while the returned document must verify its own fields and uploads.

Oracle's `/sites/<site>/jobs` search and resume-recommendation page is not an application. Shared filling and verification refuse it, even with an old verified ledger; the historical bridge uses the same fill boundary. The adapter returns to the same original posting once or retains a catalog-entry blocker without choosing another requisition. Greenhouse/React-select readback uses the visible selected value in the same control, including multiple-choice chips and the short phone-country display, rather than the cleared search input.

Keep the configured master resume and complete supported history. Deny geolocation. Dry runs never perform final submission. Confirm only a job-specific acknowledgement or authenticated employer receipt; never count account activation, talent communities or upload success. Preserve `submission_unknown` and existing duplicate guards.

October 12 repair: numeric Oracle Start/End Date Month values map to month names before search/readback. Remote country/state searches try bounded supported equivalents after an empty exact query. Native filename buttons can prove accepted uploads only inside the resume component. Oracle’s filled `resume-upload-button` component supplies that identity when no visible Resume label exists; cover-letter tiles and upload errors cannot prove it. Oracle email-preview popups close without reading their code; the original application and correlated private mail helper keep ownership.

Workday recognizes saved screening pages containing dropdowns/textareas instead of contact inputs. Header language/settings menus are excluded from applicant fields and agent actions. Ordinary forward-click failures hand the same page to the model controller. Render/transition waits use elapsed-time deadlines and stop on returned validation. Ashby nested Yes/No groups use their nearest question title, rather than the first Country title in a shared collection.

Current applicant instructions persist No for prior employment at the applying company, scoped away from family/client questions and conflicting actual employment history. That No is not an employment date/name answer. Institution catalogs prefer Other; optional institutions without Other are skipped. Free-text institutions and awarded degrees keep supported facts. The agent receives evidence for up to twenty current questions with shared references deduplicated. Cited committed text/choice answers reuse native readback only with unchanged profile, knowledge and question scope; changed facts or values require revalidation. Required questions held by an unavailable answer provider remain explicit pending questions in the verification report. Broad professional reputation/disciplinary disclosures require their own confirmed applicant answer; narrower criminal-history or privacy-discipline facts cannot establish No. Native prefill or cached readback cannot override a missing-fact review, including a filled optional disclosure. Explicitly associated help text participates in question identity, retrieval and screening validation, including OPT definitions. Single-checkbox answers use an explicit checked state instead of the acknowledgement caption.

Closed-tab recovery encrypts the same-origin continuation URL and native session storage with the existing vault, bound to the job and pinned resume. Storage is restored only during initial navigation, without replaying old state after native rotation/logout. A memory-only Oracle apply route without native session storage restarts at the exact owned posting for fresh authentication; email continuations and armed/verification-only routes are preserved. Unknown continuations remain verification-only and cannot refill/resubmit.

## CAPTCHA

`captcha_client.py` calls official `2captcha-python` methods in a cancellable subprocess: hcaptcha, recaptcha or turnstile. It sends secrets on stdin and returns the token only to the owner. Both SDK timeouts are 300 seconds and polling is five seconds. Configure `TWOCAPTCHA_API_KEY`, `CAPTCHA_TIMEOUT` and `CAPTCHA_MAX_ATTEMPTS`.

`captcha.py` observes render callbacks and pending execute promises. It delivers the token only to the same widget generation. Native completion preserves the website callback and avoids another purchase. A token from an earlier widget cannot verify a failed new challenge. Solver completion is not submission proof. Missing callbacks, changing widgets and provider errors stop unchanged paid retries. There is no custom image solver, force-enabled button or form.submit bypass.

Before final submission, hCaptcha and reCAPTCHA tokens aged 90 seconds require a bounded refresh. A failed refresh cannot be purchased again by polling the same generation. hCaptcha's observed invisible flag and request data are passed through the official SDK. Neither fresh age nor callback delivery proves that the employer will accept the token.

After a new token handoff, the browser observes the native transition for at most two seconds before classifying a still-visible challenge. This handles asynchronous callback navigation without another purchase or click; the account controller and submission evidence still verify the resulting state.

The official SDK/browser contract regression remains. Removed historical research and redundant regression scripts are recoverable from the cleanup archive outside the checkout. No fixture proves live employer acceptance.

## Accounts and email

`accounts.py` encrypts credentials with the mailbox vault. Account keys include observed employer/tenant context. `APPLICATION_PASSWORD` is the shared new-account/reset preference; `ACCOUNT_EMAIL` overrides the profile email. `ICIMS_LOGIN` supplies the owner-provided iCIMS credential. Preserve existing credentials until accepted login or reset; a Check Your Email page does not prove account existence or delivery.

Setup → Email connects Gmail with an app password (`POST /api/mail/imap`): the login is verified first, the password is sealed in the mailbox vault and never returned, and `GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD` in `.env` keep working. The mailbox must be the profile email, because challenges match mail by recipient.

`gmail_imap.py` reads Inbox, All Mail, Spam, Junk and Trash with BODY.PEEK and a 30-second transport timeout, reconnecting once on failure. OAuth Gmail searches also include the whole mailbox. `mail.py` enforces sender authentication, recipient, request time, tenant and consumption guards. HTML hrefs remain available for reset links. Greenhouse codes require the whole employer name and select a uniquely newest eligible message. Ties remain ambiguous.

Code delivery has a bounded thirty-second inline wait before parking the same durable request. Cleanup preserves the exact request parked by any owned ATS mail helper. Oracle regional workflow mail domains identify infrastructure, not another employer; authenticated sender, recipient, window and single-use checks still apply.

`email_browser.py` owns verification actions. Greenhouse aliases may enter a code only for the exact owned board and requisition; code permission does not expand trusted link origins. Workday links require tenant-bound evidence; a shared wdN origin alone is insufficient. `receipts.py` assigns one authenticated receipt to one posting. Verification and activation messages are not receipts.

Never put credentials, codes or verification URLs in model prompts, diagnostic events or screenshots. `diagnostics.py` masks secret controls and reflected secrets while retaining ordinary form values. Telegram uses those masks.

## Models, cost and speed

Provider transport failures stop the current model strategy and retain its draft. `provider_health.py` scopes cooldowns to provider, endpoint and model. A rate-limited HTTP 429 immediately respects bounded Retry-After guidance, or sixty seconds when absent; other repeated provider errors retain the five-minute cooldown. Quota exhaustion is classified separately. Application-owner capacity stays unchanged when recovery is ready; transport leases permit only one productive recovery request. One ordinary productive request may test recovery after the cooldown; no synthetic probe or automatic provider switch is used. Cancelled owners do not count as provider failures, and a late reply cannot clear a newer cooldown. Optional diagnostic and quality-advice requests do not change application-provider health, and cannot claim its productive recovery request. They are skipped while that provider is cooling down or awaiting recovery. `/api/runtime-health` and System settings expose role cooldowns and retry times. Source fetching and mailbox/receipt processing continue independently.

System exposes six persisted helper switches and independent answer/navigation selectors: MiMo, DeepSeek and Claude Haiku. `/api/agents` returns availability and model identifiers, never credentials. Changes apply at the next helper boundary; per-call config copies prevent concurrent owners from changing one another’s provider. Disabled helpers stop their work; ordinary forms can continue without a CAPTCHA helper when no challenge is present.

Haiku uses the native Anthropic Messages SDK, with JSON validated against the requested Pydantic schema. Configure `ANTHROPIC_API_KEY` (or `HAIKU_API_KEY`), `HAIKU_MODEL` and billing prices. Haiku 5.5 uses $0.10/$0.50 per million input/output tokens up to 100,000 input tokens and five times those rates above that threshold; byte-based reservations conservatively apply the same threshold. See [Anthropic model pricing](https://platform.claude.com/docs/en/models/haiku-5-5/overview). MiMo and DeepSeek use their configured OpenAI-compatible endpoints. Thinking is disabled for production answers/navigation. Native provider errors never trigger another provider purchase automatically.

`models.py` enforces whole-call deadlines, priority admission, model-call caps and daily budgets. Optional quality audits and diagnostics have background priority; answer and navigation requests go first. Quality advice skips the final two model calls reserved for owner progress. Each helper acts through the owning worker; no helper may adopt another worker’s page, invalidate its lease, or change shared provider configuration. A reasoning timeout does not recursively buy additional requests. SDK-wrapped provider and budget exceptions preserve their type; exhausting a call cap cannot schedule model-output repair. `model_call` events record provider/model, latency, token counts and estimated costs without prompt text. Improve speed by inspecting these measurements and repeated unchanged widget outcomes before adding retries.

## Tests without employer runs

Use isolated fixture databases and fake credentials. Local browser fixtures are headless and do not activate desktop Chrome.

```sh
uv run pytest -q                      # full suite, about six minutes
uv run ruff check jobpilot tests && uv run ruff format --check jobpilot tests
npm run build --prefix frontend
```

The retained suite covers CAPTCHA transport, final-control mutation, mail correlation and expiry, receipt attribution, prompt commitment, complete-history answers, helper switches, durable outbox, cancellation and configured iCIMS login. Tests do not run as part of application execution; run the full suite before every deployment.

Shared browser fixtures live in `tests/conftest.py`; synthetic mail and iCIMS observations live in `tests/support_mail.py` and `tests/support_icims.py`. Retained checks must not import deleted historical test modules.

## Safe deployment and data preservation

1. Persist global pause and disable automatic submission through `/api/control`.
2. Save the exact current deployment hold, set it to draining, and independently verify zero queued/running/submitting owners. Never restart beneath final submission.
3. Build, recreate only the app, check health/version, then restore the exact prior deployment hold. Keep any owner-requested global pause.
4. Record redacted changes, test counts, image and engine version in the maintenance journal.

PostgreSQL and applicant data use named Docker volumes. Never run `docker compose down -v` on this installation. Keep database and CDP ports private. Dedicated CDP Chrome uses the persisted `application_browser.visible` preference. The owner requested visible worker tabs on October 10: each owner foregrounds its own exact tab at startup. A false or absent preference keeps new tabs in the background. Never adopt another worker's page.

`operations/cancel.py` cancels only unreserved saved drafts after pause and drain. It preserves packets, reviews, events and receipts, disables their recovery, cancels unsent deliveries and clears cancelled verification payloads. Reserved histories remain protected and paused.

Legacy duplicate guards (including the apply-v2 ledger) were imported on 2026-10-10 as historical protection, not confirmations; the one-time import tools were then removed. Legacy accounts, answer memory, fit verdicts and browser profiles are preserved privately under `.data/migrations/2026-10-10/`.

`application_pacing.py` limits unique confirmed/reserved applications to three per employer and admits one Ashby owner with at least 150 seconds between new starts. Pacing defers admission before consuming an executor slot. Other ATS owners continue. The migrated public catalog supplies Greenhouse/Ashby board slugs and SAP/iCIMS career-page hints; every hint still goes through current fetch, freshness, suitability and duplicate checks.

The October 4 engine is retired: drafts that still carry its pipeline name continue on the current engine, as new runs do. While runner tiers are off, agent-led and reasoning-first drafts also continue on the hybrid controller (`engine_for`). Its code lives only on the `v1-oct04` branch; never attach that version to production, because it does not understand the current duplicate guards.

## ATS owners and difficult-flow escalation

`pipeline_comparison` reserves `NORMAL_WORKERS` (6) normal slots and `SPECIALIST_WORKERS` (2) specialist slots; `specialist.py` decides which failed drafts move to a specialist and how much larger its budget is (docs/INFRA.md, "Workers"). New jobs receive guidance for their actual ATS. Historical drafts continue on the current engine; the model-directed continuous controller runs only while runner tiers are enabled (off since 2026-10-14). Private account, mail and CAPTCHA helpers remain shared; there is one writer per job. Complete history, supported facts, resume identity and final-submit reservations survive handoff.

Repeated actionable failures on an unsubmitted draft may hand off after the previous browser owner has detached. The specialist reads retained failure evidence and must choose a different supported strategy. Missing personal facts, unavailable providers, email waits and uncertain submissions do not become speculative retries. Specialist attempts have their own finite budget; they cannot consume the six regular slots or reset exhausted ordinary attempts. Global employer ownership still applies across both queues.

Enable normal intake through the existing autonomous batch endpoint. Freshness, suitability, protected submission history and three applications per employer remain enforced. Only real job-specific acknowledgement or authenticated receipt counts as success. Neither eight available slots nor an agent architecture guarantees employer acceptance.

`POST /api/runs/{id}/verify` can recover a retained uncertain code-verification draft after its previous request ended. It queues a verification-only owner using the original submission reservation. The owner opens a new mail request before clicking a unique observed Send New Code/Resend control on the dedicated trusted verification page. It never resurrects cancelled challenges, refills the application or submits it again. A dedicated email-only employer login can be verified for portal reconciliation. Authentication success alone is not application confirmation. Verification errors preserve the owned browser draft; auth button labels compare case and whitespace consistently with the scanner.
