"""Make a PostgreSQL install relocatable: copy what the app runs, with every library it needs, and rewrite their links to be
relative to each file. Nothing in the result may depend on where it was built (docs/DESKTOP.md).

    relocate_pg.py <prefix of a PostgreSQL 17 install> <destination>

PostgreSQL finds its own `share` and `lib` relative to its binary, from the same relative layout it was built with, so that
layout is kept. Standard library only: this runs before the bundle's Python has anything installed.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

# What jobpilot/native runs, plus what a later major upgrade and a person's own recovery need.
PROGRAMS = ("postgres", "initdb", "pg_ctl", "psql", "pg_dump", "pg_dumpall", "pg_restore", "pg_isready", "pg_controldata", "pg_resetwal")
# Procedural languages link a whole Perl, Python or Tcl, which the app does not use and a clean Mac may not have.
NOT_WANTED = ("plperl", "plpython", "pltcl", "_plperl", "_plpython", "llvmjit")
SYSTEM = ("/usr/lib/", "/System/")


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


def relocate(prefix, destination):
    prefix, destination = Path(prefix).resolve(), Path(destination)
    version = subprocess.run([prefix / "bin" / "postgres", "--version"], capture_output=True, text=True, check=True).stdout
    if " 17." not in version:
        raise SystemExit(f"{prefix} is not PostgreSQL 17: {version.strip()}")
    (destination / "bin").mkdir(parents=True)
    libs = destination / "lib"
    pkglib = libs / "postgresql"
    pkglib.mkdir(parents=True)
    for name in PROGRAMS:
        shutil.copy2(prefix / "bin" / name, destination / "bin" / name)
    for entry in (prefix / "lib" / "postgresql").iterdir():
        if entry.is_dir() or entry.is_symlink() or entry.suffix != ".dylib":  # What links a symlink resolves to the real file.
            continue
        if any(marker in entry.name for marker in NOT_WANTED):
            continue
        shutil.copy2(entry, pkglib / entry.name)
    share = destination / "share" / "postgresql"
    shutil.copytree(prefix / "share" / "postgresql", share, ignore=shutil.ignore_patterns("*plperl*", "*plpython*", "*pltcl*"))

    # Walk everything's links, pulling in whatever is outside the system. `bundled` maps a real path to the copy's name.
    pending = [p for p in (*destination.glob("bin/*"), *pkglib.glob("*.dylib")) if is_macho(p)]
    origin = {p: (prefix / p.relative_to(destination)).resolve() for p in pending if not p.is_relative_to(libs)}
    origin.update({p: (prefix / "lib" / "postgresql" / p.name).resolve() for p in pkglib.glob("*.dylib")})
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
    prove(destination)


def prove(destination):
    """Fails the build if anything still points outside the bundle or the system."""
    bad = []
    for path in sorted(p for p in Path(destination).rglob("*") if p.is_file() and not p.is_symlink() and is_macho(p)):
        for name in load_commands(path)[1]:
            if not name.startswith(("@loader_path/", *SYSTEM)):
                bad.append(f"{path}: {name}")
    if bad:
        raise SystemExit("Not relocatable:\n" + "\n".join(bad))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    relocate(sys.argv[1], sys.argv[2])
