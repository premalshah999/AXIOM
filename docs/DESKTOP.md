# AXIOM as one installed Mac app

Download one file, drag AXIOM to Applications, double-click. No Homebrew, terminal, Docker, Python, repository or browser tab. The window
shows the same dashboard a browser shows; a loopback-only server stays inside it, and the person never sees an address.

It runs the supervisor in `jobpilot/native` ([NATIVE.md](NATIVE.md)), unforked. `scripts/axiom` and a checkout keep working exactly as before.

The disk image is 126 MB and the installed app 374 MB. On this 16 GB Mac, measured beside production with synthetic data: a first start takes 7.6 s from the window opening to the dashboard (the
database created, 3.4 s to the supervisor's "up"), a later start 5.5 s, and a Quit with nothing active 3 s.

| At idle, resident | MB |
|---|---|
| Window, plus WebKit's three helpers | 87 + 140 |
| Supervisor | 16 |
| App | 77 |
| PostgreSQL (10 processes, shared pages counted in each) | 85 |
| Worker Chrome, about:blank (4 to 8 processes) | 420 to 640 |
| Twin Chrome (4 to 6 processes) | 320 to 400 |

## What is inside

| Inside `AXIOM.app/Contents` | What | Size |
|---|---|---|
| `MacOS/AXIOM` | The window (`desktop/AXIOM.m`) | 150 KB |
| `Resources/python` | CPython 3.12 from uv's relocatable builds, with every package from `uv.lock` in its `site-packages` | 289 MB |
| ... of which `playwright` | The driver and its Node, which is most of it | 133 MB |
| `Resources/postgres` | PostgreSQL 17 with its libraries, relocated by `desktop/relocate_pg.py` | 69 MB |
| `Resources/app` | `jobpilot/`, the built dashboard, the Chrome scripts, licences | 12 MB |
| `Resources/bin/axiom` | `scripts/axiom` for a bundle: `status`, `logs`, `psql`, `hold`, `jobpilot verify`, `move-in` | |

There is no virtualenv. uv's Python needs no fixing up to be moved, and packages installed straight into it have no absolute paths to go stale,
which `uv venv --relocatable` would still leave in its activation scripts and console entry points. `build.sh` compiles every `.py` ahead of time, because a
signed bundle must not be written to.

## Why this shell

| | This (Objective-C + the system's WKWebView) | Electron |
|---|---|---|
| In the bundle | 150 KB | Chromium and Node: over 200 MB (Electron's published size, not measured here) |
| Memory | The window and WebKit's three helpers: about 225 MB resident at idle, showing the dashboard | A second Chromium beside the worker Chrome's nine tabs, on a 16 GB Mac already short of memory |
| Toolchain | `clang`, from Command Line Tools | Node and a packager |

Objective-C rather than Swift only because Swift cannot import Cocoa on Command Line Tools 16.2: both `usr/include/swift/module.modulemap` and `bridging.modulemap`
define `SwiftBridging`, and the toolchain's fix needs `sudo`. The binary builds with `clang` in about a second.

## Where things are

| | |
|---|---|
| Config and generated secrets | `~/Library/Application Support/AXIOM/.env`, 0600, created on first run and never replaced. The database password is what the cluster was created with. |
| Data | `~/Library/Application Support/AXIOM/data`, 0700: the database, runs, resumes, Chrome profiles, the access token |
| Logs | `~/Library/Logs/AXIOM`: `window.log`, `supervisor.log`, `app.log`, `postgres.log`, capped as under [NATIVE.md](NATIVE.md) |
| Ports | `~/Library/Application Support/AXIOM/ports.json`: free loopback ports, the same from one start to the next when they can be, because the page's preferences (theme, the guide's "later") belong to its address. Never 8080, 9224, 9225, 9339 or 54329, so that an app beside a checkout cannot take what that install needs. |
| Web view's own data | `~/Library/WebKit/app.axiom.desktop`: the session cookie and preferences only |

Nothing is written inside the bundle, so an upgrade is replacing it. Everything an install has is in those two Library folders.

## How a start goes

The window starts `python -m jobpilot.native.desktop boot`, which writes `.env` and picks ports if this is the first run, finds Chrome, points the supervisor's `current`
release at the bundle, and becomes the supervisor with `exec`. The window watches two small files, `boot.json` until it is running and `state.json` after. There is no socket or API between the two, so
the window can never be what an application waits on.

**No sign-in wall.** The window posts the access token to `/api/login` itself and puts the session cookie in the web view's store. The token never reaches the page, and the API's token check is unchanged:
without a session `/api/snapshot` answers 401. If the dashboard reports that its session ended, the window signs in again. First launch opens the setup guide, because nothing about it has changed: a new installation has an unfinished onboarding.

**Links.** The window shows the app and nothing else. A link to an employer's page opens in the person's own browser, and a frame from anywhere else does not load.

**Quit** is `axiom stop`: an alert if applications are in progress, then the same drain, for up to `SHUTDOWN_DRAIN_SECONDS + 30`, with the window saying so. Past the deadline the window closes and the supervisor finishes without it. Closing the window is not quitting.

**If the window dies.** A force-quit or a crash must not leave an engine submitting real applications with nothing on screen, so the supervisor notices within five seconds and drains as usual (`tick_owner`). A window opened meanwhile waits for it, then starts. A checkout install sets no owner, and outlives its terminal.

**Sleep and battery.** The supervisor's `caffeinate` holds idle sleep while applications run and for five minutes after. Nothing holds a closed lid or an empty battery, so the app warns once when it is applying on battery, and the Status menu says so for as long as it lasts.

**Start at login** is one LaunchAgent file, `~/Library/LaunchAgents/app.axiom.desktop.plist`. It needs no signature, follows the app if it moves, and is turned off by deleting it. macOS shows its usual "background item added" notice.

## Chrome

The person's own Google Chrome if it is installed, in `/Applications` or `~/Applications`. Otherwise the first run downloads Chrome for Testing from Google's servers into Application Support, with a progress bar. It needs about 1.5 GB of free disk for a moment, and says so if there is not. If the download fails the window says why and offers Try again.

## Resume tailoring and LaTeX

**Not bundled, and the app says so.** Tailoring runs `pdflatex`. On a clean Mac it is unavailable: the Status menu says "Resume tailoring is off: LaTeX is not installed", tailoring reports `compiler_missing`, and applications go on with the uploaded resume. If the person has installed MacTeX, BasicTeX or TinyTeX, the app finds it (`/Library/TeX/texbin`, `~/Library/TinyTeX`).

`tectonic` (about 20 MB compressed) is the lightest candidate and has not been tried here, because running a downloaded binary was not permitted on this build machine. Two things would need testing before bundling it. It runs XeTeX rather than pdfTeX, so a template using `\pdfgentounicode` or pdfTeX-only fonts may not compile. And it fetches its packages at first use from a third-party server, so offline tailoring would need a seeded cache.

## Upgrades

Drag the new version over the old one. All data stays, because none of it is in the bundle, and the schema migrates when the app starts, as it does in a deploy. The next major PostgreSQL would need `pg_upgrade`: a cluster of another major version is refused with a message and never touched.

If a build ever changes the ICU library, indexes on text may need rebuilding. `postgres.log` warns of a collation mismatch, and `movein.refresh_collation` is the fix PostgreSQL's warning prescribes. The bundled ICU is the production cluster's today, so moving in needs no rebuild.

## Moving in from a checkout

The source is only ever read, and is what you roll back to.

```sh
AXIOM=/Applications/AXIOM.app/Contents/Resources/bin/axiom
scripts/axiom stop                                  # In the checkout. It refuses to copy a live database or Chrome profile.
"$AXIOM" move-in --from ~/path/to/checkout --plan   # Writes nothing. Says what fits and what is in the way.
"$AXIOM" move-in --from ~/path/to/checkout          # Add --replace if the app has already been opened: what is there is renamed aside.
open /Applications/AXIOM.app                        # A hold is on: nothing applies yet. Look around.
"$AXIOM" hold off                                   # Or Settings.
```

On one volume every file is an APFS clone, so it takes almost no space however much there is. It stops at 2 GB free. Proof at the end: every entry's path, size, modification time and link target match; every database file and key is hashed on both sides, and a sample of the rest; the bundled PostgreSQL opens the copy and counts every table, before and after the two changes the move makes (queued runs' resume paths, and the hold); and the source's listing is compared with the one taken at the start. The report is `data/migration/move-in.json`.

`.env` comes along with where-things-were commented out, because a stale port could point at the old installation's. The worker and twin Chrome profiles come too, so their sign-ins carry over.

**Do not run both.** Two databases, each believing it alone applies to these jobs, would send every application twice. The app refuses to start while the installation it was moved from is running; nothing stops the reverse, so do not `scripts/axiom start` the old one again unless you are rolling back. To roll back: quit the app, then `scripts/axiom start` in the checkout, as long as the app has sent nothing. After that, the old database does not know what the app sent.

## Building

```sh
desktop/build.sh          # desktop/build/AXIOM.app and AXIOM.dmg. About 1.5 GB while it runs.
```

It needs Command Line Tools, uv, npm and PostgreSQL 17 on the build machine, and none of them on the Mac that installs the result. It fails if any binary inside links outside the bundle or the system (`otool-proof.txt`), and declares the macOS the strictest binary allows.

**Where PostgreSQL comes from.** `relocate_pg.py` takes the build machine's own Homebrew PostgreSQL, copies it and every library it links, rewrites the links to be relative, and signs them. The result has no link to Homebrew. Two things follow from Homebrew's build: the app's minimum is macOS 15, because that is what the bottle was built for, and its version is whatever that Mac has (17.8 today, while the image has 17.11). EDB's or Zonky's prebuilt binaries are the alternatives: running a downloaded binary was not permitted on this machine, so neither was tried. Zonky's 62 MB archive also lacks `psql`, `pg_isready` and `pg_dump`, which the supervisor and `move-in` use, and carries ICU 68, against production's 78, so it would force an index rebuild on moving in.

## Signing: what an Apple Developer ID adds

**Today: ad-hoc signed.** Apple Silicon requires a signature and this satisfies it, but it says nothing about who made the app. So after downloading, on macOS 15:

1. Double-clicking AXIOM says "Apple could not verify 'AXIOM' is free of malware", with only Done and Move to Trash. Control-click, Open no longer works on macOS 15.
2. The person opens System Settings, Privacy & Security, scrolls to "AXIOM was blocked", clicks Open Anyway and enters their password.
3. They open AXIOM again and confirm once more. It is remembered after that.

Someone comfortable in a terminal can skip that with `xattr -dr com.apple.quarantine /Applications/AXIOM.app`. Opened from the disk image rather than Applications, macOS runs it from a temporary read-only path, and the app says to move it first.

**With a Developer ID** (the Apple Developer Program, US$99 a year), the first open is one ordinary "downloaded from the internet" prompt naming the developer, and nothing in System Settings. It takes:

- The membership, and a **Developer ID Application** certificate in the build Mac's keychain. Only the account holder can create these, so the owner must supply them.
- `xcrun notarytool store-credentials axiom` once, with an app-specific password. `notarytool` and `stapler` are in Command Line Tools; Xcode is not needed.
- `AXIOM_SIGN_ID="Developer ID Application: NAME (TEAMID)" AXIOM_NOTARY_PROFILE=axiom desktop/build.sh`, which signs every binary with the hardened runtime and a timestamp, submits to Apple, and staples the ticket so that it opens offline too.

That path is written and has never run, because it needs the account. Expect to adjust `desktop/entitlements.plist`. It allows the JIT for Playwright's Node; the rest of the bundle should need nothing, since every library carries the same signature.
