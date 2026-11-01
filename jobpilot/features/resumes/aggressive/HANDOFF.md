# Aggressive tailoring handoff

Worktree: `axiom-codex/aggressive`, branch `codex/aggressive-tailoring`.
The registry and runner from `agent13/tailor3` through `109dc30` are merged here.
Its registry signature is unchanged on `85aaafb`. No production app, production
data, checkout `.env`, main branch, push, or deployment was used.

## Implemented

`aggressive.run(job, source, profile, ctx)` registers through the existing mode
registry. It runs the dream-candidate brief, three distinct project proposals,
source-backed skills inventory, deterministic checks and one bounded repair,
sanitization, compilation twice per attempt, the shared page-fill loop, ATS
extraction, a fresh blind recruiter call, and a bounded correction/recheck pass.
The critic receives exactly the raw posting and extracted PDF text. Its full
structured response is saved, including the verdict-changing fixes and gaps.

The later corrections in the reference `CLAUDE.md` take precedence over the old
`agent.py` prompts: no fake precision, forced course tags, hard-coded candidate
facts, keyword stuffing, or embedding service. Every string is rendered through
AXIOM's escaping and existing template, with its existing font and TeX packages.

The owner's shared `check_rules`, `claims`, evidence inventory, sanitizer,
compiler, page-fill code, typography reader and ATS parser are reused. No second
implementation of the owner's resume rules was added. Experience and Education
are immutable; rejected project edits retain the source; every held skill stays.
Original bullet counts and character windows win over the reference's content
expansion. Overflow is reported without deleting content or reducing type sizes.

Generated projects are stored in the additive `rs_aggressive_workspaces` table
and included in the preview PDF. Originals remain until explicitly deselected.
The original-projects option needs no confirmed drafts and still runs the other
pipeline steps. Approval covers the exact stored project revision; it does not
require claiming the project has been built. Draft tools do not silently become
held skills. Confirmed project entries become sources through `added_projects`.
No project approval itself approves an application or triggers a paid model call.

`frontend/src/aggressive/Drafts.tsx` implements project selection, reverting to
originals, individual project approval, the preview, the brief and the complete
critique using existing design tokens and controls. Its API is provided by
`aggressive.router.router(service)`.

## Required changes in Claude Code's owned files

**The main app is not fully integrated yet.** The following are deliberately not
patched here because the task assigns those files and flows to Claude Code.

1. **Allow an unconfirmed preview to reach review.** `_build_mode` currently
   rejects any unconfirmed project in the document/PDF, before the review screen
   can show it. This is reproducibly blocked with `error.code == "rules"`.
   Aggressive returns the requested preview, `drafts`, confirmed `added_projects`,
   and separate `report.preview_rules` / `report.send_rules`. Keep applying the
   shared rules: for preview only, allow the pending project entries as sources;
   for approval/send, require the real confirmed entries and pending-draft check.
   Retain pending previews for review even when automatic approval is enabled.
   Never mark those previews approved or relax the send gate. Recompute checks
   in the shared runner rather than trusting a mode's report alone.

2. **Mount the owned router and screen.** Include `aggressive.router.router(service)`
   before the app's GET fallback. Embed `AggressiveDrafts` with
   `report.workspace_id`. The failed preview's saved workspace is also available
   from the failed artifact's validation report. On project changes invalidate
   the current review/approval immediately, and rebuild with `fresh=True` when
   the applicant requests it. The existing cache key does not include the
   workspace revision, so an ordinary cached build is insufficient. Avoid a paid
   rebuild on each individual checkbox or project approval.

3. **Wire the revision guard.** Call
   `aggressive.guards.send_problem(session, artifact)` from the shared send guard
   and before accepting approval. It rejects missing/stale workspaces and
   unapproved selected projects. It covers Aggressive artifacts edited into
   `tailor-review-v1`, too. This prevents an older approved file from being sent
   after project choices change in another tab. The hook is implemented and
   tested here, but the shared guard does not call it yet.

4. **Forward explicit project removals.** Aggressive records the applicant's
   deselections in `report.removed_projects`. Pass them to `check_rules` in
   `_build_mode` (or add a defaulted `ModeResult.removed_projects` field). The
   owned preview route already does this. Currently the shared runner correctly
   accepts confirmed additions, but refuses an explicitly removed source project
   because that argument is not forwarded.

An existing parser limitation also surfaced: `parse_check` finds every bullet
using its first 32 characters from the beginning of the PDF text. Repeated source
bullet prefixes can therefore falsely fail the reading-order check. Match
successive occurrences from the previous position; do not change source facts
to work around this. This shared function was not edited here.

## Verification and limits

- The Aggressive tests cover compiled preview content, PDF type sizes and italic
  dates, overflow without cuts, metric/tool/length refusals, source retention,
  one bounded repair, isolated critique input, critique fixes and coverage
  regression, a non-software teaching profile, exact project approval, stale
  revisions, original-only fallback, authenticated draft endpoints, and the real
  shared pipeline with confirmed projects.
- Existing Normal and shared-rules regressions passed in the targeted matrix:
  **79 passed** before the final additional teaching case; the final Aggressive
  group finished with **17 passed in 28.08 seconds** on the final code.
- Bundled TeX was tested using the existing non-production
  `AXIOM.replaced.app/Contents/Resources/tex` binary with only its own binary
  directory plus `/usr/bin:/bin` on PATH. Its fonts, typography and ATS extraction
  passed. No new desktop bundle or clean-Mac installation was built.
- The browser test explicitly mounts the owned component/router on an empty
  test app. Servers start with `env -i`, an empty CWD, their own DATA_DIR and DB.
  Chrome uses the real HOME and `--use-mock-keychain`. It checks draft previews,
  switching to originals, specific approvals, a shared-runner refusal before
  approval, and an approved PDF's hash at a local multipart form. **The final
  browser test passed in 30.48 seconds**, including the real `Service.queue` →
  `Service.execute` → `BrowserEngine.run` → local employer receipt path. The run
  ended `confirmed` with zero worker model calls. The employer received exactly
  one `Alex_Example_Resume.pdf`, whose SHA-256 matched the approved artifact and
  the run's pinned `resume_sha`.
- Visual approval tests were omitted at the user's explicit request. No real
  model call was made: `Settings(_env_file=None)` reported no configured key.
  No live employer or production Chrome was contacted.

### Commands

The requested shared `.venv` interpreter crashes importing `readline`, before
pytest collection (exit 139, also reproduced with `python -c 'import readline'`).
Testing therefore uses an isolated Python 3.12.13 environment with a `.pth` file
pointing at the existing shared dependencies. No sync or package upgrade was run.
The environment used in this session is:

```sh
AGGRESSIVE_TEST_ENV=/var/folders/zr/313n2_7d55z9k0wq_fn0cdcc0000gn/T/axiom-aggressive-verify-6g5ztwn9/venv

APPLYPILOT_LOCK_DIR=$HOME/.cache/applypilot/codex \
UV_PROJECT_ENVIRONMENT=$AGGRESSIVE_TEST_ENV UV_NO_SYNC=1 \
AXIOM_TEST_TEX_BINARY="$HOME/Desktop/applypilot/AXIOM.replaced.app/Contents/Resources/tex/bin/universal-darwin/pdflatex" \
scripts/dev/pytest-locked.sh tests/test_aggressive_pipeline.py \
  tests/test_aggressive_routes.py tests/test_aggressive_bundle.py \
  -m "not browser" -q -p no:cacheprovider

# Run the one browser file separately, using the machine-wide browser lock.
UV_PROJECT_ENVIRONMENT=$AGGRESSIVE_TEST_ENV UV_NO_SYNC=1 \
scripts/dev/pytest-locked.sh tests/test_aggressive_browser.py -q -p no:cacheprovider

frontend/node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
"$HOME/Desktop/applypilot/axiom/.venv/bin/ruff" check \
  jobpilot/features/resumes/aggressive tests/test_aggressive_*.py
git diff --check
```

## Model cost

There are three clean-stage calls, one optional critique correction, and one
optional initial repair: **3–5 logical calls**, plus any retries imposed by the
existing model client. All use the existing configured model and the user's key.
Compilation, checks, project choices and project approvals make no model calls.
Actual request/token costs are captured from the app's existing `COST_METER`.

For the synthetic 713-character source / 307-character posting, the registry's
conservative estimate is **12,468 input + 6,500 output tokens**. At the app's
configured DeepSeek rates of $0.30 / $1.20 per million, that is **$0.0115404 per
tailoring**, excluding first-time source transcription. The shared estimator
adds that initial transcription separately (about $0.0003453 for this source).
These are estimates from configured rates, not a measured provider bill.
Stub execution costs $0; the usage-meter unit fixture verifies arithmetic with
synthetic usage and is not evidence of a paid run.
