"""Make a PostgreSQL install relocatable: copy what the app runs, with every library it needs, and rewrite their links to be
relative to each file. Nothing in the result may depend on where it was built (docs/DESKTOP.md).

    relocate_pg.py <prefix of a PostgreSQL 17 install> <destination>

`otool` cannot show the other half of this. PostgreSQL finds `share` and `lib` with make_relative_path: it takes what the compiled-in
bindir and each target share as a prefix, and if the running binary's folder ends in the rest of bindir, it swaps that for the rest of
the target. If not, it silently uses the compiled-in absolute path. So Homebrew's `/opt/homebrew/Cellar/postgresql@17/17.8/bin` has to
stay `Cellar/postgresql@17/17.8/bin` under the new root. The first layout here did not, and initdb read /opt/homebrew/share on this Mac
and would have failed on a clean one (2026-10-31, found only when a sandbox refused that path). `prove` now runs it under that refusal.

Standard library only: this runs before the bundle's Python has anything installed.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# What jobpilot/native runs, plus what a later major upgrade and a person's own recovery need.
PROGRAMS = ("postgres", "initdb", "pg_ctl", "psql", "pg_dump", "pg_dumpall", "pg_restore", "pg_isready", "pg_controldata", "pg_resetwal")
# Procedural languages link a whole Perl, Python or Tcl, which the app does not use and a clean Mac may not have.
NOT_WANTED = ("plperl", "plpython", "pltcl", "_plperl", "_plpython", "llvmjit")
SYSTEM = ("/usr/lib/", "/System/")
REFUSE = (
    '(version 1)(allow default)(deny file-read* file-map-executable process-exec* '
    '(subpath "/opt/homebrew") (subpath "/usr/local") (subpath "/Library/PostgreSQL") (subpath "/Applications/Postgres.app"))'
)


def load_commands(path):
    """(its own install name or None, [what it links])"""
    out = subprocess.run(["otool", "-L", str(path)], capture_output=True, text=True, check=True).stdout.splitlines()[1:]
    linked = [line.strip().rsplit(" (compatibility", 1)[0] for line in out]
    own = subprocess.run(["otool", "-D", str(path)], capture_output=True, text=True, check=True).stdout.splitlines()[1:]
    return (own[0].strip() if own else None), [name for name in linked if not (own and name == own[0].strip())]


def is_macho(path):
    with open(path, "rb") as f:
        return f.read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xce\xfa\xed\xfe")


def resolve(name, owner):
    """Where a linked library really is, from the file that links it."""
    if name.startswith("@loader_path/"):
        return (Path(owner).resolve().parent / name.removeprefix("@loader_path/")).resolve()
    if name.startswith("@rpath/"):
        for line in subprocess.run(["otool", "-l", str(owner)], capture_output=True, text=True, check=True).stdout.splitlines():
            line = line.strip()
            if line.startswith("path ") and " (offset" in line:
                root = line[5:].split(" (offset")[0].replace("@loader_path", str(Path(owner).resolve().parent))
                if (Path(root) / name.removeprefix("@rpath/")).exists():
                    return (Path(root) / name.removeprefix("@rpath/")).resolve()
        raise SystemExit(f"Cannot resolve {name} from {owner}")
    return Path(name).resolve()


def tails(prefix):
    """(bindir, sharedir, pkglibdir) as paths relative to the new root, by make_relative_path's own rule."""
    done = subprocess.run([prefix / "bin" / "pg_config", "--bindir", "--sharedir", "--pkglibdir"], capture_output=True, text=True, check=True)
    bindir, sharedir, pkglibdir = done.stdout.split("\n")[:3]
    common = os.path.commonpath([bindir, sharedir, pkglibdir])
    return bindir, sharedir, pkglibdir, *(Path(os.path.relpath(p, common)) for p in (bindir, sharedir, pkglibdir))


def relocate(prefix, destination):
    prefix, destination = Path(prefix).resolve(), Path(destination)
    version = subprocess.run([prefix / "bin" / "postgres", "--version"], capture_output=True, text=True, check=True).stdout
    if " 17." not in version:
        raise SystemExit(f"{prefix} is not PostgreSQL 17: {version.strip()}")
    _, sharedir, pkglibdir, bin_tail, share_tail, pkglib_tail = tails(prefix)
    bin_dir = destination / bin_tail
    bin_dir.mkdir(parents=True)
    # What the supervisor is told (desktop.py, AXIOM_PG_BIN). PostgreSQL resolves it to the real folder, whose tail is what counts.
    os.symlink(bin_tail, destination / "bin")
    libs = destination / "lib"
    pkglib = destination / pkglib_tail
    pkglib.mkdir(parents=True)
    for name in PROGRAMS:
        shutil.copy2(prefix / "bin" / name, bin_dir / name)
    for entry in Path(pkglibdir).iterdir():
        real = entry.resolve()
        if not real.is_file() or real.suffix != ".dylib":
            continue
        if any(marker in real.name for marker in NOT_WANTED):
            continue
        if not (pkglib / real.name).exists():  # Under its real name, so a symlink beside it does not make a second copy.
            shutil.copy2(real, pkglib / real.name)
    shutil.copytree(sharedir, destination / share_tail, ignore=shutil.ignore_patterns("*plperl*", "*plpython*", "*pltcl*"))

    # Walk everything's links, pulling in whatever is outside the system. `bundled` maps a real path to the copy's name.
    pending = [p for p in (*bin_dir.iterdir(), *pkglib.glob("*.dylib")) if is_macho(p)]
    origin = {p: (prefix / "bin" / p.name).resolve() for p in bin_dir.iterdir()}
    sources = {entry.resolve().name: entry.resolve() for entry in Path(pkglibdir).iterdir()}
    origin.update({p: sources[p.name] for p in pkglib.glob("*.dylib")})
    in_pkglib = {origin[p]: p for p in pkglib.glob("*.dylib")}
    bundled = dict(in_pkglib)
    done = set()
    while pending:
        path = pending.pop()
        if path in done:
            continue
        done.add(path)
        own, linked = load_commands(path)
        changes = []
        for name in linked:
            if name.startswith(SYSTEM):
                continue
            real = resolve(name, origin[path])
            copy = bundled.get(real)
            if copy is None:
                # Under its install name, not its versioned file name, so that symlinks are not needed.
                copy = libs / Path(name).name
                if not copy.exists():
                    shutil.copy2(real, copy)
                    os.chmod(copy, 0o755)
                    origin[copy] = real
                    pending.append(copy)
                bundled[real] = copy
            changes += ["-change", name, "@loader_path/" + os.path.relpath(copy, path.parent)]
        if own:
            changes = ["-id", "@rpath/" + path.name, *changes]
        for line in subprocess.run(["otool", "-l", str(path)], capture_output=True, text=True, check=True).stdout.splitlines():
            line = line.strip()
            if line.startswith("path ") and " (offset" in line:
                changes += ["-delete_rpath", line[5:].split(" (offset")[0]]
        if changes:
            os.chmod(path, 0o755)
            subprocess.run(["install_name_tool", *changes, str(path)], check=True, capture_output=True)
        # Any edit breaks the signature, and arm64 refuses to run an unsigned binary.
        subprocess.run(["codesign", "--force", "--sign", "-", str(path)], check=True, capture_output=True)
    prove(destination, bin_dir)


def prove(destination, bin_dir):
    """Fails the build if anything still points outside the bundle or the system, by link or by compiled-in path."""
    bad = []
    for path in sorted(p for p in Path(destination).rglob("*") if p.is_file() and not p.is_symlink() and is_macho(p)):
        for name in load_commands(path)[1]:
            if not name.startswith(("@loader_path/", *SYSTEM)):
                bad.append(f"{path}: {name}")
    if bad:
        raise SystemExit("Not relocatable:\n" + "\n".join(bad))
    with tempfile.TemporaryDirectory() as work:
        env = {"PATH": "/usr/bin:/bin", "LC_ALL": "en_US.UTF-8"}
        refuse = ["sandbox-exec", "-p", REFUSE]
        made = subprocess.run(
            [*refuse, bin_dir / "initdb", "-D", f"{work}/db", "-U", "applypilot", "--auth=trust", "--encoding=UTF8",
             "--locale-provider=icu", "--icu-locale=en-US", "--locale=en_US.UTF-8"],
            env=env, capture_output=True, text=True,
        )
        if made.returncode:
            raise SystemExit("initdb fails without Homebrew:\n" + made.stderr[-600:])
        ran = subprocess.run(
            [*refuse, bin_dir / "postgres", "--single", "-D", f"{work}/db", "postgres"],
            input="select to_tsvector('english', 'relocated') is not null, 'a' < 'B' collate \"en-US-x-icu\";\n",
            env=env, capture_output=True, text=True,
        )
        if ran.returncode or "ERROR" in ran.stdout + ran.stderr or "FATAL" in ran.stdout + ran.stderr:
            raise SystemExit("postgres fails without Homebrew:\n" + (ran.stdout + ran.stderr)[-600:])


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    relocate(sys.argv[1], sys.argv[2])
