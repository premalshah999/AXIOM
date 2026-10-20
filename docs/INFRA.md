# Infrastructure, configuration and workers

For the person who runs or changes the installation. Friends and family start at [SETUP.md](SETUP.md).

## What was consolidated, and why

The complaint was multiple images, conflicting versions, several frontends and services that were slow or in conflict.
This is what was found and what replaced it.

| Before | Problem | Now |
|---|---|---|
| Three containers: `app`, `db`, `browser-cleanup` | `browser-cleanup` ran `image: applypilot-app` with `pull_policy: never` and bind-mounted `close_tabs.py` over the image. It kept running the image it was created from (five days old) while `app` was recreated again and again, so two versions of the code ran in one project. It also hard-coded the image name, so the compose file could not run under any other project name. | Two services, `app` and `db`. The same sweep runs inside the app as a background task (`jobpilot/operations/close_tabs.py: watch`, `TAB_CLEANUP_SECONDS`, default 60, 0 turns it off). It starts only with a desktop Chrome configured, runs off the event loop, and writes the same `inactive_tab_cleanup` health setting. |
| `applypilot-app:latest`: 3.58 GB, 509 layers | `codex-handoff/bin/deploy.sh` stacked a `FROM applypilot-app:latest` + `COPY jobpilot/` layer on every deploy. Patch layers never delete files, and they never rebuild the dashboard, so the code and the frontend could disagree. | One Dockerfile. 2.49 GB, 30 layers. Dependencies and Chromium are separate layers, so a normal rebuild (`docker compose up -d --build app`) changes only the code layers and takes seconds. Patch layering is no longer needed. |
| Playwright installed Chromium and its headless shell | The shell (334 MB) is never used: the code launches the full Chromium binary with `--headless=new`. | `playwright install --with-deps --no-shell chromium`. |
| Base images `node:22-bookworm-slim`, `python:3.12-slim-bookworm`, `postgres:17-bookworm` | Floating tags: two builds a week apart could differ. | Pinned in the Dockerfile through ARGs: Node 22.23.2, Python 3.12.15, uv 0.12.18. PostgreSQL pinned to 17.11. `.nvmrc` and `.python-version` hold the same versions for CI and local tools; `tests/test_infra_consistency.py` fails if they drift. |
| A database password written into compose as the default | A known password on every installation. | `POSTGRES_PASSWORD` is required and `scripts/setup.sh` generates it. The database is never published to the host. |
| `ports: 127.0.0.1:8080:8080` fixed; `BASE_URL` separate | Changing the port broke every browser write (the API rejects an Origin that is not `BASE_URL`). | `APP_PORT` and `APP_BIND`; `BASE_URL` defaults from `APP_PORT`. |
| One frontend in `frontend/` | Already single, but built outside the image or not at all for patch deploys. | Still `frontend/` only. It is built inside the image by a Node stage (`tsc -b` then `vite build` into `jobpilot/static`). `jobpilot/static` is gitignored and dockerignored, so a stale local build can never reach an image. |
| `.dockerignore` sent all of `docs/` and `.env.example` into the build | Context noise. | Only `docs/third-party.md` and `docs/licenses/` (needed by the package build) are included; `.env*` never is. |
| No log limits | Container logs grow without bound on a machine with little disk. | json-file, 10 MB x 3. |
| 10 s stop grace | A restart killed the app wherever it was; a commit under way became an unknown submission. | 180 s, and the app drains first (see "Stopping and deploying"). |

Kept as they were: the non-root user (uid 10001), `init: true`, `shm_size: 1gb` (Chromium), `no-new-privileges`, `cap_drop: ALL`,
the named volumes `postgres` and `applicant-data`, PostgreSQL user and database name `applypilot` (renaming them would
break an existing volume), and the app's own `/healthz` check (the app is healthy only when the database answers).

### Dependency pins

- **Python**: `pyproject.toml` pins every runtime dependency with `==`; `uv.lock` pins the whole tree and the image installs
  with `uv sync --frozen`. Two loose ranges remain and are locked by `uv.lock`: `2captcha-python>=2.0,<3` and the dev group.
- **Node**: `frontend/package-lock.json`, installed with `npm ci`.
- **System**: Debian bookworm packages (LaTeX for resume tailoring, Chromium's libraries) float with the base image tag.
  That is the one input a rebuild cannot pin.

## Configuration

`.env.example` lists all 101 settings of `jobpilot/config.py` (class `Settings`) plus the five variables only Compose
reads. `tests/test_env_example.py` fails if a setting is missing, a documented default differs from the code, a secret has
a value, or a personal address appears.

Required: `POSTGRES_PASSWORD` (generated), and the API key of the chosen `LLM_PROVIDER`. Recommended: a Gmail app
password (Setup > Email), `TIMEZONE`, `BROWSER_CDP_URL`. Everything else has a working default.

One trap to know: pydantic rejects an empty string for numeric, boolean and enum settings, so a blank `NAME=` line is
valid only for string settings. The example keeps every default commented out for that reason.

### Audit: settings that conflict or encode one person

| Setting | Finding | What was done |
|---|---|---|
| `LLM_PROVIDER` | The old example said `mimo`; the code default is `deepseek`. | Example follows the code (`deepseek`). |
| `DEEPSEEK_THINKING` | The old example said `disabled`, the developer guide says production answers run with thinking off, but the code default is `enabled` (slower and dearer). | The example keeps `disabled` active. **Recommend flipping the code default**; not changed here to avoid a behaviour change in the architecture branch's area. |
| `TELEGRAM_WAIT_SECONDS` | Old example 120, code 0. | Example follows the code. |
| `WORKERS` | Old example and code 8, production 6 (which meant no specialists). | Replaced, see Workers. |
| `TIMEZONE` | Defaults to `UTC`. It decides the daily limit, the daily budget, schedules and the date written on forms. | `scripts/setup.sh` writes the computer's zone. An installation that relied on the old `America/New_York` default must set `TIMEZONE` in its `.env` (the original owner's production file does). |
| `CAPTCHA_SAME_IP`, `CAPTCHA_TWIN_HOSTS` | `true` / `icims.com` encode decisions taken for one owner's setup. | Documented; harmless without a 2Captcha key. |
| `ENABLE_DEMO` | `true` in the production image: the demo employer form and `/api/demo` are reachable. | Needed by `jobpilot verify` and the first-run tour, so left on; flagged. |
| `POSTGRES_PASSWORD` | Was a constant in compose. | Required and generated. |
| Origin check | `localhost` and `127.0.0.1` are different origins to the API. A friend who types `localhost:8080` gets 403 on every write. | Documented in SETUP.md. **Recommend accepting both loopback spellings** in `api.py`'s middleware (left for the architecture branch, which is reviewing the API). |
| Demographic and work-authorization answers | Owner-specific policy lives in code and the knowledge base, not in `Settings`. | Out of scope here; owned by the architecture workstream. |

Secrets stay in two places: `.env` (provider keys, Telegram, 2Captcha, the optional `APPLICATION_PASSWORD`) and the
database vault, encrypted with `DATA_DIR/mail-key` (mailbox and employer-account passwords). Neither is ever committed;
`.env` is gitignored and dockerignored.

## Workers

Every application is classified by platform. `ats.detect(url)` maps the URL to one of 25 known platforms when the job is
created (anything else is `custom`); at run time `BrowserEngine.detect_ats` looks again, because redirects and embedded frames reveal the real
platform behind a company career site. The platform then selects the flow:

| Flow | Platforms | Budget |
|---|---|---|
| Guided single-page flow (`AGENT_FIRST`) | Greenhouse, Lever, Ashby, SmartRecruiters, Workable, BambooHR | `APPLICATION_TIMEOUT` (180 s) |
| Deterministic adapter (`adapters.REGISTRY`) | Workday, Oracle, iCIMS, Taleo, SuccessFactors, Eightfold | `MULTIPAGE_TIMEOUT` (480 s) |
| Generic multi-page adapter, falling back to the guided flow | ADP, Avature, Jobvite, Dayforce, UKG and any unrecognised career site | `MULTIPAGE_TIMEOUT` |

A worker is a browser owner (a slot), not a platform: any ATS worker runs whichever flow its job needs, one writer per job.

### Pools and slots

| Setting | Default | Meaning |
|---|---|---|
| `NORMAL_WORKERS` | 6 | ATS workers, slots `1..N`. They take every new application. |
| `SPECIALIST_WORKERS` | 2 | Specialists, slots `N+1..N+S` (7 and 8 by default). `0` turns them off. |
| `SPECIALIST_AFTER_FAILURES` | 2 | Failures on ATS workers before a job moves. |
| `SPECIALIST_HARD_ATS` | `successfactors,eightfold,smartrecruiters,dayforce` | One failure is enough on these platforms. |
| `SPECIALIST_ON_STALL` | `true` | One failure is enough when the controller stalled on an unchanged form. |
| `SPECIALIST_BUDGET_MULTIPLIER` | 1.5 | Wall-clock time, model calls and per-job token cap, relative to an ATS worker. |
| `SPECIALIST_ATTEMPTS` | 2 | Specialist attempts per job. |

The pools are separate DBOS queues (`applications_normal`, `applications_specialist`) with their own concurrency, so a busy
specialist pool never idles the ATS workers and the reverse. If the specialist pool is full while an ATS slot is free, the
job falls back to an ATS worker. Only the autopilot's new-application intake is limited to ATS slots. Browser ownership
rules are unchanged: one employer at a time globally, one writer per job.

A job moves to a specialist only after an agent-fixable failure (`recovery.SPECIALIST_CAUSES`: control timeout, operation
timeout, navigation stall, control observation, validation, model output). Missing personal facts, email waits, provider
outages and uncertain submissions never qualify, and neither does a job with any submission evidence. The trigger
(`repeated_failure`, `hard_ats`, `stalled`) is stored in `run.packet.specialist_escalation.trigger`. The policy is in
`jobpilot/specialist.py`; the state machine that uses it is `recovery.schedule_recovery`.

The multiplier is applied in three places: the run's wall-clock budget (`tiers.time_budget`, both the outer deadline in
`Service._execute_owned` and the engine's own budget), the model-call cap (`tiers.call_limit`, read from the run's pool),
and the 24-hour per-job input-token cap (`tiers.job_over_budget`, at queue time).

**Legacy `WORKERS`.** It still works, keeps its old layout, and logs a warning: slots 1 to 6 are ATS workers, slots 7 and 8
specialists, a larger fleet keeps two specialists as its last slots. So `WORKERS=6` is six ATS workers and **no**
specialists. It cannot be combined with `NORMAL_WORKERS` or `SPECIALIST_WORKERS`.

**Why these hard-platform defaults.** In the production run history, failures with an agent-fixable cause were dominated
by Workday (already tuned separately), then SuccessFactors, Eightfold and SmartRecruiters, whose failures are mostly stuck
controls and stalls rather than waits. Dayforce is included for the navbar and picker problems in `docs/rea/`. Treat the
list as a starting point and edit it from the failure analysis.

## Stopping and deploying

Every supported way to restart the app is a stop: `docker compose up -d --build app`, `docker compose restart app`,
`docker compose stop`. All send SIGTERM, and the app **drains** before it exits (`Service.drain`, `jobpilot/host_health.py`).
No script, hold or manual step is needed; the code does what the old `hold.sh` plus `deploy.sh` did, in the app.

1. uvicorn stops accepting requests (waiting at most 15 s for open ones).
2. Nothing new starts: `dispatch_limit` is 0, the claim of a browser slot leaves queued runs `queued`, retries and the
   autopilot wait.
3. No application may begin its commit: `reserve_submission` refuses (`ServerDraining`). That run ends with "Worker
   interrupted before submission; draft recovery scheduled", costs no start and no repair attempt, and resumes after the
   restart.
4. Commits already under way are awaited, up to `SHUTDOWN_DRAIN_SECONDS` (default 150 s). `compose.yaml` allows 180 s
   before SIGKILL, so keep that value at least 30 s above it. `select value from settings where key = 'last_drain'`
   shows how long the last drain waited and which runs were still committing when it gave up.
5. Then the workers, mail polling and browser connections close and the process exits. Applications that were only
   filling a form stop where they are; at the next start they are marked interrupted and resume from the saved draft
   (`workers.recover_interrupted`), again at no cost in starts.

A stop with nothing committing is as quick as before. `docker compose up -d --build` builds the image while the old
container keeps working, then stops it, so the drain is the only wait.

**What can still turn a commit into "Worker interrupted during submit; reconcile before retrying"** (a
`submission_unknown` that is never retried blindly):

- SIGKILL before the drain ends: `docker kill`, `docker compose down -t 0`, `restart -t 0`, the out-of-memory killer,
  Docker Desktop quitting, a host crash or power loss.
- A commit that outlives the drain. Typical commits take 10 to 90 s (Workday, Oracle and SmartRecruiters watch the page for
  up to 90 s after the click; Dayforce up to 60 s). A Greenhouse emailed code or an Oracle code wait can add up to 150 s;
  at 150 s the drain gives up and lists the run in `last_drain.still_committing`.
- The `db` container restarting under a running app (the one-time cut-over recreates it): in-flight runs lose their
  connection and stay `running` or `submitting` until the app restarts. Stop the app first, then recreate `db`.
- A crash of the app process itself (an unhandled error in the loop, a segmentation fault).

The `execution_v2_deployment_hold` setting still works (`stage: draining` stops all intake, and `retry_due`, the
autopilot and queue admission honour it) but a deploy no longer needs it; a forgotten hold keeps stopping intake, so
never leave it at `draining`.

## Host Chromes

`scripts/chrome-worker.sh` (port 9224) and `scripts/chrome-twin.sh` (port 9225) start Chrome with the same flags and
profile folders as `jobpilot browser` and `jobpilot twin-browser`, so existing profiles keep working, but need no Python
on the host. They do nothing if the port already answers, so running them by mistake cannot start a second Chrome. The
debugging port listens on 127.0.0.1 only; Docker Desktop forwards `host.docker.internal` to it.

## Continuous integration

`.github/workflows/ci.yml`: ruff, the unit tests (everything not marked `browser`), the dashboard type-check and build, and
an image smoke test that builds the real image and starts it with PostgreSQL. Details are in the workflow's comments.

Lint debt: the repository has 30 existing `ruff check` findings and 158 files `ruff format` would rewrite. CI blocks on
syntax errors and undefined names only, and reports the rest without failing. After the other branches are merged, run
`ruff check --fix` and `ruff format` once and make both blocking.

## Cut-over notes for the orchestrator

The live project is `applypilot` and its volumes are `applypilot_postgres` and `applypilot_applicant-data`. A new compose
file run from another directory would otherwise create empty volumes under the name `axiom`.

1. In the production `.env` set `COMPOSE_PROJECT_NAME=applypilot` (keeps the volumes and the `applypilot-app-1` / `applypilot-db-1` names)
   and `POSTGRES_PASSWORD` set to the value the existing volume was created with: the production `.env`'s `POSTGRES_PASSWORD` if it has one, otherwise the
   default written in the old `compose.yaml` (`${POSTGRES_PASSWORD:-...}`). A volume keeps its first password, and Compose refuses to start without the variable.
2. Replace `WORKERS=6` with `NORMAL_WORKERS=6` and `SPECIALIST_WORKERS=2`, or leave `WORKERS=6` to keep six workers and no
   specialists for now. Keep `BROWSER_CDP_URL` and `BROWSER_TWIN_CDP_URL`.
3. Hold and drain as usual (`hold.sh`; the container being replaced is the old build, which cannot drain itself, so wait for
   `select count(*) from runs where state = 'submitting'` to reach 0), then `docker compose up -d --build --remove-orphans`. The database container is recreated as well,
   because its image reference changes from `postgres:17-bookworm` to the pinned `postgres:17.11-bookworm` (same PostgreSQL 17.11, a separate build): a few
   seconds of downtime with the data volume untouched, which is why the drain comes first. `--remove-orphans` deletes the old
   `browser-cleanup` container. Then remove its stale image and the layered images: `docker image ls` and `docker image rm` the
   ones that are not the new `applypilot-app`. Volumes are untouched.
4. `docker compose up -d --build --no-deps app` replaces `deploy.sh`'s patch layer for later releases; unlike the patch layer it also
   rebuilds the dashboard when `frontend/` changed.
5. Nothing in this branch changes the database schema. Rollback is `git checkout` of the previous commit and the same command.
6. The first start under the new compose changes the app's grace period, health start period and log rotation, and starts the
   tab sweep inside the app. Check its first healthy sweep with
   `select value from settings where key = 'inactive_tab_cleanup'` (state `healthy`).

## Open issues

- **No migration framework.** The schema comes from `Base.metadata.create_all`, which creates missing tables but never alters
  existing ones. The first release that changes a column needs a migration step (Alembic). `setup.sh` documents "migrates"
  as automatic only because nothing needs migrating yet.
- **Specialist track record.** The production history shows several hundred specialist runs, nearly all ending in
  `waiting_browser`, with no specialist confirmation among the most frequent outcomes. The forensics workstream should
  confirm that specialists add value before the hard-platform fast track is relied on.
- **Image publishing.** Building takes 5 to 10 minutes on a first install. Publishing the image from CI to a registry would
  reduce a friend's install to a pull; deliberately not done here.
- **`jobpilot verify` is order dependent.** It drives the built-in demo form, which accepts only a fixed synthetic applicant, so it
  fails on an installation that already has a real profile, and the demo jobs it creates close the open first-run sign-up
  (`owner_account.fresh_installation`). CI and `make verify` therefore run it on a clean installation. A verification that
  can run beside real data would need the demo to carry its own profile.
- **Linux hosts.** `host.docker.internal` and Chrome's loopback-only debugging port make the host-Chrome workers macOS-only.
- **Defaults** to review: `DEEPSEEK_THINKING`, `TIMEZONE`, `ENABLE_DEMO`, and the strict Origin check (see the audit table).
