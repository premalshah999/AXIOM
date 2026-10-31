#!/bin/sh
# Build AXIOM.app and AXIOM.dmg: everything a clean Mac needs, in one file (docs/DESKTOP.md).
#
#   desktop/build.sh                  everything, into desktop/build
#   AXIOM_BUILD_DIR=/path             build there instead (it needs about 1.5 GB)
#   AXIOM_SIGN_ID="Developer ID Application: ..."   sign for distribution (default: ad hoc)
#   AXIOM_NOTARY_PROFILE=name         then notarize, with credentials stored by `xcrun notarytool store-credentials`
#   AXIOM_PG_PREFIX=/path             the PostgreSQL 17 to relocate (default: brew --prefix postgresql@17)
#
# Needs a build machine with Command Line Tools, uv, npm and PostgreSQL 17. The Mac that installs the result needs none of them.
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
build=${AXIOM_BUILD_DIR:-$root/desktop/build}
sign_id=${AXIOM_SIGN_ID:--}
python_series=3.12

say() { printf '== %s\n' "$*"; }
fail() { printf 'build: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] || fail "this builds a Mac app, on a Mac"
[ "$(uname -m)" = arm64 ] || fail "arm64 only for now"
for tool in uv npm clang hdiutil iconutil codesign install_name_tool otool ditto; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is needed to build"
done
pg_prefix=${AXIOM_PG_PREFIX:-$(brew --prefix postgresql@17 2>/dev/null || true)}
[ -x "$pg_prefix/bin/postgres" ] || fail "PostgreSQL 17 was not found. Set AXIOM_PG_PREFIX, or: brew install postgresql@17"

app="$build/AXIOM.app"
contents="$app/Contents"
resources="$contents/Resources"
rm -rf "$build/AXIOM.app" "$build/dmg" "$build/AXIOM.dmg"
mkdir -p "$contents/MacOS" "$resources" "$build"

say "Python $python_series (relocatable, from uv)"
uv python install "$python_series" >/dev/null
base=$(uv python find --managed-python "$python_series")
base=$(CDPATH='' cd -- "$(dirname -- "$base")/.." && pwd -P)
ditto "$base" "$resources/python"
# uv stamps its own install path on the library's name. Nothing loads it by that name, but nothing here may carry it either.
install_name_tool -id "@rpath/libpython$python_series.dylib" "$resources/python/lib/libpython$python_series.dylib"
codesign --force --sign - "$resources/python/lib/libpython$python_series.dylib"
py="$resources/python/bin/python3"
sp="$resources/python/lib/python$python_series/site-packages"
stdlib="$resources/python/lib/python$python_series"
rm -f "$stdlib/EXTERNALLY-MANAGED" "$resources/python/BUILD"
# What an app with no terminal and no GUI toolkit of Python's own never loads.
rm -rf "$stdlib/test" "$stdlib/idlelib" "$stdlib/tkinter" "$stdlib/turtledemo" "$stdlib/ensurepip" "$stdlib/pydoc_data" \
  "$stdlib/lib2to3" "$resources/python/include" "$resources/python/share" "$resources"/python/lib/tcl* \
  "$resources"/python/lib/tk* "$resources"/python/lib/itcl* "$resources"/python/lib/thread* "$resources/python/lib/pkgconfig" \
  "$stdlib"/lib-dynload/_tkinter*
rm -f "$resources"/python/bin/idle* "$resources"/python/bin/2to3* "$resources"/python/bin/pip* "$resources"/python/bin/pydoc* \
  "$resources"/python/bin/python*-config

say "Python packages, exactly as locked"
(cd "$root" && uv export --frozen --no-dev --no-emit-project --format requirements-txt -o "$build/requirements.txt" >/dev/null)
uv pip install --python "$py" --no-deps --require-hashes --link-mode=copy --only-binary :all: --break-system-packages \
  -r "$build/requirements.txt" >/dev/null 2>"$build/pip.log" || { cat "$build/pip.log" >&2; fail "installing packages failed"; }
rm -rf "$sp/pip" "$sp"/pip-*.dist-info "$resources"/python/bin/[!p]*
# These only serve development.
find "$sp" -type d \( -name tests -o -name test \) -not -path '*/playwright/driver/*' -prune -exec rm -rf {} + 2>/dev/null || true
find "$sp" -type f \( -name '*.pyi' -o -name '*.c' -o -name '*.h' -o -name '*.pxd' \) -delete

say "PostgreSQL 17 (relocated)"
rm -rf "$resources/postgres"
"$py" "$root/desktop/relocate_pg.py" "$pg_prefix" "$resources/postgres"

say "The dashboard"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$root" "$py" -c "
import sys
from pathlib import Path
from jobpilot.native.release import build_dashboard
build_dashboard(Path(sys.argv[1]))" "$root"
[ -f "$root/jobpilot/static/index.html" ] || fail "the dashboard did not build"

say "The app"
mkdir -p "$resources/app/scripts/lib" "$resources/app/docs"
ditto "$root/jobpilot" "$resources/app/jobpilot"
find "$resources/app" -type d -name __pycache__ -prune -exec rm -rf {} +
cp "$root/scripts/chrome-worker.sh" "$root/scripts/chrome-twin.sh" "$resources/app/scripts/"
cp "$root/scripts/lib/chrome.sh" "$resources/app/scripts/lib/"
# The same licence files the image ships: AGPL for this code, and what the dashboard and the runtime bundle.
cp "$root/LICENSE" "$resources/app/LICENSE"
cp "$root/docs/third-party.md" "$resources/app/docs/third-party.md"
ditto "$root/docs/licenses" "$resources/app/docs/licenses"
mkdir -p "$resources/licenses"
for file in "$pg_prefix"/COPYRIGHT "$pg_prefix"/share/doc/postgresql*/COPYRIGHT; do
  [ -f "$file" ] && cp "$file" "$resources/licenses/PostgreSQL-COPYRIGHT.txt" && break
done
[ -f "$base/lib/python$python_series/LICENSE.txt" ] && cp "$base/lib/python$python_series/LICENSE.txt" "$resources/licenses/Python-LICENSE.txt"

# `axiom status`, `jobpilot verify`, `move-in`: the same commands as a checkout's scripts/axiom, for anyone with a terminal.
mkdir -p "$resources/bin"
cat >"$resources/bin/axiom" <<'WRAPPER'
#!/bin/sh
here=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$here" || exit 1
HOME=${HOME:?} AXIOM_BUNDLE="$here" PYTHONPATH="$here/app" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONSAFEPATH=1 \
  exec "$here/python/bin/python3" -m jobpilot.native.desktop "$@"
WRAPPER
chmod +x "$resources/bin/axiom"

say "Compiling Python ahead of time"
# A signed bundle must not be written to, so nothing is compiled at run time. Hash-based and unchecked: dragging the app
# changes file times, and a time-based .pyc would then be ignored and compiled again on every start.
PYTHONDONTWRITEBYTECODE=1 "$py" -m compileall -q -j 0 --invalidation-mode unchecked-hash "$stdlib" "$resources/app/jobpilot" >"$build/compile.log" 2>&1 \
  || { grep -v '^$' "$build/compile.log" | tail -5 >&2; printf 'build: note: some files did not compile; see %s\n' "$build/compile.log" >&2; }

say "The window"
clang -O2 -fobjc-arc -Wall -Werror -mmacosx-version-min=13.0 -arch arm64 -framework Cocoa -framework WebKit \
  "$root/desktop/AXIOM.m" -o "$contents/MacOS/AXIOM"

say "The icon"
clang -O2 -fobjc-arc -mmacosx-version-min=13.0 -arch arm64 -framework Cocoa "$root/desktop/make_icon.m" -o "$build/make_icon"
rm -rf "$build/AppIcon.iconset"
"$build/make_icon" "$root/frontend/src/shell/favicon.svg" "$build/AppIcon.iconset"
iconutil -c icns "$build/AppIcon.iconset" -o "$resources/AppIcon.icns"
rm -rf "$build/AppIcon.iconset" "$build/make_icon"

say "Proving nothing links outside the bundle"
proof="$build/otool-proof.txt"
: >"$proof"
minimum=13.0
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$root/desktop" "$py" -c "
import os, sys
from relocate_pg import is_macho
for folder, _, files in os.walk(sys.argv[1]):
    for name in files:
        path = os.path.join(folder, name)
        if not os.path.islink(path) and is_macho(path):
            print(path)" "$app" >"$build/machos.txt"
while IFS= read -r binary; do
  printf '%s\n' "$binary" >>"$proof"
  otool -L "$binary" | tail -n +2 >>"$proof"
  seen=$(otool -l "$binary" | sed -n 's/^ *minos //p' | head -1)
  # The app can only claim what its strictest binary allows.
  if [ -n "$seen" ] && [ "$(printf '%s\n%s\n' "$minimum" "$seen" | sort -t. -k1,1n -k2,2n | tail -1)" = "$seen" ]; then minimum=$seen; fi
done <"$build/machos.txt"
if grep -E '^[[:space:]]*(/opt/homebrew|/usr/local|/opt/anaconda|/Users/|/Library/)' "$proof"; then
  fail "the lines above link outside the bundle"
fi

version=$(sed -n 's/^version = "\(.*\)"/\1/p' "$root/pyproject.toml" | head -1)
sha=$(git -C "$root" rev-parse --short HEAD 2>/dev/null || echo unknown)
sed -e "s/@VERSION@/$version/" -e "s/@BUILD@/$sha/" -e "s/@MINIMUM_MACOS@/$minimum/" "$root/desktop/Info.plist" >"$contents/Info.plist"
printf 'APPL????' >"$contents/PkgInfo"

say "Signing (identity: $sign_id)"
# Notarization requires the hardened runtime and a timestamp. Node's JIT (Playwright's driver) needs the entitlements.
hardened=""
[ "$sign_id" = "-" ] || hardened="--timestamp --options runtime --entitlements $root/desktop/entitlements.plist"
# Inside out, each binary on its own: --deep does not reach code under Resources.
grep -v "/Contents/MacOS/AXIOM$" "$build/machos.txt" | tr '\n' '\0' | xargs -0 -n 40 codesign --force --sign "$sign_id" $hardened
codesign --force --sign "$sign_id" $hardened "$app"
codesign --verify --deep --strict "$app"

say "The disk image"
mkdir -p "$build/dmg"
ditto "$app" "$build/dmg/AXIOM.app"
ln -s /Applications "$build/dmg/Applications"
hdiutil create -quiet -volname AXIOM -srcfolder "$build/dmg" -ov -format ULMO "$build/AXIOM.dmg"
rm -rf "$build/dmg"
if [ "$sign_id" != "-" ]; then
  codesign --force --sign "$sign_id" --timestamp "$build/AXIOM.dmg"
  if [ -n "${AXIOM_NOTARY_PROFILE:-}" ]; then
    xcrun notarytool submit "$build/AXIOM.dmg" --keychain-profile "$AXIOM_NOTARY_PROFILE" --wait
    xcrun stapler staple "$build/AXIOM.dmg"
  fi
fi

say "Done"
printf 'App   %s (%s)\n' "$app" "$(du -sh "$app" | cut -f1)"
printf 'Image %s (%s)\n' "$build/AXIOM.dmg" "$(du -sh "$build/AXIOM.dmg" | cut -f1)"
printf 'macOS %s or later (from the strictest binary inside)\n' "$minimum"
