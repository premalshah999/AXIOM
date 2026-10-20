#!/bin/sh
# Start the worker Chrome that AXIOM's ATS and specialist workers drive over the DevTools port (default 9224).
# Run it on your computer, not in Docker, and leave it open while applications run.
# Then set BROWSER_CDP_URL=http://host.docker.internal:9224 in .env and restart the app (docker compose up -d).
#
#   scripts/chrome-worker.sh               start it (does nothing if it is already running)
#   DRY_RUN=1 scripts/chrome-worker.sh     print the Chrome command instead
#   CHROME_WORKER_PORT, CHROME_WORKER_PROFILE, CHROME_BIN override the port, profile folder and Chrome binary.
set -eu
cd "$(dirname "$0")/.."
LABEL="Worker Chrome"
PORT=${CHROME_WORKER_PORT:-9224}
# The same folder `jobpilot browser` uses, so an existing profile (and its sign-ins) keeps working.
PROFILE=${CHROME_WORKER_PROFILE:-$PWD/.data/desktop-browser}
# A capped disk cache: an unbounded one grew to about 1 GB and slowed page loads.
EXTRA_FLAGS="--disk-cache-size=268435456 about:blank"
. scripts/lib/chrome.sh
launch_chrome
