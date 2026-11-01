#!/bin/zsh
# usage: go.sh <worktree> <site> <tag> [--dry]    One run in a fresh data directory; prints the RESULT line.
WT=$1; SITE=$2; TAG=$3; shift 3
OUT=${BENCH_OUT:-/tmp/page_speed_bench}; rm -rf $OUT/$TAG; mkdir -p $OUT
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=en_US.UTF-8 TZ=UTC PYTHONDONTWRITEBYTECODE=1 ENABLE_WORKERS=false \
  PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-}" CHROMIUM_PATH="${CHROMIUM_PATH:-}" PYTHONPATH=$WT \
  taskpolicy -b ${BENCH_PY:-$HOME/Desktop/applypilot/axiom/.venv/bin/python} -I $WT/scripts/bench/page_speed/run.py $SITE $OUT/$TAG "$@" 2>&1 | grep -E "^RESULT|Traceback|Error" | tail -5
