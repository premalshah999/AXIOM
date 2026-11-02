# Aggressive tailoring: integration completed by Codex

The owner explicitly asked Codex to finish the shared integration. The earlier
list of work for Claude is completed. Claude only needs to review and merge the
branch; no screen, route, approval or send-guard wiring remains to implement.

Worktree: `axiom-codex/aggressive`; branch: `codex/aggressive-tailoring`.
Claude's shared changes through `agent13/tailor3` commit `49fa2ac` were merged
first, including sequential PDF extraction and the worker's approval recheck.
Production, production data/Chromes, the checkout `.env`, main, pushes and
deployments were not used or changed. Overview was not edited.

## Shared changes completed

- `features/__init__.py` mounts the Aggressive routes in the production app
  factory ahead of the SPA fallback.
- `resumes/modes.py` adds optional `preview_projects` and `removed_projects` to
  `ModeResult`. `review_checks` runs the existing owner rules separately for
  preview and send, verifying that a pending source matches its declared draft.
- `resumes/pipeline.py` persists validated unconfirmed previews for review,
  stores both decisions, forwards explicit removals, checks ATS extraction, and
  preserves the generation cache key.
- `resumes/choice.py` keeps pending drafts in review even when automatic approval
  is enabled. Content and workspace revision checks run for approval, queue
  admission and when the actual worker takes its PDF.
- `resumes/tailor_routes.py` exposes approval blockers and project changes. Edits
  retain pending-project restrictions. A changed project selection must be
  compiled before further resume edits.
- `resumes/router.py` preserves Aggressive provenance through the general resume
  editor. A derived artifact cannot erase the workspace revision guard.
- `frontend/src/tailoring.tsx` embeds the draft manager in the real main page,
  disables approval until the current PDF is sendable, and shows preview checks.
  Generated project wording is read-only here; selection and project approval
  live in the draft manager.
- `frontend/src/aggressive/Drafts.tsx` adds “Update project PDF” after saved
  changes. It recompiles without an AI call. Originals, replacements and
  individual draft approvals work on the same page.

## Approval, concurrency and cache behavior

Unconfirmed projects are saved and included in the preview PDF. They cannot be
approved for sending or uploaded. Approval covers the exact stored project
revision; there is no requirement to claim the project has already been built.
Draft tools do not become held skills. Confirmed project entries become sources
through `added_projects`. The original-projects option needs no confirmed drafts.

Checkbox changes and project approvals atomically advance the workspace revision,
clear the old PDF's approval and retire its generation-cache pointer. Historical
documents, PDFs and hashes remain unchanged. Server checks cover changes made
in another tab and through the API. A started application cannot change projects.

`POST /api/v2/resumes/aggressive/{workspace_id}/refresh` takes `job_id` and
`revision`. It preserves applicant edits, applies the saved choices, compiles
twice per fit attempt and reruns the shared rules and ATS checks. Publishing
rechecks the workspace revision, current artifact, source hash, profile and
posting inside the transaction. A race cannot publish an outdated PDF; failed
publishes remove their output. The new immutable artifact becomes the current
cached version. No model call is made, including on later cache reuse.

The revision follows `tailor-review-v1` and general editor artifacts. Old
approvals cannot bypass project changes with the Settings approval switch off.
Final approval identifies an exact PDF hash when approval is enabled. The
existing worker uploads those exact bytes as `First_Last_Resume.pdf`.

## Generation

The registered async mode runs reference Steps 1–7: dream-candidate brief,
ordered proof points and three distinct archetypes, source-backed skills,
deterministic gates and bounded repair, sanitizing, compilation/page fill, ATS
extraction, independent recruiter critique, and supported corrections with
rechecking. The critic sees only the raw posting and PDF text. Its complete
structured critique and unresolved requirements are saved. The last 15 distinct
jobs inform the repeat check.

The later `CLAUDE.md` corrections win over legacy `agent.py` prompts: no fake
precision, forced course tags, hard-coded candidate facts, keyword stuffing or
new embedding service. All facts come from the user's sources, apart from the
explicit draft-project exception. Existing claims/evidence, escaping, compiler,
templates, fonts and TeX packages are reused. There is no second implementation
of the owner's rules. PDF type sizes are verified; overflow never cuts content.

## Verification and limits

The functional browser test builds the real frontend to a temporary directory
with `envDir: false`, then uses production `create_app` mounts and the real main
tailoring page. Only the configured-model boundary is stubbed. The database is
fresh and the source PDF is compiled from synthetic data. Servers start from an
empty CWD using `env -i`, a private DATA_DIR/DB and the real HOME. Test Chromes use
`--use-mock-keychain`; they never attach to production.

The browser test passed in **41.43s**: choose Aggressive through the normal API,
see pending drafts, reject premature approval, switch to originals, update the
PDF, select/approve drafts, update again, and approve on the main page. Stub
generation stays at four calls including source import. Selections, approvals
and PDF updates add zero calls. The test then runs `Service.queue` →
`Service.execute` → `BrowserEngine.run` and ends `confirmed`, with zero worker
model calls. The employer receives exactly one `Alex_Example_Resume.pdf`; its
SHA-256 equals the approved artifact and the run's pinned `resume_sha`.

Integration tests cover production endpoints, cache reuse, explicit removals,
original-only fallback, cross-tab invalidation with approval off, started-job
protection, compilation races, and provenance through the general editor.
Normal, shared-rule and hostile-model regressions are included in the targeted
matrix: **122 passed in 253.31s**, including the general-editor provenance test.
Bundled TeX is exercised using the existing **non-production** bundle;
no new TeX package is required. No new desktop bundle/clean-Mac install was built.

Visual approval tests were omitted as requested. A real-provider run was not
possible: `Settings(_env_file=None)` found no configured key for any of the five
providers. No live employer was contacted.

### Reproduce

The shared `.venv` interpreter segfaults importing `readline` before collection.
Tests use isolated Python 3.12.13 with a `.pth` pointing at the existing shared
dependencies. No sync, package upgrade or shared-environment change was made.

```sh
AGGRESSIVE_TEST_ENV=/var/folders/zr/313n2_7d55z9k0wq_fn0cdcc0000gn/T/axiom-aggressive-verify-6g5ztwn9/venv

APPLYPILOT_LOCK_DIR=$HOME/.cache/applypilot/codex \
UV_PROJECT_ENVIRONMENT=$AGGRESSIVE_TEST_ENV UV_NO_SYNC=1 \
AXIOM_TEST_TEX_BINARY="$HOME/Desktop/applypilot/AXIOM.replaced.app/Contents/Resources/tex/bin/universal-darwin/pdflatex" \
scripts/dev/pytest-locked.sh tests/test_aggressive_pipeline.py \
  tests/test_aggressive_routes.py tests/test_aggressive_bundle.py \
  tests/test_aggressive_integration.py tests/test_tailoring_normal.py \
  tests/test_tailoring_rules.py tests/test_tailoring_evals.py \
  -m "not browser" -q -p no:cacheprovider --tb=short

# One browser file separately, with the machine-wide browser lock.
UV_PROJECT_ENVIRONMENT=$AGGRESSIVE_TEST_ENV UV_NO_SYNC=1 \
scripts/dev/pytest-locked.sh tests/test_aggressive_browser.py -q -p no:cacheprovider --tb=short

frontend/node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
git diff --check
```

## Cost

Generation uses **3–5 logical model calls**, plus retries from the existing
client. Choices, approval, PDF updates and deterministic checks add **zero**.
The app's `COST_METER` records request/token costs. Refreshed artifacts record
zero new AI cost and retain the generation report.

For the synthetic 713-character source and 307-character posting, the conservative
estimate is 12,468 input + 6,500 output tokens. At configured DeepSeek rates of
$0.30 / $1.20 per million, that is **$0.0115404 per generation**, plus approximately
$0.0003453 for initial source import. These are configured-rate estimates, not a
measured provider bill. Stub execution costs $0; the metering fixture is synthetic.
