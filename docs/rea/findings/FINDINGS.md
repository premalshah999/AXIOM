# Takeover findings — started 2026-10-17 05:53:00 UTC

Goal: 100 new, evidence-confirmed eligible applications. Count with `bin/status.sh 2026-10-17T05:53:00`.

## Automation-only continuation — 2026-10-17 22:22 UTC

Owner directed no further scraping work. The existing supply process had already finished; its new output was not imported. Focus remains automation.

- Commits `660df58` and `1f4e973` deployed as `execution-v2-534e7387e2d9`, healthy, hold complete. Workday method anchors, exact text rematching, bounded diagnostics and saved ATS focus are live.
- **A single ConnectError still parked a nearly completed draft.** `v3/jobpilot/models.py:120`, `:326`, `:425`. Marsh McLennan and Xfinity had two connection failures among 107 completed model requests. A typed connection error now survives model-deadline wrapping and shares the existing two-extra-request allowance with timeouts. No browser action is replayed, permanent/provider rejection errors remain non-retryable, and provider admission still gates each request. Existing cost ceiling is unchanged; at the measured average 9,944 input/74 output tokens and configured uncached prices, two regeneration calls add approximately 0.615 cents. Recorded cohort model cost was $0.3216 for 15 jobs (~2.14 cents/job).
- **Phone readback also retained the detached pre-blur locator.** `v3/jobpilot/widgets.py:837`, `v3/jobpilot/forms.py:1920`. Shared question/scope rematching now checks the live phone value after replacement, carries the new locator into subsequent checks, and never treats a failed rematch as acceptance. Full form regression verifies one write and preserved value. Existing phone masks, country-code ordering and keyboard-only widgets remain covered.
- Validation: 32 transport/remount/driver tests and 18 phone-driver tests passed. Current eligible hard-ATS counter is **33/100**, excluding all existing notices, non-hard platforms, earlier Sia off-role and Salesforce senior-role confirmations. Overall 85% success remains unproven.
- Commit `a10d466` deployed as `execution-v2-d2c40aa43de1`; container healthy and deployment script completed. No supply imports or scraping changes in this continuation.
- **Reused SuccessFactors RCM popup nodes were permanently classified as already observed.** `v3/jobpilot/widgets.py:1227`. `RCM_MARK` added markers but never cleared them. Once a catalog had been seen, hiding and reopening the same nodes yielded zero choices and exhausted search retries. Clear both observation and click markers before each opening. A second bug marked the first matching leaf even when two visible entries shared its label; mark/click only exactly one. Two new browser regressions failed before the patch; all 10 RCM/reused-popup/UI5-region tests passed afterward, including a complete repeated selection, state aliases, school catalog spelling and missing-answer behavior. No model calls added.
- Commit `fb1ea30` deployed as `execution-v2-79a75ce68f24`; health passed and hold returned to complete. Runtime verified 480 seconds, three total starts (initial plus two retries), five-second sender wait, cleared popup marks and unique-choice requirement. All automation changes are committed; no scraper changes made for the latest request.

## Evening continuation — 2026-10-17 21:56 UTC

1. **Workday account navigation recognizes the method's words but only searches buttons.** `v3/jobpilot/workday.py:780`, `:913`. A separate anonymous headless browser on RELX observed `autofillWithResume` and `applyManually` as anchors after Apply. The account loop's button-only method search cannot activate those controls and can repeat 60 scans. A shared-account helper now uses the observed method automation IDs regardless of anchor/button, preserves the chosen method and uses only manual application as a fallback. Five real-browser method tests pass. The earlier RELX run's repeated unknown states are consistent with this gap, but its saved outline alone does not prove this was its sole failure.
2. **Text readback assumes blur preserves the original DOM node.** `v3/jobpilot/widgets.py:697`, `v3/jobpilot/forms.py:1978`. Five concurrent Workday jobs reported address-field timeouts at the post-blur input-value read. Replaced inputs lose the scanner's locator marker. The driver can now request an exact question/scope rematch after input/blur replacement and returns that locator for subsequent checks. Three browser regressions prove no second write when the value survived, and no sibling-field write when rematching is ambiguous. This is a supported failure-mode repair, not a claim that every observed live timeout has been explained.
   **Compounding delay:** `v3/jobpilot/forms.py:1323` collected DOM diagnostics on the missing locator without any explicit timeout, after the bounded write had already failed. The diagnostic now uses a 500 ms locator timeout and 750 ms total cap. Real missing-locator and hung-renderer tests pass; a full FormSession regression verifies rematching retains the exact question scope and expected-value ledger. The 14-case remount/driver run passed.
3. **Minute-scale local retry delays conflict with the eight-minute lifetime.** `v3/jobpilot/recovery.py:513`, `v3/jobpilot/autopilot.py:366`. Local repairs waited 120/240 seconds or 180/360 seconds in a second fallback. Both now use 30/60 seconds; actual maintenance retains its outage delay and all paths retain three total starts and the original 480-second deadline. Removed the stale iCIMS “driven by hand” exclusion. Commit `aa66f5b` is live as `execution-v2-ab5eb10867c5`; 22 targeted budget/recovery/persistence checks passed.
4. **Saved ATS focus was honored by the batch but not direct queue entry.** `v3/jobpilot/application_pacing.py:74`, `v3/jobpilot/service.py:727`. Central admission and final reservation now honor the same saved exclusion list; verification-only receipt work remains allowed. Added seniority abbreviations SMTS/LMTS/PMTS to the existing seniority policy; paused a newly queued SMTS application before submission. Salesforce's own listing defines SMTS and LMTS as Senior/Lead Member of Technical Staff: https://careers.salesforce.com/en/jobs/jr347480/data-engineer-smtslmts-knowledge-graph-ai/ .
5. **The new-confirmation counter could count an existing application as new merely because its local job row was new.** `codex-handoff/bin/status.sh:12`. Existing-application notices and pending evidence reconciliation are now always excluded from `confirmed_new`. This corrects an earlier Eightfold overcount. Existing histories and receipts are unchanged.

Commit `660df58` contains the method/remount/scope fixes; targeted tests passed (22 initial Workday/scope tests, then 27 driver/remount/scope tests, overlapping). Deployment pending active draft completion at this entry. Latest audited goal count is **28 eligible new confirmations**, excluding non-hard ATSs, the earlier Sia off-role result, Salesforce's senior-role result and all existing-application notices. SuccessFactors added one new confirmation in the latest cohort. The 100 target and 85% aggregate success remain unproven.

## Latest admission and waiting fixes — 2026-10-17 16:05 UTC

1. **A remembered rejection survived the owner's changed company limit.** `v3/jobpilot/autopilot.py:471`, `:590`. Ready jobs for SAIC and Uber still carried saved “limit 6” rejections after the owner raised it to 12. The admission memo used only status and fit score. It now includes a digest of the effective company limit, target roles, excluded ATSs and minimum fit. A policy change permits one fresh admission check, through all existing live guards; an unchanged rejection does not loop. Prior-start filtering and canonical duplicate protections remain. Regression drives the real batch tick through rejection, unchanged tick, limit increase and successful queue.
2. **The generic verification path retained a separate 60-second sender wait.** `v3/jobpilot/email_browser.py:258`, `v3/jobpilot/mail_errors.py:46`, `v3/jobpilot/browser.py:1039`. It now uses the same five-second cap as adapters and raises a typed sender-busy result for lease-aware durable recovery. If a commit already happened, the result remains submission-unknown and does not schedule a replay. Unrelated mail errors retain their identity. No extra model calls.

Commit `4ba6c00` deployed as `execution-v2-e3e3681c040d`; container healthy and deployment hold complete. Targeted tests: 24 policy/lease/recovery/deadline checks plus 45 browser-verification/field-intent/company-limit/role checks passed. Worktree changes committed.

The preceding `8aaf2b8` pointer/readback/code-intent fixes are also live. The three latest Oracle jobs confirmed (browser segments approximately 69, 66 and 70 seconds); two needed sender-lock recovery first. Takeover counter: **25 raw / 24 eligible**, with the earlier off-role Sia result excluded. This small cohort does not prove 85% across platforms, and the 100-confirmation objective remains incomplete.

## Continuation audit — 2026-10-17 15:22 UTC

1. **A 90-second sender wait plus a five-minute retry delay consumes most of an eight-minute application.** `v3/jobpilot/inbox.py:21`, `recovery.py:395`, `mail.py:1118`, `service.py:1446`. Browser lease patience is now five seconds. The first retry becomes eligible after 15 seconds (second after 30), but recovery dispatch and browser admission both check the actual shared-sender lease before spending another start. Reused the existing lease logic rather than adding an employer exception. Preserve the observed code/link request kind so exact distinct-origin reset links can remain concurrent. Maximum two retries and the original 480-second deadline still apply. This avoids repeated blocked browser starts; it does not make ambiguous Oracle codes safe to parallelize.
2. **Durable email windows could outlive their application's hard deadline.** `v3/jobpilot/mail.py:1189`, `:1309`, `:1352`, `:1436`. Creation, durable extension, token claim and background cleanup now honor the owner's application deadline. An expired owner cannot keep a sender lease alive pending the next background sweep. Legacy/manual mail without an application deadline retains its existing behavior. Tests verify no expired token delivery, no new browser start under a known lease, and preserved code serialization/distinct-origin link concurrency.
3. **Readiness waited for irrelevant DOM changes, not application progress.** `v3/jobpilot/forms.py:1251`. Hidden templates and decorative text ticks previously reset the quiet timer on every mutation, costing up to ten seconds on every adapter step. Readiness now tracks visible semantic controls/questions/dialogs and container visibility changes. Actual application network requests, question changes and visible loading indicators still block readiness. Removed one immediately redundant full scan before the fill loop.
4. **Scanner repeats style, visibility and caption reads within the same synchronous observation.** `v3/jobpilot/js/scan.js:9`. Cache only within a single invocation; every later scan reads changed labels, values, requiredness and visibility afresh. Isolated Chromium, 96 fields, median of 12 scans: 4.10 ms before, 2.85 ms after, with identical full observation objects. This is a modest scanner optimization, not an end-to-end latency claim.

Validation: 67 targeted mail/recovery/Oracle/intent tests passed; 17 budget/deadline tests passed; 22 scanner/readiness/provider tests passed. Suites overlap. Earlier driver/readiness run: 19 passed. Tests use isolated fixture databases and standalone headless Chromium. No extra model calls, no worker Chrome access. Production 85% success remains unproven; the takeover counter remains 16 raw / 15 eligible confirmations. Existing inventory has exhausted its authorized lifetime; do not reset history or bypass retry/time caps to manufacture progress.

## Supported-correction navigation follow-up

5. **Reviewing a hidden earlier-step correction spent a write attempt before any write.** `v3/jobpilot/answer_quality.py:730`, `:767`. Repeated reviews exhausted both repair attempts before navigation could reach the field. Count only immediately before the owned writer; unchanged earlier-step or stale-node observations consume no write allowance. Actual rejected writes still exhaust at two. Application deadline and run-retry caps remain authoritative.
6. **Every Workday earlier-answer correction was sent to Application Questions, and other ATSs had no equivalent path.** `v3/jobpilot/adapters/workday.py:185` (removed override), `v3/jobpilot/adapters/base.py:857`. Replaced the hard-coded page assumption with shared navigation through one observed Back/Previous control to the exact flagged question/type/section/history-row scope. Stop on unchanged state, ambiguity, missing Back or six transitions. Each correction gets one traversal; never navigate back after commit. No inferred applicant answers or model calls added.

Validation: 38 repair/policy tests passed; browser/intent batch had 38 passes and one malformed synthetic click-handler failure. Fixed that fixture's HTML quoting; all 12 focused repair/navigation tests then passed, including a real headless browser Back click with no submission. New live SuccessFactors supply produced three additional evidence-confirmed applications: takeover raw 19, eligible 18 (one prior off-role confirmation excluded). This does not establish overall 85% success.

## Current performance priorities — 2026-10-17 06:36 UTC

1. **Independent retry budgets multiply into unbounded application lifetimes.** `v3/jobpilot/application_budget.py:46`, `service.py:1402`, `recovery.py:354`, `repair_agent.py:241`. Worker interruptions, sender waits, specialist retries, changed strategies and validation retries previously had separate limits. One canonical-requisition budget now permits an initial attempt plus two retries and 480 wall-clock seconds from the first browser start, including intervening waits. Admission, browser claim, final reservation, validation repair, mail continuation, persistence and model repair share it. Existing history counts; deploys do not reset it. Expired uncertain submissions retain protection. No extra model calls.
2. **Ordinary text pays for fake typing on every field.** `v3/jobpilot/widgets.py:697` used 28–65 ms per character even on plain inputs. Fill, blur and exact readback now run first; rejected writes get one bounded keyboard fallback. Isolated Chromium comparison, five 32-character fields with readback: before 9.080 s, after 0.060 s. This is a driver microbenchmark, not an end-to-end success-rate claim.
3. **Dropdowns wait for throttled animation frames before using an already-safe fallback.** `v3/jobpilot/widgets.py:874`, `:899`. Field controls now use the existing exact-node, enabled, visible and hit-point coverage checks immediately. Application actions keep native clicks. Observed native clicks are never replayed on timeout; navigation is re-observed instead. Final-submit reservation and evidence checks stay intact.
4. **Nominal wait limits fail to bound the actual awaited operation.** `v3/jobpilot/forms.py:1218`, `:1478`, `:598`. A loop deadline did not bound a hung renderer evaluate. Settle now bounds the entire coroutine; field writes have 12 s total (uploads 30 s), and a control-recovery episode has 30 s total inside the application deadline. Added safe readiness timing with network-busy classification.
5. **The adapter and form guard disagree about a Submit button's intent.** `v3/jobpilot/forms.py:3821`, `v3/jobpilot/adapters/base.py:815`, `:972`. Extracted one scoped account/consent classification shared by both. The adapter can advance a proven profile popup without calling final-application commit; required uploads and application fields keep final-submit protection. No employer-specific additions or model calls.
6. **Choice discovery and readiness waits spend time on irrelevant evidence.** `v3/jobpilot/widgets.py:537`, `v3/jobpilot/forms.py:2823`, `:278`. Stable options with a supported exact alias no longer wait for a literal query substring. Removed an immediate repeated option discovery against unchanged evidence. Known telemetry hosts no longer block application transport readiness; real application requests and document navigation still do.
7. **Eightfold opens the code request window after Submit can send mail.** `v3/jobpilot/adapters/others.py:54`. Arm before final commit, preserving request correlation. PayPal's observed pending window had no matching delivery; this prevents a timing race but does not claim that race caused its missing email.

Validation: targeted fixture/browser tests passed (33-case policy/driver/repair run; 15-case click/intent/React-select run; prior 27-case recovery/queue/submission checks). Browser tests used isolated headless Chromium, never worker Chrome. Production success >=85% is NOT yet demonstrated. Remaining broad issues include repeated whole-page scans per field, Oracle shared-sender contention, and site-specific CAPTCHA/account recovery. The current patch reduces redundant work; it does not assert every live blockage is solved.

## Earlier takeover findings

1. **Scheduler loads 1.28 GB of saved application packets just to choose retries.** `v3/jobpilot/autopilot.py:394` loaded full Run objects for 3,813 runs over 14 days, on the shared event loop. This wastes RAM and can cause unrelated browser/model deadlines to expire. Changed to six metadata columns and reject ineligible run states before fetching jobs. Existing backoff, daily retry limits, employer serialization, owner questions and exclusions preserved. Targeted persistence tests passed.
2. **One isolated model timeout discards minutes of browser progress.** `v3/jobpilot/models.py:119`, `:428` previously collapsed timeout into generic provider unavailability. Added a typed timeout and one retry of the model decision, at most two extra requests per run, each through normal admission and budget metering. No browser action replay; cancellation, quota, HTTP errors and budget failures do not retry. Recent successful runs averaged $0.0124; typical 10k-input/2.4k-output uncached retry costs ~$0.0059, at most ~$0.0118 additional for two typical retries. Actual reservation/budget checks remain authoritative. 17 focused tests passed.
3. **Deployment hold left dispatch disabled.** `codex-handoff/bin/hold.sh` / setting `execution_v2_deployment_hold`: observed `draining` at takeover. Released to `complete` immediately. Existing active target-400 batch retained by the idempotent start endpoint; separate takeover counter starts at 05:53 UTC.
4. **Supply command selects the wrong Python on this Mac.** `codex-handoff/supply/fresh.sh:8` invokes system Python 3.9; Oracle/Eightfold fetch imports `datetime.UTC` (3.11+). Started that fetch with `/opt/anaconda3/bin/python` (3.12), yielding 126 rows immediately. Import in progress. Workday fetch also buffers all tenant results until the slowest tenant finishes; investigate streaming next.

## Validation

2026-10-17: 17 passed: model timeout retry, provider health, stalled persistence. Tests run against isolated copied code and fixture databases in the app container; pytest installed in `/tmp/codex-deps`, not the production environment. No worker browser attached.

5. **Master resume imports wait forever for artifacts that master mode never builds.** `v3/jobpilot/autopilot.py:546` used `ready_applications` (requires a tailored artifact) to add manual jobs to the board, while `execution_services.py:259` deliberately disables tailoring in master mode. Added a master-mode path through the same discovery freshness/protection/deferral checks without the artifact dependency. Targeted test proves attempted, deferred, paused, senior and nonmanual jobs stay excluded.
6. **Blocked inventory prevents ranking fresh inventory.** `v3/jobpilot/features/discovery/readiness.py:128` counted previously Ready jobs without reapplying current role and policy gates. Production reported 45 eligible jobs, but 31 failed those gates. Applied gates to cached and legacy eligibility before pipeline backpressure. Role/discovery/persistence tests: 14 passed.
7. **Employer admission decompresses every run packet for a boolean.** `v3/jobpilot/application_pacing.py:160` selected `packet.verification_only` across all runs on each admission/final submission. Column projection still forces PostgreSQL to inspect the 1.28 GB JSON history. Filter to this employer's jobs and relevant states before JSON extraction. Six state/exemption cases plus limits API test passed. The saved limit of six applications per employer remains; 11 of 14 candidates were refused by that limit. Asked owner whether to raise it to 12.

8. **Deploy interruptions spend repair attempts and can permanently exhaust drafts.** `v3/jobpilot/recovery.py:353` treated `worker_interrupted` as a delayed, budgeted repair; `service.py:620` incremented its attempt count. Observed two exhausted and nine delayed deployment continuations. Added immediate durable continuation, preserving repair counters; queue and specialist budget checks exempt only this non-submit interruption, and continuation attempts are tagged so future counts do not charge them. Existing eligibility, latest-run, owner-control, duplicate and uncertain-submit protections remain. Five focused recovery tests passed.

06:03 UTC capacity check before 20 workers: memory_pressure 38% free, app 2.535 GiB; healthz [190,2,1,1,2] ms. Changed only WORKERS=20 in production .env. No increase above 20.

9. **Workday maintenance is misreported as an identity mismatch.** `v3/jobpilot/workday.py:743` checked origin before recognizing the provider's maintenance redirect. Saved screenshot and independent HTTP fetch show `community.workday.com/maintenance-page`, with planned outage October 17, 2026, 02:00–13:00 America/New_York. Added trusted-page detection and parsed UTC expiry, durable ATS maintenance deferral in queue admission, and exclusion from classification backpressure while down. Actual identity checks remain. Also moved synchronous discovery planning off the scheduler event loop in `workers.py:241`.
10. **An observed iCIMS signup route is ignored based on its link label.** `v3/jobpilot/adapters/icims.py:271` excludes “Connect with us” despite its same-employer `/connect` destination. Independent HTTP fetch of PowerSchool's offered route showed one email field and Sign Up. Allow that offered path; destination account checks and return-to-requisition flow still apply. Community registration is never application confirmation. Maintenance, Connect, readiness and recovery tests: 12 passed.

Owner reply: raise company limit to 12; retain hard ATSs only. Applied via PUT /api/limits preserving the approved 600k token cap. Workday outage means the first-15-minute goal of 100 starts was not achieved; no confirmations yet as of ~06:08 UTC. Keep reporting actual counts.

11. **Previously approved jobs bypass the current role policy.** `v3/jobpilot/service.py:310` trusted old approval when resuming or importing, allowing a Data Analyst retry and an AI Product Evangelist application. Added target-role, internship and saved seniority checks at canonical submit admission. Paused the Data Analyst draft; preserved Sia's real confirmation but excluded that off-role application from the takeover goal. No history was rewritten.
12. **Rejected iCIMS credentials are mistaken for a proven account.** `v3/jobpilot/adapters/icims.py:212` used KNOWN_ACCOUNT (includes credentials_rejected/reset_requested) to hide an offered employer registration route. Require actual authenticated/existing account evidence instead. Eight focused scope and iCIMS tests passed; deployed commit 2590a3d with hold released complete.

~06:21 UTC: raw status 10 confirmations, eligible goal count 9 (SmartRecruiters 7, SuccessFactors 2). PayPal submission is uncertain pending verification; do not resubmit. Oracle expanded supply 47 imported, application sender lease remains the main bottleneck.

Follow-up after de72102: `v3/jobpilot/forms.py:787` / `widgets_ats.py:61` replaced up to three browser calls per provider dropdown per scan with one bounded read per application frame. Scoped DOM IDs, supported host/signature checks, owned Dayforce selection text, and open shadow roots are preserved. A 30-dropdown fixture verifies one call and matching legacy values. 37 focused provider/form/intent tests passed. Queue wait polling also caches its immutable attempt budget, rather than repeatedly scanning alias history every second; fresh jobs still start their clock only when the browser slot is claimed.

Deployment de72102 completed healthy; hold returned complete. This release enforces the owner's new limits retroactively using existing start history, so old exhausted drafts are not re-dispatched.

Follow-up deployment 8fabc85 is live as execution-v2-3d7fa0818f1b. Verified 20 workers, MAX_SECONDS=480, MAX_STARTS=3, hold=complete, and healthz 45/3/3 ms. Eight budget tests passed after the queue-poll optimization. The deployment shell reported a trailing parse error after successful health verification because its comment was edited while it was still executing; explicitly released hold and verified the script with bash -n. No app code rollback was needed.

15:23 UTC: deployed 6cf067f as execution-v2-a238b0f1b85f. Healthz OK, hold stage=complete. Runtime verifies 480 application seconds, three total starts (two retries), five-second browser sender wait. No model calls added. SuccessFactors supply now follows observed same-origin result-page links instead of stopping after page one; fresh discoveries continue through normal API ranking/eligibility and duplicate guards.

Continuation: 6447b0d deployed as execution-v2-44ebc68da567, healthz OK and hold=complete. Removed a hidden-field repair-attempt charge and the Workday-only Application Questions assumption; scoped observed navigation now supplies that path across adapters.

7. **Menu dismissal depends on animation-frame polling in background tabs.** `v3/jobpilot/widgets.py:1113`, `:1152`. The two menu-closure waits used Playwright default animation frames; a menu could already close through a timer while the poll waited until its 600 ms timeout. Use 50 ms timer polling. The synthetic background-tab regression disables requestAnimationFrame, closes the menu after 60 ms and verifies completion below 500 ms; a permanently open sibling menu still raises. Fourteen focused picker/driver tests passed. Live SuccessFactors combobox writes still measured 3.4–7.6 seconds before this change; the exact live contribution of this wait has not yet been measured.

Supply: paginated Oracle public requisition results after confirming offset 100 returns different IDs/dates than offset 0. Tightened discovery role matching after finding bare llm matched the middle of fulfillment; no such retail rows were imported. Deduplicate normalized employer + requisition ID across tenant aliases before normal application API duplicate/eligibility checks.

8. **Fast field clicks skipped the mouse events that open real dropdowns.** `v3/jobpilot/widgets.py:874`. The early field path dispatched only click; a parent listening to mousedown never opened. Three new regressions failed against the previous implementation. Field dispatch now sends the pointer/mouse sequence after the same visibility/ownership checks, stops if a handler replaces the node, and preserves temporary button-type protection. Final application clicks keep their native path. 25 focused mouse/React/safety tests and 69 broader provider/prompt/intent tests passed.
9. **Provider readback consumed its own deadline with separate browser round trips.** `v3/jobpilot/widgets_ats.py:301`. Oracle continuation dbc3b2bfbe34423d819d044cf88c5599 reported Postal Code selection failure at _wait_committed. Value, closed state and validity were fetched separately inside a two-second budget. Read them in one atomic renderer snapshot per poll, preserving stable readback and remount checks. Latency fixtures simulate 220 ms per browser operation. 31 focused provider/readback/click tests passed. This addresses a plausible cause from the trace, not proof that this was the only cause of that live failure.
10. **Autocomplete metadata overrode the question's actual meaning.** `v3/jobpilot/email_browser.py:141`, `v3/jobpilot/adapters/base.py:364`. Micron Full Stack AI Engineer run dd786406cd9f4794af36f8291007fa69 treated Employee ID(if applicable) as a verification code after Submit because the site marked it autocomplete=one-time-code. Both classifiers now require an explicit code label, or an unlabeled autofill input with a visible code instruction. No ever-growing blacklist of applicant question labels. 37 mail/intent/browser tests passed, including true code screens, split boxes and mixed-form protection. Preserve the existing uncertain submission; this fix does not authorize resubmission.

Current eligible counter 20/100 (raw 21, prior off-role Sia confirmation excluded). Eightfold public discovery also paginated: Qualcomm search returned 10 positions with count=617; 29 new candidates imported through existing policy, three ready, one confirmed and one uncertain so far. Neither 100 nor 85% success is complete.

## Claude continuation (Workday + SmartRecruiters, REA-guided) — 2026-10-17 22:45–23:55 UTC

Codex's uncommitted missing-posting change was committed unchanged (00f9203) after its tests passed. All deploys used
codex-handoff/bin/deploy.sh from v3.

Fixes, each with a failing test first:

1. **3fe8438 — writes go to the rebuilt node.** Workday rebuilds inputs, so the `data-jp-id` tag is lost before the
   next write. HPE: 6 fields × ~31 s deadline; 95 such deadlines across 15 runs. `FormSession.live_locator`/`retag`
   adopt the rebuilt control by its unique name, else rescan once. `discover_options` no longer runs a full scan per
   picker.
2. **5ce5045 — school prompt searches the institution first.** "University of Maryland", not "…, College Park".
3. **f8eb717 — `wait_options(changed_from=…)`.** A settled list that replaced the pre-search list counts as the
   result. Requiring the query text cost 4 s per search; Field of Study was taking 7–10 s.
4. **d7611cd — dropdowns and majors.**
   - A short, non-scrolling dropdown skips type-ahead. Degree "BE" took 8.3 s before the owner policy chose BS.
   - The major policy: recorded major → `major_part` (an offered name made of its words, at least 2) → Computer
     Science. The model no longer picks among unrelated results ("Data Processing" for Data Science).
5. **3609f55 — "Other" is the last school search.** Lennar's list has no Gujarat Technological University.
6. **3230310 — value-free network journal.** NetworkMonitor records method, path, query keys, status, timing and
   redacted 4xx messages. Each step record gets `network`; repair evidence gets `network_failures`.

**Measured:** new-build Workday runs took 0.8–3.6 min (median before: 7.9), with 0 write deadlines and 0 picker
observation failures. 8 of 9 Workday jobs confirmed (Capital One, Slate Auto, Accuray, Truist ×2, Accenture, Visa ×2);
Lennar failed on the GTU school, now fixed.

**Supply:**
- Swept 45 tenants that were in the jobs table but not in wd_tenants.txt: 58 fresh postings (now 464 tenants).
- Codex's SmartRecruiters tenants: 0 new.
- REA found SR's cross-company search API, `jobs.smartrecruiters.com/sr-jobs/search` (supply/fetch_sr_search.py):
  only 6 new.
- Bing RSS ignores `site:`, and DuckDuckGo HTML returned 403 after 52 queries. That may briefly affect the app's own
  free-search discovery; don't scrape search engines again without an API key (the app supports a Serper key).
- fetch_workday.py now pages up to 3 × 20 per query, with a 30-day window.

**Open, owner's call:**
- 8 Workday jobs exhausted the 8-minute cumulative budget during the 06:00–17:00 UTC Workday maintenance window.
- The fit model skips some in-role postings scoring 58–62 on hard requirements.

### Workday apply API, from the value-free network journal (step records' `network`, 2026-10-17 23:40 runs)

Where the options come from (no UI needed):
- `GET …/jobapplication/{tenant}/values/sources/sources?jobApplication=…` — How did you hear.
- `values/phone/deviceTypes` — phone type.
- `values/educations/degrees?country=…` — degree.
- `values/educations/fieldsOfStudy` — full list, or `?search=` for typeahead.
- `GET …/schools?search=` — school typeahead.
- `POST …/{tenant}/questionnaire/{id}/definition?type=…` — **application questions and their answer options**.

Section saves:
- `POST …/jobapplication/{id}/{workexperiences,educations,source,previousworker,phonenumber,name,address,emailaddress,skills,selfidentify,resumeattachments,questionnaireresponse}`
- `PUT …/jobapplication/{id}/{uspersonalinfo,termsandconditions}`
- `PUT …/package/{id}/validate` for each step.

Next speed step (deferred while supply-bound): capture the questionnaire definition and values responses in
NetworkMonitor, then fill `discover_options` from them for `formField-<id>` fields instead of opening each picker.
That should save about 5–10 s per question page. Don't store `questionnaireanswers` (applicant data).

Readiness waits of about 6 s that end `settled:true` are Workday's own save, validate and re-render time, not
telemetry: `uxi-ingestion.us.mlde.workday.com` beacons fail in about 10 ms.

### Owner decisions 2026-10-18 ~00:25 UTC (via Claude)
- **WORKERS=12 (from 20).** Measured Workday confirmed-run time against concurrent runs:

  | Concurrent runs | Run time |
  |---|---|
  | 2–12 | 2.0–2.9 min |
  | 14–16 | 3.7–4.7 min |
  | 17–22 | 5.4–7.1 min (max 7.8, near the 8-min cap) |

  One Python event loop drives all browsers, and it saturates above about 12. Pools are now 10 normal + 2
  specialist; check with `/api/runtime-health` `application_workers`. The DBOS launch log still recites old queue
  sizes.
- **Fresh budget** for 8 Workday jobs whose 8-minute wall-clock budget was spent during Workday's 10-17 maintenance:
  - code: d8ebf3f, `application_budget_reset:<job>` = {at, reason}; it ignores earlier starts and rewrites no history;
  - data: settings written for the 8 jobs, then each resumed through the API.
- Also new: `Leader`/`AVP` count as senior (0260cf9). One GE Vernova run was cancelled; one Citi AVP and one
  GE Vernova job were paused.

### Incident 2026-10-18 00:32 UTC (Claude): stray dry-run batch
Claude meant to list job titles and accidentally sent POST /api/jobs/batch-queue with mode=dry_run for 76 "ready" jobs
that submit admission had just refused (target role, archived duplicate, company limit). Dry-run admission does not
apply those checks, so it queued 18 dry runs.
- What happened: 2 Workday sign-ins (AIG, Salesforce). No account was created and nothing was submitted.
- Cleanup: 6 runs cancelled, 11 jobs paused, 1 waiting_browser. The paused jobs were ineligible for submit anyway;
  resume one only on purpose.
- Rule: never call batch-queue (any mode) as a probe. Read eligibility from the database or a submit-mode response.

### 2026-10-18 ~01:00 UTC (Claude): Greenhouse, Ashby, SmartRecruiters and Lever with REA
- **Human-like rules removed (owner):** 2516146 drops the random 1.2–3 s pause before submit and the pointer wandering
  before clicks. Functional keystroke pacing (phone widgets, search inputs) stays.
- **Live benchmark** (tests/_bench_live_forms.py against the dedicated Chrome, never submits). On today's build, text
  fields take about 0.05 s, Greenhouse react-select questions about 0.25 s to discover + 0.4 s to fill, Ashby button
  choices 0.02 s, and the SmartRecruiters phone code 0.3 + 0.5 s. The only waste was the location typeahead pre-read
  (1.4–2.3 s for zero options); f2dca66 skips it.
- **Lever:**
  - The Postings API apply endpoint needs the *employer's* key (generated by their Super Admin), and CORS is limited
    to the employer's own domains. There's no third-party submission; use the hosted form.
  - REA on the live form: hCaptcha Enterprise (secure-api.js); execute() on submit; the token goes into
    #hcaptchaResponseInput through Lever's onSuccess callback, then the hidden #hcaptchaSubmitBtn is clicked.
  - Root cause of 10 of 15 refusals: the browser passed silently, the same-IP solver saw no puzzle, and a token bought
    from 2Captcha (solver IP) was delivered over the browser's own, so Lever returned 400. 7e08235 uses a fresh
    native pass.
- **Anti-bot layers:** Greenhouse and Ashby load reCAPTCHA Enterprise v3 (score-based, invisible).
- **ats_focus:** owner re-included greenhouse, ashby and lever; the other 14 exclusions remain. Most fresh Greenhouse
  and Ashby postings are already applied (33 of 40 were archived duplicates). supply/fetch_lever.py (public postings
  API) found 22 fresh Lever postings, 2 of them eligible.

### 2026-10-18 ~03:30 UTC (Claude): Lever's real root cause, our own hook (corrects the entry above)
Lever never confirmed: 0 of about 104 runs, 47 of them "security check refused" (HTTP 400). Bought hCaptcha tokens never
produced a confirmation on any ATS (custom/icims/lever/oracle, about 136 runs). The in-browser (same-IP) solver passed
20 times on iCIMS and Oracle.
- **Our injected scripts broke hCaptcha.** `page.add_init_script` runs in every frame, including hCaptcha's own enclave
  frames. With the token HOOK inside them, Lever's `hcaptcha.execute()` made no network request at all (no
  getcaptcha, no puzzle, no token). These were A/B probes in the dedicated Chrome on an empty form; submits were
  cancelled and POSTs aborted:
  - no hook: getcaptcha fired immediately, then an image puzzle;
  - hook in the page only: same, a puzzle;
  - hook only inside the provider frames: nothing.
  Fix: HOOK, GUARD, LOCATION_DENIED and commit arming (ARM_JS) return early in challenge-provider frames
  (captcha_observation.PROVIDER_FRAME_JS: hCaptcha, Google/recaptcha.net /recaptcha, Turnstile, DataDome, Arkose,
  GeeTest, PerimeterX). Form scanning already skipped them.
- **Lever's widget is site-executed.** `hcaptcha.render('h-captcha', {callback: onSuccess})` goes into a 0 px div whose
  hCaptcha frames are all visibility:hidden (frame=checkbox-invisible inside the enclave), and "Submit application"
  calls `execute(captchaId)`. The pre-submit pass used to buy a token and hand it to onSuccess, so Lever posted the
  solver's token and refused it. DETECT now marks that shape (`headless`, hCaptcha only, not declared invisible, has
  provider frames but none shown) as dormant before submit. After Submit, the puzzle hCaptcha shows is answered in
  this browser, and a token is never bought for that sitekey (`CaptchaSolver.site_executed`).
- **Side bug:** DETECT never returned `pending`, so `solve_same_ip(pending=...)` was always False and waited 1 s
  instead of 8 s for an execute()-opened puzzle. Fixed for every hCaptcha site.
- **Verified live with the production scripts:** the hook stays out of hCaptcha frames, DETECT is dormant before
  Submit, and after Lever's own Submit getcaptcha fires and `challenge_frame()` reports "hcaptcha nested challenge"
  in 1.1 s ("Click on the TWO shortest towers").
- Tests: tests/test_captcha_site_executed.py, tests/test_provider_frames_untouched.py.
- **Result on production (e62f1bf, 02:51 UTC):** two fresh Lever postings (Zoox, AIFund) ran the corrected flow.
  No pre-submit purchase; after Submit hCaptcha showed a real puzzle; the in-browser solver answered 2 rounds;
  hCaptcha issued its own token; Lever's onSuccess posted once, and Lever still answered **HTTP 400 "There was an error
  verifying your application"**. Lever's server therefore refuses tokens earned by this browser too, not just bought
  ones.
  - Leading hypothesis: hCaptcha Enterprise (`secure-api.js?host=jobs.lever.co`) risk-scores the session as automated,
    so the siteverify score fails Lever's threshold.
  - The signal: its hsw.js logs `EvalError` from a console probe in every run, with or without our scripts. That is
    the classic CDP `Runtime.enable` detection; Playwright enables Runtime on every frame, hCaptcha's included.
  - Next step if Lever matters: a CDP-leak-free driver (e.g. rebrowser-patches / rebrowser-playwright) or a non-CDP
    path for Lever only. That is a driver change for the owner to approve, not a form fix.
  - Until then every Lever attempt burns a posting. Fresh supply is thin anyway: 2 of 22 fresh Lever postings were
    eligible.
- **Follow-up fix (same night):** the post-submit puzzle took 34 s, and meanwhile Lever posted and refused. The
  solver's "puzzle not solved" made watch_submission return submission_unknown without reading the page; a successful
  /thanks would have been lost the same way. watch_submission now re-reads proof and rejection after a failed
  challenge, and solve() leaves a site-run widget's outcome to the page when the document was replaced mid-puzzle.
- **Greenhouse embedded receipts (e06586f):** /embed/job_app/confirmation?for=<board>&token=<post> is now bound to the
  job. Asana's "Task complete!" run (d0b8af08) ended submission_unknown because of it. The repair agent read the
  confirmation, but the run record still says unknown.

### 2026-10-18 ~04:00-06:00 UTC (Claude): fixes from REA_DIAGNOSIS_20261018.md, deployed
Owner directives: aim for 100% of eligible jobs; no 8-minute cap in production; demographics are Male, He/Him,
Asian (else South Asian), not a veteran, no disability, cisgender, straight. iCIMS is last.

- **baf83f7 Lifetime cap off in production.** The `application_budget` setting ({"max_seconds": null}) is set. Runs
  end at their own runner budget; the three-start limit stays.
- **baf83f7 New self-ID topics.** Sexual orientation, gender identity, transgender and pronouns, each with an owner
  rule. Yes/no facts match self-ID wording. The owner's facts and rules are saved (decline-first still wins when
  offered).
- **2978eed Receipts.**
  - Phrases: "submitted your application" (Workday/Adobe) and "your application is in" (Pinterest).
  - Eightfold thank-you modal.
  - 90 s watch for Workday, Oracle and SmartRecruiters.
  - Greenhouse `greenhouse.confirmation` postMessage receipt, accepted only from *.greenhouse.io origins.
- **2692498 Oracle pacing.** At most 2 Oracle runs at once, set by `application_pacing:ats_limits`. With no cap,
  sender-busy collisions now self-heal on retry; Ford, Honeywell and SAIC confirmed after a collision.
- **7d83566 + 81c6045 SmartRecruiters.** REA of oneclick-ui:
  - `submitForm()` POSTs /oneclick-ui/api/company/{c}/publication/{p}/application. On error it shows a dialog:
    "Cannot submit your application ...", "... due to validation issues", "Problem saving answers to preliminary
    question", "This job ad has expired".
  - A required preliminary question left empty fails client-side with "Please make sure that you answered all
    questions marked as required."
  - All of these now map to temporary, validation or closed.
  - Every write request during a commit is logged (`submission_requests` event).
  - SEON device-session gating exists (enableSeonIntegration) but was off for AbbVie and ServiceNow.
- **b6f1d8e Answers.**
  - BCBST's long SSN paragraph and Aadhaar/PAN are sensitive IDs, so the job is removed.
  - DoD Section 847 gets "No" ("Authorization Act" is no longer an exclusion).
  - Accommodation questions get "No" from the no-disability fact.
  - "... Answered Date" fields get today's date.
- **3abbde0 Account creation.** It retries inside password limits shown only after a refusal (Westinghouse 18-character
  maximum). An over-long configured password gets a per-employer compliant one.
- **b70f3a9 Greenhouse header button.** A "Submit Application" header button outside the form is not the final
  submit (Sparksoft, 6 runs).
- **a59937f Workday maintenance.** The maintenance page defers for any maintenance wording (AAA SoCal and USC were
  parked as "account origin" mismatches).
- **d48faab Workday locale.** Postings open in en-US (RTX fr-CA: 30 unrecognized states).

**Lever (research agent):** Postings API v0 and Data API v1 can submit applications, but only with the employer's own
key or partner OAuth. There is no applicant-side path. Open-source tools hit the same "error verifying your
application" over CDP.

**Open / owner decisions:**
- Reconcile 10 audited submissions. The classifier blocked the API call; they stay unknown and are not resubmitted
  automatically.
- Education dates are missing from the profile, so graduation questions are blocked.
- iCIMS plus-alias email.
- Fresh starts for jobs that spent all 3 starts on bugs that are now fixed (Sparksoft x2).
- UChicago-style "select no only if citizen/LPR" sponsorship wording.
- Jefferson Health's Workday school prompt returns "No Items." for every query.
- The Dayforce create-account page is not recognised as an account step (the mapping agent is collecting DOM
  signals).
- ~~Autopilot/tier "resume" events logged as "Owner requested" on every attempt~~ (fixed in 8ee5d73).


## 2026-10-18 07:00-14:45 UTC: REA maps, Oracle and Workday fixes

**Maps.** codex-handoff/rea/maps/ now has oracle.md, successfactors.md, workday.md, dayforce.md, eightfold.md,
greenhouse.md and ashby.md. Two read-only mapping agents wrote them on their own Chromes (9341/9342).

**Idle 08:20-14:10 UTC.** No code fault. The Mac was on battery (33%) and in deep sleep from 04:20 to 10:10 ET,
which pauses Docker. Overnight runs need AC power and sleep prevention.

**Commits (all deployed):**
- 8ee5d73: automatic retries are logged by actor ("Automatic retry queued (autopilot)"), and only when they queue.
- e7f6e17:
  - A start that sent nothing (sender busy, browser or model provider down, deploy interruption) no longer counts
    toward the three starts. That freed Ford, Leidos, Lennar and others.
  - Lease waits more than an hour apart start a new chain.
  - New Oracle starts wait at queue and claim time while another job holds the shared sender.
  - The repair agent ignores the app's own waits and never asks the owner for the saved email or phone (it had
    asked for Honeywell and four others).
- 04a2a62: Workday "Sign in with email" (SignInWithEmailButton) and the legalNotice cookie banner are matched by
  automation id. RTX's fr-CA posting showed Google, LinkedIn and email sign-in.
- a27ed56: Oracle's code page cannot be reopened, so Oracle waits up to 150 s for its code instead of parking at
  30 s. GM Financial looped on code requests.
- 8323efb:
  - A degree option that states a status follows the recorded end month. Lennar got "Bachelors in Progress" for a
    BE finished in June 2024; one confirmed application, Lennar Talent Community, carries it.
  - School searches get 30 s to reach "Other": 27 deadline failures on school fields since 10-16.
- f8e2193:
  - Shared-sender platforms wait in the browser for the holder (up to 200 s, one mail_wait event). Verified: Ford
    and Honeywell started together at 14:35 and both confirmed.
  - Oracle's "Too Many Attempts" lock (Fortinet, after the 10-17 storm) releases the sender and holds the retry and
    the site circuit for 35 min.
- 91532b7: Oracle generic labels ("Please provide a response") take the block's question. Holding a government
  position is owner-No. Goldman Sachs had asked the owner a subject-less question.

**Not done / open:**
- Workday school "No matching option" (35 events, 8 jobs): the search-first code exists, but some tenants (Jefferson
  Health) return "No Items." for every query.
- Ashby refusals: reCAPTCHA Enterprise score key versus our v2 solver token (ashby.md). Classify as a bot block.
- Eightfold Morgan Stanley: applyAction link_off, the real form is on Workday (eightfold.md).
- Dayforce account flow and SuccessFactors sign-in/picklists: subagents are working in worktrees wt-dayforce and
  wt-sf.
- Owner: two open subject-less questions (Honeywell email x2, Goldman "Please provide a response"); fresh starts
  for RTX and Sparksoft.


## 2026-10-18 15:00 UTC: why Oracle runs waited on email (owner question), REA + mailbox evidence

**1. The mail check, not Oracle, was slow.**
- Code waits ran 45-224 s, while form filling took 30-50 s and submit 10-15 s.
- mail_poll scans took a 46 s median per pending code window, up to 118 s, even with 0-1 candidate messages.
- Cause: the mailbox runs over Gmail IMAP, where every command took ~1.9 s (SELECT, SEARCH, even NOOP). One
  connection re-SELECTed Spam, Trash and All Mail for each search, so a window cost 15-20 commands.
- Fixes:
  - 4660aeb: one connection per folder, no Trash, Spam every 5th listing, direct fetch.
  - d017b0f: identity cache.
  - Warm code search is now 1.7 s (was ~15 s); a read is 1.8 s.

**2. Oracle codes are not from one shared sender (the owner was right).**
- The mailbox shows employer domains (recruitingnoreply@ford.com, noreply@honeywell.com, careers@talent.saic.com).
- It also shows pod addresses (edel.fa.sender@workflow…oraclecloud.com, exvu.fa.sender@oracle.gmfinancial.com).
- And display names ("Goldman Sachs Recruiting" <gsrecruiting@oracle.com>).
- Codes are now attributed per tenant (mail.oracle_attributed) and leases are per tenant. Oracle concurrency 2 → 4.

**3. Other Oracle email and verification behaviour (REA):**
- "Keep me signed in" on the verification step exists for GM Financial, Goldman, SAIC, TriNet, Pearson and UTHealth
  (globalSettings ORA_IRC_KEEP_CANDIDATE_SIGNED_IN_ENABLED=Y). It is now ticked, so later applications at those
  employers skip codes (the worker Chrome keeps cookies).
- The PIN page cannot be reopened. Parking at 30 s looped code requests, so the inline wait is now 150 s.
- The "Too Many Attempts" lock (30 min) is held for 35 min via the site circuit.
- hCaptcha (Honeywell, JPMC) multi-round: the answered images linger for seconds. The solver now waits before
  calling the answer rejected.
- Goldman "Name of Latest Employer" is a cx-select grid (role=grid/gridcell) with a fixed company list that lacks the
  applicant's employers. Only an Other / not listed option may commit; otherwise the owner is asked.
- Generic labels ("Please provide a response") take the Oracle block's question.
- A REA probe with a dummy example.com address created an inert draft on Goldman's site. Avoid further live-form
  probes there.

## 2026-10-18 16:00-19:35 UTC: owner directives (never ask before production; Claude is the main developer)

**Done and deployed:**
- **Fresh starts.** 24 retry-limited jobs whose bugs were fixed got fresh starts, plus 17 open iCIMS jobs.
- **Open questions answered.** 7 bogus "what email" questions and Goldman's government question ("No").
- **15d9c09.**
  - One-click Apply (`POST /api/jobs/{id}/apply`; the dashboard has one Apply button and no "Why it fits" form).
  - Deferred admissions return 202 and are remembered (`owner_apply:<job>`); the autopilot starts them first.
  - Admission by title (`Profile.admit_by_title`, core families only, hard blockers respected). The backfill
    readmitted 202 underscored jobs.
  - Target roles gained Data Analyst, Analytics Engineer, Machine Learning Scientist, Backend Engineer and Software
    Engineer.
- **Workday closed-posting check.** The tenant's own CXS search confirms a missing requisition. Cisco, Visa, Applied
  Materials and AIG were closed but waited as failures.
- **Stall retries** now cover Greenhouse and Ashby.
- **iCIMS plus-address** (`Profile.ats_email_aliases={"icims":"icims"}` → applicant@gmail.com). Mail
  and receipts follow the alias; the first five runs are using it.
- **Lever excluded** from dispatch (0/19, hCaptcha Enterprise) until a fix exists.

**Blocked:** reconciling 9 audited submitted-but-unknown runs. The auto-mode classifier refused "Modify Shared
Resources". The runs are Pinterest ×3, Asana, Adobe, Chewy, HPE, Boston Scientific and Texas Instruments. Ford is not
proof: "You're all set" is Oracle's progress dialog.

**All-time results** (10-07 → 10-18, latest run per job): 1,249 jobs attempted, 892 confirmed, about 80% excluding
closed and duplicate postings. Open classes:
- Lever's security check.
- Unknowns without a receipt.
- Oracle and iCIMS email waits.
- Workday account origin (SSO).
- Owner-policy questions.

**Readiness facts:** the app is single-tenant (one profile per install). A friends-and-family beta means one instance
per user (scripts/demo_instance.sh is the model) unless multi-tenancy is built.

## 2026-10-18 20:00–21:00 UTC — all-history failure review, batch 2 (Claude)

- **SmartRecruiters DataDome wall (ff25b51, deployed 20:2x).** After about ten oneclick-ui applications in ten minutes,
  every SR tenant showed DataDome's "Verification Required" slider (geo.captcha-delivery.com) over a blank page. Twelve
  jobs re-ran two to seven times each.
  - `bot_wall.py`: a DataDome frame before submission sets `ats_outage:smartrecruiters` for 45 minutes. The run is
    nothing-sent, so it is not a start. SR runs two at a time.
  - Verified live: ServiceNow's next run stopped with "A platform bot check paused SmartRecruiters …".
  - Not built: solving the slider. A 2Captcha DataDome task needs our own IP or proxy, so the backoff stays until then.
- **Waiting-answer review (30 jobs, 10-10 → 10-18): 9895234, b34d6c3, 8d81fbd.**
  - Consent: being considered for other roles (Rivian) and consent requests (HiredScore AI screening, S&P/Kensho) follow
    the consent policy. HiredScore's pending field was its `date_parts` input, which now takes today's date.
  - "Select 'Yes' to confirm you have read this statement" picks the named option (Gas South).
  - DEMOGRAPHIC no longer fires on consent requests, acknowledgements or availability questions (Kairos).
  - A generic "Please Select:" label takes its demographic topic from its options (Acuity veteran → decline option).
  - ABB's covenant described in words answers No from `has_non_compete`; the covenant's own "not" is not a negation.
    A recorded non-solicitation agreement blocks the automatic No.
  - Dayforce's "Answered Date" text field takes today's date.
  - Reason for leaving states only the recorded history (still in the role, or the month it ended).
  - Salary "in local currency" is a plain number (Michelin refused "80,000 USD").
  - A residence question limiting hiring to listed places closes the job instead of asking (Jerry, Maryland).
  - Removed as ineligible: Jerry (residence), C3.ai (2027 graduates only), Maersk (Bengaluru).
  - Still with the owner:
    - West Monroe ×6: an unofficial transcript upload.
    - Saalex ×2: supervisor and high school.
    - OPEN Data Jobs: references.
    - U-Haul: nicotine.
    - UChicago: needs citizen/LPR vs visa status. The question defines F-1 OPT as "Yes".
    - Evolent: ASP.NET experience.
    - Federal Reserve ×2: an entity checklist with no "none" option.
    - Kairos: Part 810 authorization.
- **One-click Apply grants a fresh try** when the job used its three starts (`application_budget_reset:<job>`,
  "Owner clicked Apply"). Possibly-submitted jobs still refuse.
- **Unknown resubmission (dea327a).** The autopilot resubmits one uncertain application per tick under
  `unknown_resubmit_allowed`, only when the run's saved final page shows no confirmation. A confirming page is triaged
  `unknown_triage:<job>` = submitted and never resubmitted; it waits for the owner's reconcile.
- **Pacing.** SuccessFactors runs one at a time: runs on one career host share the browser session (AMETEK/Corning).
  iCIMS also runs one at a time, because every employer shares one sender.
- **iCIMS 0/19 root cause (1e4d997).** `ACCOUNT_EMAIL` (the base address) overrode the run's +icims alias in `Accounts`,
  so sign-in kept using the old Central identity with its pending reset. Six of eight iCIMS mail challenges today went to
  the base address as password resets. A plus-address of the configured login is now that platform's login, and the
  configured iCIMS password stays with the base identity. The ADP alias was added to the profile (Person Not Found vs
  "already has an account").
- **MyGreenhouse autofill is never clicked (aaae72a).** It signs in by emailed code before filling anything; Braze waited
  on that code.
- **Merged from subagents:** Dayforce ID flow (e43d932) and SuccessFactors tenant sign-in and picklists (2e52780).
- **Ops lesson:** the full pytest suite on this Mac starved production. The load average hit 11 on 10 cores, and eight
  Greenhouse runs hit the 600 s budget between 20:19 and 20:29. Run suites under `taskpolicy -b nice -n 19`.
- **All-time (10-07 → 10-18 20:45), per job:** 1,367 attempted, 974 confirmed, 141 closed/cancelled/removed, so **79% of
  eligible**.
  - By platform: Ashby 91%, Workday 90%, SuccessFactors 87%, JazzHR 85%, Eightfold 80%, Greenhouse 79%, Oracle 75%,
    SmartRecruiters 68%, Dayforce 57%.
  - Workable 20%, iCIMS 0/19, Lever 0 (excluded).

## 2026-10-19 04:00–05:00 UTC — overnight, deploys and supply recovery (Claude)

- **The Mac ran production on battery** and hibernated at 1% twice: 20:04–20:36 and 22:40–23:36 EDT. Throughput fell
  to 0–2 runs an hour, and the Workday 900 s timeouts sat inside the battery windows. Production needs an always-on host
  on AC for the VC demo and the beta.
- **Deployed** 9895234 → be44fad (batch 2), then 6943c90, a2bdfc8 and 723e8f0 below. The full suite passed except the
  known flaky `test_stated_char_limit`.
- **Plus-address leaks** found by the REA iCIMS map and by ADP Avalon's answers.json. The Email fields came from the
  knowledge fact `personal:email` and from saved confirmed answers (`profile:confirmed`), both holding the base mailbox.
  `Resolver.platform_address()` in `resolve()` now covers every source. Verified: today's iCIMS and ADP mail challenges
  go to +icims and +adp.
- **Stranded supply.** 896 of 905 Ashby and 78 of 89 Greenhouse "new" jobs had no `job_listings` row (the 10-18 bulk
  import), so the autopilot never ranked them. 84 role-matching non-senior ones were ranked through
  `POST /api/jobs/{id}/rank`; those ranked ready were applied through `/apply` (remembered behind Ashby pacing).
- **Fresh tries** through one-click Apply: 12 fixed waiting-answer jobs (Rivian confirmed, Acuity already applied), 18
  iCIMS jobs (one at a time), and the ADP pair.
- **Unknown resubmit guard (6943c90)** also reads `forms.EMPLOYER_CONFIRMATION_RE`. Asana's final page ("Task complete!
  Thank you for sharing your information") is a submission the narrow pattern missed. Asana's job is paused; the owner
  reconciles it. Of the current unknowns, 45 have no saved page (never auto-resubmitted) and 10 show no confirmation
  (resubmit candidates).
- **a2bdfc8**
  - Free-text or unreadable-option identity questions take the owner's fact (Sentry Pronouns → He/him). A list with a
    decline still declines first.
  - "Chrome worker tab initialization failed … No employer form was opened" is nothing-sent.
- **723e8f0** A required SMS consent with only "I understand and agree" follows the terms policy (SeekWell).

## 2026-10-19 05:00–05:45 UTC — Greenhouse / Ashby / Workday / Oracle failure sweep (Claude)

- **Owner request:** fix the failures on these four platforms, and the timeouts flooding Telegram.
- **Environmental, not engine:**
  - All 27 Greenhouse 600 s timeouts fell between 20:02 and 21:43 UTC, during my full test suite and then the Mac on
    battery. At 19:00 the rate was 24/38 confirmed, avg 144 s; at 20:00 it was 7/43, avg 271 s, with 21 timeouts.
  - Workday 900 s timeouts, the veteran-commit deadlines (GM, CoStar) and the account-step stalls also sit in the
    battery windows. A last-name text field took 6.4 s to write.
- **Fixes, deployed as execution-v2-e480d462aae4; full suite 1196 passed:**
  - **92437ac — Workday Degree.** 39 jobs timed out on Education 2's "BE": the virtualized list was typed "BE" first,
    and the owner's equivalent came only after every alias. Aliases are now typed first, the policy choice is checked on
    each window, and the first attempt skips the pause.
  - **c4537cf — Telegram.**
    - Silent for nothing-sent stops, scheduled recoveries, and stalls the autopilot retries (starts left). Since 10-18
      12:00 there were 420 outcome messages, 105 of them nothing-sent.
    - Same commit: Workday "Not Specified" (Sentry's gender list) reads back as chosen. The scanner treated it as a
      placeholder, so the result was "Value not accepted" ×5.
  - **eba7634 — Oracle employer Other.** Oracle's `select_combo` lacked the employer "Other" fallback, which caused
    Goldman's "Name of Latest Employer" stall ×4.
  - **d1b861e — legal name.** "full legal name as it appears on your government ID" is answered with the applicant's
    name (Waymo ×3 had failed scope validation).
  - **45a5afb — dialogs.** A chat widget or a non-covering dialog no longer blocks clicks; a modal still does (Qualtrics
    chatbot, 3 stalls).
- **Fresh tries:** 12 jobs queued.
  - 7 are refused until tomorrow by the per-job daily model budget (cost ceiling kept): Early Warning, Broadridge,
    David Yurman, Northwestern Mutual, Expedia, 1-800 Contacts, Goldman.
- **Not changed:**
  - Workday school prompts: 35 errors since 10-18. 21 are deadlines, mostly in slow windows; 14 are "no matching
    option", meaning neither the school nor "Other" is offered.
  - Ashby reCAPTCHA-score rejections (OpenAI, Aurora): retried once. Trace Labs passed on retry before.

## 2026-10-19 05:45–07:00 UTC — 6 workers; Taleo / Eightfold / Oracle / ADP pass (Claude)

- **Workers 12 → 6** (owner): `ApplyPilot/.env WORKERS=6`, redeployed. Since 05:30, 26 confirmed and 2 timeouts
  (the period before had about 35 timeouts). Most other non-confirmations were my deploy interruptions (14).
- **Eightfold → Workday handoff (4e0d04c, c3284f6).**
  - Morgan Stanley's Eightfold Apply is a link_off to `ms.wd5.myworkdayjobs.com` (eightfold.md risk #1).
  - The navigator now hands the run to the landing platform's adapter, once and never after the commit.
  - A handoff to Workday adopts the tenant posting as the run's job URL in memory, so Workday's account-origin check
    passes.
  - Result: Morgan Stanley confirmed ×2 within minutes. It had looped six times before.
- **Oracle.**
  - JPMorgan Chase got its first confirmation since the mail fixes; Fortinet and Honeywell confirmed.
  - hCaptcha: a puzzle replaced while 2Captcha worked gets another paid round (ee0664e). Honeywell had stopped on
    "Image challenge changed while the solver was working" after a 25 s answer.
- **iCIMS crash (a18ebd7).** `captcha.py:435` raised "Frame was detached" after the sign-in click; it now ends the solve
  and re-reads the page.
- **ADP (map: rea/maps/adp.md).** The identity is the blocker, not the engine.
  - `+adp@gmail.com` and `@googlemail.com` (domain alias 445e2b7, later reverted in the profile) both got ADP's
    "Another user account already exists with the provided email address. Login using an alternate mobile number or
    an email address".
  - ADP canonicalizes Gmail onto an existing ADP identity, likely a classic ADP ID.
  - Owner needed: a non-Gmail email for ADP, or the ADP ID and password.
  - Also on the page: F5 bot defense (zeronaught.com). Error codes 4040, 4084/4008, 4011 and RATE_LIMIT come from the
    bundle.
- **Taleo:** nothing testable.
  - All 16 Taleo jobs are skipped for real reasons. Kearney's Data Scientist (score 82) requires the ability to obtain a
    Secret clearance.
  - Discovery has no Taleo source kind; Taleo supply would need a careersection feed.
- **Mistake caught:** a bulk Apply included Veregy (a skipped, empty posting). It was paused at once and the posting was
  closed, so nothing was submitted. Bulk Apply calls must filter to stalled or ready states only.
