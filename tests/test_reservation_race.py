"""The race test docs/ARCHITECTURE.md (section 7, "The biggest risk") requires before anything is extracted: the reservation
that prevents a duplicate application to a real employer must hold under concurrency.

The reservation is one PostgreSQL transaction: `Database.exclusive()` takes the app's advisory lock, and
`Service.reserve_submission` flips the run from "running" to "submitting" (a compare-and-set) and spends the day's cap in it.
Here it runs against a scratch PostgreSQL server (tests/support_pg.py), with real threads on separate connections and real
worker processes, and it asks three things:

  1. however many workers ask to commit one run, exactly one is granted, and the day's cap is spent once;
  2. however many workers ask at once for different runs, no more than the cap are granted;
  3. a process that dies between the reservation and the click leaves "submitting" on disk, which every later attempt
     (a retry, a second worker, a restart) meets as a refusal; one that dies inside the reservation leaves nothing.

Admission (`Service._admit`) is the other half: a job never has two live runs, so a second run cannot ask at all. It is
tested concurrently too. Synthetic data only; a computer without PostgreSQL 17 skips the file.
"""

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from jobpilot import host_health
from jobpilot.admission import QueueBlocked
from jobpilot.db import Budget, Database, Job, Run
from jobpilot.features._store import create_tables
from jobpilot.service import Service
from jobpilot.workers import recover_interrupted

from support_pg import ScratchServer, find_bin

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[1]
CHILD = Path(__file__).with_name("support_race_child.py")
NOT_AUTHORIZED = "Submission is not authorized for this run"


@pytest.fixture(scope="session")
def scratch_server(tmp_path_factory):
    bin_dir = find_bin()
    if bin_dir is None:
        pytest.skip("PostgreSQL 17 is not installed (brew install postgresql@17)")
    server = ScratchServer(bin_dir, tmp_path_factory.mktemp("scratch-pg") / "data").start()
    yield server
    server.stop()


@pytest.fixture
def database_url(scratch_server):
    return scratch_server.url(scratch_server.create_database())


@pytest.fixture
def service(config, database_url):
    """The Service on a fresh database of the scratch server (the conftest fixture of the same name uses SQLite)."""
    config.database_url = database_url
    db = Database(database_url)
    created = Service(db, config)
    create_tables(db.engine)
    yield created
    db.engine.dispose()


@pytest.fixture(autouse=True)
def clean_drain():
    host_health.DRAINING.clear()
    yield
    host_health.DRAINING.clear()


def today(service):
    return datetime.now(ZoneInfo(service.config.timezone)).date().isoformat()


def seed(service, name, state="running"):
    """A submit-mode run of its own job at its own employer, as the engine holds it when it reaches the final step."""
    service.db.set_setting("application_budget", {"max_seconds": None})
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    with service.db.exclusive() as s:
        s.add(Job(id=name, identity=name, url=f"https://careers.example.test/{name}", ats="oracle",
                  company=f"Employer {name}", title="Engineer", status="running"))
        s.add(Run(id=name + "-run", job_id=name, mode="submit", state=state, started_at=datetime.now(UTC).isoformat(),
                  packet={"match_approved": True, "profile": {"autonomous": True}}))
    return name + "-run"


def together(calls):
    """Each call in its own thread (so its own connection), released at the same moment: ("ok", value) or ("error", exc)."""
    barrier = threading.Barrier(len(calls))

    def go(call):
        barrier.wait()
        try:
            return "ok", call()
        except BaseException as exc:  # noqa: BLE001 - the refusal itself is what the test reads
            return "error", exc

    with ThreadPoolExecutor(len(calls)) as pool:
        return list(pool.map(go, calls))


def granted(results):
    return [r for r in results if r[0] == "ok"]


def refused(results):
    return [r[1] for r in results if r[0] == "error"]


def state_of(service, run_id):
    with service.db.session() as s:
        return s.get(Run, run_id).state


def submissions(service):
    with service.db.session() as s:
        budget = s.get(Budget, today(service))
        return budget.submissions if budget else 0


# --- 1. one run, many workers -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("workers", [2, 16])
def test_one_run_is_reserved_once_however_many_workers_ask(service, workers):
    run_id = seed(service, "one")
    results = together([lambda: service.reserve_submission(run_id)] * workers)
    assert len(granted(results)) == 1, "exactly one worker may be told to click"
    errors = refused(results)
    assert len(errors) == workers - 1 and all(str(e) == NOT_AUTHORIZED for e in errors), errors
    assert state_of(service, run_id) == "submitting"
    assert submissions(service) == 1, "the day's cap is spent once, not once per asker"


def test_a_released_reservation_is_granted_again_to_one_worker_only(service):
    """The employer refused the commit (field errors stayed): the one reservation goes back and is asked for again, by
    the same run's retry and by any duplicate delivery of it, concurrently. Still one grant."""
    run_id = seed(service, "again")
    service.reserve_submission(run_id)
    assert service.release_submission(run_id) is True
    assert submissions(service) == 0
    results = together([lambda: service.reserve_submission(run_id)] * 8)
    assert len(granted(results)) == 1
    assert submissions(service) == 1
    assert service.release_submission(run_id) is True
    assert service.release_submission(run_id) is False, "a second release must not refund the cap twice"
    assert submissions(service) == 0


def test_a_release_racing_the_reservation_never_leaves_the_cap_wrong(service):
    run_id = seed(service, "raced")
    service.reserve_submission(run_id)
    calls = [lambda: service.release_submission(run_id)] * 4 + [lambda: service.reserve_submission(run_id)] * 4
    together(calls)
    # Whatever the order, the budget agrees with the run: spent exactly when the run is "submitting".
    assert submissions(service) == (1 if state_of(service, run_id) == "submitting" else 0)


# --- 2. many runs, one cap ----------------------------------------------------------------------------------------------


def test_the_daily_cap_holds_when_different_runs_reserve_at_once(service):
    service.config.daily_application_limit = 3
    runs = [seed(service, f"job{n}") for n in range(12)]
    results = together([lambda run_id=run_id: service.reserve_submission(run_id) for run_id in runs])
    assert len(granted(results)) == 3
    assert all(str(e) == "Daily submission cap reached" for e in refused(results)), refused(results)
    assert submissions(service) == 3
    with service.db.session() as s:
        assert s.scalar(select(func.count()).select_from(Run).where(Run.state == "submitting")) == 3


# --- 3. separate processes ----------------------------------------------------------------------------------------------


def child(mode, service, run_id, start_at, daily_limit=100):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(service.config.data_dir), "RACE_DAILY_LIMIT": str(daily_limit)}
    return subprocess.Popen(
        [sys.executable, "-I", str(CHILD), mode, str(REPO), service.config.database_url, str(service.config.data_dir),
         run_id, str(start_at)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )


def finish(process):
    out, err = process.communicate(timeout=120)
    return process.returncode, out.strip(), err.strip()


def test_separate_worker_processes_are_granted_one_reservation_between_them(service):
    """Not threads of one interpreter: the CLI and the app are separate processes on one database."""
    run_id = seed(service, "procs")
    start = time.time() + 12  # Every process has imported the engine by then and asks in the same few milliseconds.
    done = [finish(p) for p in [child("reserve", service, run_id, start) for _ in range(4)]]
    assert all(code == 0 for code, _, _ in done), done
    answers = [out for _, out, _ in done]
    assert sum(a == "RESERVED" for a in answers) == 1, answers
    assert all(a == "RESERVED" or a == f"REFUSED ValueError {NOT_AUTHORIZED}" for a in answers), answers
    assert state_of(service, run_id) == "submitting" and submissions(service) == 1


# --- 4. a crash between the reservation and the click -------------------------------------------------------------------


def test_a_crash_after_the_reservation_leaves_it_on_disk_and_every_later_attempt_refused(service):
    """The process is gone before the click: whether the click happened is unknown, so nothing may click again. The row
    says "submitting" (the commit waited for the disk); a retry, a second worker and the restart's own recovery meet it."""
    run_id = seed(service, "crash")
    code, out, err = finish(child("die-after-reserve", service, run_id, time.time()))
    assert code == 9 and out == "RESERVED", (code, out, err)
    assert state_of(service, run_id) == "submitting" and submissions(service) == 1

    # Before the restart has reclassified it: nobody can reserve it again, and the job admits no new run.
    results = together([lambda: service.reserve_submission(run_id)] * 6)
    assert not granted(results) and all(str(e) == NOT_AUTHORIZED for e in refused(results))

    # The restart: "submitting" becomes an uncertain submission, never a retry.
    recover_interrupted(service)
    with service.db.session() as s:
        run, job = s.get(Run, run_id), s.get(Job, "crash")
        assert run.state == "submission_unknown" and job.status == "submission_unknown"
        assert "reconcile before retrying" in run.reason
    results = together([lambda: service.reserve_submission(run_id)] * 6)
    assert not granted(results), "the uncertain run cannot be committed again"
    assert submissions(service) == 1, "and the cap stays spent: the click may have been delivered"


def test_a_crash_inside_the_reservation_leaves_nothing_and_the_retry_is_granted_once(service):
    """Dies with the UPDATE written and the transaction open: PostgreSQL rolls it back and frees the app lock, so the run is
    still "running" with no cap spent, and the next attempt (two at once) gets exactly one grant, with no deadlock."""
    run_id = seed(service, "inside")
    code, out, err = finish(child("die-before-commit", service, run_id, time.time()))
    assert code == 9 and out == "", (code, out, err)
    assert state_of(service, run_id) == "running" and submissions(service) == 0
    results = together([lambda: service.reserve_submission(run_id)] * 2)
    assert len(granted(results)) == 1
    assert state_of(service, run_id) == "submitting" and submissions(service) == 1


def test_a_crash_before_the_reservation_is_a_free_retry(service):
    """A "running" run with no reservation clicked nothing: the restart sets it up for a draft recovery, not an
    uncertain submission, and its job may be applied to again."""
    run_id = seed(service, "early")
    recover_interrupted(service)
    with service.db.session() as s:
        assert s.get(Run, run_id).state == "waiting_browser"
    assert submissions(service) == 0


# --- 5. admission: a job never has two live runs ------------------------------------------------------------------------


@pytest.fixture
def admit(prepared, service):
    """admit() asks the queue's own admission (what Apply, the autopilot and recovery call, off the event loop) for a run of
    a job it admits in submit mode: a synthetic profile and resume, nothing dispatched to a browser."""
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    with service.db.exclusive() as s:
        s.add(Job(id="adm", identity="adm", url="https://boards.greenhouse.io/acmeexample/jobs/4001",
                  title="Software Engineer", company="Acme Example", ats="greenhouse", status="ready", score=90))
    return lambda: service._admit("adm", "submit", prepared["id"], owner_pick=True)


def runs_of(service, job_id="adm"):
    with service.db.session() as s:
        return list(s.scalars(select(Run).where(Run.job_id == job_id)))


def test_one_job_asked_for_by_many_workers_gets_one_run(service, admit):
    """The autopilot, an Apply click, a retry and a duplicate delivery can all ask in the same moment; the admission's
    "already has an active run" check and the run it writes share one transaction under the app lock."""
    results = together([admit] * 8)
    assert len(granted(results)) == 1, refused(results)
    assert all(isinstance(e, QueueBlocked) and e.code == "active_run" for e in refused(results)), refused(results)
    assert len(runs_of(service)) == 1


def test_a_reserved_run_and_then_an_uncertain_one_admit_no_second_run_even_at_once(service, admit):
    """The crash above, seen from the queue. While the row says "submitting" the job has a live run; after the restart
    reclassifies it, the job has an uncertain submission. Eight concurrent asks are refused both times (code
    active_run, then submitted_or_uncertain) and no second run is ever written."""
    (_, run_id), = [r[1] for r in together([admit])]
    with service.db.exclusive() as s:
        s.get(Run, run_id).state = "running"  # The claim _execute_owned makes before the engine runs.
    service.reserve_submission(run_id)
    assert state_of(service, run_id) == "submitting"
    during = together([admit] * 8)
    assert not granted(during) and {e.code for e in refused(during)} == {"active_run"}
    recover_interrupted(service)
    after = together([admit] * 8)
    assert not granted(after) and {e.code for e in refused(after)} == {"submitted_or_uncertain"}
    assert len(runs_of(service)) == 1


# --- 6. one run delivered more than once ------------------------------------------------------------------------------------


def test_a_run_delivered_many_times_starts_one_engine_and_one_commit(service, monkeypatch):
    """A dispatch that is retried (the outbox, a restart, two processes) delivers the same run again. The claim from
    "queued" to "running" is the only thing that stops two engines from holding one run, and with them two clicks:
    authorize_validation_retry has no compare-and-set of its own, so the claim carries that weight."""
    import asyncio

    from jobpilot import pipeline_comparison

    started, clicks = [], []

    class Engine:
        result = None

        def __init__(self, owner):
            self.owner = owner

        async def run(self, job, run):
            started.append(run["id"])
            await asyncio.to_thread(self.owner.reserve_submission, run["id"])
            clicks.append(run["id"])
            return {"state": "submission_unknown", "reason": "fixture: the click was sent"}

    monkeypatch.setattr(pipeline_comparison, "engine_for", lambda owner, run: Engine(owner))
    monkeypatch.setattr(pipeline_comparison, "slots", lambda *a, **k: ["owner-1"])
    monkeypatch.setattr(host_health, "check", lambda *a, **k: host_health.Gate(6))
    run_id = seed(service, "dup", state="queued")
    workers = [Service(Database(service.config.database_url), service.config) for _ in range(6)]
    results = together([lambda w=w: asyncio.run(w._execute_owned(run_id)) for w in workers])
    assert [r for r in results if r[0] == "error"] == [], refused(results)
    assert started == [run_id] and clicks == [run_id], "one delivery owns the run; the others return at the claim"
    assert state_of(service, run_id) == "submission_unknown" and submissions(service) == 1
    for w in workers:
        w.db.engine.dispose()
