# Failure analysis: every unsuccessful application, 2026-10-07 to 2026-10-20

Written by the forensics workstream (branch `agent/forensics`). Read-only analysis of the production database
(snapshot of 2026-10-20 about 03:30 UTC: 4,998 runs, 5,898 jobs, 174,558 events, 3,568 failure reports, 65 triage
verdicts) plus saved run artifacts, checked against the code in this repository (the October 20 snapshot).
Company and ATS names are used; nothing here is applicant data.

## 1. Executive summary

**Scale.** 1,572 jobs were attempted. 1,167 (74%) reached `confirmed` or `already_applied`; **405 never did**.
Of 4,998 runs, **3,822 (76%) ended in some other state**. Those runs used 170.6 of 255.8 worker-hours (67%) and $99.95
of the $125.37 model spend (80%). A confirmed run takes 264 s and $0.022 on average.

**Where the 405 jobs ended** (job level, last run):

| Outcome | Jobs | What it means |
|---|---:|---|
| Posting closed or internal-only | 73 | Nothing to fix. |
| Owner paused, deleted or swept by the maintenance cancel | 38 | A decision, not a defect (see flow F1). |
| Waiting for an applicant answer or answer rejected | 55 | Orchestrator's area (answers and knowledge); counted in section 4.9. |
| Outcome unknown (`submission_unknown`) | 42 | At least 12 were really submitted (section 4.1). |
| Everything else: engine, site, account, captcha, environment | 197 | Sections 4.2 to 4.8. |

**The picture moved a lot during the period.** In the last 48 hours (since 10-18 12:00 UTC) the system confirmed
267 runs against 504 non-success runs, and most of those 504 are explained by things that are not defects in the
form logic: deploys that interrupted work (84 runs), the host overload window of 10-18/19 (70 timeouts), iCIMS before
its fixes (137 runs) and the SmartRecruiters bot wall (52 runs, fixed on the evening of 10-18).

**What I found that is still live in the code (and what I changed):**

1. **Employer-hosted Greenhouse forms cannot complete the emailed-code step.** 13 of 21 jobs whose Greenhouse form
   is embedded on the employer's own site (Roblox 5 of 8, Toast 3 of 3, Stripe 2 of 2, Waymo, Asana, Qualtrics)
   never succeeded, against 80% on hosted boards. The code-origin check compares the top-level page (the
   employer's site) with the mail rule, so it rejects the only valid origin; the run then ends as
   "submission unknown" without saying why. Fixed (section 6, F-A and F-B).
2. **Successful submissions recorded as failures.** At least 12 jobs sit in `submission_unknown` although a saved
   page proves they were submitted (11 Workday "Application Submitted" modals and Oracle/Workable/Greenhouse
   pages from 10-07 to 10-14). 32 of the 42 unknown jobs have never been triaged at all. Table in section 4.1.
3. **Host overload turns into timeouts.** 223 runs (37.9 worker-hours) ended on a deadline, concentrated in three
   windows (10-14 12:00-20:00, 10-17 06:00, 10-18 19:00 to 10-19 06:00). In the last window 940 field writes hit
   the 12-second write deadline, 70 runs timed out and the median confirmed run took 250 s instead of 115 s. The
   code has no load shedding and counts these runs as spent starts (section 4.2).
4. **Duplicate submissions happened** between 10-10 and 10-14: 11 jobs have more than one confirmed run (Assured
   Guaranty, Perseus Group and others up to three times). `Service.queue` now refuses a job with a confirmed run,
   and no repeat appears after 10-16 (section 5, F7).
5. **The repair agent can save a masked phone number** (`9999999999`, Stripe 10-18) because only the `[email]`
   style placeholders were rejected. Fixed (F-C).
6. **Pipeline waste.** 455 runs repeated an identical failure for the third time or later (16 hours, 3,218 model
   calls); 99 jobs are still paused by the maintenance cancel sweep, 41 of them never applied; the sweep also
   overwrote the failure reason of 514 old runs (recoverable only from `finished` events); 2,341 Telegram
   deliveries were retried with no upper bound. The full list is in section 5.

**Already fixed before this analysis** (legacy commit ids are those recorded in `docs/rea/findings/FINDINGS.md`):
the Chrome-unavailable and deploy-interrupted runs no longer burn the three starts (e7f6e17), Oracle sender
attribution (per-tenant leases, 10-18), SmartRecruiters bot wall (ff25b51), Workday education pickers (92437ac),
Workday maintenance and locale handling (a59937f, d48faab), Eightfold handoff to Workday (4e0d04c, c3284f6),
employer application limits on Ashby (`application_pacing.application_cap`, this snapshot), the refused-resume
event flood (8ee5d73), and the iCIMS work listed in the brief. Section 4 states for each cluster when it was last
seen and what the code does now.

**Done since** (section 6): load shedding and the worker-Chrome circuit (1590276), drain before stops (08a3029), the
outbox dead-letter (4299d18), one job per requisition (7ef11c9), the `waiting_email` state (ab4bf0d), the maintenance
cancel keeping reasons (2535a85). The 12 hidden successes were reconciled on 10-20.

**Left for the orchestrator** (details in section 7): grant fresh tries to the jobs whose cause is fixed or
environmental, and decide the open owner items (Lever, ADP identity, Dayforce Global Identity, Honeywell/JPMorgan
hCaptcha).

## 2. Method and definitions

* **Unsuccessful job**: no run of the job reached `confirmed` or `already_applied` (405 jobs). **Non-success run**:
  any other run state (3,822 runs, including 16 dry runs that are not failures).
* Each run is placed in exactly one cluster by its recorded reason, with three refinements: (1) 514 runs whose
  reason was overwritten by the maintenance cancel are read from their `finished` event; (2) "The browser did not
  advance" runs are split by the last verification problem, refusal or field error before the stall; (3) free-text
  blockers written by the navigator model are split by keyword. The classifier is deterministic and was run over
  all 3,822 runs; every run has a cluster.
* **Jobs that ended here** counts jobs whose last run is in the cluster. **Runs since 10-18 12:00** is the
  liveness signal; the code was then read to check whether the cause is still present.
* "Hosted" Greenhouse means the browser opened `job-boards.greenhouse.io`; "embedded" means it opened the
  employer's own site (Roblox, Stripe, Waymo and others) with the Greenhouse form in an iframe.
* Time and cost are the recorded `elapsed` (including waiting) and `cost` of each run.

## 3. Results

### 3.1 By ATS (jobs)

| ATS | Jobs attempted | Reached confirmed / already applied | Rate | Never succeeded | of which closed | owner-stopped | needs an answer | outcome unknown | other (engine, site, account, environment) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| workday | 756 | 618 | 82% | 138 | 44 | 27 | 17 | 9 | 41 |
| greenhouse | 211 | 168 | 80% | 43 | 3 | 1 | 6 | 9 | 24 |
| ashby | 145 | 126 | 87% | 19 | 2 | 0 | 9 | 1 | 7 |
| oracle | 112 | 85 | 76% | 27 | 1 | 2 | 1 | 9 | 14 |
| smartrecruiters | 92 | 59 | 64% | 33 | 5 | 2 | 3 | 5 | 18 |
| custom | 57 | 10 | 18% | 47 | 6 | 1 | 10 | 1 | 29 |
| successfactors | 56 | 47 | 84% | 9 | 1 | 0 | 2 | 0 | 6 |
| icims | 46 | 8 | 17% | 38 | 8 | 4 | 2 | 0 | 24 |
| eightfold | 26 | 21 | 81% | 5 | 0 | 1 | 0 | 1 | 3 |
| lever | 21 | 0 | 0% | 21 | 0 | 0 | 1 | 0 | 20 |
| dayforce | 16 | 9 | 56% | 7 | 2 | 0 | 0 | 0 | 5 |
| jazzhr | 13 | 11 | 85% | 2 | 0 | 0 | 0 | 2 | 0 |
| workable | 11 | 2 | 18% | 9 | 0 | 0 | 4 | 5 | 0 |
| adp | 4 | 0 | 0% | 4 | 1 | 0 | 0 | 0 | 3 |
| rippling | 3 | 0 | 0% | 3 | 0 | 0 | 0 | 0 | 3 |
| breezy | 1 | 1 | 100% | 0 | 0 | 0 | 0 | 0 | 0 |
| taleo | 1 | 1 | 100% | 0 | 0 | 0 | 0 | 0 | 0 |
| jobvite | 1 | 1 | 100% | 0 | 0 | 0 | 0 | 0 | 0 |
| **All** | **1572** | **1167** | **74%** | **405** | **73** | **38** | **55** | **42** | **197** |

Workday is 48% of all attempts; 97 of its 138 non-success jobs are closed postings, owner decisions, answers or
unknown outcomes. The jobs with an open engine, site, account or environment cause are Workday (41), Greenhouse (24),
iCIMS (24, fixed after the data ended), SmartRecruiters (18), Oracle (14) and Lever (20, excluded). `custom` (company career sites with no
adapter) succeeds 18% of the time by design.

### 3.2 By cluster (runs)

| ID | Cluster | Runs | Jobs | Jobs that ended here | First – last | Runs since 10-18 12:00 | Top ATS | Example run |
|---|---|---:|---:|---:|---|---:|---|---|
| N1 | Form stalled: verification problem the controller could not fix | 448 | 210 | 27 | 10-07 – 10-20 | 78 | successfactors 94, workday 82, eightfold 54 | `752dca69a664` |
| N2 | Agent gave up with a free-text blocker | 426 | 185 | 24 | 10-07 – 10-20 | 32 | workday 108, successfactors 48, custom 46 | `7806ec90389c` |
| I1 | Chrome automation connection unavailable | 411 | 179 | 4 | 10-07 – 10-19 | 10 | workday 157, greenhouse 74, custom 30 | `0684966982ba` |
| Q1 | Waiting for an applicant answer (missing fact) | 275 | 188 | 51 | 10-07 – 10-19 | 25 | workday 119, greenhouse 54, custom 24 | `418827d7bc00` |
| E2 | Parked waiting for an emailed code or link | 266 | 143 | 20 | 10-07 – 10-19 | 31 | workday 94, oracle 67, eightfold 51 | `7e76b8df7dfb` |
| C1 | CAPTCHA / bot check refused or unsolved | 256 | 122 | 33 | 10-07 – 10-20 | 40 | icims 78, lever 66, oracle 33 | `7def55b5adf7` |
| A1 | Account, sign-in or sign-up did not complete | 253 | 141 | 27 | 10-07 – 10-20 | 41 | workday 112, icims 76, custom 25 | `e85259b7b3c1` |
| I4 | Deadline / timeout (run or operation) | 223 | 174 | 23 | 10-07 – 10-19 | 64 | workday 104, greenhouse 60, smartrecruiters 16 | `a489bcd8893b` |
| I3 | Model provider unavailable or budget spent | 174 | 107 | 1 | 10-07 – 10-17 | 0 | workday 92, oracle 15, greenhouse 14 | `665f77ff3264` |
| E1 | Shared email sender busy (nothing sent) | 174 | 84 | 7 | 10-07 – 10-18 | 21 | oracle 112, icims 30, eightfold 24 | `2317fb5f7f17` |
| Q2 | Answer evidence rejected / value not accepted | 156 | 73 | 4 | 10-07 – 10-19 | 15 | workday 102, greenhouse 18, eightfold 8 | `cbd51a1e072d` |
| I2 | Deploy / restart interrupted the run | 139 | 104 | 3 | 10-12 – 10-19 | 84 | workday 50, oracle 35, greenhouse 17 | `30e89bafa6d7` |
| S1 | Submission outcome unknown | 116 | 94 | 42 | 10-07 – 10-19 | 11 | greenhouse 24, oracle 18, custom 13 | `cd4046a60e86` |
| I5 | Generic "Browser control failed at file:line" | 112 | 76 | 8 | 10-07 – 10-19 | 6 | workday 26, oracle 19, icims 15 | `b2caa52b815e` |
| N3 | Agent gave up (continuous owner / controller) | 95 | 38 | 1 | 10-07 – 10-14 | 0 | workday 28, successfactors 20, eightfold 12 | `7653cda1d662` |
| O2 | Owner paused, deleted or removed under a rule | 89 | 65 | 38 | 10-07 – 10-20 | 7 | workday 39, icims 18, oracle 14 | `dc487f0c812f` |
| P1 | Posting closed or internal-only | 75 | 74 | 73 | 10-07 – 10-19 | 17 | workday 44, icims 8, custom 6 | `de7e99b65355` |
| P2 | Posting page missing, retried | 66 | 42 | 8 | 10-07 – 10-18 | 7 | workday 62, smartrecruiters 4 | `a372546f0086` |
| S2 | Employer rejection or existing application | 25 | 19 | 5 | 10-07 – 10-20 | 8 | ashby 9, custom 4, oracle 4 | `d431016d2a5e` |
| X1 | Worker crash (unhandled exception) | 18 | 14 | 1 | 10-07 – 10-16 | 0 | workday 6, custom 2, rippling 2 | `769115fcb44f` |
| dry_run | Dry run (not a failure) | 16 | 9 | 3 | 10-08 – 10-14 | 0 | greenhouse 7, workday 6, ashby 2 | `7f2b21d3cbe4` |
| P3 | ATS maintenance / outage | 9 | 5 | 2 | 10-07 – 10-19 | 7 | workday 9 | `957d56f9da3a` |
| | **All non-success runs** | **3822** | **937** | **405** | | **504** | | |

`N1` and `N2` are composite clusters; their parts follow.

**N1 split by the last problem seen before the stall**

| Sub-cluster | Runs | Jobs | First – last | Runs since 10-18 12:00 | Top ATS | Example run |
|---|---:|---:|---|---:|---|---|
| no-signal | 178 | 97 | 10-07 – 10-19 | 34 | successfactors 58, smartrecruiters 25, eightfold 25 | `5f67b9ed48ee` |
| other-field | 44 | 25 | 10-07 – 10-19 | 4 | workday 9, smartrecruiters 8, jazzhr 7 | `eecb4ee6f9a3` |
| phone | 31 | 10 | 10-13 – 10-16 | 0 | greenhouse 26, eightfold 3, successfactors 2 | `19cb4619e54e` |
| country | 20 | 12 | 10-07 – 10-17 | 0 | successfactors 8, custom 7, greenhouse 2 | `22440524f392` |
| state/province | 19 | 11 | 10-07 – 10-19 | 4 | eightfold 6, dayforce 4, workday 3 | `63f3e1d8af94` |
| school | 19 | 13 | 10-07 – 10-19 | 2 | workday 10, smartrecruiters 3, ashby 3 | `5ec860549276` |
| gender/sex | 16 | 7 | 10-07 – 10-18 | 5 | workday 14, smartrecruiters 1, successfactors 1 | `d6fdf9ba9fb0` |
| degree | 13 | 10 | 10-12 – 10-19 | 11 | workday 12, custom 1 | `dad4f3dee73d` |
| resume-gate | 13 | 6 | 10-14 – 10-17 | 0 | eightfold 11, workday 1, successfactors 1 | `149c7e1642c5` |
| work-authorization | 11 | 5 | 10-07 – 10-18 | 1 | oracle 9, greenhouse 1, workday 1 | `bcaea622d959` |
| date | 10 | 8 | 10-07 – 10-16 | 0 | eightfold 4, icims 2, oracle 2 | `05c9b93e0746` |
| answer-validation | 10 | 10 | 10-07 – 10-16 | 0 | workday 5, successfactors 2, greenhouse 1 | `52dfcf223a76` |
| email | 9 | 5 | 10-12 – 10-20 | 1 | adp 4, eightfold 3, successfactors 1 | `752dca69a664` |
| resume-attachment | 9 | 1 | 10-14 – 10-15 | 0 | successfactors 9 | `28dbfaf3f07d` |
| how-did-you-hear | 7 | 7 | 10-07 – 10-17 | 0 | workday 5, custom 1, successfactors 1 | `5efcab4e38fc` |
| field-of-study | 7 | 5 | 10-07 – 10-19 | 1 | workday 7 | `1a63efb4dba2` |
| overlay-or-validation-text | 6 | 3 | 10-17 – 10-18 | 3 | greenhouse 3, taleo 2, successfactors 1 | `78897c5ad626` |
| salary | 5 | 5 | 10-12 – 10-19 | 1 | workday 2, ashby 1, smartrecruiters 1 | `560e4f187a98` |
| veteran | 4 | 4 | 10-11 – 10-18 | 3 | workday 3, rippling 1 | `bc33c328a4f2` |
| submit-control | 4 | 4 | 10-14 – 10-19 | 1 | greenhouse 3, eightfold 1 | `94a23fce3586` |
| talent-community | 4 | 2 | 10-17 – 10-18 | 2 | greenhouse 4 | `45606fe85e2c` |
| employer | 4 | 3 | 10-18 – 10-18 | 4 | oracle 4 | `3b699e43ee0c` |
| verification | 3 | 3 | 10-12 – 10-18 | 0 | custom 1, workday 1, greenhouse 1 | `4582da6e837b` |
| location | 2 | 2 | 10-07 – 10-18 | 1 | greenhouse 1, workday 1 | `f66059764b54` |

**N2 free-text blockers written by the navigator model**

| Sub-cluster | Runs | Jobs | First – last | Runs since 10-18 12:00 | Top ATS | Example run |
|---|---:|---:|---|---:|---|---|
| other | 84 | 55 | 10-07 – 10-19 | 2 | workday 19, oracle 15, custom 12 | `9307e7bb015f` |
| resume-upload | 81 | 45 | 10-07 – 10-18 | 2 | icims 14, rippling 12, successfactors 10 | `18dd25a1d614` |
| button-no-effect | 74 | 51 | 10-07 – 10-19 | 19 | workday 13, smartrecruiters 12, successfactors 10 | `48074d7b0f6a` |
| geo-picker | 47 | 27 | 10-07 – 10-20 | 1 | successfactors 12, custom 7, icims 5 | `7806ec90389c` |
| how-did-you-hear | 37 | 17 | 10-07 – 10-19 | 2 | workday 36, greenhouse 1 | `b73b10a7a6a2` |
| needs-fact-or-policy | 28 | 14 | 10-07 – 10-16 | 0 | workday 13, smartrecruiters 4, greenhouse 3 | `e813ccd8ec20` |
| account-form | 23 | 18 | 10-07 – 10-18 | 1 | custom 6, successfactors 5, icims 4 | `1061d80f33d8` |
| date-field | 22 | 10 | 10-09 – 10-18 | 1 | eightfold 12, workday 4, smartrecruiters 2 | `4bd46b75216f` |
| education-picker | 19 | 17 | 10-07 – 10-18 | 2 | workday 11, custom 2, greenhouse 2 | `6623c14f4d29` |
| observation-timeout | 11 | 11 | 10-12 – 10-18 | 2 | smartrecruiters 4, successfactors 3, greenhouse 2 | `e25be9df07dc` |

### 3.3 Where each unsuccessful job ended (jobs, by ATS)

| Last run ended in | workday | greenhouse | ashby | oracle | smartrecruiters | icims | custom | successfactors | lever | eightfold | dayforce | other | Total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P1 Posting closed or internal-only | 44 | 3 | 2 | 1 | 5 | 8 | 6 | 1 |  |  | 2 | 1 | 73 |
| Q1 Waiting for an applicant answer (missing fact) | 17 | 5 | 8 |  | 2 | 2 | 10 | 2 | 1 |  |  | 4 | 51 |
| S1 Submission outcome unknown | 9 | 9 | 1 | 9 | 5 |  | 1 |  |  | 1 |  | 7 | 42 |
| O2 Owner paused, deleted or removed under a rule | 27 | 1 |  | 2 | 2 | 4 | 1 |  |  | 1 |  |  | 38 |
| C1 CAPTCHA / bot check refused or unsolved |  |  |  | 4 | 2 | 7 | 1 |  | 18 |  | 1 |  | 33 |
| A1 Account, sign-in or sign-up did not complete | 8 |  |  |  |  | 5 | 9 | 1 |  |  | 1 | 3 | 27 |
| N1 Form stalled: verification problem the controller could not fix | 5 | 4 |  | 3 | 4 | 1 | 1 | 5 |  | 1 | 2 | 1 | 27 |
| N2 Agent gave up with a free-text blocker | 4 | 1 | 1 | 1 | 9 | 1 | 6 |  |  |  |  | 1 | 24 |
| I4 Deadline / timeout (run or operation) | 4 | 12 |  |  | 2 |  | 4 |  |  |  | 1 |  | 23 |
| E2 Parked waiting for an emailed code or link | 5 | 1 |  | 2 |  | 5 | 5 |  |  | 2 |  |  | 20 |
| I5 Generic "Browser control failed at file:line" |  | 4 |  | 1 | 1 | 1 |  |  |  |  |  | 1 | 8 |
| P2 Posting page missing, retried | 8 |  |  |  |  |  |  |  |  |  |  |  | 8 |
| E1 Shared email sender busy (nothing sent) | 2 |  |  | 2 |  | 3 |  |  |  |  |  |  | 7 |
| S2 Employer rejection or existing application |  |  | 5 |  |  |  |  |  |  |  |  |  | 5 |
| I1 Chrome automation connection unavailable | 1 |  |  |  |  | 1 | 2 |  |  |  |  |  | 4 |
| Q2 Answer evidence rejected / value not accepted |  | 1 | 1 | 1 | 1 |  |  |  |  |  |  |  | 4 |
| I2 Deploy / restart interrupted the run |  |  |  | 1 |  |  |  |  | 2 |  |  |  | 3 |
| dry_run Dry run (not a failure) |  | 2 | 1 |  |  |  |  |  |  |  |  |  | 3 |
| P3 ATS maintenance / outage | 2 |  |  |  |  |  |  |  |  |  |  |  | 2 |
| X1 Worker crash (unhandled exception) |  |  |  |  |  |  | 1 |  |  |  |  |  | 1 |
| I3 Model provider unavailable or budget spent | 1 |  |  |  |  |  |  |  |  |  |  |  | 1 |
| N3 Agent gave up (continuous owner / controller) | 1 |  |  |  |  |  |  |  |  |  |  |  | 1 |

## 4. Clusters in detail

Each cluster states the evidence (a representative run id: the first 12 characters are enough for a `LIKE`
query on `runs.id`), the cause in code, whether it is live, and the fix. "Last seen" is the last run in the
cluster.

### 4.1 S1 Submission outcome unknown (116 runs, 94 jobs, 42 jobs ended here)

The most important cluster for correctness: an unknown run may be a real application, and the autopilot's
`resubmit_unknown` (`autopilot.py:393`) may submit it again.

Sub-causes, with representative runs:

| Sub-cause | Runs / jobs | Last seen | Cause in code | Live? |
|---|---|---|---|---|
| Greenhouse form embedded on the employer site: the emailed code is never entered | 10 runs, 9 jobs (Roblox 5 runs, Toast 3, Stripe, Waymo) | 10-19 05:17 | `email_browser.py:535-537` (`handle`) and `:600-603` compare `e.page.url`, the employer's page, with the mail rule; the code inputs live in the Greenhouse iframe. Roblox `50e9bd1d` raised "Add this employer's login origin to its email verification rule"; Stripe `e8d94af7`, Waymo `559a9a41` and Roblox `48758e4e` ended 2 to 11 s after the commit with no mail poll. | Yes. Fixed here (F-A). |
| Post-commit exception is swallowed into a generic message | all armed failures | 10-19 | `browser.py:4326-4330` returned "Submission was attempted but its outcome could not be verified" without the exception class or location, so the cause of 8 unknowns on 10-18/19 is not in the data. | Yes. Fixed here (F-B). |
| Receipt not recognised | HPE x3, Visa, Viridien, Booz Allen x2, Chewy (Workday), Ford and Texas Instruments (Oracle), United Placement (Workable), Perpay and Asana (Greenhouse) | 10-18 | Workday "Application Submitted" modal, Oracle "Thank you for your job application", Greenhouse "Task complete!" were not in the receipt rules (`forms.py:550`, legacy 2978eed and e06586f added them). | No for new runs. The old runs stay unknown (table below). |
| Greenhouse code entered, page did not leave the code step | Sparksoft `13ffd8cd` | 10-18 16:01 | `email_browser.py:760`: "No verified transition"; `code_fields_remaining: 8` after the click. Cause not provable from the saved data (stale or rejected code). | Unknown. F-B will record the detail next time. |
| Hosted Greenhouse, host overloaded | IEM `0deadef9`, Stripe 2 | 10-18 20:57 | Commit at 20:57:26, unknown 25 s later with no email step; the window had 236 write deadlines per hour. | Environment (4.2). |
| hCaptcha refusal after the commit | Lever Zoox `2cce97b6`, AIFund `bd69f621`; Honeywell `cd4046a6` | 10-19 05:52 | Employer answered HTTP 400 to a token earned by this browser (Lever); Oracle puzzle not accepted. | Yes (4.6). |
| Worker interrupted during submit | ClickHouse `25f309b1` | 10-19 05:54 | `workers.recover_interrupted` marks a `submitting` run unknown, by design. | Deploy discipline. |
| "Submission is not authorized for this run" | SAIC x2, Howmet x2 | 10-14 | `reserve_submission` saw the run no longer `running` because a controller changed it mid-commit. | Not seen since 10-14. |
| Phone rejected after the commit started | Greenhouse, 25 runs, 7 jobs | 10-14 | Client-side validation refusal recorded as unknown because the run was already armed; `watch_submission` now disarms on a validation rejection (`browser.py:3043-3069`). | No. |

**Hidden successes.** These jobs are `submission_unknown` although a saved page was reviewed and shows the
submission. They count as failures in every report and the autopilot treats them as unsafe to retry:

| Job | ATS | Job status | Evidence recorded by the triage |
|---|---|---|---|
| Perpay - Career's Page - Software Engineer, New Grad - Super Day (`81b823cc`) | greenhouse | submission_unknown | Page shows thank-you message confirming application was received. |
| Ford Motor Company - Full Stack Software Engineer (`3735a321`) | oracle | submission_unknown | final.png reviewed 2026-10-13: employer confirmation visible |
| Texas Instruments - Data Engineer (`2e51dc54`) | oracle | submission_unknown | Oracle profile shows toast Thank you for your job application and Data Engineer 25018445 status Application/Re |
| United Placement Group - AI Developer - Claude + Python (`716fbd48`) | workable | submission_unknown | Page displays 'Thank you!' and 'Your application has been submitted successfully.' |
| Booz Allen - Software Engineer (`dc7d0711`) | workday | submission_unknown | Modal reads 'Application Submitted' confirming receipt of the application. |
| Booz Allen - Full Stack Software Engineer - Junior (`25159827`) | workday | submission_unknown | Modal states 'Application Submitted' and confirms the application was received. |
| Chewy - Business Intelligence Analyst 1 (`4fa99128`) | workday | submission_unknown | final.png reviewed 2026-10-13: employer confirmation visible |
| Hewlett Packard Enterprise - Graduate Systems/Software Engineer 1 (`b75f3350`) | workday | submission_unknown | Modal confirms 'Application Submitted' and application status shows In Progress. |
| Hewlett Packard Enterprise - Systems/Software Engineer 1 (`be540385`) | workday | submission_unknown | Modal states 'Application Submitted' with checkmark confirmation; applications list shows  |
| Hewlett Packard Enterprise - AI and Machine Learning Engineering Grad (`b2a9aaea`) | workday | submission_unknown | Modal shows 'Application Submitted' with checkmark and success confirmation message. |
| Viridien - HPC Jr Linux IT Specialist (`5ed46eee`) | workday | submission_unknown | Modal states 'Application Submitted' with congratulations; application appears in Active l |
| Visa - Software Engineer (`e3b67112`) | workday | submission_unknown | Modal reads 'Application Submitted' with confirmation email message; job list shows applic |

32 of the 42 jobs that ended unknown have no triage verdict at all (`unknown_triage:<job>`), among them Asana
(final page "Task complete!", the receipt rule now matches it), seven Oracle jobs (EXL, Ford, Honeywell, Howmet x2,
SAIC x2), five SmartRecruiters, four Workday (Adobe, CACI, Morgan Stanley, Parexel) and three Workable jobs. A fresh triage with the current receipt rules is
the cheapest way to recover successes.

### 4.2 Environment: I4 deadline / timeout, I2 deploys, I1 Chrome, I3 model provider, I5 generic control failure

* **I4 Deadline / timeout: 223 runs, 174 jobs, 37.9 worker-hours, $7.10.** Last seen 10-19 19:46. 64 runs since 10-18
  12:00. Not a form defect: the same fields (Degree, Field of Study, School, Location) that write in a fraction of
  a second on a quiet host hit the 12 s write deadline (`forms.py` per-field budget) in three windows. 10-18 19:00
  to 10-19 06:00: 940 `field_error` events with `interaction: deadline`, median confirmed run 250 s (115 s in the
  hours around it), 34 Greenhouse and 19 Workday runs timed out (600 s and 900 s budgets). The FINDINGS record
  places a full test-suite run and a battery-powered Mac in those hours. `application_budget.nothing_sent`
  (`application_budget.py:30`) does not include deadline timeouts, so a job loses one of its three starts per
  overload hour (1-800 Contacts, Lyft x3, Murj, Roblox, Stripe, Lucid: all `ready` or `timed_out` now).
  *Fixed (1590276, 4d759d7):* `host_health.HostLoad` samples event-loop lag; `provider_health.dispatch_limit` halves the
  workers when loaded and runs one at a time when overloaded; a deadline timeout while overloaded is a free retry
  (`host_overloaded_result`).
* **I2 Deploy interrupted: 139 runs, 104 jobs.** 10-12 to 10-19; 84 in the last 48 hours (10-18 19:22 to 19:45:
  35 runs in 23 minutes). `workers.recover_interrupted` (`workers.py:291`) turns `running` into `waiting_browser`
  and schedules recovery; it is correct and costs no start (`NOTHING_SENT`), but every deploy restarts all in-flight
  forms from the checkpoint. The deploy script's hold/drain stage exists; the cut-over must use it.
* **I1 Chrome automation unavailable: 411 runs, 179 jobs.** 375 of them in one incident, 10-11 21:00 to 10-12 02:00
  (Workday 157, Greenhouse 74). The worker Chrome answered `/json/version` but refused the automation connection:
  `browser.py:619-631` raises `BrowserUnavailable`, which the run records as "Open the ApplyPilot Chrome window"
  (before e7f6e17 the generic wrapper wrote "Browser control failed at browser.py:441", 158 runs). Both texts are
  now `NOTHING_SENT` (`application_budget.py:29-39`), so starts are not burned. *Fixed (4d759d7, 1590276):* the texts are nothing-sent and a circuit in `dispatch_limit` pauses starts after three
  runs in a row that could not open a page (the incident dispatched about 250 runs into a dead browser in five hours). 10 runs
  since 10-18 12:00 (`attach_target`/`create_target` timeouts on 10-19).
* **I3 Model provider unavailable or budget spent: 174 runs, 107 jobs**, 10-07 to 10-17, none since 10-17 21:49
  (Workday 92). Provider cooldown (`provider_health.py:118`) and the per-job daily model budget are working as
  designed; the runs are `NOTHING_SENT`. Fixed.
* **I5 "Browser control failed at file:line": 112 runs, 76 jobs.** The wrapper at `browser.py:1188` keeps the
  exception class, a sanitised message and five frames in `diagnostics`, but the visible reason is only
  `file:line`, which changes with every edit. The 158-run 10-11 spike was I1. The rest are single occurrences in
  `widgets.py` (pickers), `forms.py`, `email_browser.py` and `captcha.py:435` (iCIMS, fixed 10-19, a18ebd7).

### 4.3 N1 / N2 / N3 Form stalls and gave-up agents (448 + 426 + 95 runs)

These are runs where the controller (or the navigator model) gave up on a page. Sub-causes by evidence, ATS and
status:

* **Greenhouse phone (26 runs, 7 jobs, 10-13/14).** "Value not accepted: Phone / Phone number is too short" after
  the form was filled; the number was typed without the format the page wanted. 0 since 10-16 (the phone is now
  retyped before the final submit, `phone_typed` events; `phone_fields.py`). Fixed.
* **SuccessFactors sign-in and Apply loops (58 runs, 23 jobs; last 10-16).** Click "Sign In"/"Apply Now" repeated
  without a transition, mostly when several applications shared one career host session (Corning, AMETEK,
  Boehringer). SuccessFactors now runs one application at a time and has its own sign-in (legacy 2e52780). One
  requisition remains: **ASSA ABLOY "AI Scientist" (81 runs over two job rows, 10-11 to 10-16)**: required
  State/Province/Region picklist that never accepted a value, work-experience date rows, resume prefill dialog. The
  second row exists only because its URL carries the hint `?ats=successfactors` and `ats.canonicalize` keeps it
  (two identities for one posting; 41 job URLs carry the hint, ASSA ABLOY is the only duplicate). Fixed by 7ef11c9 (F4).
* **Workday education and "How did you hear" pickers (about 120 field errors per day on 10-15 to 10-18).**
  Degree "BE" typed into a virtualised list first, school searches with no match, tenants whose source list is only
  "Displaced (US)" / "LOA - Job Search (US)" (Bank of America: 21 runs on 10-13 and 3 on 10-19). Fixed for Degree
  and Field of Study by 92437ac and 8323efb. The "internal applicants only" tenants are still reported as
  navigator blockers when the list shows only "Select One" (Bank of America `GFC Data Governance Analyst`); the
  deterministic close rule needs the third variant. Open (small).
* **Eightfold (54 runs, 12 jobs).** "Need verified personal fields and a resume attachment" (13 runs, 10-14 to 10-17),
  the "Date" acknowledgement combobox whose popup is a date dialog (34 field errors, 0 since 10-17), Create Account
  loops, and Morgan Stanley's `link_off` to Workday (14 runs on 10-19, handoff added in 4e0d04c/c3284f6). Fixed.
* **Dayforce (22 runs, 6 jobs; 10-16 and 10-18).** "Next"/"Sign In" loops on Global Identity: the page is gated by an
  invisible reCAPTCHA (York Space `b5385abb`) or controls the password field itself (Imperial Brady `a7e5ae15`). Open
  (account/captcha, 2 jobs). The "Country dialing code" picker (53 field errors) was the navbar `role=menu` bug.
* **SmartRecruiters (25 + 12 runs).** Wait loops on blank pages: 20 runs between 10-18 20:00 and 20:08 were the
  DataDome wall (`bot_wall.py`, ff25b51, deployed 20:2x). "Ländercode" and "School location" pickers on German
  and international postings (17 field errors). Open (small).
* **Oracle (31 runs).** Work authorization (9 runs, 0 since 10-18), "Name of Latest Employer" (eba7634),
  "Start Date Month/Year" rows created by the resume import (64 `unclassified` field errors, 19 since 10-18),
  hCaptcha (4.6). Partly open.
* **Free-text blockers (N2, 426 runs).** The navigator model's own words: resume upload "Value not accepted:
  Search" (81 runs, 45 jobs: the fill targeted the country-code search box instead of the file input; Rippling 12,
  iCIMS 14, SuccessFactors 10, Workday 10), a click that changes nothing (74), geography pickers (47), "how did you
  hear" (37), dates (22), education (19). 5 runs since 10-19 06:00; the continuous owner that wrote most of them
  was retired on 10-14 (`N3`, 95 runs, 0 since 10-14).
* **Greenhouse single cases:** Braze (3 jobs, 10-18 19:49 to 19:58: the MyGreenhouse autofill sends an emailed code
  before any field; fixed by aaae72a on 10-19, not re-run), Qualtrics `94a23fce` (a stepped form with Continue and
  Previous; "Final submit control was not unique" with no candidates; 4 runs).

Tables 3.2 give the per-sub-cause counts and example runs.

### 4.4 E1 Shared email sender busy (174 runs, 84 jobs) and E2 parked on an email (266 runs, 143 jobs)

* **E1** (Oracle 112, iCIMS 30, Eightfold 24): "Another application kept the same email sender busy; nothing was
  sent". Last seen 10-18 21:44 (iCIMS, serialised on purpose). Oracle's per-tenant code attribution and leases
  (10-18, `mail.oracle_attributed`) removed it. Fixed.
* **E2** is not a failure: the run parked on a durable email wait and a *continuation run* finishes the application
  (Workday 94, Oracle 67, Eightfold 51, iCIMS 39). It was counted as a failed run because its state was
  `waiting_browser`; since ab4bf0d it is `waiting_email`. 20 jobs ended here: iCIMS 5 (the Central identity issue, fixed 10-19/20), Eightfold PayPal
  (8 runs, no delivery), Citco, Braze, Workday HPE. See flow F9.

### 4.5 A1 Account, sign-in and sign-up (253 runs, 141 jobs)

Workday 112, iCIMS 76, custom 25, SuccessFactors 24.
* Workday "did not reach an application form within the account setup window" (49 runs, `workday.py:1240`, 10-09
  to 10-18) and "account step did not respond" (27 runs): the account loop saw an unrecognised page
  (`unexplained_state`, 125 events: `loading`, empty, or cookie banners such as `legalNotice`). Fixed in part by
  04a2a62 (sign-in with email), d48faab (locale) and 3abbde0 (password limits); 4 runs since 10-18.
* "Account origin does not match this employer" (23 runs; 8 on 10-10, 14 on 10-17, 1 on 10-19): SSO landing on a
  different tenant host; fixed with the maintenance and handoff changes (a59937f, c3284f6).
* iCIMS Central identity (62 runs, 31 jobs): covered by the iCIMS work of 10-19/20 listed in the brief (including the
  WebAuthn interstitials and "could not sign in and no password reset was offered", 9 runs on 10-19). Not repeated
  here.
* ADP (10 runs, 3 jobs): "Person Not Found (4040)" then "email already has an account": an identity decision
  (ADP canonicalises the Gmail alias onto an existing ADP identity), not engine. Needs the owner (a non-Gmail
  address or the ADP credentials).
* custom sites (Apple, Vigil Labs, Farmers, TikTok): rejected credentials or password fields that do not take the
  value. No adapter; low value.

### 4.6 C1 CAPTCHA and bot checks (256 runs, 122 jobs)

* **Lever (66 runs, 21 jobs, 0 confirmed ever).** hCaptcha Enterprise accepts the puzzle and still returns HTTP 400
  "There was an error verifying your application" (REA, FINDINGS 10-18). Lever is excluded from dispatch.
  *Open decision:* a CDP-leak-free driver or leave Lever out.
* **iCIMS (78 runs).** hCaptcha behind Central; the twin Chrome (9225) mints tokens (brief). 15 runs on 10-18 to
  10-20.
* **Oracle hCaptcha (33 runs, 9 jobs; 19 on 10-14, 3 on 10-19).** Multi-round image puzzles: "puzzle not solved in this
  browser" (JPMorgan Chase 2 jobs, Honeywell). Honeywell "Advanced Data Scientist" has 17 runs. Open.
* **Greenhouse (23 runs, 21 jobs; 10-10 to 10-12 only).** reCAPTCHA Enterprise score refusals ("security check
  refused"); not seen since 10-12.
* **SmartRecruiters (9 runs)**: DataDome slider (`bot_wall.py`), backed off 45 minutes since 10-18.

### 4.7 S2 Employer rejection or existing application (25 runs, 19 jobs)

Ashby 9: "We couldn't submit your application" with the employer's own limits: Benchling "maximum of 3 active
applications" (3 jobs, 10-19 13:58 to 14:17) and OpenAI "no more than 5 times in any 180 day span". The
snapshot already recognises this (`forms.py:5022` and `application_pacing.application_cap`, `browser.employer_limit_result`
closes the job and records the cap so that the employer's other jobs wait). The data predates it. Fixed.

### 4.8 P1, P2, P3 Postings, X1 crashes, O2 owner decisions

* **P1 closed or internal-only (75 runs, 73 jobs):** Workday 44; "internal applicants only" is detected from the
  source list. Correct early exits.
* **P2 posting page missing, retried (66 runs, 42 jobs, last 10-18):** KLA and Texas Capital Bank each ran 14 times
  (zero model calls) before two distinct runs closed them; `workday.missing_posting_result` now closes after two
  runs or one run when the tenant search no longer lists the requisition (`tenant_search_lists`). Fixed.
* **P3 Workday maintenance (9 runs):** deferred an hour at a time; Fiserv "session/server error persisted" 10-19 is
  Workday-side.
* **X1 worker crash (18 runs, 14 jobs, last 10-16 15:29):** `TypeError` x5 (Allstate, Workday, 10-12/13),
  `AttributeError`, `KeyError` (SuccessFactors account recovery, 10-16, `browser.py:3279`, `accounts.py:257`),
  `NameError` (Rippling, 10-11), `MailPending` (Oracle, 10-10). None since 10-16. Since 10-13 each stores a `failure_trace`.
* **O2 owner pause/delete (89 runs, 65 jobs)** and the **maintenance cancel** (flow F1).

### 4.9 Q1 and Q2 Questions the engine could not answer (275 + 156 runs; reported, not fixed here)

The orchestrator owns answer validation and retrieval. 188 jobs had at least one `waiting_answer` run, 51 jobs ended
there and 4 more in Q2. By tenant the open questions are listed in `docs/rea/findings/REA_DIAGNOSIS_20261018.md`
section 8 and in FINDINGS 10-18 (West Monroe transcript and GPA x6, references, nicotine, entity checklists, Part 810,
sponsorship wording). Counts of validator causes over all 2,585 `answer_validation` events: `unoffered_choice` 460
(+ 230 for the location topic), `unsupported_evidence` 469, `disclosure_scope` about 960 (prior employment 357,
authorization 132, government 113, restriction 104, export control 67, consent 46, citizenship 34), `question_polarity`
69 (sponsorship), `field_limit` 25. Since 10-18 12:00: `unoffered_choice` 71 (+ 53 location), `disclosure_scope` about
110 (citizenship 30, authorization 25, government 24, consent 24). `knowledge_inference_declined`: 292 events
(`low_confidence`, `unoffered_option`). The top live widget side of this, Greenhouse "Location (City)" (28
`unsupported_choice` before 10-18, 25 deadline errors since), is a typeahead that has no options until text is
typed; the validator rejects the proposed city because the 10 options shown are not those typed. Orchestrator.

## 5. Obviously wrong pipeline flows

| # | Flow | Evidence | Status |
|---|---|---|---|
| F1 | **The maintenance cancel rewrites history and pauses jobs forever.** `operations/cancel.py:41-60` sets *every* historic draft run of a job to `cancelled`, overwrites `run.reason` ("Owner cancelled saved application during maintenance") and pauses the job (`owner_cancelled_drafts`). | 514 runs, 148 jobs (10-07 to 10-10), $17.75 and 17 hours of work already spent in those runs. The real reasons survive only in `finished` events. 99 jobs are still paused by it (52 are confirmed since, 41 `cancelled` and never retried, 5 unknown, 1 already applied). | Fixed (2535a85): the run's own state and reason are kept in the cancel sentence. The 41 jobs still await a fresh try (section 7). |
| F2 | **Refused retries logged as owner actions.** The autopilot's retry loop wrote "Owner requested resume" for every attempt, accepted or not. | 9,374 such events; 3,230 in one hour on 10-17; 5,702 on 10-17/18. | Fixed (legacy 8ee5d73, `job_control.py:94-108`; in the first AXIOM import). |
| F3 | **Retrying a deterministic failure.** Runs 3+ in a row with the same reason. | 455 runs, 16 hours, 3,218 model calls: form stalls (147 runs, 43 jobs), email waits (45), sender busy (38), waiting for an answer (32), captcha (30), answer rejected (27), account (25), agent gave up (17). | Mostly bounded now by `application_budget` (three starts, 480 s optional; legacy, in the first AXIOM import; 4d759d7 stops infrastructure stops from spending a start). The still-open ones are the unattended `resubmit_unknown` and one-click Apply fresh tries on a known identity conflict (ADP 15 runs). |
| F4 | **One requisition, two job rows.** `ats.canonicalize` keeps the hint `?ats=successfactors`. | ASSA ABLOY: 81 runs, 2 rows, 0 confirmed. 41 job URLs carry the hint. | Fixed (7ef11c9): `ats.canonicalize` drops the platform hint (only a known platform name; any other `ats` value stays). `ats.identity_variants` also looks up the older identity, so rows and `submission_guard:` settings written before still protect and are not duplicated. |
| F5 | **Post-commit exceptions lose their cause.** `browser.py:4326` returned one fixed sentence for any `ValueError`/`PlaywrightError` after the commit. | 8 unknown Greenhouse runs on 10-18/19 have no diagnostics. | Fixed (47adac4). |
| F6 | **Wrong origin check for embedded Greenhouse.** See 4.1. | 13 of 21 embedded jobs never succeeded. | Fixed (faa1ffa). |
| F7 | **Duplicate submissions.** `unknown_resubmit` ("resubmit them till you get a confirmation", owner 10-12) plus retries of runs that had submitted. | 11 jobs with repeated confirmed runs 10-10 to 10-14: Assured Guaranty x3, Perseus Group x3, Asana x2, LSEG x2, Dexcom x2, Cboe x2; Uber, Boston Scientific, Auto-Owners and ibgllc started a new run after a confirmed one. | `Service.queue` now refuses any job with a confirmed or already-applied run (`service.py:369`); the narrowed resubmit only runs when the saved final page shows no confirmation. No second confirmation after 10-13 02:08; two later runs started after a confirmation (Boston Scientific 10-14, ibgllc 10-16) and ended without a submission. |
| F8 | **The repair agent saves placeholders as answers.** `[email]` x4 (Oracle 10-17, Stripe, Sparksoft and iPipeline 10-18/20) and the masked phone `9999999999` (Stripe 10-18). | 141 `fix_applied` outcomes among 337 repair events; accepted overrides are traced to the masked evidence, which is also what was saved. 34 repair-triggered retries, 9 later confirmed. | `[email]` rejected 10-19; masked numbers refused as an override (63e07ed). Fixed. |
| F9 | **A parked wait is reported as a failed run.** The state `waiting_browser` means both "needs repair" and "parked on a durable email wait". | 266 E2 runs (143 jobs) counted as failures; Telegram once messaged each. | Fixed (ab4bf0d): a result with `waiting_kind=email` finishes as `waiting_email`. It files no failure report, asks no repair, sends no Telegram outcome and is neither "blocked" nor "failed" in the batch summary, which counts it separately; the web app labels it "Waiting for email". Lease waits (sender busy) stay `waiting_browser`. Rows written before this change keep the old state, and every consumer still accepts both. |
| F10 | **Deploys interrupt running applications.** | 139 runs; 84 in 48 hours; 35 runs in 23 minutes on 10-18 19:22. | Fixed (08a3029): every stop drains first (no new commit, wait for commits under way), and `INTERRUPTED` runs cost no start (4d759d7). |
| F11 | **No load shedding.** See 4.2. | 223 deadline runs, 37.9 hours. | Fixed (1590276): `host_health` loop-lag gate in `provider_health.dispatch_limit`, and a deadline timeout under overload is a free retry. 4d759d7 counts a dead Chrome endpoint and an overloaded host as nothing sent. |
| F12 | **Telegram delivery is retried with no upper bound.** `outbox.py:178` backs off to 300 s and never gives up. | 2,341 `notification_error` events; one run retried 192 times in 2 h 13 (10-13), 1,976 on that day. | Fixed (4299d18): an outbox delivery that keeps failing is parked as `dead` instead of retried for ever. |
| F13 | **No dispatch circuit breaker for the worker Chrome.** | I1: about 250 runs into a dead browser on 10-11/12. | Fixed (1590276): three runs in a row that could not open a page pause starts for 1, 2, 4 ... 15 minutes, then one probe run (`host_health.note_browser`, read by `dispatch_limit`). Covered by `tests/test_host_health.py`; nothing more was needed. |
| F14 | **An unknown-outcome job is only ever re-examined if its saved final page shows no confirmation, and 32 of 42 were never triaged.** | 4.1 | Orchestrator action. Done 2026-10-20: the 12 hidden successes were reconciled; the other 47 `submission_unknown` jobs were triaged read-only and stay unknown for `resubmit_unknown`'s receipt check (docs/OVERHAUL.md). |

## 6. Fixes made on this branch

Each has a regression test. The earlier ones are merged to `main`; ids of items fixed outside this branch are in the
section 5 table.

| Item | Fix | Commit | Test |
|---|---|---|---|
| F-A, F6 | Embedded Greenhouse forms take their emailed code in the board iframe | faa1ffa | `test_embedded_greenhouse_code.py` |
| F-B, F5 | A failure after the commit keeps its exception and code locations | 47adac4 | `test_exception_diagnostics.py` |
| F-C, F8 | The repair agent refuses a masked phone number as an override | 63e07ed | `test_repair_masked_values.py` |
| I1, I4, F11, F13 | Load shedding and the worker-Chrome circuit in `dispatch_limit`; nothing-sent accounting | 1590276, 4d759d7 | `test_host_health.py`, `test_application_budget.py`, `test_runner_tiers.py` |
| F10 | Drain before every stop | 08a3029 | `test_drain.py` |
| F12 | Dead-letter for a failing outbox delivery | 4299d18 | `test_outbox_generation.py` |
| F4 | `ats.canonicalize` ignores the feed's platform hint; older identities still found | 7ef11c9 | `test_platform_hint_identity.py` |
| F9 | `waiting_email` run state for a parked email wait | ab4bf0d | `test_waiting_email_state.py` |
| F1 | Maintenance cancel keeps each run's state and reason | 2535a85 | `test_cancel_saved_drafts.py` |

Not changed in stored data: runs written before ab4bf0d keep `waiting_browser` with `waiting_kind=email`, and every
consumer still accepts both states. Jobs stored with the hinted identity keep it; their duplicates are found through
`identity_variants` rather than migrated.

## 7. What remains for the orchestrator

1. **Recover hidden successes.** Reconcile the 12 jobs in section 4.1 (`service.reconcile(run_id, True, evidence)`),
   then triage the other 32 jobs that ended `submission_unknown` with the current receipt rules and the saved
   `final.png`. Until then they are counted as failures and shielded from retry.
2. **Fresh tries for jobs whose cause is fixed or environmental** (one-click Apply grants one): the appendix lists them
   under I4 (23 jobs), I5 (8), E1 (7), I1 (4), I2 (3), P3 (2), I3 (1), X1 (1); Braze (3, fixed by aaae72a);
   the 41 jobs the maintenance cancel left `cancelled` and paused (check each posting is still open first); and the
   embedded Greenhouse jobs after the F-A fix (Roblox, Stripe, Waymo, Toast), after the unknown ones are triaged so
   nothing is submitted twice.
3. **Load shedding before the 6+2 cut-over.** Done (1590276, section 6). Deploy only through the hold/drain stage (08a3029).
4. **Owner or product decisions:** Lever (excluded; needs a CDP-leak-free driver), ADP (needs a non-Gmail address or
   the ADP credentials), Dayforce Global Identity (invisible reCAPTCHA: York Space, Imperial Brady), Oracle
   hCaptcha (JPMorgan Chase x2, Honeywell, 13 to 17 runs each), ASSA ABLOY State/Province picklist, the Bank of
   America "Select One" internal-only variant, Qualtrics' stepped Greenhouse form.
5. **Small structural fixes.** All done (section 6): the `ats` hint (7ef11c9), `waiting_email` (ab4bf0d), the outbox
   dead-letter (4299d18) and the browser-outage circuit (1590276). The two still-open duplicate-start paths in F3
   (unattended `resubmit_unknown`, one-click Apply on a known identity conflict) are policy, not code defects.
6. **Answers (orchestrator):** the Q1/Q2 questions in section 4.9 and the REA lists; the location typeahead
   validator rejection; `knowledge_inference_declined` (292 events).

## Appendix: every unsuccessful job by the cluster its last run ended in

Format: `ATS: company (number of runs)`. A star marks a job whose last run was overwritten by the maintenance
cancel (its cluster comes from the original `finished` event).

**P1 Posting closed or internal-only** (73 jobs)

- workday: Aderant (2); Amentum (2); Amentum (2); American International Group (2); Applied Materials (2); Applied Materials (2); Bank of America (1); Bank of America (1); Bank of America (3); BlueCross BlueShield of Tenn (2); CVS Health (2); Capital One (2); Cisco (2); Cisco (2); Danaher (1); Dexcom (2); Early Warning (12); Fidelity Investments (2); GE Appliances (2); GE Vernova (2); Global Payments (2); Humana (4); IDEXX (2); Jabil (2); KLA (1); Lilly (2); Lilly (2); Marigold (2); NVIDIA (1); RTX (5); Raytheon (2); Raytheon (2); SEL (2); Sierra Nevada Corporation (2); Snap (1); State Street (2); The Federal Reserve System (2); The Walt Disney Company (4); Thrivent (1); Thrivent (2); Toyota (1); University of Rochester (2); Visa (2); Walt Disney (8)
- icims: Apex Service Partners (10); H&R Block (10); Oregon Health & Science Univ (4); SAS (2); careers-cotiviti.icims.com (1); careers-cotiviti.icims.com (1); careers-ice.icims.com (6); careers-msci.icims.com (6)
- custom: Lyft (5) *; Microsoft (1); Microsoft (1); Microsoft (6); Microsoft (8); Point (7)
- smartrecruiters: City of San Francisco (1); Eurofins (1); Flywire (1); ServiceNow (6); Woongjin (5)
- greenhouse: Cayuse (1); Together AI (1); Yugabyte (1)
- ashby: Clera (1); Snowflake (3)
- dayforce: NAVEX (1); Security Benefit (1)
- successfactors: Westinghouse Electric Compan (5)
- oracle: JPMorgan Chase (5)
- adp: Veregy (1)

**Q1 Waiting for an applicant answer (missing fact)** (51 jobs)

- workday: Air Liquide (3); Automation Anywhere (1) *; BCBST (5); Evolent (1); Fidelity National Informatio (1); Intel (1); Lowe's (2); Lowe's (3); Maersk (1); Maersk (17); SHI International (3); Teladoc Health (1) *; The Federal Reserve System (3); The Federal Reserve System (4); U-Haul (3); University of Chicago (1); University of Rochester (3) *
- custom: Block (5); C3.ai (6); Roblox (10); Roblox (5); West Monroe (1); West Monroe (2); West Monroe (3); West Monroe (3); West Monroe (3); West Monroe (6)
- ashby: Jerry (1); Luminai (1); NerdWallet (1); Sentry (1); Sentry (1); Solace Health (1); quora (2); suno (1)
- greenhouse: Gas South (1); Kairos Power (1); NIH-NCBI (5); National Information Solutio (3) *; National Information Solutio (5) *
- workable: OPEN Data Jobs (2); Saalex (11); Saalex (7); Scientific Systems Company (4)
- successfactors: ASSA ABLOY (53); Acuity (2)
- icims: Bruno Independent Living Aid (2); Emory University (20)
- smartrecruiters: Crown Innovations (4); Mattel (1)
- lever: Eastern Research Group (4)

**S1 Submission outcome unknown** (42 jobs)

- workday: Adobe (1); Booz Allen (1); Booz Allen (6); CACI (3); Chewy (3); Morgan Stanley (1); Parexel (1); Viridien (9); Visa (9)
- oracle: Caesars Entertainment (16); EXL (1); Ford Motor Company (1); Ford Motor Company (12); Honeywell (2); Howmet Aerospace (2); Howmet Aerospace (2); SAIC (1); SAIC (1)
- greenhouse: Asana (1); Industrial Electric Manufact (4); Pinterest (4); Roblox (1); Roblox (3); Stripe (1); Toast (1); Toast (1); Waymo (6)
- workable: BIP Ventures (7); Becoming (1); TMEIC Corporation Americas (1); The Anywhere (6); United Placement Group (4)
- smartrecruiters: AbbVie (4); Eversana (9); Intuitive Surgical (4); SBT Global (3); Versant Media (2)
- jazzhr: OmegaHires (1); OmegaHires (1)
- custom: IXL Learning (4)
- eightfold: Micron Technology (7)
- ashby: ClickHouse (1)

**O2 Owner paused, deleted or removed under a rule** (38 jobs)

- workday: AIG (1); Abbott (1); Adobe (1); Citi (1); GE Vernova (1); GE Vernova (1); Guidehouse (1); Guidehouse (1); Guidehouse (1); Hewlett Packard Enterprise (1); LexisNexis Risk Solutions (1); LexisNexis Risk Solutions (1); Lowe's (1); Lowe's (1); Lowe's (1); Lowe's (1); Lowe's (1); Lowe's (2); Pfizer (3); RELX (1); Salesforce (1); Salesforce (1); Salesforce (1); State Street (1); State Street (1); The Walt Disney Company (4); Walt Disney (1)
- icims: Atlassian (20); D'Addario (8); MCI Careers (2); MCI Careers (3)
- oracle: American Express (1); Honeywell (3)
- smartrecruiters: Intuitive Surgical (1); Intuitive Surgical (1)
- greenhouse: National Information Solutio (1) *
- custom: TikTok (1)
- eightfold: Qualcomm (14)

**C1 CAPTCHA / bot check refused or unsolved** (33 jobs)

- lever: AHEAD (12); AHEAD (7); AHEAD (8); AIFund (1); Alloy.ai (9); Artera (4); Brillio (5); Datalab USA (8); Laminar (2); Laminar (7); Laminar (8); Lightcast (2); Lightcast (6); Palantir (6); Perr&Knight (4); Shield AI (1); Shield AI (7); zoox (1)
- icims: AmTrust Financial (3) *; By Light (25); By Light (7); Hamilton Company (11) *; Harbor Foods (17) *; Ring Power (1) *; RoviSys (5) *
- oracle: Honeywell (1); Honeywell (17); JP Morgan Chase (4); JPMorgan Chase (9)
- smartrecruiters: ServiceNow (4); Wellmark (3)
- custom: ZS (5)
- dayforce: York Space Systems (4)

**A1 Account, sign-in or sign-up did not complete** (27 jobs)

- custom: America's Tire (1); Apple (5); Farmers Insurance (1); Garmin (4) *; Goldman Sachs (3) *; L3Harris Technologies (3) *; TikTok (4); Universal Health Services (3) *; Vigil Labs (10)
- workday: Abbott (1) *; AssetMark (3) *; AssetMark (4) *; F5 (1) *; Hewlett Packard Enterprise (2) *; Highmark Health (2) *; LexisNexis (2); PNC Financial Services (2) *
- icims: Atlassian (14); Atlassian (8); Interstate Waste Services (8); Southern Star Central Gas Pi (4) *; Stifel (1) *
- adp: Applications International C (6) *; Avalon (5); ZincFive (10)
- successfactors: AMETEK (10)
- dayforce: Imperial Brady (9)

**N1 Form stalled: verification problem the controller could not fix** (27 jobs)

- successfactors: ASSA ABLOY (28); Bayer (3); Capgemini (2); Corning (11) *; Westinghouse Electric Compan (8)
- workday: Broadridge (2); Corteva (1); David Yurman (2); Expedia Group (2); Jefferson Health (8)
- greenhouse: Braze (3); Braze (3); Coherent Solutions (1); Qualtrics (4)
- smartrecruiters: LinkedIn (3); Mirantis (3); Prosidian Consulting (3); Sopra Steria I2S (7)
- oracle: Emerson Electric (8) *; Goldman Sachs (6); SAIC (4)
- dayforce: Sarnova (13) *; Security Benefit (8)
- rippling: Veryable (16)
- custom: TikTok (1) *
- eightfold: Morgan Stanley (4)
- icims: career-schwab.icims.com (12)

**N2 Agent gave up with a free-text blocker** (24 jobs)

- smartrecruiters: AbbVie (3); Arista Networks (3); Boyd Gaming (3); Circle Logistics (17); Intuitive Surgical (3); ServiceNow (3); Sopra Steria I2S (3); Sopra Steria I2S (4); Wellmark (3)
- custom: Amazon (1) *; Microsoft (1) *; Microsoft (4) *; Microsoft (8); Rivian (2) *; Roku (6)
- workday: Bank of America (21); Bank of America (3); Snap (7); Toyota (3)
- icims: FRONTIER TECHNOLOGY (4) *
- rippling: Anthony & Sylvan Pools (18)
- oracle: Texas Instruments (2)
- greenhouse: Lyft (3)
- ashby: Anori (1)

**I4 Deadline / timeout (run or operation)** (23 jobs)

- greenhouse: 1-800 Contacts (7); Lucid Motors (3); Lyft (3); Lyft (3); Lyft (4); Murj (5); Perpay - Career's Page (1); Pinterest (1); Pinterest (3); Roblox (3); Roblox (5); Stripe (5)
- custom: Kenway Consulting (11); Microsoft (2); Publicis Groupe (12); TikTok (9)
- workday: Booz Allen (1); Intel (1); Northwestern Mutual (2); Omnicom Group (1)
- smartrecruiters: Sopra Steria I2S (5); Wellmark (1)
- dayforce: Forté (4)

**E2 Parked waiting for an emailed code or link** (20 jobs)

- icims: Peraton (5) *; PowerSchool Group (22); career-schwab.icims.com (3); career-schwab.icims.com (4); careers-hamiltoncompany.icim (10)
- custom: Abnormal (2); Amazon (5) *; Amazon (5) *; ByteDance (5) *; Medpace (4) *
- workday: Bank of Montreal (1) *; Hewlett Packard Enterprise (2); Hewlett Packard Enterprise (2); Hewlett Packard Enterprise (4); Micron Technology (1) *
- oracle: Citco (2); Southern Company (7) *
- eightfold: Lumen (7); PayPal (8)
- greenhouse: Braze (3)

**I5 Generic "Browser control failed at file:line"** (8 jobs)

- greenhouse: Reformation (9); Roblox (1); Sparksoft Corporation (4); Toast (9)
- icims: iPipeline (12)
- rippling: Veryable (5)
- smartrecruiters: WesternDigital (15)
- oracle: Ford Motor Company (1)

**P2 Posting page missing, retried** (8 jobs)

- workday: Barry-Wehmiller (1) *; ICF (2) *; KLA (14); Leidos (1) *; Oklahoma State Government (3) *; Premera (4) *; State Street (1); Texas Capital Bank (14)

**E1 Shared email sender busy (nothing sent)** (7 jobs)

- icims: By Light (3); General Dynamics (12); General Dynamics Mission Sys (3)
- oracle: Emerson Electric (6); Ford Motor Company (2)
- workday: Highmark Health (1) *; Micron Technology (3) *

**S2 Employer rejection or existing application** (5 jobs)

- ashby: Aurora Innovation (2); Benchling (2); Benchling (2); Benchling (2); openai (4)

**I1 Chrome automation connection unavailable** (4 jobs)

- custom: Arcfield (5); Stripe (5)
- icims: Liberty Mutual (19)
- workday: Booz Allen (1)

**Q2 Answer evidence rejected / value not accepted** (4 jobs)

- greenhouse: Affirm (2) *
- ashby: MatX (1)
- smartrecruiters: M3 USA (2)
- oracle: Citizens Financial Group (1)

**I2 Deploy / restart interrupted the run** (3 jobs)

- lever: PointClickCare (4); Shield AI (1)
- oracle: Oracle (11)

**dry_run Dry run (not a failure)** (3 jobs)

- greenhouse: National Information Solutio (2); Upstart (4)
- ashby: replit (2)

**P3 ATS maintenance / outage** (2 jobs)

- workday: Fiserv (4); ING (3)

**X1 Worker crash (unhandled exception)** (1 jobs)

- custom: Microsoft (5)

**I3 Model provider unavailable or budget spent** (1 jobs)

- workday: University of Central Florid (3) *

**N3 Agent gave up (continuous owner / controller)** (1 jobs)

- workday: Pfizer (4)

