#!/bin/sh
# The test gate, in tiers. The exit status is pytest's own: nothing here pipes it, and neither may a caller.
#
#   scripts/dev/gate.sh fast      every test that needs no browser, four processes, about 3 minutes
#   scripts/dev/gate.sh gallery   the real traps through the real engine in a real browser (tests/gallery), about 2 minutes
#   scripts/dev/gate.sh smoke     everything listed in tests/smoke_tests.txt, browser or not: the guards on duplicate sends, wrong
#                                 jobs, consent, mail and false confirmations, the mail, gallery and Aggressive flows, and one or two
#                                 tests per platform
#   scripts/dev/gate.sh release   fast, then gallery, then the browser tests of the smoke list that those two haven't run: what a
#                                 release has to pass
#   scripts/dev/gate.sh browser   every browser test, one at a time, for changes to the engine: long, run it on mains power,
#                                 and read the slowest-tests table it ends with (the full run is not part of every release)
#
# Through pytest-locked.sh, at background priority, like every test run on this machine.
set -u
root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$root" || exit 2

fast() { scripts/dev/pytest-locked.sh -m "not browser" -q -p no:cacheprovider "$@"; }
gallery() { scripts/dev/pytest-locked.sh tests/test_gallery.py -q -p no:cacheprovider "$@"; }
smoke() { scripts/dev/pytest-locked.sh -m smoke -q -p no:cacheprovider --durations=15 --durations-min=1 "$@"; }
smoke_browser() { scripts/dev/pytest-locked.sh -m "browser and smoke and not gallery" -q -p no:cacheprovider --durations=15 --durations-min=1 "$@"; }
browser() { scripts/dev/pytest-locked.sh -m "browser and not gallery" -q -p no:cacheprovider --durations=40 --durations-min=1 "$@"; }

tier=${1:-release}
[ "$#" -gt 0 ] && shift
case "$tier" in
  fast) fast "$@" ;;
  gallery) gallery "$@" ;;
  smoke) smoke "$@" ;;
  browser) browser "$@" ;;
  release)
    fast "$@" || exit $?
    gallery "$@" || exit $?
    smoke_browser "$@"
    ;;
  *)
    echo "usage: gate.sh fast|gallery|smoke|release|browser [pytest options]" >&2
    exit 2
    ;;
esac
