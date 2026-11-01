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
  # Chrome asks macOS for its "Chrome Safe Storage" key, and that request can put a system dialog on the person's screen, in
  # front of everything, that outlives this Chrome: "A keychain cannot be found to store "Chrome"" with Cancel and Reset To
  # Defaults. Someone clicked that away 42 times on 2026-11-01. Nothing here needs the macOS keychain, so:
  # a profile Chrome has never opened has nothing encrypted with the real key, and stays off the keychain for good (the
  # marker: a later start has to read what this one wrote); and a HOME with no login keychain cannot use the real key anyway.
  # A profile already on the real key keeps it, because switching would make Chrome discard its saved sign-ins.
  [ -e "$PROFILE/Local State" ] || : >"$PROFILE/.no-keychain"
  keychain=""
  if [ -e "$PROFILE/.no-keychain" ]; then
    keychain="--use-mock-keychain"
  elif [ "$(uname -s)" = "Darwin" ] && [ ! -e "$HOME/Library/Keychains/login.keychain-db" ] && [ ! -e "$HOME/Library/Keychains/login.keychain" ]; then
    keychain="--use-mock-keychain"
  fi
  # These flags mirror `jobpilot browser` / `jobpilot twin-browser` in jobpilot/cli.py. The debugging port is bound to
  # this computer only (127.0.0.1); Docker reaches it as host.docker.internal.
  # Nobody is meant to touch this Chrome, so nothing in it may wait for a person, and none of these three reach a page:
  # --hide-crash-restore-bubble: "Restore pages? Chrome didn't shut down correctly" after a hard kill or a power cut.
  # --noerrdialogs: "Your preferences file is corrupt" and "Something went wrong when opening your profile", which block
  #   Chrome, debugging port included, until someone presses OK. Chrome carries on without the damaged file.
  # --deny-permission-prompts: a site's notification or camera request gets a no instead of a bubble nobody answers.
  set -- "$chrome" \
    "--remote-debugging-port=$PORT" \
    "--remote-debugging-address=127.0.0.1" \
    "--user-data-dir=$PROFILE" \
    --no-first-run \
    --no-default-browser-check \
    --hide-crash-restore-bubble \
    --noerrdialogs \
    --deny-permission-prompts \
    --disable-background-timer-throttling \
    --disable-renderer-backgrounding \
    --disable-backgrounding-occluded-windows \
    --renderer-process-limit="${RENDERER_LIMIT:-8}" \
    ${keychain} \
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
