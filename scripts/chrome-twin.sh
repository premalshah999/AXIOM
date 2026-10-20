#!/bin/sh
# Start the twin Chrome (default port 9225): a second, clean Chrome that mints hCaptcha tokens for iCIMS-style sites.
# Playwright must never attach to it, because an automation-attached browser is exactly what hCaptcha distrusts.
# It starts with no window and opens a minimized one per token.
# Then set BROWSER_TWIN_CDP_URL=http://host.docker.internal:9225 in .env and restart the app.
#
#   scripts/chrome-twin.sh               start it (does nothing if it is already running)
#   DRY_RUN=1 scripts/chrome-twin.sh     print the Chrome command instead
#   CHROME_TWIN_PORT, CHROME_TWIN_PROFILE, CHROME_BIN override the port, profile folder and Chrome binary.
set -eu
cd "$(dirname "$0")/.."
LABEL="Twin Chrome"
PORT=${CHROME_TWIN_PORT:-9225}
# The same folder `jobpilot twin-browser` uses.
PROFILE=${CHROME_TWIN_PROFILE:-$PWD/.data/twin-browser}
EXTRA_FLAGS="--no-startup-window --disk-cache-size=134217728"
. scripts/lib/chrome.sh
launch_chrome
