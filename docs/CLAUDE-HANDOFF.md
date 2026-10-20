> **Historical handoff — superseded by the [live deployment report](integration.md#live-results--mac-deployment-october-34-2026).**
> October 4 update: Oracle/JPMorgan Chase and Workday/Mimecast are submitted with both website and authenticated email confirmations. Do not resubmit either job. iCIMS/HealthEdge is blocked on account access. The integrated app is deployed locally on `codex/local-production`.

# Claude Code implementation handoff — 2026-10-03

Copy the prompt below into Claude Code. This is an engineering handoff based on a local code review, isolated tests, and actual employer runs. Preserve the user's data and the existing working applications.

---

You are continuing ApplyPilot. Implement and verify the work below. The user wants autonomous applications with minimal questions and low model cost. Complete the engineering work that can be tested in your environment. Clearly identify any live checks that require the user's Mac; never count a mock application as a real submission.

## Repository baseline

- `main` at `ef1a26c` contains the locally deployed browser, encrypted accounts, two-layer knowledge base, Gmail, Telegram, CAPTCHA fallback, and receipt matching.
- Your branch `claude/tender-carson-fkhswq` at `b544d64` adds ATS adapters, custom widget drivers, structured history, and mock ATS tests.
- These commits are **siblings** with common ancestor `08ca918`, not successive versions. Integrate both branches on a new integration branch. Do not overwrite main with your branch.
- A small subsequent local change to `jobpilot/answers.py` explicitly delegates wording, emphasis, and selection of supported examples to the model. Preserve that policy.
- Preserve all `.env` secrets, PostgreSQL volumes, saved profile/review answers, resumes, account records, receipts, and open browser sessions. Tests use isolated databases and fake credentials. Never print secrets.

## Verified results

Your Docker/frontend build succeeds. Your existing suite passes: **93 tests in 164.02 seconds**, run in an isolated Docker container on the Mac. Those are synthetic tests, not live application evidence.

Three additional negative tests in `docs/claude-review-regressions.py` fail on your unchanged branch, reproducing the unauthenticated OTP and false-completion defects below. Copy that file into the integrated test suite and make all three pass. The locally deployed app's post-update health/API/assets/persisted-evidence recheck passed.

The production database has six earlier confirmed applications, each with a matched employer receipt email: Blend, My Funded Futures, Cargomatic, Zip Co, May Mobility, and Ramp. Never resubmit those jobs.

A new bounded retry of each problematic ATS produced **zero new submissions**:

| ATS | Employer | Run | Result |
| --- | --- | --- | --- |
| Workday | Mimecast | `47602c4f8cde4812971e62ecdfd07e13` | `waiting_browser`: application form not reached within account setup window |
| iCIMS | HealthEdge | `a3bd688b338f4c398c149875913831ec` | `waiting_browser`: two image-solver rounds did not clear the challenge |
| Oracle | JPMorgan Chase | `ed43cd63c8c8447c92b0859b678a1e95` | `waiting_browser`: solver returned coordinates outside the allowed puzzle region |

Current authorized test URLs:

- Workday: https://mimecast.wd5.myworkdayjobs.com/en-US/Mimecast-Careers/job/Machine-Learning-Engineer-II_R6424-1
- iCIMS: https://careers-healthedge.icims.com/jobs/8601/machine-learning-engineer/job
- Oracle: https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1002/job/210771693/

Read-only tests of your scanner/classifier against the actual open Chrome tabs found:

- iCIMS and Oracle CAPTCHA controls (`Skip`, `Refresh Challenge`, accessibility controls) enter the application observation, and the step is classified as `form`.
- Workday's open **Reset Password** dialog is classified as `job_details` because background job controls take priority.

## Priority 1 — make state and evidence trustworthy

1. **False completion, reproduced:** `adapters/base.py` accepts `Thank you for your interest. Sign up for job alerts.` as `CONFIRMATION`. With `committed=True`, `on_confirmation()` returns `confirmed` even when `form.proof()` returns `None`, synthesizing an `explicit_confirmation_page` receipt. Before commit it incorrectly returns `already_applied`. Require explicit application-specific evidence tied to the current job/run. An acknowledgement, newsletter page, account activation, or upload success is insufficient. Never fabricate a receipt when proof is absent; retain `submission_unknown` after an uncertain commit.
2. **Untrusted OTP, reproduced:** `inbox.py` calculates `Message.authenticated` but `Inbox.wait()` never checks it. An unauthenticated message from `attacker@unrelated.test`, mentioning employer `Acme` and containing one code, is consumed. Validate the receiving provider's authentication header, aligned sender domain, exact recipient, request window, and tenant identity. Reject conflicting matching messages instead of choosing the newest named message. Keep OTPs/links/passwords outside model prompts and traces.
3. Preserve main's `receipts.py`, explicit rejection handling, employer spam cooldown, and duplicate/uncertain-submission protection. Keep `website_confirmed` and `email_confirmed` as separate evidence fields.
4. Add negative tests for all three cases above and for restart during a final-submit request. Synthetic happy paths alone currently miss these regressions.

## Priority 2 — integrate and fix the actual blocked steps

1. Preserve main's `BROWSER_CDP_URL` and CLI `browser` command. Docker must attach to the dedicated Mac Chrome session. Every run owns one page; route handlers and browser agents must not close or mutate another worker's page. Preserve current and popup geolocation denial from `privacy.py`.
2. Port your adapter/widget improvements into that browser session. Scan application frames with bounded timeouts. Exclude CAPTCHA provider frames from applicant fields and navigation; expose them only to the CAPTCHA service. Handle modal precedence: active sign-in/reset/create dialogs before background Apply buttons.
3. Workday: add an explicit reset-email step, use visible overlay controls, inspect redacted response status/error codes, wait for the actual application form rather than temporary password-field disappearance. Distinguish reset request, reset email, reset success, account activation, and authenticated application. `otp.workday.com` was an actual sender for this applicant. Preserve known credentials until a replacement is confirmed. Do not burn repeated reset attempts when delivery has not occurred.
4. Preserve main's `TWOCAPTCHA_API_KEY` fallback. CapSolver rejects hCaptcha token tasks; your branch currently ignores this fallback key entirely. Use CapSolver only for supported challenges. The configured 2Captcha service uses its documented `CoordinatesTask` API. Main now sends JPEG at quality 80; the earlier PNG exceeded 600 kB.
5. Improve the image fallback using a **puzzle-only crop**, separate instructions, known coordinate transforms, a challenge identity/hash, and a clear click-versus-drag contract. Current full-widget screenshots include navigation controls and cause invalid coordinates. Reobserve between rounds; reject stale solutions and clicks outside puzzle bounds. Validate website acceptance, not just token delivery or API success. Keep provider/time/spend limits; do not increase concurrency or blindly repeat failed tasks.
6. Schema migration: preserve main's facts, narratives, reviewed answers, source=LinkedIn, consent settings, and autonomous flag while adding structured address/history. Migrate confirmed address facts into `Profile.address`; do not lose existing values through Pydantic's ignored extra fields. Main's encrypted shared credentials and your `ACCOUNT_PASSWORD`/Account table need an explicit migration; an unset new environment variable must not silently disable account creation.
7. The user has already saved work authorization, sponsorship, citizenship/OPT details, source, relationship/government/conviction answers, and address in the local profile. Read those facts locally; do not ask again or embed private details in this document. Normalize question scope and negation before reusing an answer.

## Autonomous answer policy

The AI chooses wording, relevant examples, and narrative emphasis without review when supported by the resume/profile and job description. It may synthesize a new why-company/why-role answer; a verbatim stored answer is unnecessary. Use the closest valid offered option when the underlying fact is known. Skip unsupported optional fields. Follow the user's recorded consent and LinkedIn source policy. Do not invent personal facts, qualifications, employment dates, identity, legal disclosures, or eligibility. Ask through Telegram only for a genuinely missing required fact, save the answer with provenance, then resume. These current failures are principally account/challenge/state problems, so removing answer validation alone will not fix them.

## Priority 3 — orchestration and model cost

Keep DBOS responsible for job scheduling, durable state, cancellation, retries and final-submit reservation. Extract services with typed contracts:

- **Browser worker:** one owner per application tab; deterministic ATS state machine and widget operations.
- **Mailbox service:** IMAP/OAuth polling, trusted OTP/link correlation, one-use consumption, and receipt matching. No LLM required to handle secrets.
- **Account service:** per-employer identity/session state, encrypted credentials, bounded reset/verification flow.
- **Answer service:** exact facts and reusable KB first; inexpensive structured model for new narratives; stronger model only after a specific unresolved ambiguity. Cache with profile version, question scope, options, employer and relevant job context.
- **CAPTCHA service:** supported provider dispatch, isolated puzzle image, bounded attempts, website acceptance evidence.
- **Receipt service:** DOM and authenticated email evidence; no LLM decides whether a submission happened.
- **Telegram service:** events → one edited status card per run, owner-only replies, durable question IDs, save-and-resume. Add presentation polish after the ATS fixes.

Use typed events such as `NeedEmail`, `EmailMatched`, `NeedFact`, `FactSaved`, `ChallengePending`, `SubmissionAttempted`, `ReceiptMatched`; include run/job IDs and correlation IDs, never raw secrets. Put secrets in the vault and pass handles. Persist waiting states and wake workers from events instead of holding all browser workers in polling loops. Enforce one browser writer per run and a lease for ambiguous shared-sender challenges. Parallelize independent employers; preserve employer rate limits and cooldowns.

LangGraph is optional for the bounded unknown-page reasoning subflow. If introduced, use `run_id` as thread ID and persistent PostgreSQL checkpoints, with references to browser/vault state. DBOS remains the single owner of queue/retry/commit. A checkpoint cannot make an employer's external submit endpoint idempotent: a crash after sending and before recording acknowledgement still requires receipt reconciliation. First fix the known adapter defects and measure model calls, elapsed time, intervention count, and verified receipts per run.

## Acceptance and delivery

1. Integration build passes; tests from **both branches** plus new negative cases pass using isolated fixtures.
2. Owned dry runs cause zero final submissions; owned submit runs create exactly one receipt.
3. On the Mac, attempt one authorized live job per ATS, with a fresh browser observation and existing saved profile. Each success needs the real employer's acknowledgement plus matched receipt email when available. Record exact run IDs, elapsed time and costs. Stop and report exact blockers if the environment cannot reach live ATS sites.
4. Verify restart/resume, no duplicate submissions, no secret logging, denied geolocation, Telegram answer reuse, and existing six confirmations preserved.
5. Deliver focused commits, a migration note, test evidence, and a truthful live-results table. Leave no claim that all ATSs work based solely on mocks.

Reference documentation:

- 2Captcha CoordinatesTask: https://2captcha.com/api-docs/coordinates
- DBOS durable workflows: https://docs.dbos.dev/python/tutorials/workflow-tutorial
- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence

---

## Local review artifacts

- Isolated Claude checkout: `../ApplyPilot-claude-review` (detached at `b544d64`).
- Local deployed app: http://127.0.0.1:8080.
- The remote branch was inspected, built and tested; it was not merged or deployed over production.
- No secrets or full application screenshots are included in this handoff.
