# ApplyPilot diagnosis: failures, unconfirmed applications, answers, weak ATSs (2026-10-18)

**Method.** REA's `reverse-engineer-anything` workflow:
- a question checklist and a finding ledger;
- passive and bundle analysis in the dedicated Chrome (port 9339, never the worker Chrome);
- the production database: latest submit-mode run per job, last 7 days;
- saved final screenshots of every unconfirmed run.

**REA limitation.** `capture-browser-scenario` failed twice with `execution_failure` (no detail). Navigation was
therefore driven by Playwright, and REA's `analyze-web-bundle`, `inspect-web-page` and `export-web-scripts` read the
resulting pages.

**Constraints.** Nothing was submitted, and no account was created. The iCIMS probe used a random `example.com` address.

## Scoreboard (7 days; success = confirmed ÷ jobs with a real outcome)

| ATS | Jobs | Confirmed | Rate | Asks | Failed | Unknown |
|---|---|---|---|---|---|---|
| Workday | 508 | 418 | 90% | 15 | 26 | 7 |
| Ashby | 57 | 50 | 91% | 4 | 1 | 0 |
| Greenhouse | 109 | 88 | 81% | 5 | 8 | 7 |
| SmartRecruiters | 58 | 43 | 81% | 1 | 3 | 6 |
| SuccessFactors | 51 | 34 | 77% | 3 | 7 | 0 |
| Eightfold | 20 | 13 | 68% | 0 | 4 | 2 |
| Dayforce | 12 | 6 | 60% | 1 | 3 | 0 |
| **Oracle** | 98 | 48 | **52%** (24% in the last 48 h) | 0 | 34 | 11 |
| **iCIMS** | 18 | 0 | **0%** | 0 | 13 | 0 |
| **Lever** | 19 | 0 | **0%** | 1 | 16 | 2 |

## 1. Submitted applications we fail to record (highest leverage)

Of 43 `submission_unknown` jobs, the final pages show **at least 11 were real submissions**:

| Job | What the page shows | Why it was missed |
|---|---|---|
| Pinterest ×3 (Greenhouse embed) | "Good news: your application is in! Thank you for your interest…" | The phrase is in no receipt rule. The embed URL ownership was fixed tonight in e06586f, but the phrase is still missing. |
| Asana (Greenhouse embed) | "Task complete!" | Fixed tonight (e06586f). |
| Adobe (Workday) | "Congratulations! You've successfully submitted your application" | The dialog rule requires the order "application … submitted". |
| Chewy (Workday) | "Congratulations! We have received your application." | The dialog was still fading in at the end of the 30 s post-submit watch. |
| HPE (Workday) | "Application Submitted – successfully been submitted; more tasks: Complete Questionnaire" | The run went into an email wait instead of reading the dialog. |
| Boston Scientific (Eightfold) | Modal: "Thank you for your application, Alex!" | The Eightfold receipt reads `#form-submission-success-message` and the empty-state title, not this modal. |
| Texas Instruments (Oracle) | Toast "Thank you for your job application"; tile "Application/Resume Received" | Recorded as unknown although the tile matches. Check the reconciliation path. |
| EXL, Ford (Oracle) | "You're all set, Alex! Saving job application info…" | Oracle was still saving when the watch ended. |

**Greenhouse embedded on employer sites (Toast ×2).** The final page is the job page with "Apply now". REA/JS evidence
from the Greenhouse embed bundle: on a successful POST it calls
`window.parent.postMessage("greenhouse.confirmation","*")` and then navigates to its confirmation route. The parent
page can close the iframe, so page text never shows a receipt.

**Fix.**
- Add the missing phrases ("your application is in", "submitted your application").
- Extend the post-submit watch for Workday and Oracle (about 90 s; it only watches).
- Read the Eightfold modal.
- Record the `greenhouse.confirmation` postMessage as receipt evidence on employer pages that embed Greenhouse.
- Read the Workday dialog before entering any email wait.

## 2. SmartRecruiters: submits that fail silently (6 jobs)

**What the screenshots show.** All six final pages are still on "Preliminary questions", with Submit enabled and every
consent ticked. No HTTP error was recorded, and nothing changed for 40-130 s after the click.

**REA evidence.**
- Bundle analysis `ev_7de096c1c6cf72b498b67d132b3094d787e540cee13e51257de4eb9f8f208ebf`.
- Page inspection `ev_84cd7749b64956c075fd6498b2bc31190947b5ef64d7cee6561b0ccfd427bd03`.
- Scripts `oneclick-ui/static/403.*.js` and `157.*.js` / `421.*.js`.

**What the code does.** `submitForm()` POSTs to
`/oneclick-ui/api/company/{company}/publication/{publication}/application`.
- On success: `redirectService.handleSuccessfulApplication()` goes to `/oneclick-ui/company/…/job/…/success`.
- On error: it only hides the loader, calls `errorFormatterService.showApplicationErrorMessage()` (a transient
  notification inside SmartRecruiters' shadow-DOM components) and tracks the failure. It does not navigate.
- The page also runs Cloudflare JSD (`/cdn-cgi/challenge-platform/.../jsd`) and DataDome (`dd-tags.js`).

**Inference.** Each of the six hit the error branch. The engine cannot see it, because `submission_response` only logs
4xx POSTs from the job's own origin, and the rejection reader does not see shadow-DOM notifications. Whether the six were
validation errors (400) or bot blocks (403) is **unknown** from saved evidence.

**Fix.**
- Log every write request and its status (any origin) during a commit.
- Read SmartRecruiters' notification (shadow DOM) as a refusal.
- Then classify: validation → repair; bot block → back off.

## 3. Oracle (52% → 24% in 48 h)

- **Shared verification sender, 21 jobs.** "Another application kept the same email sender busy; nothing was sent."
  - The engine locks per email sender so a code can't be handed to the wrong application.
  - Oracle Recruiting sends all tenants' codes from one sender. Parallel Oracle runs that reach the code step all lose
    but one (47 runs in the 10-17 06:00 hour).
  - The lock is hit after the browser opened and the form was filled, so each loser burns its 8-minute budget.
  - **Fix:** pace Oracle dispatch the way Ashby is paced (one code-requiring Oracle run at a time), or wait for the lock
    without consuming budget. Re-run the 20 affected jobs with fresh budgets (owner OK needed).
- **Code step waits** (Citco: "You're almost done – enter the code sent to …"): five jobs are waiting for the email code.
- **Late confirmations** (EXL, Ford): see §1.
- **"Submission is not authorized for this run"** (SAIC, Howmet): `reserve_submission` saw the run no longer
  `running`, because a controller changed it mid-commit. Nothing was submitted; the forms show validation errors.

## 4. iCIMS (0 of 18)

- **Your email is already bound to an iCIMS Central identity.**
  - "Central identifier accepted; password step reached" appears 34 times. The engine itself ran "iCIMS: create
    account" 3 times, and those Central accounts were never verified.
  - There is no password, and no verification or reset email has ever arrived: every mail poll shows `candidate_count: 0`.
  - The owner confirms there are no iCIMS accounts and no iCIMS email.
- **A fresh email needs no account.** Probe on careers-ice: "Enter Your Information" (email) → Next → straight to
  **Candidate Profile** (resume, name, address…), with an invisible hCaptcha (sitekey 94fee806…).
- **Fix (owner decision):** apply to iCIMS with a Gmail plus-alias (e.g. `…+icims@gmail.com`). iCIMS sees a new email
  and skips Central; mail still arrives in the same inbox. Also stop the engine from creating Central accounts it cannot
  verify.
- **Eide Bailly:** this tenant's email step also **requires a phone number** ("Invalid Data Error … Phone Number:
  required"), which the engine left empty.

## 5. Lever (0 of 19)

- hCaptcha Enterprise (`secure-api.js`) refuses tokens our own browser earned after solving the puzzle. HTTP 400 "error
  verifying your application" on Zoox and AIFund after tonight's fix.
- Likely cause: CDP automation detection. hsw.js runs a console/`EvalError` probe in every run.
- Needs a driver change (e.g. rebrowser-patches), which is an owner decision. Until then, exclude Lever.

## 6. Sign-in / sign-up decisions

- **SuccessFactors (Westinghouse):** signed in with no password ("A password is required"), while "Not a registered
  user yet? Create an account" was on the page.
- **Dayforce (Imperial Brady, Security Benefit):** the "Create Your Account" form was filled and consented but never
  went through.
- **Workday account origin mismatch (AAA SoCal, UCAR, USC):** the sign-in landed on a different host than the job's
  tenant, and the guard stopped.

## 7. Timeouts and stalls

- **8-minute deadline, 13 jobs** (Workday 6, Greenhouse 5, Oracle 1, other 1). Many were 2nd or 3rd attempts whose
  budget was spent by earlier attempts or deploy interruptions. 1-800 Contacts stuck on an SMS-consent checkbox.
- **Eightfold Morgan Stanley** never left the job page.
- **Workday Northwestern Mutual** stuck on an open demographics dropdown.
- **SuccessFactors stalls** (Boehringer, Capgemini, Corning) sit on long job/application pages. Not diagnosed further.

## 8. Applications waiting for answers (7 days)

**Should be automatic under existing owner policy:**
- SSN / last 4 of SSN / Aadhar / PAN (Lowe's ×2, BCBST, Maersk): the policy is to **drop** these jobs.
- Leidos DoD Section 847: the always-No government rule.
- Accommodation questions (Accenture, Gas South): No.
- Graduation date (Quora, C3.ai): from education.
- "Disability Questionnaire Answered Date" (HH Global): today's date.
- Minimum salary (Michelin): profile salary.
- Pronouns (Sentry): decline if offered.

**Owner input needed:**
- Race/ethnicity with no decline option (Asana, Pinterest, Roblox ×2, Block, Acuity).
- Transcript and GPA (West Monroe ×6).
- References (Scientific Systems).
- Essays (Luminai, NIH-NCBI).
- Nicotine use (U-Haul).
- AI-screening consent (S&P Global, Kensho).
- Suno's Mon-Fri in-office expectation.
- Employment entities (Federal Reserve).
- Kairos Power availability and Part 810.

## Prioritised fixes

1. Receipt gaps (§1): about 11 already-submitted applications plus every future one. Small code changes plus tests.
2. iCIMS plus-alias email (§4): unlocks a 0% ATS. Owner decision, then a small change.
3. Oracle pacing for the shared sender (§3), and re-run 20 jobs (owner OK for budgets).
4. SmartRecruiters commit observability and refusal reading (§2).
5. Answer-policy gaps (§8): SSN drop, government/accommodation No, dates, salary, pronouns.
6. Sign-in vs create-account (§6): Westinghouse/Dayforce.
7. Lever: exclude, or approve a driver change (§5).
