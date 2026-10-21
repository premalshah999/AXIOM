# ATS timing: where an application's time goes

Measured 2026-10-20 from the production database, read-only (`codex-handoff/bin/q.sh`). Durations are
`runs.elapsed` in seconds, for runs in state `confirmed`. Nothing in this file is applicant data.

Two windows are used:

- **14 days** (2026-10-06 to 2026-10-20): the whole recent history. It mixes code versions, so Workday's figure still
  includes the days before the October 17-18 speed work.
- **Since 2026-10-18**: the current code. The settle events (`settle_timing`) exist only from 2026-10-17 15:25 UTC,
  so every settle figure uses this window.

## Duration of confirmed runs

| ATS | n (14 d) | median | p90 | n (since 10-18) | median | p90 |
|---|---:|---:|---:|---:|---:|---:|
| Workday | 662 | 334 s | 700 s | 194 | 257 s | 713 s |
| Greenhouse | 174 | 127 s | 317 s | 71 | 116 s | 321 s |
| Ashby | 127 | 40 s | 168 s | 71 | 35 s | 176 s |
| Oracle | 85 | 142 s | 420 s | 31 | 120 s | 270 s |
| SmartRecruiters | 59 | 101 s | 409 s | 11 | 55 s | 301 s |
| SuccessFactors | 37 | 106 s | 211 s | n/a | | |
| Eightfold | 21 | 211 s | 340 s | 7 | 226 s | 307 s |
| JazzHR | 11 | 81 s | 141 s | n/a | | |
| Dayforce | 9 | 241 s | 359 s | n/a | | |
| iCIMS | 7 | 71 s | 142 s | 7 | 71 s | 142 s |

Fewer than five confirmed runs in the window (Taleo, Breezy, Jobvite, Workable) are left out.

Workday's median is 4.3 minutes since October 18, above the 2-3 minutes seen in the isolated runs after the speed work,
and its p90 is 12 minutes: the tail is long, so most of the remaining Workday time is in a minority of slow runs.

## Where the time goes

Average seconds per confirmed run, since 2026-10-18. These are floors, not totals: the events are written only
when a wait lasts 1 s or more (`settle_timing`) or a field write lasts 3 s or more (`field_timing`), and model time is
the sum of the model calls' own durations (some run concurrently).

| ATS | settle wait | of which hit its deadline | slow field writes | model calls | run median |
|---|---:|---:|---:|---:|---:|
| Workday | 44 s (10.4 waits) | 24 s | 61 s (8.0 writes) | 17 s (8.7 calls) | 257 s |
| Greenhouse | 2 s (0.9) | 1 s | 30 s (4.1) | 9 s (4.3) | 116 s |
| Ashby | 4 s (1.5) | 1 s | 8 s (1.0) | 9 s (3.5) | 35 s |
| Oracle | 29 s (6.5) | 17 s | 11 s (2.0) | 8 s (4.6) | 120 s |
| SmartRecruiters | 10 s (2.7) | 4 s | 20 s (2.1) | 12 s (3.9) | 55 s |
| Eightfold | 45 s (8.4) | 19 s | 35 s (5.3) | 9 s (5.4) | 226 s |
| iCIMS | 16 s (7.0) | 3 s | 2 s (0.4) | 12 s (10.6) | 71 s |

Over the 14 days the same breakdown for the ATSs with no run since October 18: SuccessFactors 3 s of settle, 12 s of slow
field writes, 20 model calls (10.7 s); Dayforce 10 s, 11 s, 18 calls (9.8 s). Oracle also waits on the mail
(18 s per run on average between "waiting for a matching email" and the match), which is outside this work.

What the table says:

1. **Field writes are the largest measured cost on every ATS except iCIMS.** Greenhouse is almost entirely comboboxes:
   429 slow writes in 14 days (3,300 s), the school, city, country, month and degree pickers, many ending on the
   field's 12 s write budget.
   A write is a chain of small renderer calls (evaluate, click, attribute read); on the shared worker Chrome each one
   waits for a busy renderer, so the *count* of calls sets the time.
2. **Settle waits are the second cost where the page is heavy** (Workday, Oracle, Eightfold: 29-45 s per run).
   More than half of that is waits that ran to their deadline (settled = false), not waits that ended early.
3. **Model time is 8-17 s per run and mostly already batched.** `AnswerBatch` is one call per page (or four
   questions); `NavigationDecision`, `ControlDecision` and the audit are sequential by nature.
4. **Queue time** (`phase_timing.queue_s`) is not application time and is excluded from `elapsed`.

### Settle waits that ended on their deadline (2026-10-17 15:25 UTC onward, all runs)

| ATS | waits >= 1 s | ended settled | ended on the deadline | deadline waits with requests in flight |
|---|---:|---:|---:|---:|
| Workday | 3,221 | 1,934 | 1,287 | 915 |
| Oracle | 412 | 303 | 109 | 50 |
| Eightfold | 129 | 94 | 35 | 18 |
| iCIMS | 617 | 528 | 89 | 26 |
| Greenhouse | 282 | 147 | 135 | 10 |
| Ashby | 162 | 118 | 44 | 0 |

(The event did not say what held a wait, so the last column is read from the network state at the deadline. The event
now says; see "Instrumentation".)

## What was changed

Each change is its own commit on `agent/perf` with a regression test that fails on the code before it (verified by
running the new tests against `HEAD` before the change).

| Commit | Change | Measured effect (quiet local Chromium, protocol round trips) |
|---|---|---|
| `3318fe8` | `Locator.evaluate` is one call (evalOnSelector) instead of waitForSelector + evaluate + dispose; falls back to Playwright's own path for a late, missing or ambiguous element | Workday dropdown write 42 -> 26; prompt (value present) 73 -> 45; prompt (value missing) 220 -> 134; Greenhouse school combobox 88 -> 48 |
| `1a9ed10` | Settle waits inside the page (one call per frame) instead of one call per 0.1 s poll; the network check stays in Python; `settle_timing` says what held a deadline wait | 1.2 s render burst 14 -> 1 calls; deadline wait on an animated page 7 -> 1; quiet page 4 -> 1 |
| `50e10a1` | `dismiss_pickers` is one call when no list stays open (was 3 on every picker write) | 3 -> 1 per picker write |
| `74d0f28` | `fill_current` skips scans of a page it just scanned | page with nothing to write: 5 -> 3 scans (7 -> 3 with terms dialogs on) |
| `ee7e201` | Test support only | |

Not done, and why:

- **Serial model calls.** `AnswerBatch` already batches (one call per page, parts of four run two at a time),
  `JevChoice` is one batched call per resolve, and the quality audit is one call per unchanged context. The remaining
  serial calls are the navigator, the control agent and `choose` callbacks, each of which depends on the previous
  page state. A batch would trade correctness for speed, so no change was made.
- **Fixed sleeps after clicks in the widget drivers** (about 50 `asyncio.sleep` calls in `widgets.py`, `workday.py`,
  `adapters/`). Most guard a specific renderer race and need their own signal; replacing them blind risks the
  read-back guarantees. The settle events now record `held_by`, so the next pass can target the waits that matter.

## Expected savings per confirmed run

These are estimates, not measurements: the round-trip counts were measured locally, the seconds are the production
slow-field and settle averages above scaled by the measured call reductions. Field writes are bound by the number of
renderer round trips, so a 35-45% cut in calls is taken as 25-40% of the slow-write seconds (the part that waits for
the site is unchanged). Settle waits that end on the deadline (the site holds them) are not shortened; the saving is
the renderer load of the polling. Scan savings are two scans per page that needs no writes, about 0.1-1 s each.

| ATS | Median now | Field writes | Settle polling | Scans | Expected saving | Expected median |
|---|---:|---:|---:|---:|---:|---:|
| Workday | 257 s | 15-25 s | 2-4 s | 2-4 s | 19-33 s | about 225-240 s |
| Greenhouse | 116 s | 8-12 s | 0 | 1-2 s | 9-14 s | about 102-107 s |
| Ashby | 35 s | 2-3 s | 0 | 1 s | 3-4 s | about 31-32 s |
| Oracle | 120 s | 3-4 s | 3-6 s | 1-2 s | 7-12 s | about 108-113 s |
| SmartRecruiters | 55 s | 5-8 s | 1 s | 1 s | 7-10 s | about 45-48 s |
| Eightfold | 226 s | 9-14 s | 4-8 s | 1-2 s | 14-24 s | about 202-212 s |
| iCIMS | 71 s | 0-1 s | 2-4 s | 1 s | 3-6 s | about 65-68 s |
| SuccessFactors, Dayforce, JazzHR | no recent sample | similar share (10-14%) of field time | | | not estimated | |

The saving is about 8-12% on Workday, Greenhouse, SmartRecruiters and Eightfold. It does not reproduce the Workday
6-7 -> 2-3 minute result: that came from removing whole steps (stale writes, model calls); what is left here are the
costs of the driver's own protocol chatter. The larger remaining costs are the deadline waits (Workday 24 s, Eightfold
19 s, Oracle 17 s per run) and the 12 s field-write budget that Greenhouse comboboxes hit
(`Location (City)`, `Degree`, `School`, `Country`: 25, 22, 17 and 14 deadline failures since 10-18). Verify with the
`held_by` field after the next production day, then target those.

## Instrumentation added

`settle_timing` now carries `held_by` (`network`, `page` or `navigation`) and, when requests were in flight at the
deadline, `requests`: up to three `METHOD host path` strings with token-like path segments redacted. After the
next production day, `select data->>'held_by', count(*) from events where kind='settle_timing' and
data->>'settled'='false' group by 1` says which part of the deadline waits is the site and which is the page.

## Round 2: Greenhouse comboboxes (2026-10-21)

Deadline failures since 10-18: Location (City) 25, Degree 22, School 17-20, Country 14. Method: production
`field_error` / `field_timing` / `verification` events (read-only), the REA map, and a synthetic react-select page
(`tests/support_react_select.py`) with the observed ids, a debounced remote search, a "Loading..." notice and a
"No options" notice. The synthetic page reproduced two slow paths on a quiet machine:

| Path | Cause | Before | After |
|---|---|---|---|
| Location typed as "City, ST" | `wait_options` waited the whole 4 s for a row containing the query text; the list had settled at once and the applicant's address already picks from it (`location_resolver`) | 5.2 s | about 2 s |
| School not offered | The menu says "No options"; that is no option row, so the 4 s wait, each alias (2 s) and the "Other" search (2 s) ran in full | 7.0 s, then fail | under 5 s, then fail (same error) |

Not reproduced: Degree and Country on a quiet page take 0.5 s and commit. Their 5-14 s in production is renderer load
times the call count (32 round trips for a plain pick), so they gain only from the call cut (menu id looked up
once instead of every poll). Where production evidence stops: `settle_timing.held_by` has not produced data yet (the
field ships with the next deploy), and the saved step pages are not readable from here, so the real menu text for an
unlisted School and the real debounce are assumed from the REA map ("Loading..." / "No options" are react-select's
defaults). The no-results rule needs the notice to persist 1.2 s and never fires while "Loading..." is shown.
