"""scripts/release.py decides what happens to the running app when something goes wrong: every way out, on stand-in bundles.

No real app, database or swap of anything real. A stand-in bundle is a shell script that records what it was asked and answers as told.
What is asserted is what the release must never get wrong: the old build back in place, the hold back to what it was, nothing replaced
while a commit is under way, and the exit status of the stage that failed.
"""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_script", ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

HOLD_BEFORE = '{"stage": "complete"}'


def stand_in(path, name):
    """A bundle whose `axiom` records its calls in `calls`, reads its answers from `answers.json` beside it."""
    bin_dir = path / "Contents" / "Resources" / "bin"
    bin_dir.mkdir(parents=True)
    script = bin_dir / "axiom"
    script.write_text(f"""#!/bin/sh
echo "{name} $*" >> "{path.parent}/calls"
case "$1 $2" in
  "smoke --no-fill"*) cat "{path.parent}/smoke_final" 2>/dev/null; exit $(cat "{path.parent}/smoke_final_status" 2>/dev/null || echo 0) ;;
  "smoke "*) exit $(cat "{path.parent}/smoke_status_{name}" 2>/dev/null || echo 0) ;;
  "status --json") echo '{{"phase":"running","children":{{"app":{{"state":"up"}}}}}}'; exit 0 ;;
  "hold "*) exit 0 ;;
  "stop"*) exit $(cat "{path.parent}/stop_status" 2>/dev/null || echo 0) ;;
  "psql "*) case "$*" in
      *"select value::text from settings where key = 'execution_v2_deployment_hold'"*) cat "{path.parent}/hold_value" ;;
      *"state = 'submitting'"*) cat "{path.parent}/committing" 2>/dev/null || echo 0 ;;
      *"state in ('running','submitting')"*) cat "{path.parent}/in_flight" 2>/dev/null || echo 0 ;;
      *) echo 0 ;;
    esac; exit 0 ;;
esac
exit 0
""")
    script.chmod(0o755)
    return path


@pytest.fixture
def scene(tmp_path, monkeypatch):
    """Two stand-in bundles and a Release ready to drain, swap and check, with gate, build and copy already done."""
    work = tmp_path / "work"
    work.mkdir()
    app = stand_in(tmp_path / "Applications" / "AXIOM.app", "old")
    new = stand_in(work / "AXIOM.new.app", "new")
    (tmp_path / "Applications" / "hold_value").write_text(HOLD_BEFORE)
    (work / "hold_value").write_text(HOLD_BEFORE)
    # Each bundle sees its answers beside it, and a rename moves it: both folders hold the same scene files.
    monkeypatch.setattr(release, "POLL_SECONDS", 0)
    monkeypatch.setattr(release, "QUIT_SECONDS", 1)
    monkeypatch.setattr(release, "START_SECONDS", 2)
    real_run = release.run

    def run(args, **kwargs):
        if str(args[0]) in {"open", "pkill", "pgrep"}:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return real_run(args, **kwargs)

    monkeypatch.setattr(release, "run", run)
    rel = release.Release(release.options(["--app", str(app), "--work", str(work), "--drain-seconds", "1"]))
    rel.preflight = rel.gate = rel.build = rel.replay = lambda: None
    rel.corpus = lambda *a, **k: None
    rel.head = "abc123"
    rel.new_engine = "execution-v2-new"
    rel.verify_copy = lambda: None
    return SimpleNamespace(rel=rel, app=app, new=new, work=work, apps=tmp_path / "Applications")


def tell(scene, name, value):
    (scene.work / name).write_text(str(value))
    (scene.apps / name).write_text(str(value))


def calls(scene):
    found = []
    for folder in (scene.work, scene.apps):
        path = folder / "calls"
        found += path.read_text().splitlines() if path.exists() else []
    return found


def which_build(scene):
    script = (scene.app / "Contents" / "Resources" / "bin" / "axiom").read_text()
    return "new" if 'echo "new ' in script else "old"


def status_of(rel):
    try:
        return rel.ship()
    except release.Failed as exc:
        return exc.code


def test_the_happy_path_installs_the_new_build_and_puts_the_hold_back(scene):
    assert status_of(scene.rel) == 0
    assert which_build(scene) == "new"
    log = calls(scene)
    assert any("hold on" in c for c in log)
    assert any("update settings set value" in c and '"complete"' in c for c in log), "the hold's exact earlier value goes back"
    assert log.index(next(c for c in log if "hold on" in c)) < log.index(next(c for c in log if c.endswith("stop")))


def test_a_failed_smoke_restores_the_previous_build_and_the_hold(scene):
    tell(scene, "smoke_status_new", 1)
    assert status_of(scene.rel) == 6
    assert which_build(scene) == "old"
    assert (scene.work / "AXIOM.failed.app").exists()
    assert not scene.rel.holding
    assert any("update settings set value" in c for c in calls(scene))


def test_a_commit_still_running_when_time_is_up_stops_the_release_with_nothing_changed(scene):
    tell(scene, "in_flight", 1)
    tell(scene, "committing", 1)
    assert status_of(scene.rel) == 4
    assert which_build(scene) == "old"
    log = calls(scene)
    assert not any(c.endswith(" stop") for c in log), "nothing may be stopped beneath a commit"
    assert not scene.rel.holding


def test_never_cut_gives_up_rather_than_interrupt_a_run(scene):
    scene.rel.options.never_cut = True
    tell(scene, "in_flight", 1)
    assert status_of(scene.rel) == 4
    assert which_build(scene) == "old"


def test_a_run_that_outlives_the_drain_is_stopped_and_counted_as_a_free_resume(scene):
    tell(scene, "in_flight", 1)
    assert status_of(scene.rel) == 0
    assert which_build(scene) == "new" and scene.rel.cut == 1


def test_an_app_that_will_not_stop_is_not_replaced(scene, monkeypatch):
    real = release.Installed.processes
    monkeypatch.setattr(release.Installed, "processes", lambda self: ["1"] if self.app == scene.app else real(self))
    assert status_of(scene.rel) == 5
    assert which_build(scene) == "old"
    assert not scene.rel.holding


def test_a_hold_someone_else_set_is_still_on_afterwards(scene):
    theirs = json.dumps({"stage": "draining", "reason": "the owner's own hold"})
    tell(scene, "hold_value", theirs)
    assert status_of(scene.rel) == 0
    assert any("update settings set value" in c and "the owner's own hold".replace("'", "''") in c for c in calls(scene))


def test_being_interrupted_during_the_drain_restores_the_hold(scene):
    def interrupted():
        raise release.Failed("Interrupted by signal 15", 8)

    scene.rel.drain = interrupted
    assert status_of(scene.rel) == 8
    assert which_build(scene) == "old" and not scene.rel.holding


def test_the_gate_failure_is_read_from_its_real_status(tmp_path, monkeypatch):
    """A red gate was deployed through `gate | tail -1 && deploy` (2026-10-29): the output says nothing here, the status decides."""
    rel = release.Release(release.options(["--work", str(tmp_path)]))
    monkeypatch.setattr(release, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))
    with pytest.raises(release.Failed) as failed:
        rel.gate()
    assert failed.value.code == 2


def test_a_dirty_checkout_is_refused_because_no_commit_would_name_what_ships(tmp_path, monkeypatch):
    rel = release.Release(release.options(["--work", str(tmp_path), "--until", "verified"]))
    monkeypatch.setattr(release, "git", lambda *a: " M jobpilot/browser.py" if a[0] == "status" else "abc123")
    with pytest.raises(release.Failed) as failed:
        rel.preflight()
    assert failed.value.code == 2 and "uncommitted" in str(failed.value)
