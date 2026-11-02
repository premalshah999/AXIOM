#!/usr/bin/env python3
"""Ship the Mac app: gate, build, prove a copy, drain, swap, prove the running app, and undo all of it if any of that fails.

    python3 scripts/release.py                    the whole thing, from a clean checkout on the commit to ship
    python3 scripts/release.py --until verified   stop before touching the running app: gate, build, prove the copy
    python3 scripts/release.py --dmg AXIOM.dmg    ship a build already made (still proved before it is installed)
    python3 scripts/release.py --skip-gate        when the gate already passed on this commit; the script says so in its log

Every stage's real exit status decides what happens next, never a pipe's (a red gate was deployed through `gate | tail -1 && deploy`,
2026-10-29). Standard library only: this runs before anything is known to work.

The order is the point:

  preflight   right commit, clean tree, disk room, nothing half done, no earlier hold of someone else's to clobber
  gate        scripts/dev/gate.sh release: the fast suite, then the gallery of real traps through the real engine, then the smoke list of browser tests; then, where this
              computer has a private corpus, what this commit reads differently on real saved pages (reported, never blocking)
  build       desktop/build.sh
  verified    a complete copy outside the install location: signature, every file at its size, Python imports, and the copy's own
              engine applying to a local site. Writing into /Applications can be refused part way (2026-11-01), so nothing is
              copied there: the copy moves in with one rename. Then the upgrade check: the installed build and this one on one
              throwaway data folder.
  drain       the hold goes on, and in-flight runs get time to finish. A commit under way always gets it: if one is still running
              when time is up the release stops here, having changed nothing.
  swap        stop the app (which itself never cuts a commit), rename the old one away, rename the copy in, open it
  smoke       `axiom smoke`: services, API, database, schema, workers, assets, that the process runs the code just built, and the dry
              fill again from where it is now installed. Any failure restores the previous build.
  release     the hold goes back to what it was, and only now can anything start on the new build

A run a stop interrupts (one that outlived the drain) resumes for free when the app is back (docs/DESKTOP.md, "Shipping"). The hold is
restored on every path out, including this script being interrupted.

Everything is overridable: AXIOM_APP (default /Applications/AXIOM.app), AXIOM_RELEASE_DIR (default ~/.cache/axiom-release; needs about
3 GB, on the same volume as the app so the swap is a rename).
"""

import argparse
import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("preflight", "gate", "build", "verified", "drained", "installed", "released")
MIN_FREE_GB = 4.0
DRAIN_SECONDS = 1200  # 95% of confirmed applications took under 570 s and 99% under 1,250 s (production, 2026-10-30 to 11-01).
POLL_SECONDS = 5
START_SECONDS = 120
QUIT_SECONDS = 60
HOLD_RESTORE_SECONDS = 90
IN_FLIGHT = "select count(*) from runs where state in ('running','submitting')"
COMMITTING = "select count(*) from runs where state = 'submitting'"
HOLD_VALUE = "select value::text from settings where key = 'execution_v2_deployment_hold'"


class Failed(Exception):
    """A stage said no. `code` is the script's exit status, one per stage, so a caller can tell them apart."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class Log:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a")
        self.began = time.monotonic()

    def say(self, text):
        line = f"[{time.monotonic() - self.began:6.0f}s] {text}"
        print(line, flush=True)
        self.file.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {text}\n")
        self.file.flush()


def run(args, *, check=False, capture=True, timeout=None, env=None, cwd=None, output=None):
    """A command's real status. stdin is closed: a tool waiting on a terminal must fail, not hang a release."""
    kwargs = {"stdin": subprocess.DEVNULL, "timeout": timeout, "env": env, "cwd": cwd, "text": True}
    if output is not None:
        with open(output, "a") as stream:
            done = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, **kwargs)
    elif capture:
        done = subprocess.run(args, capture_output=True, **kwargs)
    else:
        done = subprocess.run(args, **kwargs)
    if check and done.returncode:
        raise Failed(f"{Path(str(args[0])).name} exited {done.returncode}: {(done.stderr or '')[-300:].strip()}")
    return done


class Installed:
    """One bundle on this Mac, spoken to only through its own `axiom` command."""

    def __init__(self, app):
        self.app = Path(app)
        self.resources = self.app / "Contents" / "Resources"
        self.axiom = self.resources / "bin" / "axiom"
        self.python = self.resources / "python" / "bin" / "python3"

    def exists(self):
        return self.axiom.exists()

    def command(self, *args, timeout=120):
        return run([self.axiom, *args], timeout=timeout)

    def sql(self, statement):
        done = self.command("psql", "-XAt", "-c", statement, timeout=30)
        if done.returncode:
            raise Failed(f"The database did not answer: {(done.stderr or done.stdout)[-200:].strip()}")
        return done.stdout.strip()

    def isolated(self, *args, timeout=300, output=None, extra=None):
        """This bundle's Python on this bundle's files, with nothing of the shell's or the running app's."""
        env = {"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TZ": "UTC", "PYTHONPATH": str(self.resources / "app"),
               "PYTHONSAFEPATH": "1", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
               "OPENSSL_CONF": str(self.resources / "openssl.cnf"), **(extra or {})}
        return run([self.python, *args], timeout=timeout, env=env, cwd=self.resources, output=output)

    def engine_version(self):
        done = self.isolated("-c", "from jobpilot.runtime import engine_version; print(engine_version())", timeout=120)
        if done.returncode:
            raise Failed("This build's Python could not read its own code: " + (done.stderr or "")[-300:], 3)
        return done.stdout.strip().splitlines()[-1]

    def build_id(self):
        with (self.app / "Contents" / "Info.plist").open("rb") as stream:
            return plistlib.load(stream).get("CFBundleVersion", "")

    def processes(self):
        done = run(["pgrep", "-f", f"^{self.app}/Contents"])
        return done.stdout.split()


def git(*args):
    return run(["git", "-C", str(ROOT), *args], check=True).stdout.strip()


class Release:
    def __init__(self, options):
        self.options = options
        self.work = Path(options.work).expanduser()
        self.work.mkdir(parents=True, exist_ok=True)
        self.log = Log(self.work / "release.log")
        self.app = Installed(options.app)
        self.copy = Installed(self.work / "AXIOM.new.app")
        self.previous = Installed(self.work / "AXIOM.previous.app")
        self.failed_copy = self.work / "AXIOM.failed.app"
        self.dmg = Path(options.dmg) if options.dmg else self.work / "build" / "AXIOM.dmg"
        self.head = ""
        self.hold_before = None  # The hold's exact value before this release: restored, not assumed to be "complete".
        self.holding = False
        self.swapped = False
        self.cut = 0

    def say(self, text):
        self.log.say(text)

    # ---- preflight ---------------------------------------------------------------------------------------------------

    def preflight(self):
        self.head = git("rev-parse", "--short", "HEAD")
        dirty = git("status", "--porcelain", "--", "jobpilot", "frontend/src", "desktop", "scripts", "pyproject.toml", "uv.lock")
        if dirty and not self.options.allow_dirty:
            raise Failed("The checkout has uncommitted changes, which the build would ship without a commit to name them:\n" + dirty, 2)
        free = shutil.disk_usage(self.work).free / 1024**3
        if free < MIN_FREE_GB:
            raise Failed(f"{free:.1f} GB free under {self.work}; a build needs {MIN_FREE_GB} GB", 2)
        if os.stat(self.work).st_dev != os.stat(self.app.app.parent).st_dev:
            raise Failed(f"{self.work} and {self.app.app} are on different volumes: the swap would not be a rename", 2)
        if self.options.until != "verified":
            if not self.app.exists():
                raise Failed(f"Nothing is installed at {self.app.app}: use the disk image for a first install", 2)
            if run(["pgrep", "-f", "pytest-locked"]).stdout.strip() and not self.options.dmg:
                self.say("note: other test runs are holding the machine-wide lock; the gate waits its turn")
        self.say(f"preflight ok: {self.head}, {free:.0f} GB free")

    # ---- gate --------------------------------------------------------------------------------------------------------

    def gate(self):
        if self.options.skip_gate:
            self.say(f"gate SKIPPED by request on {self.head}")
            return
        output = self.work / "gate.out"
        output.write_text("")
        began = time.monotonic()
        done = run([ROOT / "scripts" / "dev" / "gate.sh", "release"], output=output, cwd=ROOT,
                   env={**os.environ, "UV_NO_SYNC": "1"})
        if done.returncode:
            failures = [line[:140] for line in output.read_text().splitlines() if line.startswith(("FAILED", "ERROR"))][:8]
            raise Failed(f"The gate exited {done.returncode}. See {output}\n" + "\n".join(failures), 2)
        self.say(f"gate passed in {time.monotonic() - began:.0f} s")

    def corpus(self, command, *args, timeout=600):
        """The private replay of real saved pages (scripts/dev/replay_corpus.py), when this computer has a corpus. It informs and never
        blocks: a change is the thing to read, not a failure, and an installation without saved pages has nothing to say."""
        venv = Path(os.environ.get("UV_PROJECT_ENVIRONMENT") or ROOT / ".venv")
        runner = ROOT / "scripts" / "dev" / "replay_corpus.py"
        if not (venv / "bin" / "python").exists() or not (Path(os.environ.get("AXIOM_CORPUS_DIR", "~/.cache/axiom-corpus")).expanduser() / "baseline.json").exists():
            return None
        try:
            return run(["taskpolicy", "-b", "nice", "-n", "19", venv / "bin" / "python", "-I", runner, command, *args], timeout=timeout)
        except subprocess.TimeoutExpired:
            self.say(f"the corpus replay did not finish in {timeout} s; going on without it")
            return None

    def replay(self):
        done = self.corpus("compare")
        if done is None:
            return
        lines = done.stdout.strip().splitlines()
        if not lines:
            self.say(f"real pages: not read, the replay exited {done.returncode}: {(done.stderr or '')[-200:].strip()}")
            return
        self.say("real pages: " + lines[0] + (" (the full list is in replay_corpus's last-compare.txt: read it)" if done.returncode == 3 else ""))

    # ---- build -------------------------------------------------------------------------------------------------------

    def build(self):
        if self.options.dmg:
            self.say(f"using the build at {self.dmg}")
            return
        # A stale dashboard from an earlier checkout would otherwise be shipped: its files' times say nothing after a merge.
        shutil.rmtree(ROOT / "jobpilot" / "static" / "assets", ignore_errors=True)
        (ROOT / "jobpilot" / "static" / "index.html").unlink(missing_ok=True)
        output = self.work / "build.log"
        output.write_text("")
        began = time.monotonic()
        env = {**os.environ, "AXIOM_BUILD_DIR": str(self.work / "build"), "UV_NO_SYNC": "1"}
        done = run(["taskpolicy", "-b", "nice", "-n", "10", ROOT / "desktop" / "build.sh"], output=output, env=env, cwd=ROOT)
        if done.returncode:
            raise Failed(f"The build exited {done.returncode}. See {output}", 3)
        self.say(f"built in {time.monotonic() - began:.0f} s")

    # ---- the copy ----------------------------------------------------------------------------------------------------

    def verify_copy(self):
        attached = run(["hdiutil", "attach", self.dmg, "-nobrowse", "-readonly"], check=True).stdout
        mounts = [line.split("\t")[-1].strip() for line in attached.splitlines() if "/Volumes/" in line]
        if not mounts:
            raise Failed("The disk image did not mount", 3)
        shutil.rmtree(self.copy.app, ignore_errors=True)
        try:
            copied = run(["ditto", Path(mounts[-1]) / "AXIOM.app", self.copy.app])
        finally:
            run(["hdiutil", "detach", mounts[-1], "-quiet"])
        if copied.returncode:
            raise Failed(f"Copying out of the image failed ({copied.returncode}); the running app is untouched", 3)
        signature = run(["codesign", "--verify", "--deep", "--strict", self.copy.app])
        if signature.returncode:
            raise Failed(f"The copy's signature is bad: {signature.stderr[-200:]}", 3)
        problem = self.copy.isolated(
            "-c", "import sys; from jobpilot.native.desktop import install_problem; p = install_problem(sys.argv[1]); print(p); "
            "raise SystemExit(1 if p else 0)", str(self.copy.resources), timeout=120)
        if problem.returncode:
            raise Failed(f"The copy is incomplete: {(problem.stdout + problem.stderr)[-300:]}", 3)
        imports = self.copy.isolated("-c", "import ssl, sqlalchemy, playwright, psycopg, fastapi, jobpilot.api.app, jobpilot.browser", timeout=120)
        if imports.returncode:
            raise Failed(f"The copy's Python cannot load the app: {imports.stderr[-300:]}", 3)
        self.new_engine = self.copy.engine_version()
        built = self.copy.build_id()
        if not self.options.dmg and built != self.head:
            raise Failed(f"The build says it is {built}, the checkout is on {self.head}", 3)
        self.say(f"copy verified: build {built}, engine {self.new_engine}, {sizeof(self.copy.app)}")
        began = time.monotonic()
        filled = self.copy.isolated("-m", "jobpilot.native.smoke", "--fill", timeout=300)
        if filled.returncode:
            raise Failed(f"The new build could not apply to a local site: {(filled.stdout + filled.stderr)[-500:]}", 3)
        self.say(f"the copy's engine applied to a local site in {time.monotonic() - began:.0f} s")
        if self.app.exists() and not self.options.skip_upgrade_check:
            began = time.monotonic()
            checked = run([sys.executable, ROOT / "scripts" / "dev" / "upgrade_check.py", "--old", self.app.app, "--new", self.copy.app,
                           "--work", self.work / "upgrade"], output=self.work / "upgrade.log", timeout=600)
            if checked.returncode:
                raise Failed(f"The upgrade check failed. See {self.work / 'upgrade.log'}", 3)
            self.say(f"upgrade check passed in {time.monotonic() - began:.0f} s, on a throwaway data folder")

    # ---- drain -------------------------------------------------------------------------------------------------------

    def hold_on(self):
        self.hold_before = self.app.sql(HOLD_VALUE)
        done = self.app.command("hold", "on")
        if done.returncode:
            raise Failed(f"The hold could not be set: {done.stderr[-200:]}", 4)
        self.holding = True

    def restore_hold(self, app):
        """Back to exactly what it was. A hold someone set before this release is theirs to lift."""
        if not self.holding:
            return
        value = (self.hold_before or "").replace("'", "''")
        deadline = time.monotonic() + HOLD_RESTORE_SECONDS  # The database may just be coming back, after a refused stop or a rollback.
        while True:
            if value:
                done = app.command("psql", "-XAt", "-c", f"update settings set value = '{value}'::json where key = 'execution_v2_deployment_hold'")
            else:
                done = app.command("hold", "off")
            if done.returncode == 0:
                self.holding = False
                self.say("hold restored")
                return
            if time.monotonic() >= deadline:
                self.say(f"WARNING: the hold could not be restored: {(done.stderr or '')[-200:]}. Nothing can start until: axiom hold off")
                return
            time.sleep(2)

    def drain(self):
        """Wait for what a stop could cut. Queued runs wait out the hold holding no browser, and start after it (service/execution.py)."""
        deadline = time.monotonic() + self.options.drain_seconds
        began = time.monotonic()
        clear = 0
        while time.monotonic() < deadline:
            active = int(self.app.sql(IN_FLIGHT) or 0)
            clear = clear + 1 if active == 0 else 0
            if clear >= 2:  # Twice, a moment apart: a claim that read the hold just before it went on is visible by the second look.
                self.say(f"drained in {time.monotonic() - began:.0f} s")
                return
            time.sleep(POLL_SECONDS)
        committing = int(self.app.sql(COMMITTING) or 0)
        active = int(self.app.sql(IN_FLIGHT) or 0)
        if committing:
            raise Failed(f"{committing} application(s) are still in their commit after {self.options.drain_seconds} s. Nothing was changed.", 4)
        if self.options.never_cut:
            raise Failed(f"{active} application(s) are still running after {self.options.drain_seconds} s, and --never-cut was given.", 4)
        self.cut = active
        self.say(f"{active} application(s) still filling after {self.options.drain_seconds} s: they stop here, nothing was sent, and each "
                 "resumes for free when the app is back")

    # ---- swap --------------------------------------------------------------------------------------------------------

    def stop(self, bundle):
        """Stop a bundle's app and window. The stop itself drains commits (Service.drain)."""
        done = bundle.command("stop", timeout=600)
        if done.returncode:
            self.say(f"`axiom stop` exited {done.returncode}: {(done.stderr or done.stdout)[-200:].strip()}")
        run(["pkill", "-TERM", "-f", f"^{bundle.app}/Contents/MacOS/AXIOM"])
        deadline = time.monotonic() + QUIT_SECONDS
        while bundle.processes() and time.monotonic() < deadline:
            time.sleep(1)
        return not bundle.processes()

    def wait_up(self, bundle):
        deadline = time.monotonic() + START_SECONDS
        while time.monotonic() < deadline:
            done = bundle.command("status", "--json", timeout=30)
            if done.returncode == 0:
                state = json.loads(done.stdout)
                children = state.get("children") or {}
                if children and state.get("phase") == "running" and all(c.get("state") in ("up", "external", "done") for c in children.values()):
                    return True
            time.sleep(2)
        return False

    def swap(self):
        self.stopped_at = datetime.now(timezone.utc).isoformat()
        if not self.stop(self.app):
            run(["open", self.app.app])
            raise Failed("The running app did not stop, so nothing was replaced. It has been asked to carry on.", 5)
        shutil.rmtree(self.previous.app, ignore_errors=True)
        os.rename(self.app.app, self.previous.app)
        try:
            os.rename(self.copy.app, self.app.app)
        except OSError as exc:
            os.rename(self.previous.app, self.app.app)
            run(["open", self.app.app])
            raise Failed(f"The swap was refused ({exc}); the old build is back in place", 5) from exc
        self.swapped = True
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.say(f"swapped; the previous build is at {self.previous.app}")
        run(["open", self.app.app], check=True)

    def smoke(self):
        if not self.wait_up(self.app):
            raise Failed("The new build did not come up", 6)
        done = self.app.command("smoke", "--engine", self.new_engine, "--since", self.started_at, "--hold-expected", timeout=420)
        for line in (done.stdout or "").splitlines():
            self.say(line)
        if done.returncode:
            raise Failed("The smoke check of the installed build failed", 6)

    def rollback(self, why):
        """The new build came up wrong. The database keeps what the new build added (migrations only add), so the old build runs on it."""
        self.say(f"ROLLING BACK: {why}")
        self.stop(self.app)
        shutil.rmtree(self.failed_copy, ignore_errors=True)
        os.rename(self.app.app, self.failed_copy)
        os.rename(self.previous.app, self.app.app)
        self.swapped = False
        run(["open", self.app.app])
        back = self.wait_up(self.app)
        if back:
            self.restore_hold(self.app)
        check = self.app.command("smoke", "--no-fill", timeout=120) if back else None
        # Status 2 is argparse's: a previous build older than `axiom smoke` came up, and cannot say more.
        word = "healthy" if check and check.returncode == 0 else "up, too old to check itself" if check and check.returncode == 2 else "NOT HEALTHY: look at it now"
        self.say(f"previous build restored ({word}); the failed build is at {self.failed_copy}")

    def report_runs(self):
        """What the release did to applications, from the database's own record."""
        stamp = self.stopped_at
        cut = self.app.sql(f"select count(*) from runs where finished_at >= '{stamp}' and reason like 'Worker interrupted before%'")
        unknown = self.app.sql(f"select count(*) from runs where finished_at >= '{stamp}' and reason like 'Worker interrupted during%'")
        drain = self.app.sql("select value::text from settings where key = 'last_drain'")
        self.say(f"runs: {cut} interrupted before submission (each resumes for free), {unknown} interrupted during a commit; last drain {drain}")

    # ---- the whole thing ---------------------------------------------------------------------------------------------

    def ship(self):
        self.preflight()
        self.gate()
        self.replay()
        self.build()
        self.verify_copy()
        if self.options.until == "verified":
            self.say(f"verified and left at {self.copy.app}; the running app was not touched")
            return 0
        self.hold_on()
        try:
            self.drain()
            self.swap()
            try:
                self.smoke()
            except Failed as exc:
                self.rollback(str(exc))
                raise
            self.report_runs()
            self.restore_hold(self.app)
            final = self.app.command("smoke", "--no-fill", timeout=120)
            for line in (final.stdout or "").splitlines():
                self.say(line)
            if final.returncode:
                self.say("WARNING: the first check after the hold lifted did not pass; the new build stays, look at it now")
                return 7
        finally:
            self.restore_hold(self.app if self.app.exists() else self.previous)
        if not self.options.dmg and self.dmg.exists():
            shutil.move(str(self.dmg), self.work / "AXIOM.shipped.dmg")  # What was installed, for anyone else to install.
        self.corpus("collect")  # Pages are deleted after 7 days: keep some of this week's, and read them with what just shipped.
        self.corpus("baseline")
        self.say(f"shipped {self.head}: engine {self.new_engine}")
        return 0


def sizeof(path):
    done = run(["du", "-sh", path])
    return done.stdout.split()[0] if done.returncode == 0 and done.stdout else "?"


def options(argv=None):
    parser = argparse.ArgumentParser(description="Ship the Mac app", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--app", default=os.environ.get("AXIOM_APP", "/Applications/AXIOM.app"))
    parser.add_argument("--work", default=os.environ.get("AXIOM_RELEASE_DIR", "~/.cache/axiom-release"))
    parser.add_argument("--dmg", help="A disk image already built: no build")
    parser.add_argument("--skip-gate", action="store_true", help="The gate already passed on this commit")
    parser.add_argument("--skip-upgrade-check", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true", help="Ship uncommitted changes (never for production)")
    parser.add_argument("--until", choices=["verified"], help="Stop before touching the running app")
    parser.add_argument("--drain-seconds", type=int, default=DRAIN_SECONDS, help="How long running applications get to finish")
    parser.add_argument("--never-cut", action="store_true", help="Give up rather than interrupt an application that outlives the drain")
    return parser.parse_args(argv)


def main(argv=None):
    release = Release(options(argv))

    def interrupted(signum, _frame):
        raise Failed(f"Interrupted by signal {signum}", 8)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, interrupted)
    try:
        return release.ship()
    except Failed as exc:
        release.say(f"RELEASE FAILED: {exc}")
        return exc.code
    except Exception as exc:  # A bug here must still say what state it left things in.
        release.say(f"RELEASE FAILED unexpectedly: {type(exc).__name__}: {exc}. Swapped: {release.swapped}. Holding: {release.holding}")
        return 9


if __name__ == "__main__":
    raise SystemExit(main())
