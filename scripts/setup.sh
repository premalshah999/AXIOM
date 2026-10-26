#!/bin/sh
# One command from a fresh clone to a running AXIOM.
#
#   scripts/setup.sh              create .env if needed, build the image, start, wait until healthy, print the URL
#   scripts/setup.sh --env-only   only create or check .env (no Docker needed)
#
# Re-running it is safe: an existing .env and database password are never replaced. Values used only when .env is
# first created: APP_PORT, APP_BIND, COMPOSE_PROJECT_NAME and TIMEZONE (for example APP_PORT=8081 scripts/setup.sh).
# The database schema is created by the app on first start; there is no separate migration step.
set -eu
cd "$(dirname "$0")/.."

say() { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

env_only=0
for arg in "$@"; do
  case "$arg" in
    --env-only) env_only=1 ;;
    -h | --help) sed -n '2,9p' "$0"; exit 0 ;;
    *) fail "unknown option: $arg (try --help)" ;;
  esac
done

random_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
  else
    LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 48
    echo
  fi
}

# The computer's IANA time zone (macOS and Linux), or nothing.
detect_timezone() {
  if [ -L /etc/localtime ]; then
    target=$(readlink /etc/localtime)
    case "$target" in *zoneinfo/*) printf '%s' "${target##*zoneinfo/}"; return 0 ;; esac
  fi
  if [ -r /etc/timezone ]; then head -n 1 /etc/timezone; fi
  return 0
}

create_env() {
  [ -f .env.example ] || fail ".env.example is missing. Run this from the root of a full clone."
  port=${APP_PORT:-8080}
  case "$port" in '' | *[!0-9]*) fail "APP_PORT must be a number, got '$port'" ;; esac
  tz=${TIMEZONE:-$(detect_timezone)}
  case "$tz" in [A-Za-z]*/[A-Za-z_+-]* | UTC) ;; *) tz="" ;; esac
  umask 077
  tmp=$(mktemp ".env.XXXXXX")
  cdp=""
  if [ "$(uname -s)" = "Darwin" ] && [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
    # The worker and twin Chrome run on this Mac; make up starts both (see the end of this script).
    cdp="http://host.docker.internal:9224"
  fi
  sed -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(random_hex)/" \
    -e "s#^BROWSER_CDP_URL=.*#BROWSER_CDP_URL=$cdp#" .env.example >"$tmp"
  {
    printf '\n# ---- Written by scripts/setup.sh on %s ----\n' "$(date +%Y-%m-%d)"
    if [ "$port" != "8080" ]; then
      printf 'APP_PORT=%s\nBASE_URL=http://127.0.0.1:%s\n' "$port" "$port"
    fi
    if [ -n "${APP_BIND:-}" ]; then printf 'APP_BIND=%s\n' "$APP_BIND"; fi
    if [ -n "${COMPOSE_PROJECT_NAME:-}" ]; then printf 'COMPOSE_PROJECT_NAME=%s\n' "$COMPOSE_PROJECT_NAME"; fi
    if [ -n "$tz" ]; then printf 'TIMEZONE=%s\n' "$tz"; fi
    if [ -n "$cdp" ]; then printf 'BROWSER_TWIN_CDP_URL=http://host.docker.internal:9225\n'; fi
    # Each worker drives its own Chrome tab: a 16 GB Mac ran out of memory with eight (2026-10-26).
    mem_gb=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 17179869184) / 1073741824 ))
    if [ "$mem_gb" -le 16 ]; then
      printf 'NORMAL_WORKERS=3\nSPECIALIST_WORKERS=1\nDISCOVERY_WORKERS=1\n'
    fi
  } >>"$tmp"
  mv "$tmp" .env
  chmod 600 .env
  say "Created .env with a generated database password. Your API keys go in the app (Setup > Connections), not here."
}

if [ ! -f .env ]; then
  create_env
else
  say "Using the existing .env (not modified)."
  if ! grep -Eq '^POSTGRES_PASSWORD=.+' .env; then
    fail ".env has no POSTGRES_PASSWORD. If this computer already has an AXIOM or ApplyPilot database volume, set it to
the password that database was created with (an installation from before this setup took it from the default in its old compose.yaml).
Otherwise add a new one: printf 'POSTGRES_PASSWORD=%s\n' \"\$(openssl rand -hex 24)\" >> .env"
  fi
fi

[ "$env_only" -eq 0 ] || exit 0

command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Install Docker Desktop: https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required ('docker compose'). Update Docker Desktop."
docker info >/dev/null 2>&1 || fail "Docker is installed but not running. Start Docker Desktop, then run this again."

GIT_SHA=${GIT_SHA:-$(git rev-parse --short HEAD 2>/dev/null || echo unknown)}
export GIT_SHA

say "Building and starting AXIOM. The first build downloads about 1 GB and takes several minutes."
if ! docker compose up -d --build --wait --wait-timeout 900; then
  say "" >&2
  say "AXIOM did not become healthy. Last log lines:" >&2
  docker compose logs --tail=40 app db >&2 || true
  say "" >&2
  say "If the error says a port is already allocated, set APP_PORT (and BASE_URL) in .env to a free port." >&2
  say "More help: docs/SETUP.md, Troubleshooting." >&2
  exit 1
fi

port=${APP_PORT:-$(sed -n 's/^APP_PORT=//p' .env | tail -n 1)}
url=${BASE_URL:-$(sed -n 's/^BASE_URL=//p' .env | tail -n 1)}
url=${url:-http://127.0.0.1:${port:-8080}}

if grep -Eq '^BROWSER_CDP_URL=http' .env; then
  # The browsers the workers drive. Both scripts do nothing when their Chrome is already running.
  scripts/chrome-worker.sh >/dev/null 2>&1 || say "Could not start the worker Chrome; run: make chrome"
  scripts/chrome-twin.sh >/dev/null 2>&1 || true
fi

say ""
say "AXIOM is running:  $url"
say "  1. Open that address and create your account."
say "  2. Go to Setup > Connections and paste your AI key (DeepSeek is the cheapest). Test it with the Test button."
say "  3. Go to Setup > Email and connect Gmail with an app password (the README shows how)."
say "  4. Follow the setup guide: resume, contact details, work authorization, the jobs you want."
if ! grep -Eq '^BROWSER_CDP_URL=.+' .env; then
  say "  Note: no Google Chrome was found, so AXIOM uses a built-in headless browser. Many job sites distrust it;"
  say "        install Google Chrome, then run: make chrome  and set BROWSER_CDP_URL in .env (README, Troubleshooting)."
fi
say "Optional self-test, only before you create your account:  make verify"
say "Stop: make down.   Logs: make logs.   Full guide: docs/SETUP.md"
