# Running AXIOM without Docker

One command, one process tree, on a Mac: `scripts/axiom start` (or `make start`) starts PostgreSQL, the worker and twin Chromes
and every service in [`jobpilot/native/services.toml`](../jobpilot/native/services.toml), waits until they are healthy and opens
the dashboard. Today that list holds one service, the app. Docker keeps working exactly as before; nothing in `compose.yaml` or the
`Dockerfile` changed, and `make up` is still the Docker path.

## Why

Docker Desktop's VM is the largest thing on a 16 GB Mac, and most of it is not the app. Measured on 2026-10-30 with five
workers running:

| | Memory |
|---|---|
| Docker VM, as macOS charges it (`top`, footprint) | 6,156 MB, all of it dirty, so macOS cannot reclaim it |
| Docker Desktop's helper processes on the Mac | 477 MB |
| Inside the VM: the app's own memory (cgroup `anon`) | 1,072 to 1,572 MB |
| Inside the VM: PostgreSQL's own memory (`anon` + `shmem`, 128 MB `shared_buffers`) | 255 to 324 MB |
| Inside the VM: the guest's file cache, which macOS would reclaim if it were its own | 3,100 MB |

The native scratch instance, with synthetic data (25 MB database, 13 connections), polled at 1 Hz with demo applications
running back to back:

| | Resident | Footprint |
|---|---|---|
| PostgreSQL, 64 MB `shared_buffers`, 18 processes | 184 to 196 MB | 277 MB |
| App with its Playwright driver, one application at a time | 112 to 329 MB | 353 MB |
| Supervisor | 13 MB | 15 MB |

Those native numbers do not yet show production's load. With the app at what it uses in the container and PostgreSQL at about
what it uses there, the projection is 1.4 to 2.0 GB against 6.6 GB: about 4.6 GB back. The Chromes already run on the Mac in
both setups, so they neither gain nor lose. Treat the projection as such until it has run with real workers; `scripts/axiom
status` shows the real numbers.

Timings on the same scratch instance: from an empty folder 7.4 s (including creating the database), a later start 5.7 s, a stop
with nothing committing 2 s, `axiom restart app` 5 s. Starting on a production-sized database will take longer.

## Commands

| Command | What it does |
|---|---|
| `scripts/axiom setup` | Installs what is missing: `.env`, PostgreSQL 17 (Homebrew), the Python packages and the dashboard. Safe to repeat. |
| `scripts/axiom start` | Starts everything and opens the dashboard. `--no-open` skips the browser. |
| `scripts/axiom stop` | Drains safely, then stops everything. Up to 180 s while a commit finishes, as under Compose. |
| `scripts/axiom status` | What is running, its memory, whether a hold is on, sleep, power and disk. Warns on battery. |
| `scripts/axiom restart [name ...]` | Everything, or the named services (or `postgres`, `worker`, `twin`) alone. |
| `scripts/axiom logs [name] [-f]` | A service's log (the first by default), `postgres` or `supervisor`. Capped at 10 MB x 3 as under Compose. |
| `scripts/axiom psql` | `psql` on the native database. |
| `scripts/axiom jobpilot token` | Any app command with the running app's environment (`verify`, `reset-password`, ...). |
| `scripts/axiom hold on\|off` | Stop or allow new applications. |
| `scripts/axiom deploy [--wait]` | Snapshot the checkout and restart on it once nothing is active. `scripts/deploy.sh` calls this when the native app is running. |
| `scripts/axiom migrate plan\|db\|files` | Move a Docker installation here (below). |

## The services are a list

The supervisor knows no service by name. It reads this record for each entry in `services.toml`, so running more of them, for example a
gateway, an applier, discovery and mail, is a change to that file and not to the launcher:

```toml
[[service]]
name = "applier"                                 # Its log, its row in `axiom status`, `axiom restart applier`.
command = ["{python}", "-m", "jobpilot.cli", "serve-applier", "--port", "{port}"]    # {python} {port} {data_dir} {root}
port = 8091                                      # Checked free before anything starts.
port_setting = "APPLIER_PORT"                    # The `.env` key that may change it.
health_url = "http://127.0.0.1:{port}/healthz"   # Answers 200 when ready.
restart = "always"                               # always | on-failure | never
required = true                                  # false: a failure is reported, and the rest carry on.
after = ["postgres", "worker"]                   # Started once these answer, stopped before them.
restart_with = ["postgres"]                      # Restarted when one of these restarts.
drains = true                                    # A stop waits SHUTDOWN_DRAIN_SECONDS + 30. Otherwise stop_grace_seconds.
[service.env]
ROLE = "applier"                                 # On top of what every service gets: DATA_DIR, DATABASE_URL, BASE_URL, ...
```

It stays one app. There is one thing to start and one address to open, `BASE_URL`, and it counts as up only once every required service is.
Stopping goes in reverse dependency order, each service draining with what it depends on still up. Each service runs from the same release.

Anything wrong in the list (an unknown field, a port taken twice, a name nothing starts, a circle in `after`, a command that binds every
interface, a health check that is not local) stops `axiom start` with a message naming the entry, before anything has started. To try a list
without changing the file, set `AXIOM_SERVICES=/path/to/list.toml`. A deploy that changes the list restarts everything, because it is a new
process tree; otherwise only the services restart.

Health checks are reported, not acted on, as under Compose: a service that has stopped answering but still runs is shown in `axiom status` and
left alone, because restarting it mid-application costs more than a slow answer.

## What the supervisor does

It is a small standard-library process (`jobpilot/native`, 13 MB) that does what Compose and Docker's init did:

- **Restarts.** A child that exits is started again as its policy says, and an exit 0 from `POST /api/system/restart` is a restart. A
  child that exits five times in a row within a minute stops the installation cleanly with a message that `axiom status` repeats. Optional
  ones (the twin Chrome, or a service with `required = false`) are reported instead, and the rest continue.
- **Stop order.** Services first, in reverse dependency order, then the Chromes, then PostgreSQL. SIGTERM goes to the process alone, so that
  the app drains with Playwright's driver alive, for the same 180 s as `stop_grace_period`.
- **Process reaping.** Each child leads its own process group, and whatever a dead child left behind is killed with the group.
- **Database restarts.** The database comes back first, then whatever names it in `restart_with` restarts, so that interrupted runs recover.
- **Sleep.** While applications are active, and for five minutes after, `caffeinate -i -m -w <supervisor pid>` prevents idle sleep. It
  dies with the supervisor. It does not stop the Mac sleeping when the lid closes or the battery runs out, which is why `axiom status`
  warns on battery.
- **A killed supervisor.** The children keep running and `axiom status` says so. `axiom stop` or the next `axiom start` stops them
  safely from the pids in the state file, checked against each process's start time so that a reused pid is never signalled.
- **Docker alongside.** `axiom start` refuses while a container of this image runs: two installations with two databases would apply
  to the same jobs twice. `AXIOM_ALLOW_DOCKER=1` allows it only for a scratch instance on other ports, and never with a Chrome it did
  not start.

It also carries the environment Compose set: `DATA_DIR`, `DATABASE_URL`, `DBOS_DATABASE_URL`, `BASE_URL`, and the Chrome addresses
with `host.docker.internal` rewritten to `127.0.0.1`, so one `.env` works for both. The app runs with `TZ=UTC`, the container's zone,
because `date.today()` and stored timestamps rely on it; `TIMEZONE` still decides the daily limits and form dates. File descriptors
are raised from macOS's default of 256.

**The code that runs is a release, not the checkout.** Most imports here are inside functions, so a process running from the checkout
would load whatever `git merge` put there, half old modules and half new, in the middle of an application. `axiom deploy` snapshots
`jobpilot/` into `DATA_DIR/native/releases/` as `COPY jobpilot/` does for the image, and flips `current`. The last three are kept; to
roll back, point `current` at an older one and run `axiom restart app`. Merging in the checkout changes nothing until you deploy.

**Security.** The app, PostgreSQL and both Chrome debugging ports listen on 127.0.0.1 only (`APP_BIND` is ignored here), PostgreSQL
requires its password over TCP and has no socket file, the data directory is 0700 and `.env` 0600. There is no equivalent of the
container's non-root user, `no-new-privileges` or `cap_drop: ALL`: the app runs as you, and can read what you can read.

## The database

| Option | Verdict |
|---|---|
| Homebrew `postgresql@17` | **Chosen.** 80 MB, the same major version as the container, restores its dumps directly, and is one command to install. The supervisor runs its own cluster in `DATA_DIR/postgres` on port 54329, so an existing server on 5432 is neither used nor disturbed; no `brew services`. |
| A packaged binary (Postgres.app, EDB, embedded builds) | Works through `AXIOM_PG_BIN` and is found by itself in the usual places. Bundling one would save nothing in memory and would make this repository responsible for its security updates. |
| SQLite | **Not for production.** See below. |

Settings are on the command line (`jobpilot/native/pg.py`), so `ps` shows what is in force: `shared_buffers=64MB` (the container has
128 MB), `max_connections=60` (the app's pool of 30 and DBOS's 20, plus tools; a test keeps this above the pools), no JIT and no parallel
workers. The app's date arithmetic stays in UTC, and text sorts as it did in the container (ICU `en-US`; macOS's own `en_US.UTF-8` sorts
by byte).

**Durability is better than in Docker, and costs less than it could.** Docker's flush measures 89 us here, which is not a real flush, and
macOS's `fsync` (61 us) is not either; a real one (`F_FULLFSYNC`) takes 4.3 ms. So every flush here is real
(`wal_sync_method=fsync_writethrough`), which keeps the WAL's order through a power cut. Waiting for one on all 60 event inserts a second
would load an event loop already known to starve on database time, so plain commits do not wait (a crash can lose their last half second)
and `Database.exclusive()` turns the wait back on: a reservation that says `submitting` is on the drive before the click.

**Can SQLite carry nine workers?** Its write rate is not the obstacle. The app's own `Database` class, with nine threads at production's
mix and at three times that, ran with no errors on a small file (`exclusive` p99 about 230 to 290 ms, against 80 to 200 ms on PostgreSQL;
event inserts p99 40 to 60 ms, against 17 to 37 ms). What rules it out is everything that test did not include: DBOS's queues on SQLite have
never carried this load, every write in the app would queue behind one writer with a 30 s timeout on one event loop, there is no route to
bring 2.9 GB of existing data and 2.2 million DBOS rows across, and the saving is only PostgreSQL's 200 to 350 MB. It stays what it is: the
fallback for demos and tests.

## Moving a Docker installation here

Docker is only ever read. Every command against it is `pg_dump`, a `psql` select, or `find`/`tar`/`sha256sum` in a throwaway container that
mounts the volume read-only. Each step can be run again, and rolling back is starting the containers.

- `migrate plan` writes nothing and says whether everything fits.
- `migrate files` is a sync: keys and documents first, then whole runs, newest first. It measures the free disk before every 64 MB batch and
  stops at a floor (`--keep-free-gb`, default 4), exiting 3 with the oldest runs still in Docker. Run again later, it fetches only the
  difference. HTML and JSON are stored compressed by the file system (6:1 on production samples), so nothing that reads them has to know.
- `migrate db` streams `pg_dump` into `pg_restore` with no file in between, refuses while the app is connected, compares exact row counts for
  every table, repoints queued runs from `/app/.data` to `DATA_DIR`, and leaves a hold on, so that nothing starts until you have looked.

`DATA_DIR` stays what `.env` says, normally `.data` in the checkout, where the worker and twin Chrome profiles already are. They are not moved
or copied, so their sign-ins carry over and rolling back needs nothing. Because that folder holds your data, never run `git clean -x` here.

### Cut-over

Plug in the Mac first. From the checkout, on the branch you are deploying:

```sh
# 1. Prepare, with Docker still serving. Nothing here changes Docker.
scripts/axiom setup
scripts/axiom migrate plan

# 2. If it says it does not fit: reclaim what is not your data, then plan again. Docker.raw shrinks only after the VM trims.
docker builder prune -f
docker image prune -f
docker system df

# 3. Copy the files now, at low priority, so that the downtime is short. It may stop at the disk floor; that is fine.
scripts/axiom migrate files

# 4. Stop the Docker app. It drains for up to 180 s; the database container stays up.
docker compose stop app
docker compose exec -T db psql -U applypilot -d applypilot -Atc "select count(*) from runs where state='submitting'"
# Expect 0. Otherwise those runs were still committing when the drain ended; they reconcile as usual after the start.

# 5. The database and what changed in the files since step 3.
scripts/axiom migrate db       # Must end "Verified: N tables and M rows match Docker exactly".
scripts/axiom migrate files    # Exit 0: complete. Exit 3: the oldest runs stay in Docker for now; keep the volume.

# 6. Stop what Docker was using, and free its memory.
docker compose stop db
kill $(lsof -tiTCP:9224 -sTCP:LISTEN) $(lsof -tiTCP:9225 -sTCP:LISTEN)
osascript -e 'quit app "Docker"'

# 7. Start. The hold is on, so nothing applies yet. Sign in with the same password; the token is the same.
scripts/axiom start
scripts/axiom status

# 8. When the dashboard looks right, start applying.
scripts/axiom hold off
```

Afterwards, compare `scripts/axiom status` with the table at the top and watch `host_load_state`: commits are a few milliseconds slower here
than Docker's flush that was not one.

Once a few days have passed, `migrate files` has said complete and you do not expect to roll back, the Docker volumes can go. That is the only
step that destroys anything, and nothing here runs it for you: `docker volume rm <project>_postgres <project>_applicant-data`.

### Rolling back

**Before `hold off`, or before anything has been sent natively.** Docker's database and volume have not changed:

```sh
scripts/axiom stop
open -a Docker    # Wait for it to start.
docker compose up -d
make chrome && make twin
```

**After applications have been sent natively.** Docker's database does not know about them and would apply again, so they have to go back first:

```sh
scripts/axiom hold on    # Then wait for `scripts/axiom status` to show 0 active.
PGPASSWORD=$(sed -n 's/^POSTGRES_PASSWORD=//p' .env) "$(brew --prefix postgresql@17)/bin/pg_dump" \
  -h 127.0.0.1 -p 54329 -U applypilot -d applypilot -Fc -f "$HOME/axiom-native.dump"
CUTOVER='2026-11-01T00:00'    # When you cut over, in UTC: only runs from then on go back.
RUNS=$(scripts/axiom psql -XAtc "select 'runs/'||id from runs where created_at >= '$CUTOVER'" |
  while read -r run; do [ -d ".data/$run" ] && echo "$run"; done)
scripts/axiom stop

open -a Docker           # Wait for it to start.
docker compose up -d db
docker compose exec -T db pg_restore -U applypilot -d applypilot --clean --if-exists --no-owner --no-privileges \
  < "$HOME/axiom-native.dump"
docker compose exec -T db psql -U applypilot -d applypilot -c \
  "update settings set value=(value::jsonb||'{\"stage\":\"complete\"}')::json where key='execution_v2_deployment_hold'"
PROJECT=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' .env)
tar -C .data -cf - $RUNS | docker run --rm -i --network none -v "${PROJECT:-axiom}_applicant-data:/data" \
  postgres:17.11-bookworm sh -c 'tar -C /data -xf - && chown -R 10001:10001 /data/runs'
docker compose up -d
make chrome && make twin
```

This replaces Docker's database with the native one, so it is yours to run, not the tool's. It was exercised end to end on a scratch container.

## What still assumes Docker

- `make up`, `down`, `restart`, `logs`, `ps`, `token`, `verify` and `update` are the Docker path. Natively: `scripts/axiom` and its table above.
- There is no start at login: Docker Desktop brought its containers back after a reboot, and this needs `scripts/axiom start`. A launchd agent would be
  a small addition, deliberately not made yet.
- Resume tailoring needs `pdflatex` on the Mac (`brew install --cask basictex`). The image installs it; without it tailoring reports `compiler_missing`
  and nothing else changes.
- CI builds and tests the image. It runs this package's tests on Linux, but nothing runs the native app on a Mac in CI.
- `jobpilot browser`, `twin-browser` and the comments in `scripts/chrome-*.sh` still describe Docker. They work; the supervisor uses those scripts for
  every Chrome flag.
- Tools outside this repository that call `docker exec`, `docker cp` or `docker compose` need the native equivalents: `scripts/axiom psql`, `.data/runs/`
  and `scripts/axiom logs`.

## Troubleshooting

**`postgres is not running`.** `scripts/axiom logs postgres`. `could not bind` means something else holds the port: set `AXIOM_PG_PORT` in `.env`.

**`AXIOM is also running in Docker`.** That is the guard above. `docker compose stop app` first.

**`worker is not running ... If another Chrome is using its profile`.** A Chrome started by hand on the same profile without the debugging port. Quit it.

**A Chrome is shown as `external`.** Something else started it on that port. It is left alone and not stopped by `axiom stop`; quit it and `axiom restart` to
have it supervised.

**`A database restore did not finish`.** `scripts/axiom migrate db` clears the partial restore and runs it again.

**The Mac still sleeps.** `caffeinate` holds only idle sleep. With the lid closed it needs power and an external display; on battery it sleeps regardless.
