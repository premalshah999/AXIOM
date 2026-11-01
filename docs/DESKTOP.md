# AXIOM as one installed Mac app

Download one file, drag AXIOM to Applications, double-click. No Homebrew, terminal, Docker, Python, repository or browser tab. The window
shows the same dashboard a browser shows; a loopback-only server stays inside it, and the person never sees an address.

It runs the supervisor in `jobpilot/native` ([NATIVE.md](NATIVE.md)), unforked. `scripts/axiom` and a checkout keep working exactly as before.

The disk image is 161 MB and the installed app 436 MB. On this 16 GB Mac, measured beside production with synthetic data: a first start takes about 7 s from the window opening to the dashboard (the
database created), a later start about 6 s, and a Quit with nothing active 3 to 4 s. `jobpilot verify` passes on the installed app.

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
| `Resources/python` | CPython 3.12 from uv's relocatable builds, with every package from `uv.lock` in its `site-packages` | 290 MB |
| ... of which `playwright` | The driver and its Node, which is most of it | 133 MB |
| `Resources/postgres` | PostgreSQL 17 with its libraries, relocated by `desktop/relocate_pg.py` | 69 MB |
| `Resources/tex` | pdfTeX, Metafont and the TeX files they use, by `desktop/bundle_tex.py` | 60 MB |
| `Resources/app` | `jobpilot/`, the built dashboard, the Chrome scripts, licences | 14 MB |
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

**When it stops.** The window tells an ordinary stop (`axiom stop`, a signal from another program) from a failure by the supervisor's own word: phase `stopped` with no reason shows "AXIOM has stopped" and a link to open it again, never "could not start". **An incomplete copy** is caught before anything starts, from `install-manifest.txt`. **Help** has Copy Diagnostics (no keys, addresses or mail) and Check for Updates (asks GitHub for the newest release, only ever opens its page).

**Quit** is `axiom stop`: an alert if applications are in progress, then the same drain, for up to `SHUTDOWN_DRAIN_SECONDS + 30`, with the window saying so. Past the deadline the window closes and the supervisor finishes without it. Closing the window is not quitting.

**If the window dies.** A force-quit or a crash must not leave an engine submitting real applications with nothing on screen, so the supervisor notices within five seconds and drains as usual (`tick_owner`). A window opened meanwhile waits for it, then starts. A checkout install sets no owner, and outlives its terminal.

**Sleep and battery.** The supervisor's `caffeinate` holds idle sleep while applications run and for five minutes after. Nothing holds a closed lid or an empty battery, so the app warns once when it is applying on battery, and the Status menu says so for as long as it lasts.

**Start at login** is one LaunchAgent file, `~/Library/LaunchAgents/app.axiom.desktop.plist`. It needs no signature, follows the app if it moves, and is turned off by deleting it. macOS shows its usual "background item added" notice.

## Chrome

The person's own Google Chrome if it is installed, in `/Applications` or `~/Applications`. Otherwise the first run downloads Chrome for Testing from Google's servers into Application Support, with a progress bar. It needs about 1.5 GB of free disk for a moment, and says so if there is not. If the download fails the window says why and offers Try again.

## Resume tailoring and LaTeX

Tailoring works on a Mac with no LaTeX: `pdflatex` is in the bundle, first on the app's PATH, and adds 60 MB installed (about 22 MB compressed).

`desktop/bundle_tex.py` runs the app's own renderers across every template, font, base size and header with `pdflatex -recorder`, and copies what the `.fls` files say was opened. Then it widens, because the editor also takes hand-written LaTeX: every shape in each font family, all of `tex/latex` and `tex/generic`, and Metafont with the EC and CM sources, since `\\texttt` under T1 has no Type 1 font here and the compiler draws one in its scratch folder. TeX Live finds everything relative to its binary (`$SELFAUTOPARENT`), so no environment is needed; the trees are marked `!!` in `texmf.cnf`, so `ls-R` files are written. The build fails unless the copy, with `PATH=/usr/bin:/bin`, compiles all 92 combinations and opens nothing outside itself.

**All five fonts are offered,** which is more than a TinyTeX checkout today: its TeX has no Charter or Palatino, so those two are greyed out there. They come from TeX Live's package archive, pinned by SHA-256 (`FONT_PACKAGES`). They are font data that pdfTeX reads; nothing in them is run.

**Licences.** `docs/licenses/TeXLive-LICENSE.TL.txt` allows redistribution "with or without modification, for profit or not", asks that a changed distribution says so (`Resources/tex/README.AXIOM`), and forbids claiming copyright on the whole. Each file keeps its own licence (GPL for the binaries, LPPL for LaTeX, GUST and others for fonts). The GPL means the source of those binaries must be available: the README points to TeX Live and offers it on request, so whoever distributes the DMG has to be able to honour that.

**A hand-written `\usepackage` of something not installed** fails with LaTeX's own "not found", exactly as it does on a checkout's TinyTeX: every package that TeX has is bundled, and none is ever downloaded at run time.

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

It needs Command Line Tools, uv, npm, PostgreSQL 17 and TeX Live or TinyTeX on the build machine, and none of them on the Mac that installs the result. It fails if any binary inside links outside the bundle or the system (`otool-proof.txt`), and declares the macOS the strictest binary allows.

**Where PostgreSQL comes from.** `relocate_pg.py` takes the build machine's own Homebrew PostgreSQL, copies it and every library it links, rewrites the links to be relative, and signs them. `otool` cannot show the other half: PostgreSQL finds its `share` and `lib` folders from its compiled-in paths, and falls back to the absolute ones unless its own folder ends in the same tail as the compiled-in bindir. So the layout keeps `Cellar/postgresql@17/<version>/bin`, and the build fails unless `initdb` and `postgres` run under a sandbox that refuses Homebrew. Before that, the app worked here only because this Mac has Homebrew, and could never have started on a clean one. One thing follows from that: its version is whatever that Mac has (17.8 today, while the image has 17.11). EDB's or Zonky's prebuilt binaries are the alternatives: running a downloaded binary was not permitted on this machine, so neither was tried. Zonky's 62 MB archive also lacks `psql`, `pg_isready` and `pg_dump`, which the supervisor and `move-in` use, and carries ICU 68, against production's 78, so it would force an index rebuild on moving in.

## Proving a clean run on a Mac that is not clean

`otool` and a scrubbed PATH were not enough: they passed while `initdb` was reading `/opt/homebrew/share`. What found it is a profile that refuses what a clean Mac would not have, and reports everything else.

Run the installed binary with `env -i` under `sandbox-exec`, with this at its core, and every process it starts inherits it:

```
(allow default)
(allow file-read* (with report))
(deny file-read* file-map-executable process-exec* (subpath "/opt/homebrew") (subpath "/usr/local") (subpath "/Library/Developer") (subpath "$HOME") ...)
(deny process-exec* (literal "/usr/bin/git") (literal "/usr/bin/python3") ...)     ; On a clean Mac these only offer to install the developer tools.
(deny network-outbound) (allow network-outbound (remote ip "localhost:*"))
```

`log stream --predicate 'sender == "Sandbox"'` then shows each read, without root: `fs_usage` and `dtruss` need it. The kernel coalesces repeated reports and may drop some under load, so the list of reads is strong evidence, and the refusal is the proof: a path that is refused cannot be used, whether or not it was reported.

**What this cannot show, and only a second Mac or macOS user can:**

- The real first open: quarantine, Gatekeeper and "Open Anyway". `spctl` here says `rejected`, as the signing section expects, but nobody has walked through it.
- A Mac where these paths are truly absent rather than refused. A program that behaves differently on `ENOENT` than on `EPERM` would not show up. System libraries differ too: this is macOS 15.2 only.
- A real Chrome-for-Testing run. The download is exercised for real and unpacked (367 MB), but running a downloaded binary is not permitted on this machine, and the sandbox has to let the installed Chrome run outside itself.
- Privacy prompts, which are per-user and already answered here for some things; the firewall's prompt, if it is on; WebKit's helpers, which launchd starts outside the profile.

## Parity with the checkout app

Each row was driven in the installed app's own window, through `AXIOM_SHELL_DRIVE`, with `env -i`, a scratch HOME and the refusing profile above, so Python and PostgreSQL could reach only loopback. "Not exercised" means nobody ran it here.

| Feature | Exercised | Not exercised, and why |
|---|---|---|
| Sign-in | The window signs itself in. The API answers 401 without a session and throttles wrong tokens (401, then 429). Signing out signs back in. | |
| Setup guide | Opens on first launch. | Its eleven steps were not walked. |
| Overview, live work | A real run seen while running. | |
| Jobs | Started, Discovered and Reserved render. A pasted link is saved through the dialog's route. | The dialog's own form. |
| Needs you | Renders, empty. | A real question, which needs an employer's form. |
| Applications | All three tabs. CSV saved to disk through a save panel that was really shown, using the page's URL and `download` name. | The page's own link, which appears only with a confirmed non-demo row. A person clicking Save: the driver answers it. |
| Resumes | Upload through a real open panel. PDF.js draws inside the window. Editor page. | Choosing the file by hand: NSOpenPanel cannot be scripted, so the driver hands over the file after the panel is shown. |
| Tailoring | The bundled pdfTeX compiles from the running app with every template and all five fonts, plus hand-written LaTeX. The switch refuses "on" without an approved master, and "off" answers. | The model step, end to end: it needs a key, network and an approved master. |
| Profile | Every tab renders. Photo upload through the panel. | |
| Drag and drop | A file on a pasteboard through WebKit's real drop path reached the page's drop zone. | A physical drag from Finder. |
| Settings | Every tab renders. Telegram, 2Captcha, Serper and DeepSeek save and Test, each answering plainly that it cannot reach the service. TypeSafe's Test only says "Saved", by design. Secrets stay masked. | A Test that succeeds: that needs real keys and network. |
| Gmail | The app-password error, and the sign-in link's plain error without Google client settings. | A real mailbox, by rule. |
| Employer accounts | Route and tab. | A real employer sign-in. |
| Database | Test against a real server and a dead one. Switch to Server, copying data with counts verified, local PostgreSQL stopped, and back to Local. | Supabase: the owner runs that. |
| Themes, Cmd-K | Both themes, through the account menu. The palette, through a real key event. | |
| WebKit gaps | `alert`, `confirm`, `prompt`, clipboard, new-window links, blob downloads. External links reach `NSWorkspace`. | The browser actually opening: the driver records the address instead, so as not to open tabs on this Mac. |
| Chromes | The worker and twin start and are supervised. A synthetic application is submitted through the worker, twice. | A real employer, by rule. |
| Discovery | The route queues 680 sources. | Any board actually fetched: the network is closed on purpose, because a test instance once ran discovery against real boards. |
| Mail polling, receipts | The loop runs with no mailbox. The demo form's receipt is matched. Python has the system's 128 root certificates. | A receipt from a real mailbox. |
| Recovery | The app killed mid-run: the supervisor restarts it, and the run reaches "draft recovery scheduled", with nothing submitted. | |
| Repair | | Code read only: it needs a model key and network. |
| Update path | The app replaced over itself: data, token and ports kept. Restart from Settings. `axiom deploy` is refused by design. | A real newer version with a schema change. |
| Quit, force-quit | Drains in 3 to 5 s, leaving nothing running. A force-quit stops the engine in about 6 s. | Quit from the menu: the driver uses the same path through SIGTERM. |
| Logs, Status menu, Start at Login | All through the real menus' actions, in the scratch HOME. The battery warning fired on real battery. | Login itself: launchd loading the agent. |
| Moving in | A real-schema install: verified, opened held, released, and refused while the old one runs. The real `--plan` read the production folder and rightly reported it blocked. | The real move: the owner runs it. |

**Known flake.** `Page.goto` stalled for 60 s in about one synthetic run in five, in this test setup. Chrome relaunched itself in about one launch in four here (flags intact), which may be why, though the cause was not proved; a scratch HOME also makes it install its updater, which a real HOME would not. Such a run ends `waiting_browser` and retries; nothing is sent. The supervisor stops a relaunched Chrome at Quit, and at the next start.

## macOS 15 minimum

The window asks for 13, Python and pdfTeX for 11. Only PostgreSQL and the libraries it came with say 15.0, because that is what Homebrew's bottle for this Mac was built for. `build.sh` declares what the strictest binary allows.

**It does not matter much.** The app is arm64 only, and every Apple Silicon Mac can run macOS 15, so nobody is locked out by hardware, only by not having updated; macOS 26 is current.

What would lower it, none of it needing a downloaded binary to be run on the build machine beyond what is noted:

1. **`vtool -set-build-version macos 13.0 15.0`** on the bundled PostgreSQL. Nothing is downloaded, but it only changes a claim: a symbol the build took from macOS 15 would still fail at load, on a Mac we do not have. `MacOSX13.3.sdk` is installed here, so its `.tbd` files could check the imported symbols statically first. This is the cheapest, and needs testing on a real older Mac.
2. **Homebrew's older bottles** (`brew fetch --bottle-tag=arm64_sonoma postgresql@17`, and each library), which say 14. Those are downloaded binaries that the relocator's proof would run, so that needs the owner's permission.
3. **Building PostgreSQL, ICU and OpenSSL from source** with `MACOSX_DEPLOYMENT_TARGET=13.0`. This reaches the lowest version and is the most work, and running their build scripts is also running downloaded code.

## Signing: what an Apple Developer ID adds

**Today: ad-hoc signed.** Apple Silicon requires a signature and this satisfies it, but it says nothing about who made the app. So after downloading, on macOS 15:

1. Double-clicking AXIOM says "Apple could not verify 'AXIOM' is free of malware", with only Done and Move to Trash. Control-click, Open no longer works on macOS 15.
2. The person opens System Settings, Privacy & Security, scrolls to "AXIOM was blocked", clicks Open Anyway and enters their password.
3. They open AXIOM again and confirm once more. It is remembered after that.

The same steps, and a message to paste to a friend, are in [FRIENDS.md](FRIENDS.md); the disk image carries them as `Read me first.txt`.

Someone comfortable in a terminal can skip that with `xattr -dr com.apple.quarantine /Applications/AXIOM.app`. Opened from the disk image rather than Applications, macOS runs it from a temporary read-only path, and the app says to move it first.

**With a Developer ID** (the Apple Developer Program, US$99 a year), the first open is one ordinary "downloaded from the internet" prompt naming the developer, and nothing in System Settings. It takes:

- The membership, and a **Developer ID Application** certificate in the build Mac's keychain. Only the account holder can create these, so the owner must supply them.
- `xcrun notarytool store-credentials axiom` once, with an app-specific password. `notarytool` and `stapler` are in Command Line Tools; Xcode is not needed.
- `AXIOM_SIGN_ID="Developer ID Application: NAME (TEAMID)" AXIOM_NOTARY_PROFILE=axiom desktop/build.sh`, which signs every binary with the hardened runtime and a timestamp, submits to Apple, and staples the ticket so that it opens offline too.

That path is written and has never run, because it needs the account. Expect to adjust `desktop/entitlements.plist`. It allows the JIT for Playwright's Node; the rest of the bundle should need nothing, since every library carries the same signature.
