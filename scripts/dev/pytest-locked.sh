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
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$PWD/.venv}"

# The whole non-browser suite (no test path given, `-m "not browser"`) runs in PYTEST_WORKERS processes (default 4,
# 0 = off): every test has its own temp database, so files are independent, and 2,376 tests went from 506 s to about
# 150 s (2026-10-29). Four workers use about 1.5 GB and leave cores to production; browser runs and targeted runs
# stay in one process (a browser per worker is the memory incident above; xdist start-up costs more than a few files).
workers="${PYTEST_WORKERS:-4}"
whole=0
case " $* " in *"not browser"*) whole=1 ;; esac
for arg in "$@"; do
  case "$arg" in
    -n | -n* | --numprocesses* | *.py | *::* | tests | tests/ | tests/*) whole=0 ;;
  esac
done
if [ "$whole" -eq 1 ] && [ "$workers" -gt 0 ] 2>/dev/null &&
  taskpolicy -b nice -n 19 uv run --frozen python -c "import xdist" 2>/dev/null; then
  set -- -n "$workers" --dist loadfile "$@"
fi
taskpolicy -b nice -n 19 uv run --frozen python -m pytest "$@"
