# Integration of `main` and the adapter engine

Branch `claude/tender-carson-fkhswq` merges `main` (ef1a26c, the app running on your Mac) with the
deterministic ATS engine (b544d64). Nothing in `main` was overwritten; `main` itself is untouched.

## Upgrade (migration note)

1. Keep `.env` and the Docker volumes as they are. No manual database step is needed.
2. Check out the branch on the Mac and rebuild: `docker compose up --build -d`
   (keep running `python -m jobpilot.cli browser` first, as today).
3. Optional new settings in `.env` (defaults are fine):

| Setting | Default | Meaning |
| --- | --- | --- |
| `ENGINE` | `adapters` | `adapters`: deterministic drivers for multi-page portals (Workday wizard, Oracle, iCIMS, Taleo, SuccessFactors, Eightfold, career sites), falling back to main's guided flow. `guided`: main's flow everywhere, exactly as before. |
| `CAPTCHA_MAX_ROUNDS` | `4` | Paid 2Captcha image rounds per run (one hCaptcha challenge is often 2-3 rounds). `CAPTCHA_MAX_ATTEMPTS` still bounds challenges per page. |
| `APPLICATION_PASSWORD` | empty | Unchanged meaning. `ACCOUNT_PASSWORD` is accepted as the same setting. |
| `ACCOUNT_EMAIL` | profile email | Optional login email override. |
| `ACCOUNT_PASSWORD_RESET` | `true` | Allow recovery through the reset email when an employer rejects the saved password. |
| `MULTIPAGE_TIMEOUT` | `480` | Seconds for multi-page portals (single-page forms keep `APPLICATION_TIMEOUT`). |
| `TELEGRAM_WAIT_SECONDS` | `120` | How long a running application keeps its page open for a live Telegram answer before deferring. |
| `TRACE_STEPS` | `true` | Per-step summaries (and screenshots/HTML for non-sensitive steps) under `DATA_DIR/runs/<id>/steps`. |

Removed: `AUTO_REQUEUE` (an answered question always resumes its application), `ACCOUNT_AUTO_CREATE`
(the profile switch "Create and reuse employer accounts" decides), `IMAP_HOST/PORT`.

What happens automatically at startup (idempotent, safe to restart):

- **Profile.** Every stored key is kept, including keys this version does not know. Saved address
  facts (`street_address`, `postal_code`, `county`, `country_of_residence`, ...) fill the new
  structured address only where it is empty. Reviewed answers, `application_source` (LinkedIn), the
  consent switches and `autonomous` are untouched. Saving the profile from the dashboard never
  drops reviewed answers.
- **Credentials.** The shared login already stored encrypted in your database keeps being used for
  every employer. `APPLICATION_PASSWORD`/`ACCOUNT_PASSWORD` only seeds a new shared login, and an
  unset variable never disables account creation (one strong password is generated once).
  Per-employer records keep their saved password until a reset is confirmed.
- **Earlier adapter branch data** (only if that branch ever ran against this database): its
  `knowledge` table is copied into reviewed answers with scope, and its `accounts` table into
  encrypted records only where its fingerprint proves the configured password. The old tables
  are left in place; `migrated:*` settings record the one-time import.
- **Email rules.** Existing rules are kept; Workday rules gain `otp.workday.com`. Oracle, iCIMS,
  Taleo, SuccessFactors, Eightfold, SmartRecruiters, Avature and Jobvite rules are created on first
  use (Greenhouse and Workday already were).

## How each ATS is driven (`ENGINE=adapters`)

| ATS | Path |
| --- | --- |
| Greenhouse, Lever, Ashby (+ SmartRecruiters, Workable, BambooHR) | main's guided flow and email verification, unchanged; desktop navigator as fallback |
| Workday | main's `WorkdayAuth` for account access (both engines), then the Workday adapter for the wizard |
| Oracle, iCIMS, Taleo, SuccessFactors, Eightfold, unknown career sites | adapter state machine; unrecognized pages fall back to the guided flow, never after a submit was attempted |

## Trust rules

- **Confirmed** needs a website confirmation captured after this run's submit (it must differ from
  anything shown before the click), an authenticated acknowledgement email from the employer or an
  ATS sending for it, or your reconciliation. Anything else becomes `submission_unknown` (if a
  submit was attempted) or needs review. A confirmation-looking page this run did not produce is
  never recorded as an application.
- Receipts keep **`website_confirmed`** and **`email_confirmed`** separately. An authenticated email
  from an unrelated sender is kept as evidence but cannot confirm.
- `submission_unknown` is never retried automatically. A restart during the final submit marks the
  run `submission_unknown`; it cannot be queued again; a trusted acknowledgement email can still
  confirm it later.
- **Codes and links** come only from main's MailService: Google's receiving `Authentication-Results`
  with aligned DMARC, a sender in the employer's rule (aligned subdomains; employer-owned domains
  for families that send that way), the exact applicant address, the request window opened before
  the website sent the mail, no conflicting tenant (e.g. `globex@myworkday.com` never verifies an
  Acme application), exactly one matching message, consumed once. Runs whose employers share a
  sender are serialized (shared-sender lease), so a code cannot reach the wrong application.
- **Secrets** stay out of model prompts, events and traces: codes are sealed in the vault until the
  browser claims them; auth-field values are masked in every observation; account and
  verification steps keep only a text summary (no screenshot or HTML).

## Answers (autonomous policy)

The AI chooses wording, examples and emphasis without asking when the resume/profile and job
description support it, and may synthesize why-company/why-role answers. With a known fact it
picks the closest valid option; it skips unsupported optional fields; consent and the LinkedIn
source follow your switches. It never invents personal facts, qualifications, dates, identity,
legal disclosures or eligibility. Only a genuinely missing required fact is asked on Telegram (one
question at a time, a running application first); the reply is saved with provenance and the
application resumes. Relationship and motivation answers stay scoped to their employer.

## Test evidence

Run on this branch (Linux, Chromium 1194, no network access to employers):

```
.venv/bin/python -m pytest -q        # 162 passed (main's tests + the adapter suite + trust tests)
.venv/bin/ruff check jobpilot tests  # clean
cd frontend && npm run build         # type check + build pass
```

Owned (mock) coverage relevant to the handoff: dry runs on every mock portal record zero
submissions; owned submit runs record exactly one submission and one website receipt; decoy
verification emails (spoofed, look-alike domain, other recipient, other tenant) are never entered;
a restart during submit cannot resubmit; CAPTCHA widgets never become fields or navigation; an open
Reset Password dialog takes precedence over the page behind it; geolocation is denied in pages
and popups; resume after a Telegram answer. **These are mock results, not evidence that live
tenants work.**

## Live results — Mac deployment, October 3–4, 2026

The integrated app is deployed locally on `codex/local-production`. Existing secrets, browser,
profile, account records, resumes, and all earlier employer confirmations were preserved.

| ATS / employer | Latest evidence | Status |
| --- | --- | --- |
| Oracle / JPMorgan Chase, job 210771693 | Submit run `e9c732a6286a45ae9d662380a82b74ab`: explicit application thank-you page, role listed Under Consideration; authenticated employer email “We received your job application (Job Number: 210771693)” matched and stored | **Website and email confirmed. Do not resubmit.** |
| Workday / Mimecast, R6424-1 | Submit run `42dbe7e309794ccaa84e64a0637f25b3`: Candidate Home lists “Application Received”; authenticated “Thanks for your application!” email matched and stored | **Website and email confirmed. Do not resubmit.** |
| Workday / Snap, Q426SWEML1 | Dry run `859b7c262ae74aaeac20bd8b55278747` reached final review in 29.94 seconds with one model call ($0.0068). Submit run `337e8d0f01964e1c838b434c2b88a1b3` reached the saved review page and submitted once in 5.70 seconds with zero model calls. Workday showed a confirmation, then authenticated mail from `snapchat@myworkday.com` acknowledged the exact role. Both evidence fields are stored. | **Website and email confirmed. Do not resubmit.** |
| iCIMS / HealthEdge, job 8601 | Run `08d56e20de2a439480061f979993f1fc`: live CAPTCHA accepted; identity/password screens reached; site explicitly rejects saved login. Reset request shows Check Your Email, but no matching reset email is present, including Spam. No sign-up control offered; registration route returns an employer login error | **Blocked on account access; not submitted.** |
| iCIMS / SK Life Science, job 1947 | Dry run `fa1ca906c8534b6db476cfef81acf73f`: live CAPTCHA accepted, but the saved password was rejected. The reset page said to check email; no reset mail from iCIMS or the employer arrived. | **Blocked on account access; not submitted.** |

Oracle's final successful submit took 11.04 seconds and zero model calls. Workday's took 10.69 seconds. This is the final
commit duration, **not** the total debugging time. Two earlier submits were explicitly rejected
by Oracle's validation; neither is counted as a successful application.

Live fixes include country and address option matching, keyboard-driven dependent fields,
collapsed education validation, required-field markers, resume-card verification, and optional
URL formatting. Workday authentication uses keyboard events, scoped controls, trusted mail,
and bounded reset recovery. Google's parent-domain DMARC result is accepted only when the
exact sender domain has a passing DKIM signature. Bare reset URLs inside HTML are supported.
Snap also exposed three general answer errors: spouse-based authorization was conflated with
general work authorization, a mention of disability in an onsite question was mistaken for a
demographic disclosure, and the requested date retained an old value. These were fixed before
submission and verified on the final review page. A saved Workday review page is now recognized
as an application form on resume. Authenticated Workday acknowledgements can match an exact
tenant sender when the email omits the company name but names the job.

Current user settings: one worker, Telegram disabled, autonomous supported answers, LinkedIn
source, terms/privacy accepted, marketing accepted only when mandatory. Demographics and
legal answers use the user's saved facts; missing facts are not fabricated.

Open Studio at http://127.0.0.1:8080. Start the existing installation with:

```sh
python -m jobpilot.cli browser  # only if the dedicated ApplyPilot Chrome is closed
docker compose up -d
```

The isolated full suite passed **195 tests in 412.66 seconds** before these latest live fixes.
Afterward, targeted engine/Workday tests had **11 passes** and one newly added test failure;
the cause was normalized hyphen spacing, now fixed and the failing test passed on rerun.
The saved-review and answer-scope tests passed together (**2 passes**), and all receipt tests
passed (**4 passes**). Ruff check and formatting pass on the changed files. The full suite has
not been rerun since these latest fixes.

Do not probe or queue confirmed jobs again. No further password reset has been queued for the
blocked iCIMS accounts. A working employer login or a delivered reset email is needed before
resuming either iCIMS application. Both were dry runs; neither attempted final submission.

The earlier confirmations at Blend,
My Funded Futures, Cargomatic, Zip Co, May Mobility and Ramp must also remain untouched.

## Not done (later work)

The handoff's P3 items are only partly in place: the shared-sender lease, sealed secrets in the
vault, and persisted waiting states (`waiting_answer`, `waiting_browser` with the open desktop
tab) exist; typed service boundaries, typed events, vault handles for every secret, and
LangGraph-style checkpoints do not. DBOS still owns queueing, retries and the single commit.
