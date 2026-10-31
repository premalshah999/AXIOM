#!/usr/bin/env python3
"""Phase (b) of docs/ARCHITECTURE.md section 7: move every module to the home `module_map.py` gives it, as a script that re-runs on the latest main.

    scripts/dev/move.py plan [b1..b5]            # read-only: what each step would move, extract, edit, and what it defers
    scripts/dev/move.py apply b1 [--commit]      # does it (git mv, import rewrites, symbol extraction); refuses a dirty tree
    scripts/dev/move.py apply --through b5       # b1 to b5 in order; a step already applied is a no-op, so this is the re-run
    scripts/dev/move.py baseline                 # record what must not change: test count, routes and OpenAPI, dated comment lines
    scripts/dev/move.py verify                   # compare with the baseline, plus the static checks below
    scripts/dev/move.py lint                     # import-linter in report-only mode (the contracts of ARCHITECTURE.md 5.2)
    scripts/dev/move.py dry-run --through b5     # all of it on a throwaway clone of HEAD, which is deleted after

Nothing here runs unless `apply` is given: the tree is untouched by every other command. The steps and what is in them:

    b1 core, domain, contracts, llm, and every SYMBOL_MOVES group     b4 control
    b2 discovery, mail                                                b5 applier (not the five giants), desktop
    b3 knowledge, ledger                                              b6 the five giants (forms, browser, widgets, adapters/base,
                                                                         telegram): splitting them is the plan's own step, not a move

How a move keeps the rest of the repository working, which is what lets each step land within hours of starting:

* Files are `git mv`'d and nothing is left behind at the old path, so a fixer's branch that edited `jobpilot/forms.py` still
  merges into the moved file (git follows the rename; a shim left at the old path would turn that into a conflict).
* `jobpilot/_moved.py` (generated) installs a finder that answers `jobpilot.<old name>` with the real module at its new home:
  `sys.modules["jobpilot.x"] is sys.modules["axiom_y.x"]`, so `patch("jobpilot.x.name")` still hits the real module (the trap in
  ARCHITECTURE.md 6.2), `python -m jobpilot.cli` and `python -m jobpilot.native` still run, and a fixer's new `from jobpilot.x import y`
  still works. The finder is the shim layer of phase b7 and goes with it.
* Only the files that moved are rewritten: relative imports become absolute, imports of other moved modules point at the new home
  (at any depth, function-level imports included), and an imported name that moved to a lower layer is imported from there.

Standard library only (it calls the repository's ruff and pytest, and import-linter, when asked). Read the plan before the apply:
decisions that are this script's own are in SYMBOL_HOMES, ASSETS and PATH_FIXES, each with the reason.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import collections
import contextlib
import functools
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import symtable
import tempfile
import tokenize

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import module_map as mm  # noqa: E402

ROOT, PACKAGE = mm.ROOT, mm.PACKAGE
LINE = 110
SKELETON = ""  # a package marker this script made; the module that owns that path replaces it
ATTRIBUTION = "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"

STEPS = {
    "b1": ("core", "domain", "contracts", "llm"),
    "b2": ("discovery", "mail"),
    "b3": ("knowledge", "ledger"),
    "b4": ("control",),
    "b5": ("applier", "desktop"),
}
# The plan splits these in b6 (6.2), and the module that is split keeps its place until then: its imports, its patches and its
# tests are the part of the job that is not a move. Their pure helpers do leave in b1 (SYMBOL_MOVES).
GIANTS = {"forms.py", "browser.py", "widgets.py", "adapters/base.py", "telegram.py"}

# Modules that a move would break and that need a person's change first. They keep their place (the aliases and the markers in the
# new packages let everything around them move), and `plan` lists them. {rel: why}
HOLD = {
    "answers/__init__.py": "re-exports the applier's Resolver, so a domain package built from it imports the applier whenever domain/answers/* is "
    "imported (answer_context becomes domain/answers/propositions.py and knowledge.py imports that: a cycle through the resolver at import). "
    "The map ends the re-exports (callers name the module); do that, then the file moves",
}

FACADE = (
    "a package __init__.py that imports its own package's modules: as the __init__ of a package other code imports a child of, it would run "
    "those imports first. With the children spread over owners that is a cycle (the dry run found `answers`, `api`, `features.knowledge` and "
    "`adapters`). The new package gets an empty __init__ and this file stays until its re-exports end, as the map says for answers and mail"
)

# Where a SYMBOL_MOVES group lands inside its owner. The map names only the owner; a path here is either what ARCHITECTURE.md
# 6.1 / 6.2 or a note in the map says, or the default: the name of the module the group leaves.
SYMBOL_HOMES = {
    ("widgets.py", "domain"): "matching.py",  # 6.2: norm, best_option, parse_date, aliases. No Playwright
    ("browser.py", "domain"): "posting/gone.py",  # 6.2
    ("features/discovery/policy.py", "domain"): "posting/seniority.py",  # map note: SENIOR and friends
    ("features/discovery/__init__.py", "domain"): "posting/gate.py",  # map note: gate() is read at admission
    ("features/discovery/feeds.py", "domain"): "posting/levels.py",
    ("repair_agent.py", "domain"): "answers/masks.py",  # map note
    ("owner_questions.py", "domain"): "answers/owner_view.py",  # map note
    ("execution_services.py", "knowledge"): "packet.py",  # map note
    ("recovery.py", "ledger"): "recovery.py",  # map note
    ("mail/__init__.py", "domain"): "mail_identity.py",  # map note; the definitions are already in mail/helpers.py
    # Not named anywhere: chosen here for what the code is. The map says only the owner.
    ("forms.py", "domain"): "confirmation.py",
    ("discovery.py", "domain"): "posting/entry.py",  # the map note says ats.py; a new file cannot collide with a module that moves later
    ("failure_reports.py", "domain"): "text_clean.py",
    ("telegram.py", "core"): "notify_config.py",
    ("telegram_workers.py", "core"): "notify_slots.py",
    ("owner_apply.py", "ledger"): "picks.py",  # the map note says admission.py, which only moves in b3
    ("service/queueing.py", "ledger"): "fit.py",
    ("operations/superseded.py", "ledger"): "superseded.py",  # the map note: runs inside completion
    ("operations/reset.py", "domain"): "reset_keys.py",
    ("knowledge.py", "domain"): "answer_topics.py",
    ("features/resumes/claims.py", "domain"): "resume_claims.py",
    ("features/knowledge/migration.py", "domain"): "knowledge_rows.py",
    ("diagnostics.py", "ledger"): "help_images.py",
    ("schemas.py", "core"): "job_titles.py",
    ("jobboard.py", "domain"): "board_types.py",
    ("adapters/icims.py", "domain"): "icims_urls.py",
    ("admission.py", "domain"): "admission_types.py",
    ("tiers.py", "llm"): "tiers.py",
    ("receipts.py", "domain"): "receipt_states.py",
    ("workers.py", "discovery"): "workers.py",
}

# Files the code finds by `Path(__file__)`: they move with the module that reads them, to where that lookup still lands.
ASSETS = [
    ("jobpilot/data/employer_catalog.json", "jobboard.py", "board/data/employer_catalog.json"),  # jobboard.py -> board/__init__.py
    ("jobpilot/native/services.toml", "native/services.py", "services.toml"),  # Path(__file__).with_name
]

# Source edits a move needs and a rename cannot do. Each `old` must be found (or `new` already be there): when main has drifted
# the run stops and names the file, because a silent miss is a deploy that cannot find its code. `nodes` are the top-level names
# whose AST is allowed to differ afterwards (verify). `step` is when it is needed, `rel` the module (found wherever it is now).
FRAME_FILTER = re.compile(r'"/jobpilot/" in ([A-Za-z_][\w.]*+)(?! or "/axiom_")')  # possessive: no backtracking to a shorter name
PATH_FIXES = [
    dict(
        step="b1",
        rel="runtime.py",
        nodes=["engine_version"],
        why="engine_version hashed `Path(__file__).parent`, which is axiom_core alone after the move; hash every package",
        edits=[
            (
                "    root = Path(__file__).parent\n    digest = hashlib.sha256()\n"
                '    for file in sorted([*root.rglob("*.py"), *root.rglob("*.js")]):\n'
                "        digest.update(str(file.relative_to(root)).encode())\n"
                "        digest.update(file.read_bytes())\n",
                "    from importlib import import_module\n\n    from jobpilot._moved import PACKAGES\n\n"
                "    digest = hashlib.sha256()\n"
                '    for name in ("jobpilot", *PACKAGES):  # the code is several packages now (ARCHITECTURE.md 5.1)\n'
                "        root = Path(import_module(name).__file__).parent\n"
                '        for file in sorted([*root.rglob("*.py"), *root.rglob("*.js")]):\n'
                '            digest.update((name + "/" + str(file.relative_to(root))).encode())\n'
                "            digest.update(file.read_bytes())\n",
            )
        ],
    ),
    dict(
        step="b1",
        rel="native/release.py",
        nodes=["source_id", "make"],
        why="a release is a snapshot of the code, and the code is no longer only jobpilot/: copy every package next to it (PYTHONPATH is the release)",
        edits=[
            ('"--", "jobpilot")', '"--", *(d for d in SOURCE_DIRS if (root / d).exists()))'),
            ("KEEP = 3\n", 'KEEP = 3\nSOURCE_DIRS = ("jobpilot", "libs", "services", "desktop")\n'),
            ("def make(cfg, say=print):", 'def package_dirs(root):\n    """Each package of the new layout, which the release lays out flat beside jobpilot/."""\n'
             '    return sorted([*root.glob("libs/*/src/axiom_*"), *root.glob("services/*/src/axiom_*"), *root.glob("desktop/axiom_*")])\n\n\n'
             "def make(cfg, say=print):"),
            (
                'shutil.copytree(root / "jobpilot", staging / "jobpilot", ignore=shutil.ignore_patterns("__pycache__"))\n',
                'shutil.copytree(root / "jobpilot", staging / "jobpilot", ignore=shutil.ignore_patterns("__pycache__"))\n'
                "    for package in package_dirs(root):\n"
                '        shutil.copytree(package, staging / package.name, ignore=shutil.ignore_patterns("__pycache__"))\n',
            ),
        ],
    ),
    dict(
        step="b4",
        rel="api/routes/frontend.py",
        nodes=["STATIC"],
        why="the dashboard is built into jobpilot/static, which the package split leaves where it is; `parents[2]` was jobpilot/",
        edits=[
            (
                'STATIC = Path(__file__).resolve().parents[2] / "static"',
                'STATIC = Path(__import__("jobpilot").__file__).resolve().parent / "static"  # built into jobpilot/static (Dockerfile, vite)',
            )
        ],
    ),
    dict(
        step="b5",
        rel="native/services.py",
        nodes=["source_file"],
        why="a release lays the packages out flat, so its list is axiom_desktop/services.toml; a release made before the move (a rollback) still has jobpilot/native/",
        edits=[
            (
                '        packaged = Path(release_dir) / "jobpilot" / "native" / "services.toml"\n        if packaged.exists():\n            return packaged\n',
                '        for packaged in (Path(release_dir) / "axiom_desktop" / "services.toml", Path(release_dir) / "jobpilot" / "native" / "services.toml"):\n'
                "            if packaged.exists():\n                return packaged\n",
            )
        ],
    ),
]
# The lint commands name the directories; they gain the new ones as those appear. (path, regex)
LINT_COMMANDS = [".github/workflows/ci.yml", "Makefile", "DEVELOPER.md"]
LINT_RE = re.compile(r"(ruff (?:check|format --check)) jobpilot(?: libs| services| desktop)* tests")

# Run these tests as well when a step touches the code they cover (never a browser file here): the ones that read source by path.
PATH_TESTS = ["tests/test_native_supervisor.py", "tests/test_native_dashboard_build.py", "tests/test_infra_consistency.py"]

DATE = re.compile(r"20\d\d-\d\d-\d\d")


class Stop(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------------------- layout
def pkg_name(owner):
    return f"axiom_{owner}"


def pkg_dir(owner):
    if owner == "desktop":
        return ROOT / "desktop" / "axiom_desktop"
    return ROOT / ("services" if owner in mm.SERVICES else "libs") / owner / "src" / pkg_name(owner)


def split_target(rel):
    owner, path = mm.MAP[rel][0].split(":", 1)
    return owner, path


def dest(rel):
    owner, path = split_target(rel)
    return pkg_dir(owner) / path


def dotted_of(owner, path):
    parts = [pkg_name(owner), *path[:-3].split("/")] if path.endswith(".py") else [pkg_name(owner)]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def new_dotted(rel):
    return dotted_of(*split_target(rel))


def old_dotted(rel):
    return mm.dotted(rel)


@functools.lru_cache(maxsize=None)
def facade(rel):
    path = PACKAGE / rel
    if not rel.endswith("/__init__.py") or not path.exists():
        return False
    for st in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(st, ast.ImportFrom) and (st.level or (st.module or "").startswith("jobpilot")):
            return True
        if isinstance(st, ast.Import) and any(a.name.startswith("jobpilot") for a in st.names):
            return True
    return False


def step_of(rel):
    if rel == "__init__.py" or rel in GIANTS or rel in HOLD or facade(rel):
        return None
    owner = split_target(rel)[0]
    return next(step for step, owners in STEPS.items() if owner in owners)


def is_moved(rel):
    return not (PACKAGE / rel).exists() and dest(rel).exists()


def where(rel):
    """The file as it is now: at the old path, or at its new home."""
    for path in (PACKAGE / rel, dest(rel)):
        if path.exists():
            return path
    return None


OLD = {old_dotted(rel): rel for rel in mm.MAP}  # every mapped module by its old dotted name, moved or not


def owner_of(rel):
    return mm.MAP[rel][0].split(":", 1)[0]


def package_dirs():
    return [pkg_dir(o) for o in mm.OWNERS if pkg_dir(o).exists()]


def source_files(*, new_only=False):
    roots = package_dirs() if new_only else [PACKAGE, *package_dirs()]
    return sorted(p for r in roots for p in r.rglob("*.py"))


# ----------------------------------------------------------------------------------------------------------------------- git
def git(*args, check=True):
    done = subprocess.run(["git", "-C", str(ROOT), *map(str, args)], capture_output=True, text=True)
    if check and done.returncode:
        raise Stop(f"git {' '.join(map(str, args))}: {done.stderr.strip()}")
    return done.stdout


def show(ref, rel):
    done = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:{rel}"], capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


# ---------------------------------------------------------------------------------------------------------- import rewriting
class Unsupported(Exception):
    pass


NOTES: list[str] = []  # things the script left alone and a person should look at


def abs_prefix(node, ctx, is_pkg):
    if not node.level:
        return node.module or ""
    base = ctx.split(".") if is_pkg else ctx.split(".")[:-1]
    keep = len(base) - (node.level - 1)
    return ".".join([*base[:keep], *([node.module] if node.module else [])])


def lookup(prefix, name):
    full = f"{prefix}.{name}"
    if full in OLD:
        return "module", OLD[full]
    if prefix in OLD:
        return "attr", OLD[prefix], name
    return None


def module_loc(rel):
    return new_dotted(rel) if is_moved(rel) else old_dotted(rel)


def done_symbols(rel):
    """{name: home dotted} for the names that left this module, read from the re-export line the extraction leaves in it."""
    path = where(rel)
    out = {}
    if path is None or rel not in mm.SYMBOL_MOVES:
        return out
    homes = {dotted_of(o, home_path(rel, o)): o for o in set(mm.SYMBOL_MOVES[rel].values())}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ImportFrom) and not node.level and node.module in homes:
            out.update({a.name: node.module for a in node.names})
    return out


def attr_loc(rel, name, depth=0):
    """Where `from <rel> import name` should read from after the moves: the lower layer a name left for, or the moved module."""
    moved = done_symbols(rel)
    if name in moved:
        return moved[name], name
    if depth < 4 and name in mm.SYMBOL_MOVES.get(rel, {}) and (path := where(rel)):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:  # a name the map moves that this module only re-exports
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (alias.asname or alias.name) == name:
                        prefix = abs_prefix(node, old_dotted(rel) if (PACKAGE / rel).exists() else old_dotted(rel), rel.endswith("__init__.py"))
                        hit = lookup(prefix, alias.name)
                        if hit and hit[0] == "attr":
                            return attr_loc(hit[1], hit[2], depth + 1)
                        if hit and hit[0] == "module":
                            break
    return module_loc(rel), name


def render_from(module, names, indent=""):
    """`from module import a, b as c`, wrapped at LINE the way ruff format does."""
    items = [f"{n} as {a}" if a else n for n, a in names]
    one = f"{indent}from {module} import {', '.join(items)}"
    if len(one) <= LINE:
        return one
    body = "".join(f"{indent}    {i},\n" for i in items)
    return f"{indent}from {module} import (\n{body}{indent})"


def rewrite_node(node, ctx, is_pkg):
    """The replacement for one import statement, or None to leave it as it is."""
    if isinstance(node, ast.ImportFrom):
        prefix = abs_prefix(node, ctx, is_pkg) if ctx else (node.module or "")
        if not (prefix == "jobpilot" or prefix.startswith("jobpilot.")) or any(a.name == "*" for a in node.names):
            if node.level:
                raise Unsupported("relative star import" if any(a.name == "*" for a in node.names) else "relative import without a context")
            return None
        groups: dict[str, list] = collections.OrderedDict()
        bare = []
        for alias in node.names:
            hit = lookup(prefix, alias.name)
            if hit is None:
                module = module_loc(OLD[prefix]) if prefix in OLD else prefix
                groups.setdefault(module, []).append((alias.name, alias.asname))
            elif hit[0] == "module":
                target = module_loc(hit[1])
                parent, _, last = target.rpartition(".")
                asname = alias.asname or (alias.name if last != alias.name else None)
                if parent:
                    groups.setdefault(parent, []).append((last, asname))
                else:
                    bare.append(f"import {target} as {asname or alias.name}")
            else:
                module, name = attr_loc(hit[1], hit[2])
                groups.setdefault(module, []).append((name, alias.asname))
        lines = [render_from(m, names) for m, names in groups.items()] + bare
        text = "\n".join(lines)
        if not node.level and len(groups) == 1 and not bare and node.module in groups:
            same = [(a.name, a.asname) for a in node.names] == groups[node.module]
            if same:
                return None
        return text
    names = []
    for alias in node.names:
        if alias.name == "jobpilot" or alias.name.startswith("jobpilot."):
            walk = alias.name
            while walk not in OLD and "." in walk:
                walk = walk.rpartition(".")[0]
            if walk in OLD and is_moved(OLD[walk]):
                if walk != alias.name or not alias.asname:
                    raise Unsupported(f"`import {alias.name}` without `as` reaches a module that moved")
                names.append(f"{module_loc(OLD[walk])} as {alias.asname}")
                continue
        names.append(f"{alias.name} as {alias.asname}" if alias.asname else alias.name)
    text = "import " + ", ".join(names)
    return None if text == ast.unparse(node) else text


def line_starts(text):
    offsets, total = [0], 0
    for line in text.splitlines(keepends=True):
        total += len(line)
        offsets.append(total)
    return offsets


def char_at(lines, starts, lineno, col):
    return starts[lineno - 1] + len(lines[lineno - 1].encode()[:col].decode())  # ast columns are UTF-8 bytes


def rewrite_text(text, ctx, is_pkg):
    """Rewrite every import statement and dynamic import of a moved module in `text`. Returns (text, changes)."""
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    starts = line_starts(text)
    edits = []
    for node in ast.walk(tree):
        rep = None
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            try:
                rep = rewrite_node(node, ctx, is_pkg)
            except Unsupported as why:
                NOTES.append(f"{ctx or '?'} line {node.lineno}: {why}")
            if rep is None:
                continue
            first, last = char_at(lines, starts, node.lineno, node.col_offset), char_at(lines, starts, node.end_lineno, node.end_col_offset)
            indent = text[starts[node.lineno - 1] : first]
            comments = re.findall(r"#.*", text[first:last])
            if indent.strip():  # `if x: import y`
                rep = "; ".join(rep.split("\n"))
                indent = ""
            rep = ("\n" + indent).join(rep.split("\n"))
            if comments:
                rep = ("\n" + indent).join([*comments, rep])
            edits.append((first, last, rep))
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            value = node.args[0].value
            if name in ("__import__", "import_module") and value in OLD and is_moved(OLD[value]):
                arg = node.args[0]
                first, last = char_at(lines, starts, arg.lineno, arg.col_offset), char_at(lines, starts, arg.end_lineno, arg.end_col_offset)
                edits.append((first, last, repr(module_loc(OLD[value])).replace("'", '"')))
    for first, last, rep in sorted(edits, reverse=True):
        text = text[:first] + rep + text[last:]
    return text, len(edits)


def rewrite_moved_files():
    """Every file of the new packages: relative imports become absolute and moved modules are imported from their new homes."""
    reverse = {dest(rel): rel for rel in mm.MAP if dest(rel).exists()}
    changed = 0
    for path in source_files(new_only=True):
        rel = reverse.get(path)
        ctx = old_dotted(rel) if rel else None
        text = path.read_text(encoding="utf-8")
        new, count = rewrite_text(text, ctx, bool(rel and rel.endswith("__init__.py")))
        if count and new != text:
            path.write_text(new, encoding="utf-8")
            changed += count
    return changed


# -------------------------------------------------------------------------------------------------- symbol moves (SYMBOL_MOVES)
def home_path(rel, owner):
    if (rel, owner) in SYMBOL_HOMES:
        return SYMBOL_HOMES[(rel, owner)]
    stem = rel[:-3].replace("/", "_")
    return (stem[: -len("___init__")] if stem.endswith("___init__") else stem) + ".py"


def module_bindings(tree):
    """{name: (statement, top_level)} for definitions and for imports, looking into if/try/with at module level."""
    defs, imps = {}, {}

    def visit(body, top):
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs[st.name] = (st, top)
            elif isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = st.targets if isinstance(st, ast.Assign) else [st.target]
                for t in targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                            defs.setdefault(n.id, (st, top))
            elif isinstance(st, (ast.Import, ast.ImportFrom)):
                for a in st.names:
                    imps[(a.asname or a.name).split(".")[0]] = (st, top, a)
            elif isinstance(st, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                for field in ("body", "orelse", "finalbody", "handlers"):
                    for part in getattr(st, field, []) or []:
                        visit(part.body if isinstance(part, ast.ExceptHandler) else [part], False)

    visit(tree.body, True)
    return defs, imps


def header_names(node):
    """Names a definition reads when it is executed (decorators, defaults, annotations, bases), which symtable does not report."""
    out = set()
    parts = list(getattr(node, "decorator_list", []))
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        parts += [*a.defaults, *[d for d in a.kw_defaults if d]]
        parts += [x.annotation for x in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg] if x and x.annotation]
        parts += [node.returns] if node.returns else []
    elif isinstance(node, ast.ClassDef):
        parts += [*node.bases, *[k.value for k in node.keywords]]
    for part in parts:
        out.update(n.id for n in ast.walk(part) if isinstance(n, ast.Name))
    return out


def scope_globals(table):
    out = {s.get_name() for s in table.get_symbols() if s.is_referenced() and s.is_global()}
    for child in table.get_children():
        out |= scope_globals(child)
    return out


def referenced(node, tables):
    """Module-level names a top-level node needs."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        table = tables.get((node.name, node.lineno))
        names = header_names(node) | (scope_globals(table) if table else {n.id for n in ast.walk(node) if isinstance(n, ast.Name)})
    else:
        value = getattr(node, "value", None)
        names = {n.id for n in ast.walk(value) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)} if value else set()
        if value:  # a comprehension's loop variable or a lambda's argument is not a module-level name
            names -= {n.id for n in ast.walk(value) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            names -= {a.arg for n in ast.walk(value) if isinstance(n, ast.Lambda) for a in [*n.args.args, *n.args.kwonlyargs]}
    return names


def start_line(lines, node):
    first = min([node.lineno, *[d.lineno for d in getattr(node, "decorator_list", [])]])
    while first > 1 and lines[first - 2].startswith("#"):  # the comment block directly above moves with the code (incident notes)
        first -= 1
    return first


NEW_TO_OLD = None


def normalized(node):
    """An AST dump that ignores import statements (rewritten by design), the dotted names of moved modules in strings
    (`__import__("jobpilot.x")`), and the frame filter's second clause."""
    global NEW_TO_OLD
    if NEW_TO_OLD is None:
        NEW_TO_OLD = {new_dotted(rel): old_dotted(rel) for rel in mm.MAP}

    class Strip(ast.NodeTransformer):
        def visit_Import(self, n):
            return None

        visit_ImportFrom = visit_Import

        def visit_Constant(self, n):
            return ast.Constant(NEW_TO_OLD[n.value]) if isinstance(n.value, str) and n.value in NEW_TO_OLD else n

        def visit_BoolOp(self, n):
            self.generic_visit(n)
            if isinstance(n.op, ast.Or) and len(n.values) == 2 and "/axiom_" in ast.dump(n.values[1]):
                return n.values[0]
            return n

    return ast.dump(Strip().visit(ast.parse(ast.unparse(node)).body[0]))


class Group:
    """The names of one module that leave for one lower-layer home."""

    def __init__(self, rel, owner, names):
        self.rel, self.owner, self.names, self.requested = rel, owner, names, list(names)
        self.path = pkg_dir(owner) / home_path(rel, owner)
        self.dotted = dotted_of(owner, home_path(rel, owner))
        self.status, self.reason, self.nodes, self.order, self.imports, self.stale = "todo", "", [], [], [], []

    def label(self):
        return f"{self.rel}: {', '.join(self.requested)} -> {self.owner}:{home_path(self.rel, self.owner)}"

    def done(self):
        return self.path.exists() and all(n in done_symbols(self.rel) for n in self.names)

    def analyse(self, patched):
        src = where(self.rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        defs, imps = module_bindings(tree)
        tables = {(t.get_name(), t.get_lineno()): t for t in symtable.symtable(src, self.rel, "exec").get_children()}
        self.stale = [n for n in self.names if n not in defs and n not in imps]  # a map entry for a name the cleanup removed
        self.names = [n for n in self.names if n not in self.stale]
        reexports = [n for n in self.names if n not in defs]
        if not self.names:
            self.status, self.reason = "stale", f"{', '.join(self.stale)} no longer exist in the module; drop them from SYMBOL_MOVES"
            return
        if reexports and len(reexports) == len(self.names):
            self.status, self.reason = "retarget", "already defined elsewhere; importers are pointed at the definition"
            return
        if reexports:
            self.status, self.reason = "defer", f"{', '.join(reexports)} is only re-exported here while the rest is defined here"
            return
        seen, work, needed = set(self.names), list(self.names), {}
        while work:
            name = work.pop()
            stmt, top = defs[name]
            if not top:
                self.status, self.reason = "defer", f"{name} is defined inside an if/try block"
                return
            for used in referenced(stmt, tables):
                if used in defs and used not in seen:
                    seen.add(used)
                    work.append(used)
                elif used in imps and used not in defs:
                    needed[used] = imps[used]
                elif used not in defs and not hasattr(builtins, used) and used != name:
                    self.status, self.reason = "defer", f"{name} reads {used}, which this module does not bind at its top level"
                    return
        nodes = sorted({id(defs[n][0]): defs[n][0] for n in seen}.values(), key=lambda s: s.lineno)
        self.nodes, self.order = nodes, [n for n in seen]
        lines = src.splitlines()
        # Gate 1: what the moved code decorates itself with, patches and rebinds.
        for st in nodes:
            for dec in getattr(st, "decorator_list", []):
                if "DBOS" in ast.unparse(dec):
                    self.status, self.reason = "defer", f"{st.name} is a DBOS workflow: it moves with the service that runs it"
                    return
        hit = sorted(set(self.order) & patched)
        if hit:
            self.status, self.reason = "defer", f"tests patch {', '.join(hit)} on the old module, which would stop reaching the moved code"
            return
        moved_ids = {id(s) for s in nodes}
        for st in tree.body:
            if id(st) in moved_ids:
                continue
            for n in ast.walk(st):
                if isinstance(n, ast.Global) and set(n.names) & set(self.order):
                    self.status, self.reason = "defer", f"{', '.join(set(n.names) & set(self.order))} is rebound with `global` by code that stays"
                    return
        # Gate 2: every import the moved code makes, at any depth, must be one the new owner may make.
        me = old_dotted(self.rel)
        self.imports = []
        for st in nodes:
            for n in ast.walk(st):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    target = self.check_import(n, me, self.rel.endswith("__init__.py"))
                    if target:
                        self.status, self.reason = "defer", target
                        return
        for name, (stmt, _, alias) in needed.items():
            target = self.check_import(stmt, me, self.rel.endswith("__init__.py"), only=alias)
            if target:
                self.status, self.reason = "defer", target
                return
            self.imports.append((stmt, alias))
        self.status = "move"
        self.lines = lines

    def check_import(self, node, me, is_pkg, only=None):
        names = [only] if only else node.names
        if isinstance(node, ast.ImportFrom):
            prefix = abs_prefix(node, me, is_pkg)
            for alias in names:
                if prefix.split(".")[0] in ("playwright", "browser_use") and self.owner != "applier":
                    return f"imports {prefix}, which only the applier may"
                if prefix.startswith("axiom_") and not mm.allowed(self.owner, prefix.split(".")[0][len("axiom_"):]):
                    return f"imports {prefix}, a layer {self.owner} may not import"
                if not (prefix == "jobpilot" or prefix.startswith("jobpilot.")):
                    continue
                hit = lookup(prefix, alias.name)
                rel, name = (hit[1], None) if hit and hit[0] == "module" else (hit[1], hit[2]) if hit else (None, None)
                if rel == self.rel:
                    return f"moved code imports its own module ({self.rel}) inside a function"
                if rel:
                    target_owner = mm.SYMBOL_MOVES.get(rel, {}).get(name) or owner_of(rel)
                    if not mm.allowed(self.owner, target_owner):
                        return f"imports {rel} ({target_owner}), a layer {self.owner} may not import"
        else:
            for alias in names:
                if alias.name.split(".")[0] in ("playwright", "browser_use") and self.owner != "applier":
                    return f"imports {alias.name}, which only the applier may"
        return None

    def build(self):
        """Write the home, cut the code out of the module, and leave one re-export line behind."""
        src = where(self.rel).read_text(encoding="utf-8")
        lines = src.splitlines()
        tree = ast.parse(src)
        spans = sorted((start_line(lines, st), st.end_lineno) for st in self.nodes)
        runs = []  # contiguous runs, so original spacing between neighbours is kept verbatim
        for first, last in spans:
            if runs and all(not l.strip() for l in lines[runs[-1][1] : first - 1]):
                runs[-1][1] = last
            else:
                runs.append([first, last])
        body = "\n\n\n".join("\n".join(lines[a - 1 : b]) for a, b in runs)
        shown = ", ".join(self.requested[:4]) + (f" and {len(self.requested) - 4} more" if len(self.requested) > 4 else "")
        doc = f'"""Moved out of jobpilot/{self.rel} by scripts/dev/move.py (docs/ARCHITECTURE.md 6.1): {shown}, with what they need.\n\nIt imports nothing from the module it left, so a lower layer can use it."""'
        head = ["from __future__ import annotations\n"] if any(isinstance(s, ast.ImportFrom) and s.module == "__future__" for s in tree.body) else []
        plain, froms = set(), collections.OrderedDict()
        for stmt, alias in self.imports:
            item = f"{alias.name} as {alias.asname}" if alias.asname else alias.name
            if isinstance(stmt, ast.ImportFrom):
                froms.setdefault(("." * stmt.level) + (stmt.module or ""), set()).add((alias.name, alias.asname))
            else:
                plain.add(f"import {item}")
        imports = sorted(plain) + [render_from(m, sorted(names)) for m, names in sorted(froms.items(), key=lambda kv: kv[0].lstrip("."))]
        text = "\n".join([doc, "", *head, *imports, "", "", body, ""])
        text, _ = rewrite_text(text, old_dotted(self.rel), self.rel.endswith("__init__.py"))  # relative -> absolute, at the home's depth
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")
        # Cut from the module, bottom up, then close the gaps.
        keep = list(lines)
        for first, last in sorted(spans, reverse=True):
            del keep[first - 1 : last]
        out = re.sub(r"\n{4,}", "\n\n\n", "\n".join(keep) + "\n")
        # Re-export, because the old module's own code and everything that imported these names from it still reads them there.
        new_tree = ast.parse(out)
        last_import = 0  # the end of the import block at the top, so the line cannot make later imports "not at top" (E402)
        for st in new_tree.body:
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                last_import = st.end_lineno
            elif not (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant)):
                break
        used = used_names(new_tree)
        names = sorted(self.order, key=str.lower)
        line = render_from(self.dotted, [(n, None) for n in names])
        line = line.replace("\n)", "\n)", 1) if "\n" in line else line
        unused = [n for n in names if n not in used]
        if unused:
            line = (line + "  # noqa: F401  moved; removed with the shims (docs/ARCHITECTURE.md 7, b7)") if "\n" not in line else line.replace(
                f"from {self.dotted} import (", f"from {self.dotted} import (  # noqa: F401  moved; removed with the shims (b7)", 1
            )
        rows = out.split("\n")
        rows[last_import:last_import] = line.split("\n")
        out = "\n".join(rows)
        # Imports only the moved code used are dead in the module now.
        out = drop_dead_imports(out, {a.asname or a.name.split(".")[0] for _, a in self.imports})
        where(self.rel).write_text(out, encoding="utf-8")
        verify_defs(src, self)


def used_names(tree):
    """Every name the code reads: identifiers, string annotations (`"date | None"`), `__all__`. Prose in strings does not count."""
    out = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    notes = [n.annotation for n in ast.walk(tree) if isinstance(n, (ast.arg, ast.AnnAssign)) and n.annotation]
    notes += [n.returns for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.returns]
    for note in notes:
        for const in ast.walk(note):
            if isinstance(const, ast.Constant) and isinstance(const.value, str):
                try:
                    out |= {n.id for n in ast.walk(ast.parse(const.value, mode="eval")) if isinstance(n, ast.Name)}
                except SyntaxError:
                    pass
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets):
            out |= {c.value for c in ast.walk(n.value) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    return out


def drop_dead_imports(text, candidates):
    """Remove an imported name that the moved code was the only reader of."""
    tree = ast.parse(text)
    lines = text.split("\n")
    used = used_names(tree)
    for st in reversed([s for s in tree.body if isinstance(s, (ast.Import, ast.ImportFrom))]):
        if isinstance(st, ast.ImportFrom) and st.module == "__future__":
            continue
        keep = [a for a in st.names if (a.asname or a.name.split(".")[0]) not in candidates or (a.asname or a.name.split(".")[0]) in used]
        if len(keep) == len(st.names):
            continue
        if keep:
            mod = ("." * st.level) + (st.module or "")
            new = render_from(mod, [(a.name, a.asname) for a in keep]) if isinstance(st, ast.ImportFrom) else "import " + ", ".join(a.name for a in keep)
            indent = lines[st.lineno - 1][: st.col_offset]
            lines[st.lineno - 1 : st.end_lineno] = (indent + new.lstrip() if "\n" not in new else new).split("\n")
        else:
            del lines[st.lineno - 1 : st.end_lineno]
    return "\n".join(lines)


def verify_defs(old_src, group):
    """The AST of everything that moved is the same in its new home (imports aside)."""
    old = {n: normalized(s) for s in ast.parse(old_src).body for n in names_of(s) if n in group.order}
    new = {n: normalized(s) for s in ast.parse(group.path.read_text(encoding="utf-8")).body for n in names_of(s)}
    bad = [n for n in old if new.get(n) != old[n]]
    if bad:
        raise Stop(f"{group.label()}: AST differs after the move for {', '.join(bad)}")


def names_of(stmt):
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [stmt.name]
    if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        return [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]
    return []


def patched_names():
    """Strings that name something a test patches: monkeypatch.setattr(obj, "name"), patch("jobpilot.mod.name"), patch.object."""
    out = set()
    for path in (ROOT / "tests").rglob("*.py"):
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call):
                fn = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                if fn in ("setattr", "setitem", "delattr", "object", "patch", "multiple"):
                    for i, a in enumerate(n.args):
                        if isinstance(a, ast.Constant) and isinstance(a.value, str):
                            out.add(a.value.rsplit(".", 1)[-1] if a.value.startswith("jobpilot.") else a.value) if (a.value.startswith("jobpilot.") or i == 1 or fn == "object") else None
    return out


def groups():
    out = []
    for rel, moves in mm.SYMBOL_MOVES.items():
        by_owner = collections.defaultdict(list)
        for name, owner in moves.items():
            if name != "*":
                by_owner[owner].append(name)
        # A module that has moved took its extraction decision with it: judging it again would read its rewritten imports.
        out += [Group(rel, owner, names) for owner, names in by_owner.items() if where(rel) and not is_moved(rel)]
    return out


# ----------------------------------------------------------------------------------------------------------- aliases and layout
def alias_table():
    return {old_dotted(rel): new_dotted(rel) for rel in mm.MAP if step_of(rel) and is_moved(rel)}


MOVED_PY = '''"""Generated by scripts/dev/move.py; do not edit. Phase (b) of docs/ARCHITECTURE.md section 7, and gone with the shims in b7.

While the modules move, `jobpilot.<old name>` answers with the module at its new home: the same object, registered under both names,
so patching either reaches the real code. Anything not listed here is still a file of the package.
"""

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

PACKAGES = __PACKAGES__

ALIASES = {
__ALIASES__
}
_PACKAGES = __OLD_PACKAGES__

_HERE = Path(__file__).resolve().parent
# A checkout keeps each package under <services|libs>/<name>/src; a release lays them out beside jobpilot/ (native/release.py).
for _pattern in ("libs/*/src", "services/*/src", "desktop"):
    for _src in sorted(_HERE.parent.glob(_pattern)):
        if _src.is_dir() and str(_src) not in sys.path:
            sys.path.append(str(_src))


def _children(module, alias):
    """`from jobpilot.pkg import child` asks the real package for `child` under the real package's name, which is wrong for a
    child that moved elsewhere or has not moved. Answer it from the old names instead."""
    prefix, inherited = alias + ".", module.__dict__.get("__getattr__")

    def __getattr__(name):
        full = prefix + name
        folder = _HERE.joinpath(*full.split(".")[1:])
        if full in ALIASES or folder.with_suffix(".py").exists() or (folder / "__init__.py").exists():
            return importlib.import_module(full)
        if inherited is not None:
            return inherited(name)
        raise AttributeError(f"module {module.__name__!r} has no attribute {name!r}")

    module.__getattr__ = __getattr__


class _Alias(importlib.abc.Loader):
    def __init__(self, target, alias):
        self.target, self.alias, self.spec = target, alias, None

    def create_module(self, spec):
        module = importlib.import_module(self.target)
        self.spec = module.__spec__  # the import system stamps this alias's spec on the module; exec_module puts the real one back
        return module

    def exec_module(self, module):
        if self.spec is not None:
            module.__spec__ = self.spec
        if self.alias in _PACKAGES and "_aliased" not in module.__dict__:
            module._aliased = self.alias
            _children(module, self.alias)

    def get_code(self, fullname):  # python -m jobpilot.cli runs the real file's code
        return importlib.util.find_spec(self.target).loader.get_code(self.target)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        new = ALIASES.get(name)
        if new is not None:
            real = importlib.util.find_spec(new)
            spec = importlib.machinery.ModuleSpec(name, _Alias(new, name), origin=getattr(real, "origin", None), is_package=name in _PACKAGES)
            spec.has_location = bool(getattr(real, "has_location", False))
            return spec
        parent = name.rpartition(".")[0]
        if parent in ALIASES:  # a child of a package whose __init__ moved, and which has not moved itself yet
            folder = _HERE.joinpath(*parent.split(".")[1:])
            if folder.is_dir():
                return importlib.machinery.PathFinder.find_spec(name, [str(folder)])
        return None


sys.meta_path.insert(0, _Finder())
'''


def write_moved():
    aliases = alias_table()
    pkgs = sorted(pkg_name(o) for o in mm.OWNERS if pkg_dir(o).exists())
    old_pkgs = sorted(old_dotted(r) for r in mm.MAP if r.endswith("__init__.py") and old_dotted(r) in aliases)
    body = "".join(f'    "{o}": "{n}",\n' for o, n in sorted(aliases.items()))
    text = (
        MOVED_PY.replace("__PACKAGES__", repr(tuple(pkgs)))
        .replace("__ALIASES__", body.rstrip("\n"))
        .replace("__OLD_PACKAGES__", "{" + ", ".join(f'"{p}"' for p in old_pkgs) + "}" if old_pkgs else "set()")
    )
    path = PACKAGE / "_moved.py"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    init = PACKAGE / "__init__.py"
    source = init.read_text(encoding="utf-8")
    hook = "from . import _moved  # noqa: E402,F401  the old names of moved modules (docs/ARCHITECTURE.md 7, phase b)\n"
    if "_moved" not in source:
        init.write_text(source.rstrip("\n") + "\n\n" + hook, encoding="utf-8")
    git("add", "jobpilot/_moved.py", "jobpilot/__init__.py")


def ensure_packages():
    """An __init__.py in every directory of every new package. `core` carries the version the root package had."""
    for owner in mm.OWNERS:
        root = pkg_dir(owner)
        if not any(root.rglob("*.py")) and not root.exists():
            continue
        for folder in [root, *[p for p in root.rglob("*") if p.is_dir() and p.name != "__pycache__"]]:
            marker = folder / "__init__.py"
            if not marker.exists() and any(folder.glob("*.py")) or (folder == root and not marker.exists()):
                marker.parent.mkdir(parents=True, exist_ok=True)
                body = '"""AXIOM core library (docs/ARCHITECTURE.md 5.1)."""\n\n__version__ = "1.0.0"\n' if (owner == "core" and folder == root) else SKELETON
                marker.write_text(body, encoding="utf-8")
                git("add", marker)
    for folder in sorted((p for r in package_dirs() for p in r.rglob("*") if p.is_dir() and p.name != "__pycache__"), reverse=True):
        if any(f.suffix == ".py" for f in folder.rglob("*.py")) and not (folder / "__init__.py").exists():
            (folder / "__init__.py").write_text(SKELETON, encoding="utf-8")
            git("add", folder / "__init__.py")


# ------------------------------------------------------------------------------------------------------------------- plan/apply
def pending(step):
    return [rel for rel in mm.MAP if step_of(rel) == step and (PACKAGE / rel).exists()]


def apply_fix(fix):
    path = where(fix["rel"]) if "rel" in fix else ROOT / fix["path"]
    if path is None or not path.exists():
        raise Stop(f"{fix['rel']}: not found, main has changed the module this edit is for")
    text = path.read_text(encoding="utf-8")
    for old, new in fix["edits"]:
        if new in text:
            continue
        if old not in text:
            raise Stop(f"{path.relative_to(ROOT)}: expected text not found ({fix['why']}); main has drifted, update PATH_FIXES in move.py")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


def lint_paths():
    return " ".join([d for d in ("jobpilot", "libs", "services", "desktop") if (ROOT / d).exists()] + ["tests"])


def fix_lint_commands():
    for name in LINT_COMMANDS:
        path = ROOT / name
        if path.exists():
            text = path.read_text(encoding="utf-8")
            new = LINT_RE.sub(lambda m: f"{m.group(1)} {lint_paths()}", text)
            if new != text:
                path.write_text(new, encoding="utf-8")


def fix_frame_filters():
    changed = []
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        if '"/jobpilot/"' in text:
            new = FRAME_FILTER.sub(lambda m: f'("/jobpilot/" in {m.group(1)} or "/axiom_" in {m.group(1)})', text)
            if new != text:
                path.write_text(new, encoding="utf-8")
                changed.append(path)
    return changed


def apply_step(step):
    todo = pending(step)
    print(f"== {step}: {len(todo)} modules")
    patched = patched_names()
    if step == "b1":
        for group in groups():
            if group.done():
                continue
            group.analyse(patched)
            print(f"   symbols {group.status:8} {group.label()}" + (f"  [{group.reason}]" if group.reason else ""))
            if group.status == "move":
                group.build()
    for rel in todo:
        target = dest(rel)
        if target.exists():
            if target.read_text(encoding="utf-8") != SKELETON:
                raise Stop(f"{target.relative_to(ROOT)} exists and is not a placeholder; {rel} cannot move onto it")
            git("rm", "-qf", target)
        target.parent.mkdir(parents=True, exist_ok=True)
        git("mv", PACKAGE / rel, target)
    for old, rel, new in ASSETS:
        if step_of(rel) == step and (ROOT / old).exists():
            target = pkg_dir(owner_of(rel)) / new
            target.parent.mkdir(parents=True, exist_ok=True)
            git("mv", ROOT / old, target)
    for folder in sorted((p for p in PACKAGE.rglob("*") if p.is_dir()), reverse=True):  # old directories left with only bytecode
        if not any(f.suffix not in (".pyc",) and f.is_file() for f in folder.rglob("*")):
            shutil.rmtree(folder, ignore_errors=True)
    ensure_packages()
    for fix in PATH_FIXES:
        if fix["step"] <= step:
            apply_fix(fix)
    fix_frame_filters()
    fix_lint_commands()
    count = rewrite_moved_files()
    ensure_packages()
    write_moved()
    git("add", "-A", "--", *[n for n in ("jobpilot", "libs", "services", "desktop", *LINT_COMMANDS) if (ROOT / n).exists()])
    print(f"   {len(todo)} files moved, {count} imports rewritten")


def commit(step):
    git("commit", "-q", "-m", f"Move {', '.join(STEPS[step])} modules to their packages ({step} of docs/ARCHITECTURE.md section 7)\n\n"
        f"Mechanical: scripts/dev/move.py apply {step}. git mv, import rewrites and the old-name aliases in jobpilot/_moved.py; no behaviour change.\n\n{ATTRIBUTION}")


def cmd_plan(steps):
    patched = patched_names()
    for step in steps:
        todo = pending(step)
        by_owner = collections.Counter(owner_of(r) for r in todo)
        print(f"== {step}: {len(todo)} modules to move ({', '.join(f'{o} {n}' for o, n in by_owner.items()) or 'none pending'})")
        if step == "b1":
            for g in groups():
                if g.done():
                    print(f"   symbols done     {g.label()}")
                    continue
                g.analyse(patched)
                extra = f"  [+{len(g.order) - len(g.names)} dragged, {len(g.imports)} imports]" if g.status == "move" and len(g.order) > len(g.names) else ""
                print(f"   symbols {g.status:8} {g.label()}{('  [' + g.reason + ']') if g.reason else ''}{extra}")
        for old, rel, new in ASSETS:
            if step_of(rel) == step:
                print(f"   asset {old} -> {split_target(rel)[0]}:{new}")
        for fix in PATH_FIXES:
            if fix["step"] == step:
                print(f"   edit  {fix['rel']}: {fix['why']}")
    deferred = [r for r in mm.MAP if r in GIANTS and (PACKAGE / r).exists()]
    print(f"== b6 (not this script): {', '.join(sorted(deferred))}")
    for rel in mm.MAP:
        if (PACKAGE / rel).exists() and (rel in HOLD or facade(rel)):
            print(f"== held: {rel}" + (f": {HOLD[rel]}" if rel in HOLD else ""))
    print(f"   held because: {FACADE}")


# ---------------------------------------------------------------------------------------------------------------- the checks
def python_bin():
    for candidate in (os.environ.get("AXIOM_PYTHON"), os.path.join(os.environ.get("UV_PROJECT_ENVIRONMENT", ""), "bin/python"), str(ROOT / ".venv/bin/python")):
        if candidate and os.path.exists(candidate):
            return candidate
    return sys.executable


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=kw.pop("cwd", ROOT), **kw)


def collect():
    """pytest --collect-only over the whole suite, through the machine-wide lock the fixers share. Returns (nodeids, errors)."""
    env = dict(os.environ, UV_NO_SYNC="1", UV_PROJECT_ENVIRONMENT=os.environ.get("UV_PROJECT_ENVIRONMENT") or str(pathlib.Path(python_bin()).parents[1]))
    done = run(["sh", str(ROOT / "scripts/dev/pytest-locked.sh"), "--collect-only", "-q", "-p", "no:cacheprovider"], env=env)
    ids = sorted(l for l in done.stdout.splitlines() if "::" in l and not l.startswith(" "))
    errors = [l for l in done.stdout.splitlines() if l.startswith("ERROR")]
    return ids, errors, done.stdout[-600:] + done.stderr[-600:]


ROUTES_PY = r"""
import hashlib, json, tempfile
from jobpilot.api import create_app
from jobpilot.config import Settings
with tempfile.TemporaryDirectory() as d:
    cfg = Settings(_env_file=None, database_url="", dbos_database_url="", gmail_address="", gmail_app_password="", telegram_bot_token="",
        telegram_user_id="", telegram_enabled=True, serper_api_key="", mimo_api_key="", llm_provider="mimo", deepseek_api_key="",
        google_client_id="", google_client_secret="", browser_cdp_url="", twocaptcha_api_key="", application_password="", icims_login="",
        account_email="", haiku_api_key="", data_dir=d, app_token="t", enable_workers=False, answer_quality_enabled=False,
        browser_vision_recovery=False, failure_reports_enabled=False, allow_private_urls=True, chromium_path="", snapshot_cache_seconds=0).prepare()
    app = create_app(cfg)
    def flat(routes, prefix=""):  # FastAPI wraps each included router; walk down to the endpoints
        for r in routes:
            if hasattr(r, "original_router"):
                yield from flat(r.original_router.routes, prefix + (getattr(r.include_context, "prefix", "") or ""))
            else:
                yield [type(r).__name__, prefix + getattr(r, "path", ""), sorted(getattr(r, "methods", None) or []), getattr(r, "name", "")]
    rows = sorted(flat(app.routes))
    try:
        # FastAPI names two schemas that share a class name after the module (`jobpilot__reserved__Section`), so a move renames
        # them in the document. Everything else must be byte for byte the same: compare with the module part dropped.
        doc, long_names = app.openapi(), []
        def short(node):
            if isinstance(node, dict):
                return {k: (v.rsplit("__", 1)[-1] if k == "$ref" and isinstance(v, str) else short(v)) for k, v in node.items()}
            return [short(v) for v in node] if isinstance(node, list) else node
        schemas = doc.get("components", {}).get("schemas", {})
        long_names = sorted(n for n in schemas if "__" in n)
        canon = {"paths": short(doc["paths"]), "schemas": sorted(json.dumps(short(v), sort_keys=True) + "|" + k.rsplit("__", 1)[-1] for k, v in schemas.items())}
        spec = hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()
    except Exception as exc:
        spec = "unavailable: " + type(exc).__name__
        long_names = []
    print(json.dumps({"routes": rows, "openapi": spec, "schema_names_with_module": len(long_names)}))
"""


def route_table():
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TMPDIR", "USER")}
    done = run([python_bin(), "-c", ROUTES_PY], env={**keep, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=300)
    if done.returncode:
        raise Stop("route table failed: " + done.stderr[-800:])
    return json.loads(done.stdout.splitlines()[-1])


def date_lines():
    out = collections.Counter()
    for path in source_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            if DATE.search(line) and not line.lstrip().startswith(("from ", "import ")):
                out[line.strip()] += 1
    return out


def ruff_counts():
    ruff = pathlib.Path(python_bin()).parent / "ruff"
    paths = [d for d in ("jobpilot", "libs", "services", "desktop") if (ROOT / d).exists()]
    done = run([str(ruff), "check", "--no-cache", "--output-format", "json", "--select", "E4,E7,E9,F,B", "--ignore", "B008", *paths])
    try:
        return dict(collections.Counter(item["code"] for item in json.loads(done.stdout or "[]")))
    except ValueError:
        return {"unavailable": 1}


def cmd_baseline(out):
    ids, errors, tail = collect()
    if errors or not ids:
        raise Stop("collection failed before any move:\n" + tail)
    data = {
        "commit": git("rev-parse", "HEAD").strip(),
        "collect": ids,
        **route_table(),
        "dates": dict(date_lines()),
        "ruff": ruff_counts(),
    }
    pathlib.Path(out).write_text(json.dumps(data))
    print(f"baseline {data['commit'][:8]}: {len(ids)} tests collected, {len(data['routes'])} routes, {sum(data['dates'].values())} dated lines, "
          f"openapi {data['openapi'][:12]}, ruff {sum(data['ruff'].values())} findings")


def index_modules():
    """{dotted: path} for jobpilot and for every new package, plus the aliases."""
    index = {}
    for root, top in [(PACKAGE, "jobpilot"), *[(pkg_dir(o), pkg_name(o)) for o in mm.OWNERS if pkg_dir(o).exists()]]:
        for path in root.rglob("*.py"):
            parts = [top, *path.relative_to(root).with_suffix("").parts]
            if parts[-1] == "__init__":
                parts.pop()
            index[".".join(parts)] = path
    return index


def check_imports():
    """Every `jobpilot.*` / `axiom_*` import in the repository, at any depth, resolves to a module and a name that exist."""
    index, aliases, names_cache, bad = index_modules(), alias_table(), {}, []
    paths_of = {v: k for k, v in index.items()}

    def real(dotted):
        return index.get(dotted) or index.get(aliases.get(dotted, ""))

    def names(path):
        if path not in names_cache:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            defs, imps = module_bindings(tree)
            star = any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in ast.walk(tree))
            getattr_ = any(isinstance(n, ast.FunctionDef) and n.name == "__getattr__" for n in tree.body)
            names_cache[path] = None if (star or getattr_) else set(defs) | set(imps)
        return names_cache[path]

    for path in [*source_files(), *sorted((ROOT / "tests").rglob("*.py")), *sorted((ROOT / "scripts").rglob("*.py"))]:
        ctx = paths_of.get(path)
        is_pkg = path.name == "__init__.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                if node.level and not ctx:
                    continue
                prefix = abs_prefix(node, ctx, is_pkg) if node.level else (node.module or "")
                if prefix.split(".")[0] not in ("jobpilot",) and not prefix.startswith("axiom_"):
                    continue
                target = real(prefix)
                where_ = f"{path.relative_to(ROOT)}:{node.lineno}"
                if target is None:
                    bad.append(f"{where_}: no module {prefix}")
                    continue
                for alias in node.names:
                    if alias.name == "*" or real(f"{prefix}.{alias.name}"):
                        continue
                    found = names(target)
                    if found is not None and alias.name not in found:
                        bad.append(f"{where_}: {prefix} has no name {alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if (alias.name.split(".")[0] == "jobpilot" or alias.name.startswith("axiom_")) and real(alias.name) is None:
                        bad.append(f"{path.relative_to(ROOT)}:{node.lineno}: no module {alias.name}")
    return bad


def check_ast(commit):
    """Everything defined at the top level of a module before the moves is defined, identically, somewhere after them."""
    new = collections.defaultdict(set)
    for path in source_files():
        for st in ast.parse(path.read_text(encoding="utf-8")).body:
            for n in names_of(st):
                new[n].add(normalized(st))
    allowed = {n for fix in PATH_FIXES for n in fix.get("nodes", [])}
    same = 0
    changed, missing = [], []
    for rel in mm.MAP:
        old = show(commit, f"jobpilot/{rel}")
        if old is None:
            continue
        for st in ast.parse(old).body:
            for n in names_of(st):
                key = normalized(st)
                if key in new.get(n, ()):
                    same += 1
                elif n in new and n in allowed:
                    same += 1
                elif n in new:
                    changed.append(f"{rel}: {n}")
                else:
                    missing.append(f"{rel}: {n}")
    return same, changed, missing


def leftovers():
    """Things a rename leaves for a person: relative imports and jobpilot imports inside the new packages, and path strings."""
    relative, flat = 0, collections.Counter()
    for path in source_files(new_only=True):
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom) and n.level:
                relative += 1
            elif isinstance(n, ast.ImportFrom) and (n.module or "").startswith("jobpilot"):
                walk = n.module
                while walk not in OLD and "." in walk:
                    walk = walk.rpartition(".")[0]
                flat[OLD.get(walk, walk)] += 1
    paths = []
    for path in [*source_files(), *sorted((ROOT / "tests").rglob("*.py"))]:
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and re.fullmatch(r"(jobpilot|frontend)(/[\w./-]*)?", n.value):
                paths.append(f"{path.relative_to(ROOT)}:{n.lineno} {n.value!r}")
    return relative, flat, paths


def cmd_verify(baseline_path, with_collect=True, strict=True):
    base = json.loads(pathlib.Path(baseline_path).read_text())
    failures = []

    def report(name, ok, detail=""):
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    bad = check_imports()
    report("every import resolves (module and name, any depth)", not bad, f"{len(bad)} unresolved; first: {bad[:4]}" if bad else "")
    if with_collect:
        ids, errors, tail = collect()
        gone, added = sorted(set(base["collect"]) - set(ids)), sorted(set(ids) - set(base["collect"]))
        report(f"pytest --collect-only count unchanged ({len(base['collect'])} -> {len(ids)})", len(ids) == len(base["collect"]) and not gone and not errors,
               f"errors {errors[:3]} gone {gone[:3]} added {added[:3]}" + (tail if errors else ""))
        now = route_table()
        report(f"route table identical ({len(now['routes'])} routes)", now["routes"] == base["routes"], str([r for r in now["routes"] if r not in base["routes"]][:3]))
        report(f"OpenAPI paths and schemas identical (module part of {now['schema_names_with_module']} schema names aside)", now["openapi"] == base["openapi"], f"{base['openapi'][:12]} -> {now['openapi'][:12]}")
    same, changed, missing = check_ast(base["commit"])
    report(f"AST of every moved top-level definition identical ({same} identical)", not changed and not missing, f"changed {changed[:5]} missing {missing[:5]}")
    old_dates, new_dates = collections.Counter(base["dates"]), date_lines()
    lost, gained = old_dates - new_dates, new_dates - old_dates
    report(f"lines citing a dated incident unchanged ({sum(old_dates.values())})", not lost and not gained, f"lost {list(lost)[:2]} gained {list(gained)[:2]}")
    before, after = base["ruff"], ruff_counts()
    worse = {c: (before.get(c, 0), n) for c, n in after.items() if n > before.get(c, 0)}
    report("ruff findings no worse (E4 E7 E9 F B)", not worse, str(worse))
    relative, flat, paths = leftovers()
    report("no relative imports left in the new packages", relative == 0, str(relative))
    print(f"info jobpilot.* imports still made from the new packages (the deferred giants): {dict(flat)}")
    print(f"info {len(paths)} path strings 'jobpilot'/'frontend' in source and tests that a move can leave pointing nowhere: {paths[:6]}")
    for note in NOTES:
        print("note", note)
    return 1 if failures and strict else 0


# ------------------------------------------------------------------------------------------------- import-linter, report only
def contracts(existing):
    """ARCHITECTURE.md 5.2, cut down to the packages that exist so far."""
    services = [pkg_name(o) for o in mm.SERVICES if pkg_name(o) in existing]
    libs = [pkg_name(o) for o in ("ledger", "knowledge", "llm", "contracts", "domain", "core") if pkg_name(o) in existing]
    out = ["[importlinter]", "root_packages =", *[f"    {p}" for p in sorted(existing)], "include_external_packages = True", ""]
    if len(services) > 1:
        out += ["[importlinter:contract:services-independent]", "name = Services never import each other", "type = independence", "modules =", *[f"    {s}" for s in services], ""]
    if len(libs) > 1:
        out += ["[importlinter:contract:library-layers]", "name = Libraries depend only downwards", "type = layers", "layers =", *[f"    {l}" for l in libs], ""]
    if libs and services:
        out += ["[importlinter:contract:libs-never-import-services]", "name = No library imports a service", "type = forbidden", "source_modules =", *[f"    {l}" for l in libs],
                "forbidden_modules =", *[f"    {s}" for s in services], ""]
    if "axiom_discovery" in existing or "axiom_mail" in existing:
        sources = [p for p in ("axiom_discovery", "axiom_mail") if p in existing]
        if "axiom_ledger" in existing:
            out += ["[importlinter:contract:ledger-is-private]", "name = Only control and the applier touch the run ledger", "type = forbidden", "source_modules =", *[f"    {s}" for s in sources], "forbidden_modules =", "    axiom_ledger", ""]
    if "axiom_mail" in existing:
        sources = [p for p in ("axiom_control", "axiom_applier", "axiom_discovery") if p in existing]
        if sources:
            out += ["[importlinter:contract:mail-holds-the-mailbox]", "name = Only mail may import the mail transport", "type = forbidden", "source_modules =", *[f"    {s}" for s in sources],
                    "forbidden_modules =", "    axiom_mail.imap", "    axiom_mail.transport", ""]
    others = [p for p in sorted(existing) if p != "axiom_applier"]
    out += ["[importlinter:contract:browser-only-in-applier]", "name = Only the applier may import a browser driver", "type = forbidden", "source_modules =", *[f"    {p}" for p in others],
            "forbidden_modules =", "    playwright", ""]
    if "axiom_control" in existing:
        out += ["[importlinter:contract:gateway-on-orchestrator]", "name = Inside control, the gateway sits on the orchestrator", "type = layers", "containers = axiom_control", "layers =",
                "    gateway | notify", "    orchestrator | owner | resumes | ops", ""]
    return "\n".join(out)


def cmd_lint(extra_path):
    existing = {pkg_name(o) for o in mm.OWNERS if pkg_dir(o).exists()}
    if not existing:
        print("lint: no new package yet; the contracts have nothing to check before b1")
        return 0
    config = pathlib.Path(tempfile.mkdtemp()) / "importlinter.ini"
    config.write_text(contracts(existing), encoding="utf-8")
    exe = os.environ.get("LINT_IMPORTS") or shutil.which("lint-imports")
    cmd = [exe] if exe else ["uvx", "--from", "import-linter==2.15", "lint-imports"]
    srcs = [str(pkg_dir(o).parent) for o in mm.OWNERS if pkg_dir(o).exists()]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([*srcs, str(ROOT), *([extra_path] if extra_path else [])]))
    done = run([*cmd, "--config", str(config), "--no-cache"], env=env)
    text = done.stdout + done.stderr
    analysed = re.search(r"Analyzed (\d+) files, (\d+) dependencies", text)
    kept = re.search(r"Contracts: (\d+) kept, (\d+) broken", text)
    print(f"import-linter, report only (its exit status {done.returncode} is ignored): {kept.group(0) if kept else 'no summary:' + text[-300:]}"
          f"; {analysed.group(0) if analysed else ''}")
    lines = text.split("\n")
    if "Broken contracts" in text:
        body = lines[lines.index("Broken contracts") + 2 :]
        title, chains = None, collections.OrderedDict()
        for i, line in enumerate(body):
            if i + 1 < len(body) and body[i + 1] and set(body[i + 1]) == {"-"} and line.strip():
                title = line.strip()
                chains[title] = 0
            elif title and line.startswith("- "):
                chains[title] += 1
        for name, count in chains.items():
            print(f"   {count:4} import chains break: {name}")
    print("   (the five giants are still in jobpilot/ until b6, so imports of them are not seen)")
    return 0


# ------------------------------------------------------------------------------------------------------------------ dry run
def cmd_dry_run(through, keep, scratch, extra_path):
    work = pathlib.Path(tempfile.mkdtemp(prefix="move-dry-", dir=scratch))
    tree = work / "tree"
    try:
        subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(tree)], check=True)
        for name in ("move.py", "module_map.py"):  # the copy runs this script, committed or not
            shutil.copy(HERE / name, tree / "scripts/dev" / name)
        git_in = lambda *a: subprocess.run(["git", "-C", str(tree), *a], check=True, capture_output=True, text=True)
        git_in("add", "-A")
        git_in("-c", "user.name=dry-run", "-c", "user.email=dry-run@localhost", "commit", "-q", "--allow-empty", "-m", "the script under test")
        me = [sys.executable, str(tree / "scripts/dev/move.py")]
        base = str(work / "baseline.json")
        steps = [("baseline", [*me, "baseline", "--out", base]),
                 ("apply", [*me, "apply", "--through", through, "--commit"]),
                 ("re-run (must be a no-op)", [*me, "apply", "--through", through]),
                 ("verify", [*me, "verify", "--baseline", base]),
                 ("lint", [*me, "lint", *(["--extra-path", extra_path] if extra_path else [])])]
        status = 0
        for label, cmd in steps:
            print(f"-- {label}")
            done = subprocess.run(cmd, cwd=tree, env={**os.environ, "UV_PROJECT_ENVIRONMENT": os.environ.get("UV_PROJECT_ENVIRONMENT", str(pathlib.Path(python_bin()).parents[1]))})
            status = status or done.returncode
            if done.returncode and label in ("baseline", "apply"):
                break
        dirty = git_in("status", "--porcelain").stdout.strip()
        print(f"-- tree after the re-run: {'clean' if not dirty else 'DIRTY ' + dirty[:300]}")
        return status or (1 if dirty else 0)
    finally:
        if keep:
            print("kept", tree)
        else:
            shutil.rmtree(work, ignore_errors=True)


HERE = pathlib.Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("steps", nargs="*", default=list(STEPS))
    p = sub.add_parser("apply")
    p.add_argument("steps", nargs="*")
    p.add_argument("--through")
    p.add_argument("--commit", action="store_true")
    p.add_argument("--allow-dirty", action="store_true")
    p = sub.add_parser("baseline")
    p.add_argument("--out", default=str(ROOT / ".git" / "move-baseline.json"))
    p = sub.add_parser("verify")
    p.add_argument("--baseline", default=str(ROOT / ".git" / "move-baseline.json"))
    p.add_argument("--no-collect", action="store_true")
    p = sub.add_parser("lint")
    p.add_argument("--extra-path", default="")
    p = sub.add_parser("dry-run")
    p.add_argument("--through", default="b5")
    p.add_argument("--keep", action="store_true")
    p.add_argument("--scratch", default=None)
    p.add_argument("--extra-path", default="")
    args = parser.parse_args()
    try:
        with contextlib.redirect_stdout(io.StringIO()) as quiet:
            status = mm.check()
        if status:
            print(quiet.getvalue(), end="")
            return 1
        if args.cmd == "plan":
            cmd_plan(args.steps)
        elif args.cmd == "apply":
            steps = args.steps or [s for s in STEPS if args.through and s <= args.through]
            if not steps:
                raise Stop("name the steps (b1 ... b5) or --through bN")
            if not args.allow_dirty and git("status", "--porcelain").strip():
                raise Stop("the working tree is not clean; commit or stash first (or --allow-dirty)")
            for step in steps:
                apply_step(step)
                if args.commit and git("status", "--porcelain").strip():
                    commit(step)
            for note in NOTES:
                print("note", note)
        elif args.cmd == "baseline":
            cmd_baseline(args.out)
        elif args.cmd == "verify":
            return cmd_verify(args.baseline, not args.no_collect)
        elif args.cmd == "lint":
            return cmd_lint(args.extra_path)
        elif args.cmd == "dry-run":
            return cmd_dry_run(args.through, args.keep, args.scratch, args.extra_path)
    except Stop as why:
        print(f"stop: {why}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
