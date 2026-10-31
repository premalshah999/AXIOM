"""scripts/dev/move.py: the rewrites and the old-name finder that let phase (b) of docs/ARCHITECTURE.md move every module
while fixers keep landing on main. Synthetic code only; the state of the real tree (moved or not) must not matter, so the
tests fix what counts as moved."""

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def move(monkeypatch):
    spec = importlib.util.spec_from_file_location("move_under_test", ROOT / "scripts" / "dev" / "move.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts" / "dev"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT / "scripts" / "dev"))
    monkeypatch.setattr(module, "done_symbols", lambda rel: {})  # symbol moves read the tree; these tests do not
    return module


def test_a_relative_import_in_a_moved_file_names_the_new_home_of_each_module(move, monkeypatch):
    monkeypatch.setattr(move, "is_moved", lambda rel: True)
    source = "from .ats import detect\nfrom . import host_health\n\n\ndef f():\n    from .application_budget import nothing_sent\n"
    out, count = move.rewrite_text(source, "jobpilot.autopilot", False)
    assert count == 3
    assert "from axiom_domain.ats import detect" in out
    assert "from axiom_ledger import host_gate as host_health" in out  # the local name stays, so the body is untouched
    assert "    from axiom_ledger.budget import nothing_sent" in out  # function-level imports count, with their indentation


def test_an_import_of_a_module_that_has_not_moved_becomes_absolute_and_stays_in_jobpilot(move, monkeypatch):
    monkeypatch.setattr(move, "is_moved", lambda rel: False)
    out, _ = move.rewrite_text("from .ats import detect, ATS as A\n", "jobpilot.autopilot", False)
    assert out == "from jobpilot.ats import detect, ATS as A\n"


def test_an_absolute_import_that_is_already_right_is_left_alone(move, monkeypatch):
    monkeypatch.setattr(move, "is_moved", lambda rel: True)
    source = "from axiom_domain.ats import detect\nimport json\nfrom jobpilot.ats import detect\n"
    out, _ = move.rewrite_text(source, "jobpilot.autopilot", False)
    assert out == "from axiom_domain.ats import detect\nimport json\nfrom axiom_domain.ats import detect\n"
    again, count = move.rewrite_text(out, "jobpilot.autopilot", False)
    assert (again, count) == (out, 0)  # running it twice changes nothing


def test_a_dynamic_import_of_a_moved_module_is_followed(move, monkeypatch):
    monkeypatch.setattr(move, "is_moved", lambda rel: True)
    out, _ = move.rewrite_text('x = __import__("jobpilot.runtime", fromlist=["engine_version"])\n', "jobpilot.cli", False)
    assert '"axiom_core.runtime"' in out


def test_the_frame_filter_learns_the_new_package_names_and_only_once(move):
    line = '    if "/jobpilot/" in frame.filename\n'
    once = move.FRAME_FILTER.sub(lambda m: f'("/jobpilot/" in {m.group(1)} or "/axiom_" in {m.group(1)})', line)
    assert once == '    if ("/jobpilot/" in frame.filename or "/axiom_" in frame.filename)\n'
    assert move.FRAME_FILTER.sub(lambda m: "CHANGED", once) == once


def test_the_map_has_one_home_per_module_and_every_step_is_covered(move):
    homes = [move.dest(rel) for rel in move.mm.MAP]
    assert len(homes) == len(set(homes))
    stepped = {rel for rel in move.mm.MAP if move.step_of(rel)}
    held = set(move.GIANTS) | set(move.HOLD) | {"__init__.py"} | {rel for rel in move.mm.MAP if move.facade(rel)}
    assert stepped | held == set(move.mm.MAP)


def test_the_finder_answers_an_old_name_with_the_same_module_and_leaves_the_rest(tmp_path, move):
    """`jobpilot.old` is the module at its new home, not a copy of it, so patching either name reaches the real code."""
    (tmp_path / "jobpilot").mkdir()
    (tmp_path / "jobpilot" / "__init__.py").write_text("from . import _moved\n")
    (tmp_path / "jobpilot" / "kept.py").write_text("VALUE = 1\n")
    (tmp_path / "jobpilot" / "pkg").mkdir()
    (tmp_path / "jobpilot" / "pkg" / "late.py").write_text("LATE = 2\n")
    (tmp_path / "newhome").mkdir()
    (tmp_path / "newhome" / "__init__.py").write_text("")
    (tmp_path / "newhome" / "moved.py").write_text("import sys\nNAME = __name__\n\n\ndef main():\n    print('ran', __name__)\n\n\nif __name__ == '__main__':\n    main()\n")
    (tmp_path / "newhome" / "package.py").write_text("LOCAL = 3\n")
    text = (
        move.MOVED_PY.replace("__PACKAGES__", "('newhome',)")
        .replace("__ALIASES__", '    "jobpilot.old": "newhome.moved",\n    "jobpilot.pkg": "newhome.package",')
        .replace("__OLD_PACKAGES__", '{"jobpilot.pkg"}')
    )
    (tmp_path / "jobpilot" / "_moved.py").write_text(text)
    probe = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, "NEWHOME")
        import jobpilot.old, newhome.moved
        from jobpilot import old
        assert jobpilot.old is newhome.moved is old, "one module under two names"
        assert newhome.moved.__spec__.name == "newhome.moved", "the real module keeps its own spec"
        import jobpilot.kept
        assert jobpilot.kept.VALUE == 1
        from jobpilot.pkg import late   # a child that did not move, under a package whose __init__ did
        assert late.LATE == 2
        import jobpilot.pkg.late
        assert jobpilot.pkg.late is late
        """
    ).replace("NEWHOME", str(tmp_path))
    done = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    ran = subprocess.run([sys.executable, "-m", "jobpilot.old"], cwd=tmp_path, env={"PYTHONPATH": str(tmp_path)}, capture_output=True, text=True)
    assert ran.returncode == 0 and "ran __main__" in ran.stdout, ran.stderr  # `python -m jobpilot.cli` keeps working
