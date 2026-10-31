"""The code the native app runs: a snapshot of the checkout, as `COPY jobpilot/` is in the image.

Most of this codebase imports inside functions, so a process running from the checkout would load whatever `git merge`
put there an hour after it started, half old modules and half new, in the middle of an application. An image cannot do
that; a snapshot cannot either. Deploying makes a new snapshot and flips `current`, rolling back flips it back, and
editing or merging in the checkout changes nothing until then.
"""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .env import NativeError

KEEP = 3


def source_id(root):
    def git(*args):
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)
        return done.stdout.strip() if done.returncode == 0 else ""

    sha = git("rev-parse", "--short", "HEAD") or "nogit"
    return sha + ("-dirty" if git("status", "--porcelain", "--", "jobpilot") else "")


def packages_stale(frontend):
    """npm records what it installed. A lock file newer than that record means the packages changed since: the first native
    deploy of the new fonts built against the old node_modules, and every font was a 404 (2026-10-31)."""
    installed = frontend / "node_modules" / ".package-lock.json"
    lock = frontend / "package-lock.json"
    return lock.exists() and (not installed.exists() or lock.stat().st_mtime > installed.stat().st_mtime)


def dashboard_stale(root):
    index = root / "jobpilot" / "static" / "index.html"
    frontend = root / "frontend"
    if not index.exists() or packages_stale(frontend):
        return True
    built = index.stat().st_mtime
    sources = [p for name in ("package-lock.json", "index.html", "vite.config.ts") if (p := frontend / name).exists()]
    sources += [p for p in (frontend / "src").rglob("*") if p.is_file()] if (frontend / "src").is_dir() else []
    return any(p.stat().st_mtime > built for p in sources)


def unresolved_assets(static):
    """Package paths left in the built CSS. Vite only warns about a `url()` it cannot find and the build still succeeds."""
    found = set()
    for sheet in (static / "assets").glob("*.css"):
        found.update(re.findall(r"""url\(["']?(@[^)"']+)""", sheet.read_text(errors="replace")))
    return sorted(found)


def build_dashboard(root, say=print):
    """Build the dashboard when it is missing or older than its sources. `jobpilot/static` is never committed."""
    if not dashboard_stale(root):
        return False
    npm = shutil.which("npm")
    if not npm:
        raise NativeError("The dashboard needs building and npm was not found. Install Node 22: brew install node@22")
    frontend = root / "frontend"
    say("Building the dashboard ...")
    if packages_stale(frontend):
        subprocess.run([npm, "ci"], cwd=frontend, check=True, stdout=subprocess.DEVNULL)
    if subprocess.run([npm, "run", "build"], cwd=frontend, stdout=subprocess.DEVNULL).returncode:
        raise NativeError("The dashboard did not build. Run: cd frontend && npm run build")
    if missing := unresolved_assets(root / "jobpilot" / "static"):
        raise NativeError("The dashboard built without these files: " + ", ".join(missing))
    return True


def make(cfg, say=print):
    """Snapshot the checkout into a new release and return its path. It is not in use until `activate`."""
    root = cfg.root
    build_dashboard(root, say)
    name = time.strftime("%Y%m%d-%H%M%S") + "-" + source_id(root)
    staging = cfg.releases / (".staging-" + name)
    shutil.copytree(root / "jobpilot", staging / "jobpilot", ignore=shutil.ignore_patterns("__pycache__"))
    if (root / "uv.lock").exists():
        shutil.copyfile(root / "uv.lock", staging / "uv.lock")  # A deploy compares it, to know if packages changed.
    target = cfg.releases / name
    while target.exists():  # Two in one second.
        target = target.with_name(target.name + "x")
    staging.rename(target)
    return target


def activate(cfg, release):
    """Point `current` at the release, atomically, and keep the newest few for rollback."""
    link = cfg.home / ".current-next"
    link.unlink(missing_ok=True)
    link.symlink_to(release)
    os.replace(link, cfg.current)
    keep = {Path(release).name}
    for old in sorted((p for p in cfg.releases.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        if old.name in keep:
            continue
        if old.name.startswith(".staging-") or len(keep) >= KEEP:
            shutil.rmtree(old, ignore_errors=True)
        else:
            keep.add(old.name)


def current(cfg):
    """The active release, resolved, or None. Resolving at each start keeps a running process on the path it began with
    when `current` flips underneath it."""
    return cfg.current.resolve() if cfg.current.exists() else None


def ensure(cfg, say=print):
    found = current(cfg)
    if found:
        return found
    release = make(cfg, say)
    activate(cfg, release)
    return release.resolve()
