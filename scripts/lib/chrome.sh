# Shared by chrome-worker.sh and chrome-twin.sh (sourced, not executed).
# Needs PORT, PROFILE and LABEL set by the caller; EXTRA_FLAGS may add flags.
# DRY_RUN=1 prints the command instead of starting Chrome.

find_chrome() {
  if [ -n "${CHROME_BIN:-}" ]; then printf '%s' "$CHROME_BIN"; return 0; fi
  mac="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  if [ -x "$mac" ]; then printf '%s' "$mac"; return 0; fi
  for name in google-chrome google-chrome-stable chromium chromium-browser; do
    if command -v "$name" >/dev/null 2>&1; then command -v "$name"; return 0; fi
  done
  return 1
}

# Already listening? Then leave it alone: never start a second Chrome on a port that is in use.
chrome_running() {
  curl -fsS -m 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1
}

launch_chrome() {
  if chrome_running; then
    echo "$LABEL is already running on port $PORT. Nothing to do."
    return 0
  fi
  chrome=$(find_chrome) || { echo "error: Google Chrome was not found. Install it, or set CHROME_BIN." >&2; return 1; }
  mkdir -p "$PROFILE"
  chmod 700 "$PROFILE" 2>/dev/null || true
  # These flags mirror `jobpilot browser` / `jobpilot twin-browser` in jobpilot/cli.py. The debugging port is bound to
  # this computer only (127.0.0.1); Docker reaches it as host.docker.internal.
  set -- "$chrome" \
    "--remote-debugging-port=$PORT" \
    "--remote-debugging-address=127.0.0.1" \
    "--user-data-dir=$PROFILE" \
    --no-first-run \
    --no-default-browser-check \
    --disable-background-timer-throttling \
    --disable-renderer-backgrounding \
    --disable-backgrounding-occluded-windows \
    ${EXTRA_FLAGS}
  if [ "${DRY_RUN:-0}" = "1" ]; then
    printf '%s\n' "$@"
    return 0
  fi
  nohup "$@" >/dev/null 2>&1 &
  i=0
  while [ "$i" -lt 30 ]; do
    if chrome_running; then
      echo "$LABEL started on port $PORT (profile: $PROFILE). Leave it open while applications run."
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  echo "error: Chrome did not open its debugging port $PORT within 30 seconds." >&2
  return 1
}
