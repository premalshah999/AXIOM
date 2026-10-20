#!/bin/sh
# Run pytest with one machine-wide lock, at background priority.
#
# Several worktrees are tested on one 16 GB Mac. Browser suites run in parallel once exhausted its memory and
# starved the production workers (2026-10-18), so every pytest run here waits for the previous one to finish.
#
# usage (from a worktree root): scripts/dev/pytest-locked.sh tests/test_x.py -q
# The worktree's own virtualenv (.venv, created with `uv sync --frozen`) is used unless UV_PROJECT_ENVIRONMENT is set.
LOCK="${APPLYPILOT_LOCK_DIR:-$HOME/.cache/applypilot}/pytest.lock"
mkdir -p "$(dirname "$LOCK")"
waited=0
while ! mkdir "$LOCK" 2>/dev/null; do
  # A run that died without releasing the lock frees it after 45 minutes.
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +45 2>/dev/null)" ]; then
    rm -rf "$LOCK"
    continue
  fi
  if [ "$waited" -eq 0 ]; then
    echo "pytest-locked: waiting for $(cat "$LOCK/owner" 2>/dev/null || echo 'another run')" >&2
  fi
  waited=1
  sleep 5
done
trap 'rm -rf "$LOCK"' EXIT INT TERM
printf '%s (%s)\n' "$PWD" "$$" > "$LOCK/owner"
UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$PWD/.venv}" taskpolicy -b nice -n 19 uv run --frozen python -m pytest "$@"
