"""The owner's offline license key (jobpilot/licensing.py, scripts/axiom_license.py, docs/LICENSING.md).

Seams: the `jobpilot.licensing` interface (codes, keys, `Licensing`), `GET/POST /api/license`, the two gates every new application
passes (`Service._admit` and the claim in `Service._execute_owned`), and the owner's command. Every key here is made with a
throwaway key pair, for synthetic Mac ids. The pass/fail proof of the screen is a real browser on an empty install, not these."""

import asyncio
import base64
import importlib.util
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from jobpilot import licensing
from jobpilot.admission import QueueBlocked
from jobpilot.db import Job, Run, Setting, now
from jobpilot.licensing import LicenseError, Licensing, mac_code_for, make_key

MY_MAC, OTHER_MAC = "11111111-AAAA-BBBB-CCCC-000000000001", "22222222-AAAA-BBBB-CCCC-000000000002"
NOON = datetime(2027, 3, 10, 12, 0, tzinfo=UTC)


def public_text(private):
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def owner():
    """The owner's key pair, made fresh."""
    return Ed25519PrivateKey.generate()


@pytest.fixture
def required(monkeypatch, owner):
    """A build that asks for a key, on the Mac MY_MAC."""
    monkeypatch.setattr(licensing, "load_build", lambda: licensing.Build(True, public_text(owner)))
    monkeypatch.setattr(licensing, "hardware_uuid", lambda: MY_MAC)
    licensing._instances.clear()
    yield
    licensing._instances.clear()


class Clock:
    def __init__(self, at=NOON):
        self.at = at

    def __call__(self):
        return self.at

    def days(self, n):
        self.at += timedelta(days=n)


def key_for(owner, mac_uuid, days_from_now, clock_date=NOON.date(), name=""):
    return make_key(owner, mac_code_for(mac_uuid), clock_date + timedelta(days=days_from_now), clock_date, name)


# --- this Mac's code ---------------------------------------------------------------------------------------------------------


def test_the_mac_code_is_short_stable_and_does_not_contain_the_hardware_id():
    code = mac_code_for(MY_MAC)
    assert len(code) == 14 and code.count("-") == 2
    assert code == mac_code_for(MY_MAC) == mac_code_for(MY_MAC.lower())
    assert code != mac_code_for(OTHER_MAC)
    assert licensing.normalize_mac_code(code.lower().replace("-", " ")) == code
    assert "11111111" not in code and MY_MAC.replace("-", "")[:8] not in code


def test_a_mistyped_mac_code_is_refused_in_words():
    with pytest.raises(LicenseError, match="not a Mac code"):
        licensing.normalize_mac_code("ABC")


# --- keys --------------------------------------------------------------------------------------------------------------------


def test_a_key_for_this_mac_opens_it_and_shows_its_end_date(required, owner, tmp_path):
    lic = Licensing(tmp_path, Clock())
    assert lic.status()["state"] == "none"
    status = lic.install(key_for(owner, MY_MAC, 30, name="Sam"))
    assert status["state"] == "valid" and status["starts_allowed"] and status["days_left"] == 30
    assert status["expires"] == "2027-04-09" and status["name"] == "Sam" and not status["blocking"]


def test_a_key_copied_to_another_mac_is_refused_and_says_whose_it_is(required, owner, tmp_path):
    lic = Licensing(tmp_path, Clock())
    with pytest.raises(LicenseError) as refused:
        lic.install(key_for(owner, OTHER_MAC, 30))
    words = str(refused.value)
    assert "different Mac" in words and mac_code_for(OTHER_MAC) in words and mac_code_for(MY_MAC) in words
    assert lic.status()["state"] == "none" and not lic.key_path.exists()


def test_a_forged_key_is_refused(required, tmp_path):
    lic = Licensing(tmp_path, Clock())
    forger = Ed25519PrivateKey.generate()
    with pytest.raises(LicenseError, match="not genuine"):
        lic.install(key_for(forger, MY_MAC, 30))
    assert lic.status()["state"] == "none"


def test_a_key_with_its_date_edited_is_refused(required, owner, tmp_path):
    token = key_for(owner, MY_MAC, 2)
    head, signature = token[len(licensing.KEY_PREFIX) :].split(".")
    payload = json.loads(licensing._unb64(head))
    payload["exp"] = "2099-01-01"
    edited = licensing.KEY_PREFIX + licensing._b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()) + "." + signature
    with pytest.raises(LicenseError, match="not genuine"):
        Licensing(tmp_path, Clock()).install(edited)


@pytest.mark.parametrize("text", ["", "hello", "AXIOM1-", "AXIOM1-abc.def", "AXIOM1-!!!.???", "AXIOM1-a.b.c"])
def test_things_that_are_not_keys_are_refused_in_words(required, tmp_path, text):
    with pytest.raises(LicenseError) as refused:
        Licensing(tmp_path, Clock()).install(text)
    assert "key" in str(refused.value).lower() and "Traceback" not in str(refused.value)


def test_a_key_pasted_with_line_breaks_and_spaces_still_works(required, owner, tmp_path):
    token = key_for(owner, MY_MAC, 5)
    wrapped = "\n".join(token[i : i + 40] for i in range(0, len(token), 40)) + "\n  "
    assert Licensing(tmp_path, Clock()).install(wrapped)["state"] == "valid"


def test_an_already_expired_key_is_refused_when_entered(required, owner, tmp_path):
    with pytest.raises(LicenseError, match="ran out on"):
        Licensing(tmp_path, Clock()).install(key_for(owner, MY_MAC, -1))


# --- expiry and the clock ----------------------------------------------------------------------------------------------------


def test_a_key_works_through_its_last_day_and_stops_the_day_after(required, owner, tmp_path):
    clock = Clock()
    lic = Licensing(tmp_path, clock)
    lic.install(key_for(owner, MY_MAC, 3))
    clock.days(3)
    assert lic.status()["state"] == "valid" and lic.status()["days_left"] == 0
    clock.days(1)
    status = lic.status()
    assert status["state"] == "expired" and not status["starts_allowed"] and "ran out on March 13, 2027" in status["reason"]
    assert not status["blocking"]  # The app opens; only new applications are held.


def test_setting_the_clock_back_does_not_bring_an_expired_key_back(required, owner, tmp_path):
    clock = Clock()
    lic = Licensing(tmp_path, clock)
    lic.install(key_for(owner, MY_MAC, 3))
    clock.days(5)
    assert lic.status()["state"] == "expired"
    clock.days(-5)  # The person sets the Mac's clock back to when the key was good.
    status = lic.status()
    assert status["state"] == "expired" and "clock is behind" in status["clock_note"]
    # A fresh process remembers it too: the memory is a file, not the object.
    again = Licensing(tmp_path, clock)
    assert again.status()["state"] == "expired"


def test_a_clock_set_back_before_installing_does_not_make_an_old_key_installable(required, owner, tmp_path):
    clock = Clock()
    lic = Licensing(tmp_path, clock)
    lic.install(key_for(owner, MY_MAC, 3))
    clock.days(10)
    lic.status()
    clock.days(-10)
    with pytest.raises(LicenseError, match="ran out on"):
        Licensing(tmp_path, clock).install(key_for(owner, MY_MAC, 3))


def test_a_key_lasts_through_its_last_day_on_the_persons_calendar_though_the_server_runs_in_utc(required, owner, tmp_path):
    # The Mac app's server process has TZ=UTC (native/env.py), as this file's fixture sets it. March 13 is the last day.
    clock = Clock()
    new_york = Licensing(tmp_path / "ny", clock, zone="America/New_York")
    new_york.install(key_for(owner, MY_MAC, 3))
    clock.at = datetime(2027, 3, 14, 2, 0, tzinfo=UTC)  # 9 p.m. on March 13 there.
    assert new_york.status()["state"] == "valid" and new_york.status()["days_left"] == 0
    clock.at = datetime(2027, 3, 14, 6, 0, tzinfo=UTC)  # 2 a.m. on March 14 there.
    assert new_york.status()["state"] == "expired"
    assert Licensing(tmp_path / "ny", clock, zone="No/Such_Zone").status()["state"] == "expired"  # Falls back; does not crash.


def test_the_installation_reads_its_calendar_from_the_settings(required, service):
    service.config.timezone = "Asia/Tokyo"
    assert licensing.licensing(service.config).zone == "Asia/Tokyo"


def test_a_key_file_that_is_not_text_reads_as_no_key_and_does_not_break_the_app(required, owner, tmp_path):
    lic = Licensing(tmp_path, Clock())
    lic.install(key_for(owner, MY_MAC, 3))
    lic.key_path.write_bytes(b"\xff\xfe\x00\x80")
    lic._cached = None
    status = lic.status()
    assert status["state"] == "none" and not status["blocking"]


def test_a_key_that_cannot_be_saved_is_refused_in_words(required, owner, tmp_path, monkeypatch):
    def full(*_):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(licensing, "_write_private", full)
    with pytest.raises(LicenseError, match="could not save"):
        Licensing(tmp_path, Clock()).install(key_for(owner, MY_MAC, 3))


def test_the_license_memory_files_are_private(required, owner, tmp_path):
    lic = Licensing(tmp_path, Clock())
    lic.install(key_for(owner, MY_MAC, 3))
    lic.status()
    for path in (lic.key_path, lic.seen_path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(lic.folder.stat().st_mode) == 0o700


def test_a_stored_key_carried_to_another_mac_stops_new_applications_but_the_app_opens(required, owner, tmp_path, monkeypatch):
    lic = Licensing(tmp_path, Clock())
    lic.install(key_for(owner, MY_MAC, 30))
    monkeypatch.setattr(licensing, "hardware_uuid", lambda: OTHER_MAC)  # The data folder was copied to another Mac.
    moved = Licensing(tmp_path, Clock())
    status = moved.status()
    assert status["state"] == "invalid" and "different Mac" in status["reason"] and not status["starts_allowed"]
    assert not status["blocking"]  # It had been activated; the person's data stays reachable.


def test_a_key_file_swapped_for_a_forged_one_is_not_honoured(required, owner, tmp_path):
    lic = Licensing(tmp_path, Clock())
    lic.install(key_for(owner, MY_MAC, 30))
    lic.key_path.write_text(key_for(Ed25519PrivateKey.generate(), MY_MAC, 3000))
    lic._cached = None
    assert lic.status()["state"] == "invalid" and not lic.status()["starts_allowed"]


def test_no_key_on_first_open_is_the_whole_screen_and_a_deleted_key_later_is_not(required, owner, tmp_path):
    clock = Clock()
    lic = Licensing(tmp_path, clock)
    first = lic.status()
    assert first["state"] == "none" and first["blocking"] and first["code"] == mac_code_for(MY_MAC)
    lic.install(key_for(owner, MY_MAC, 30))
    lic.key_path.unlink()
    clock.days(1)
    later = lic.status()
    assert later["state"] == "none" and not later["blocking"] and not later["starts_allowed"]


def test_no_network_is_touched_by_checking_or_entering_a_key(required, owner, tmp_path, monkeypatch):
    def refuse(*_, **__):
        raise AssertionError("licensing opened a network connection")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    lic = Licensing(tmp_path, Clock())
    lic.status()
    lic.install(key_for(owner, MY_MAC, 30))
    assert lic.status()["state"] == "valid"


# --- off by default ----------------------------------------------------------------------------------------------------------


def test_licensing_is_off_unless_the_build_says_so(tmp_path, monkeypatch):
    monkeypatch.delenv("AXIOM_LICENSE_BUILD", raising=False)
    monkeypatch.setattr(licensing, "BUILD_FILE", tmp_path / "no-such-file.json")
    assert licensing.load_build().required is False
    lic = Licensing(tmp_path / "data", Clock())
    status = lic.status()
    assert status["state"] == "off" and status["starts_allowed"] and not status["blocking"] and not status["required"]
    licensing.require_license(SimpleNamespace(data_dir=tmp_path / "data", timezone="UTC"))  # Does not raise, and writes nothing.
    assert not (tmp_path / "data" / "license").exists()
    with pytest.raises(LicenseError, match="does not ask for a key"):
        lic.install("AXIOM1-x.y")


def test_the_build_setting_is_read_from_the_file_the_build_writes(tmp_path, monkeypatch, owner):
    build = tmp_path / "license_build.json"
    build.write_text(json.dumps({"required": True, "public_key": public_text(owner)}))
    monkeypatch.setattr(licensing, "BUILD_FILE", build)
    assert licensing.load_build() == licensing.Build(True, public_text(owner))
    build.write_text("{not json")
    assert licensing.load_build().required is False
    build.write_text(json.dumps({"required": "yes"}))  # Only an exact true turns it on.
    assert licensing.load_build().required is False


def test_the_environment_cannot_switch_off_a_build_that_asks_for_a_key(tmp_path, monkeypatch, owner):
    build = tmp_path / "license_build.json"
    build.write_text(json.dumps({"required": True, "public_key": public_text(owner)}))
    elsewhere = tmp_path / "other.json"
    elsewhere.write_text(json.dumps({"required": False}))
    monkeypatch.setattr(licensing, "BUILD_FILE", build)
    monkeypatch.setenv("AXIOM_LICENSE_BUILD", str(elsewhere))
    assert licensing.load_build().required is True


# --- the gates ---------------------------------------------------------------------------------------------------------------


def seed_job(service, name="acme", **job):
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    with service.db.exclusive() as s:
        s.add(Job(id=name, identity=name, url=f"https://careers.example.test/{name}", ats="greenhouse", company=name.title(),
                  title="Engineer", status="ready", score=90, **job))
    return name


def put_key(service, token, activated=True):
    folder = service.config.data_dir / "license"
    folder.mkdir(exist_ok=True)
    (folder / "key.txt").write_text(token)
    (folder / "seen.json").write_text(json.dumps({"latest": datetime.now(UTC).isoformat(), "activated": activated}))
    licensing._instances.clear()


def test_a_new_application_does_not_start_on_an_expired_key_and_writes_nothing(required, owner, service):
    job = seed_job(service)
    today = date.today()
    put_key(service, make_key(owner, mac_code_for(MY_MAC), today - timedelta(days=1), today - timedelta(days=30)))
    with pytest.raises(QueueBlocked) as refused:
        service._admit(job, "submit", owner_pick=True)
    assert refused.value.code == "license" and refused.value.outcome == "deferred"
    assert "run out" in str(refused.value) and "already started finish" in str(refused.value)
    with service.db.session() as s:
        assert s.query(Run).count() == 0
        assert s.get(Setting, "owner_pick:" + job) is None  # A refused click leaves nothing behind.


def test_a_valid_key_lets_admission_go_on_to_its_other_checks(required, owner, service):
    job = seed_job(service)
    today = date.today()
    put_key(service, make_key(owner, mac_code_for(MY_MAC), today + timedelta(days=5), today))
    try:
        service._admit(job, "submit", owner_pick=True)
    except (QueueBlocked, ValueError) as other:
        assert getattr(other, "code", "") != "license"  # Whatever else stops this synthetic job, it is not the key.


def test_a_demo_job_sends_nothing_and_is_never_held_by_the_key(required, service):
    job = seed_job(service, name="demo", demo=True)
    try:
        service._admit(job, "dry_run")
    except (QueueBlocked, ValueError) as other:
        assert getattr(other, "code", "") != "license"


def test_a_continuation_of_an_application_already_started_is_never_held(required, service):
    licensing.require_license(service.config, continuing=True)
    with pytest.raises(QueueBlocked):
        licensing.require_license(service.config)  # No key at all: a fresh start is held.


def claim_blockers(service, monkeypatch):
    from jobpilot.service import execution as service_module

    class Claimed(Exception):
        pass

    def stop_at_the_engine(*_):
        raise Claimed

    monkeypatch.setattr("jobpilot.pipeline_comparison.engine_for", stop_at_the_engine)
    monkeypatch.setattr(service, "check_cooldown", lambda *_: None)
    slept = []

    async def end_after_a_wait(seconds):
        slept.append(seconds)
        with service.db.exclusive() as s:
            for run in s.query(Run).filter(Run.state == "queued"):
                assert run.started_at is None  # Nothing was claimed under the expired key.
                run.state = "paused"  # The end of this fixture's loop.

    monkeypatch.setattr(service_module, "asyncio", SimpleNamespace(**{**vars(asyncio), "sleep": end_after_a_wait}))
    return Claimed, slept


def queued(service, name, **packet):
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    with service.db.exclusive() as s:
        s.add(Job(id=name, identity=name, url=f"https://careers.example.test/{name}", ats="oracle", status="ready"))
        s.add(Run(id=name + "-run", job_id=name, mode="submit", state="queued", packet=packet))
    return name + "-run"


async def test_a_queued_run_waits_while_the_key_has_run_out_and_says_why(required, owner, service, monkeypatch):
    _, slept = claim_blockers(service, monkeypatch)
    today = date.today()
    put_key(service, make_key(owner, mac_code_for(MY_MAC), today - timedelta(days=1), today - timedelta(days=30)))
    run_id = queued(service, "fresh")
    await service._execute_owned(run_id)
    assert slept == [5]
    assert "run out" in service.claim_gates[run_id]


async def test_a_run_that_continues_one_already_started_is_claimed_under_an_expired_key(required, owner, service, monkeypatch):
    claimed, _ = claim_blockers(service, monkeypatch)
    today = date.today()
    put_key(service, make_key(owner, mac_code_for(MY_MAC), today - timedelta(days=1), today - timedelta(days=30)))
    for name, packet in (("code", {"email_wait_resume": True}), ("again", {"source_run_id": "earlier"}), ("check", {"verification_only": True})):
        run_id = queued(service, name, **packet)
        try:
            await service._execute_owned(run_id)
        except claimed:
            pass
        with service.db.session() as s:
            assert s.get(Run, run_id).started_at is not None, name


async def test_a_queued_run_starts_under_a_valid_key(required, owner, service, monkeypatch):
    claimed, _ = claim_blockers(service, monkeypatch)
    today = date.today()
    put_key(service, make_key(owner, mac_code_for(MY_MAC), today + timedelta(days=5), today))
    run_id = queued(service, "fresh")
    try:
        await service._execute_owned(run_id)
    except claimed:
        pass
    with service.db.session() as s:
        assert s.get(Run, run_id).started_at is not None


def test_an_expired_key_does_not_touch_a_running_application_or_its_commit(required, owner, service):
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    with service.db.exclusive() as s:
        s.add(Job(id="live", identity="live", url="https://careers.example.test/live", ats="greenhouse", company="Live",
                  title="Engineer", status="running", score=90))
        s.add(Run(id="live-run", job_id="live", mode="submit", state="running", started_at=now(), created_at=now(),
                  packet={"match_approved": True, "profile": {"autonomous": True}, "worker_slot": 1}))
    today = date.today()
    put_key(service, make_key(owner, mac_code_for(MY_MAC), today - timedelta(days=1), today - timedelta(days=30)))
    assert licensing.licensing(service.config).status()["state"] == "expired"
    service.reserve_submission("live-run")  # The running application reaches its commit as it would have.
    with service.db.session() as s:
        assert s.get(Run, "live-run").state == "submitting"


# --- when a key runs out: the real queue, the real claim -------------------------------------------------------------------


class Waited(Exception):
    """The claim went to sleep instead of claiming."""


class StubEngine:
    """Everything up to the engine is the real code; this is what the browser would have done. `during` runs inside the run."""

    armed = False
    result = None

    def __init__(self, during=None):
        self.during = during
        self.runs = []

    async def run(self, job, run):
        self.runs.append(run["id"])
        return self.during(run) if self.during else {"state": "failed", "reason": "Stub engine ended"}


def real_claim(service, monkeypatch, engine):
    """The real claim, with the wait at its gates raising instead of sleeping, so a test sees "it would wait"."""
    from jobpilot.service import execution as service_module

    async def waited(_seconds):
        raise Waited

    monkeypatch.setattr("jobpilot.pipeline_comparison.engine_for", lambda *_: engine)
    monkeypatch.setattr(service_module, "asyncio", SimpleNamespace(**{**vars(asyncio), "sleep": waited}))


def fresh_key(owner, clock, days):
    today = clock.at.date()
    return make_key(owner, mac_code_for(MY_MAC), today + timedelta(days=days), today)


@pytest.fixture
def licensed(required, owner, prepared, service, client, monkeypatch):
    """An empty install on a build that asks for a key: a real resume and profile from the real routes, a key with three days left
    entered through the real route, and a clock the test moves. Only the Mac's id is synthetic."""
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    clock = Clock(datetime.now(UTC))
    lic = licensing.licensing(service.config)
    lic.clock = clock
    assert client.post("/api/license/key", json={"key": fresh_key(owner, clock, 3)}).json()["state"] == "valid"
    engine = StubEngine()
    real_claim(service, monkeypatch, engine)
    return SimpleNamespace(clock=clock, lic=lic, resume_id=prepared["id"], engine=engine)


def ready_job(service, name, resume_id):
    with service.db.exclusive() as s:
        s.add(Job(id=name, identity=name, url=f"https://boards.greenhouse.io/{name}/jobs/{1000 + len(name)}", ats="greenhouse", company=name.title(),
                  title="Engineer", status="ready", score=90, resume_id=resume_id))
    return name


async def clicked_apply(service, licensed, name):
    ready_job(service, name, licensed.resume_id)
    return (await service.queue(name, "submit", owner_pick=True))["id"]


def state_of(service, run_id):
    with service.db.session() as s:
        run = s.get(Run, run_id)
        return run.state, run.started_at, s.get(Job, run.job_id).status


async def test_a_queued_job_waits_when_its_key_runs_out_says_why_in_queue_and_starts_on_a_new_key(licensed, owner, service, client):
    from jobpilot.queue_order import active_rows

    run_id = await clicked_apply(service, licensed, "acme")
    licensed.clock.days(4)
    assert licensed.lic.status()["state"] == "expired"
    service.claim_gates = {}
    with pytest.raises(Waited):
        await service._execute_owned(run_id)
    state, started, job_status = state_of(service, run_id)
    assert state == "queued" and started is None and job_status == "queued" and licensed.engine.runs == []
    row = next(r for r in active_rows(service) if r["run_id"] == run_id)
    assert "run out" in row["gate"] and "Settings" in row["gate"]
    with service.db.session() as s:
        assert "worker_slot" not in s.get(Run, run_id).packet  # It holds no browser while it waits.

    assert client.post("/api/license/key", json={"key": fresh_key(owner, licensed.clock, 30)}).json()["state"] == "valid"
    await service._execute_owned(run_id)
    assert licensed.engine.runs == [run_id]


async def test_a_running_application_goes_through_its_commit_when_the_key_runs_out_during_it(licensed, service, monkeypatch):
    run_id = await clicked_apply(service, licensed, "acme")
    seen = {}

    def during(run):
        licensed.clock.days(4)
        seen["key"] = licensed.lic.status()["state"]
        service.reserve_submission(run["id"])  # The commit, under the expired key.
        seen["at_commit"] = state_of(service, run["id"])[0]
        receipt = {"type": "application_created", "status": 201, "job_id": run["job_id"], "run_id": run["id"]}
        return {"state": "confirmed", "reason": "The employer's own record", "receipt": receipt}

    licensed.engine.during = during
    await service._execute_owned(run_id)
    assert seen == {"key": "expired", "at_commit": "submitting"}
    assert state_of(service, run_id)[0] == "confirmed" and state_of(service, run_id)[2] == "confirmed"


async def test_a_second_job_queued_behind_a_run_that_outlives_the_key_is_the_one_that_waits(licensed, service):
    first = await clicked_apply(service, licensed, "acme")
    second = await clicked_apply(service, licensed, "globex")

    def during(run):
        licensed.clock.days(4)
        return {"state": "failed", "reason": "Stub engine ended"}

    licensed.engine.during = during
    await service._execute_owned(first)
    assert licensed.engine.runs == [first]
    with pytest.raises(Waited):
        await service._execute_owned(second)
    assert state_of(service, second)[0] == "queued" and licensed.engine.runs == [first]


async def test_a_click_on_apply_after_the_key_ran_out_writes_no_run_and_starts_on_its_own_after_a_new_key(licensed, owner, service, client):
    from jobpilot.owner_apply import due, start

    ready_job(service, "acme", licensed.resume_id)
    licensed.clock.days(4)
    answer = await start(service, "acme")
    assert answer["state"] == "waiting" and answer["code"] == "license" and "Settings" in answer["message"]
    with service.db.session() as s:
        assert s.query(Run).count() == 0
    assert await due(service, 5) == 0  # Still held; asking again costs nothing and loses nothing.
    with service.db.exclusive() as s:
        request = s.get(Setting, "owner_apply:acme")
        assert request is not None
        request.value = {**request.value, "not_before": ""}  # Its wait of a minute is over.
    assert await due(service, 5) == 0
    assert client.post("/api/license/key", json={"key": fresh_key(owner, licensed.clock, 30)}).json()["state"] == "valid"
    with service.db.exclusive() as s:
        request = s.get(Setting, "owner_apply:acme")
        request.value = {**request.value, "not_before": ""}
    assert await due(service, 5) == 1
    with service.db.session() as s:
        assert [r.state for r in s.query(Run)] == ["queued"]


async def test_an_answered_question_carries_on_under_an_expired_key_through_admission_and_the_claim(licensed, service):
    from jobpilot.db import Review

    first = await clicked_apply(service, licensed, "acme")
    await service._execute_owned(first)  # It starts under the good key, and asks a question.
    with service.db.exclusive() as s:
        s.get(Run, first).state = "waiting_answer"
        s.get(Run, first).packet = {**s.get(Run, first).packet, "match_approved": True}
        s.get(Job, "acme").status = "waiting_answer"
        s.add(Review(id="q1", run_id=first, question="Years of Python?", key="years_python", options=[]))
    licensed.clock.days(4)
    answer = await service.resolve_review("q1", "5")
    assert answer["message"] == "Saved. The application is continuing.", answer
    with service.db.session() as s:
        second = answer["run_id"]
        assert s.get(Run, second).state == "queued" and s.get(Run, second).packet[licensing.CONTINUES] is True
    await service._execute_owned(second)
    assert licensed.engine.runs[-1] == second


async def test_a_retry_of_a_started_application_is_not_held_but_a_new_click_on_an_old_attempt_is(licensed, service):
    from jobpilot.job_control import manage

    first = await clicked_apply(service, licensed, "acme")
    await service._execute_owned(first)
    assert state_of(service, first)[1] is not None and state_of(service, first)[0] not in {"queued", "running"}
    with service.db.exclusive() as s:
        s.get(Run, first).state, s.get(Job, "acme").status = "failed", "failed"
        s.get(Run, first).packet = {**s.get(Run, first).packet, "match_approved": True}
    licensed.clock.days(4)
    with pytest.raises(QueueBlocked) as refused:
        await service.queue("acme", "submit", owner_pick=True)  # Apply on an attempt that simply ended: a new start.
    assert refused.value.code == "license"
    second = (await manage(service, "acme", "resume", actor="repair"))["run_id"]  # The retry of it.
    await service._execute_owned(second)
    assert licensed.engine.runs[-1] == second


async def test_a_job_never_started_is_not_let_through_by_asking_for_a_retry(licensed, service):
    from jobpilot.job_control import manage

    ready_job(service, "acme", licensed.resume_id)
    licensed.clock.days(4)
    with pytest.raises(QueueBlocked) as refused:
        await manage(service, "acme", "resume")
    assert refused.value.code == "license"


async def test_stop_then_resume_under_an_expired_key_carries_on_with_the_application_it_interrupted(licensed, service):
    from jobpilot import stop

    run_id = await clicked_apply(service, licensed, "acme")
    with service.db.exclusive() as s:
        s.get(Run, run_id).state, s.get(Run, run_id).started_at = "running", now()
        s.get(Job, "acme").status = "running"
    licensed.clock.days(4)
    assert service.return_to_queue(run_id, stop.stopped_result(), 3.0)
    assert state_of(service, run_id)[:2] == ("queued", None)
    await service._execute_owned(run_id)
    assert licensed.engine.runs == [run_id]


# --- with no build setting ---------------------------------------------------------------------------------------------------


@pytest.fixture
def no_build_setting(monkeypatch, tmp_path):
    monkeypatch.delenv("AXIOM_LICENSE_BUILD", raising=False)
    monkeypatch.setattr(licensing, "BUILD_FILE", tmp_path / "absent" / "license_build.json")
    licensing._instances.clear()
    yield
    licensing._instances.clear()


async def test_with_no_build_setting_a_run_is_admitted_and_claimed_with_no_key_and_leaves_no_trace(no_build_setting, prepared, service, monkeypatch):
    from jobpilot import stop

    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    engine = StubEngine()
    real_claim(service, monkeypatch, engine)
    ready_job(service, "acme", prepared["id"])
    run_id = (await service.queue("acme", "submit", owner_pick=True))["id"]
    with service.db.session() as s:
        assert licensing.CONTINUES not in s.get(Run, run_id).packet
    await service._execute_owned(run_id)
    assert engine.runs == [run_id]
    # A later retry and Stop's return to the queue add nothing either.
    with service.db.exclusive() as s:
        s.get(Run, run_id).state = "running"
        s.get(Job, "acme").status = "running"
    assert service.return_to_queue(run_id, stop.stopped_result(), 1.0)
    with service.db.session() as s:
        assert licensing.CONTINUES not in s.get(Run, run_id).packet
    assert not (service.config.data_dir / "license").exists()


def test_with_no_build_setting_the_key_route_changes_nothing_and_says_so_in_words(no_build_setting, client, service):
    answer = client.post("/api/license/key", json={"key": "AXIOM1-anything.at-all"})
    assert answer.status_code == 400 and "does not ask for a key" in answer.json()["detail"]
    assert client.get("/api/license").json()["state"] == "off"
    assert not (service.config.data_dir / "license").exists()


def test_the_key_has_two_gates_and_no_route_or_middleware_checks_it():
    """What a build with no setting could notice: only admission and the claim ever ask, and both pass (the test above). No route or
    middleware reads it, so none can refuse anything over a key. The screen's own two routes only read and enter a key."""
    root = ROOT / "jobpilot"
    users = {
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if path.name != "licensing.py" and "licensing import" in path.read_text(errors="replace")
    }
    assert users == {"api/routes/license.py", "service/queueing.py", "service/execution.py"}
    assert "licens" not in (root / "api" / "middleware.py").read_text().lower()


# --- what desktop/build.sh writes --------------------------------------------------------------------------------------------


ROOT = Path(__file__).resolve().parents[1]
BUILD_SH = ROOT / "desktop" / "build.sh"
SHELL_START = """say() { :; }; fail() { printf 'build: %s\\n' "$*" >&2; exit 1; }\n"""


def from_build_script(first_line_starts, last_line):
    """The very lines of desktop/build.sh, so that this tests what the build runs."""
    lines = BUILD_SH.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(first_line_starts))
    end = next(i for i in range(start, len(lines)) if lines[i] == last_line)
    return "\n".join(lines[start : end + 1]) + "\n"


def small_bundle(tmp_path):
    resources = tmp_path / "AXIOM.app" / "Contents" / "Resources"
    code = resources / "app" / "jobpilot"
    code.mkdir(parents=True)
    for name in ("__init__.py", "licensing.py"):
        shutil.copy(ROOT / "jobpilot" / name, code / name)
    return resources


def build_step(tmp_path, resources, **env):
    home = tmp_path / "owner-home"
    return subprocess.run(
        ["/bin/sh", "-eu", "-c", SHELL_START + from_build_script("# The build setting for licensing", "fi")],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "AXIOM_LICENSE_HOME": str(home / "axiom-license"), "root": str(ROOT),
             "resources": str(resources), "py": sys.executable, **env},
    )


def first_check(tmp_path, **env):
    home = tmp_path / "owner-home"
    return subprocess.run(
        ["/bin/sh", "-eu", "-c", SHELL_START + from_build_script("# Licensing, asked now", "esac")],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "AXIOM_LICENSE_HOME": str(home / "axiom-license"), **env},
    )


def setting_in(resources):
    return resources / "app" / "jobpilot" / "license_build.json"


def as_the_app_runs(resources, tmp_path):
    """A new process with the environment bin/axiom and the window give the app: this bundle's code, nothing from the checkout."""
    code = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from jobpilot import licensing\n"
        "status = licensing.Licensing(Path(sys.argv[1])).status()\n"
        "print(json.dumps({'file': licensing.__file__, 'required': status['required'], 'state': status['state'],"
        " 'blocking': status['blocking']}))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "data")], capture_output=True, text=True, timeout=60, check=True, cwd=resources,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(resources / "app"), "PYTHONDONTWRITEBYTECODE": "1",
             "PYTHONNOUSERSITE": "1", "PYTHONSAFEPATH": "1"},
    )
    return json.loads(done.stdout)


@pytest.mark.parametrize("word", [None, "off"])
def test_a_build_without_the_setting_carries_no_license_file_even_with_a_stray_one_in_the_checkout(tmp_path, word):
    resources = small_bundle(tmp_path)
    setting_in(resources).write_text('{"required": true, "public_key": "stray"}')
    done = build_step(tmp_path, resources, **({"AXIOM_LICENSE": word} if word else {}))
    assert done.returncode == 0, done.stderr
    assert not setting_in(resources).exists()
    answer = as_the_app_runs(resources, tmp_path)
    assert answer["required"] is False and answer["state"] == "off" and answer["file"].startswith(str(resources))
    assert not (tmp_path / "data" / "license").exists()


def test_axiom_license_on_writes_the_setting_the_bundled_code_reads_compiled_or_not(tmp_path, owner):
    resources = small_bundle(tmp_path)
    public = public_text(owner)
    done = build_step(tmp_path, resources, AXIOM_LICENSE="on", AXIOM_LICENSE_PUBLIC_KEY=public)
    assert done.returncode == 0, done.stderr
    assert json.loads(setting_in(resources).read_text()) == {"required": True, "public_key": public}
    assert b"private" not in setting_in(resources).read_bytes().lower()
    answer = as_the_app_runs(resources, tmp_path)
    assert answer["required"] is True and answer["state"] == "none" and answer["blocking"] is True
    assert answer["file"] == str(resources / "app" / "jobpilot" / "licensing.py")
    # The build compiles everything ahead of time, in this mode: the module must still find the file beside its source.
    subprocess.run([sys.executable, "-m", "compileall", "-q", "--invalidation-mode", "unchecked-hash", str(resources / "app")], check=True, timeout=60)
    assert as_the_app_runs(resources, tmp_path)["required"] is True


def test_axiom_license_on_takes_the_public_key_of_the_owners_own_pair(tmp_path, command, capsys):
    home = tmp_path / "owner-home" / "axiom-license"
    os.environ["AXIOM_LICENSE_HOME"] = str(home)  # The `command` fixture's monkeypatch undoes this.
    command.main(["init"])
    capsys.readouterr()
    command.main(["public"])
    public = capsys.readouterr().out.strip()
    resources = small_bundle(tmp_path)
    done = build_step(tmp_path, resources, AXIOM_LICENSE="on")
    assert done.returncode == 0, done.stderr
    assert json.loads(setting_in(resources).read_text())["public_key"] == public
    assert first_check(tmp_path, AXIOM_LICENSE="on").returncode == 0


@pytest.mark.parametrize("bad", ["not-a-key", "abc", 'quote"breaks-the-json', base64.b64encode(b"short").decode()])
def test_a_public_key_typed_wrong_stops_the_build_and_leaves_no_setting(tmp_path, bad):
    resources = small_bundle(tmp_path)
    done = build_step(tmp_path, resources, AXIOM_LICENSE="on", AXIOM_LICENSE_PUBLIC_KEY=bad)
    assert done.returncode != 0 and "not a valid key" in done.stderr
    assert not setting_in(resources).exists()


def test_axiom_license_on_with_no_key_pair_stops_the_build_in_words(tmp_path):
    done = first_check(tmp_path, AXIOM_LICENSE="on")
    assert done.returncode != 0 and "scripts/axiom-license init" in done.stderr
    resources = small_bundle(tmp_path)
    late = build_step(tmp_path, resources, AXIOM_LICENSE="on")
    assert late.returncode != 0 and "scripts/axiom-license init" in late.stderr and not setting_in(resources).exists()


def test_another_word_for_axiom_license_stops_the_build_at_once(tmp_path):
    done = first_check(tmp_path, AXIOM_LICENSE="yes")
    assert done.returncode != 0 and "on or off" in done.stderr
    assert first_check(tmp_path).returncode == 0 and first_check(tmp_path, AXIOM_LICENSE="off").returncode == 0


def test_the_setting_is_written_before_the_build_compiles_lists_and_seals_the_app():
    text = BUILD_SH.read_text()
    order = [
        'ditto "$root/jobpilot" "$resources/app/jobpilot"',
        '>"$resources/app/jobpilot/license_build.json"',
        "-m compileall",
        "write_install_manifest(sys.argv[1])",
        'codesign --force --sign "$sign_id" $hardened "$app"',
        'hdiutil create',
    ]
    places = [text.index(step) for step in order]
    assert places == sorted(places)


# --- HTTP --------------------------------------------------------------------------------------------------------------------


def test_the_license_screen_works_before_anyone_has_signed_in(required, owner, app, service):
    web = TestClient(app)  # No token, no session.
    first = web.get("/api/license")
    assert first.status_code == 200
    body = first.json()
    assert body["required"] and body["state"] == "none" and body["blocking"] and body["code"] == mac_code_for(MY_MAC)
    assert web.get("/api/snapshot").status_code == 401  # Only the two license routes are open.
    wrong = web.post("/api/license/key", json={"key": key_for(owner, OTHER_MAC, 30, date.today())})
    assert wrong.status_code == 400 and "different Mac" in wrong.json()["detail"]
    forged = web.post("/api/license/key", json={"key": key_for(Ed25519PrivateKey.generate(), MY_MAC, 30, date.today())})
    assert forged.status_code == 400 and "not genuine" in forged.json()["detail"]
    assert web.get("/api/license").json()["state"] == "none"
    long = web.post("/api/license/key", json={"key": "x" * 5000})
    assert long.status_code == 400 and "Paste only the key" in long.json()["detail"]
    good = key_for(owner, MY_MAC, 30, date.today())
    # Public, but only for the app's own page: another web page cannot change the key, even with a genuine one.
    assert web.post("/api/license/key", json={"key": good}, headers={"Origin": "https://elsewhere.example"}).status_code == 403
    assert web.post("/api/license/key", json={"key": good}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert web.get("/api/license").json()["state"] == "none"
    answer = web.post("/api/license/key", json={"key": good})
    assert answer.status_code == 200 and answer.json()["state"] == "valid"
    assert web.get("/api/license").json()["starts_allowed"] is True


def test_a_good_key_can_be_replaced_and_a_refused_replacement_keeps_the_good_one(required, owner, app, service):
    web = TestClient(app)
    today = date.today()
    assert web.post("/api/license/key", json={"key": key_for(owner, MY_MAC, 5, today)}).json()["expires"] == (today + timedelta(days=5)).isoformat()
    assert web.post("/api/license/key", json={"key": key_for(Ed25519PrivateKey.generate(), MY_MAC, 90, today)}).status_code == 400
    assert web.get("/api/license").json()["expires"] == (today + timedelta(days=5)).isoformat()
    assert web.post("/api/license/key", json={"key": key_for(owner, MY_MAC, 90, today)}).json()["expires"] == (today + timedelta(days=90)).isoformat()
    assert web.get("/api/license").json()["days_left"] == 90


def test_a_build_without_licensing_reports_it_off_and_changes_nothing(app, service, monkeypatch):
    monkeypatch.setattr(licensing, "BUILD_FILE", service.config.data_dir / "absent.json")
    licensing._instances.clear()
    body = TestClient(app).get("/api/license").json()
    assert body["required"] is False and body["state"] == "off" and body["starts_allowed"] and not body["blocking"]


# --- the owner's command ---------------------------------------------------------------------------------------------------


@pytest.fixture
def command(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_LICENSE_HOME", str(tmp_path / "home" / "axiom-license"))
    path = Path(__file__).resolve().parents[1] / "scripts" / "axiom_license.py"
    spec = importlib.util.spec_from_file_location("axiom_license_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_command_makes_the_private_key_once_outside_the_repo_for_the_owner_only_and_warns(command, tmp_path, capsys):
    code = mac_code_for(MY_MAC)
    assert command.main(["sign", code, "--days", "30", "--name", "Sam"]) == 0
    printed = capsys.readouterr()
    folder = tmp_path / "home" / "axiom-license"
    private = folder / "private.key"
    assert stat.S_IMODE(private.stat().st_mode) == 0o600 and stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert "BACK UP" in printed.err
    repo = Path(__file__).resolve().parents[1]
    assert repo not in private.resolve().parents
    key = printed.out.strip()
    assert key.startswith("AXIOM1-")
    first_private = private.read_text()
    # The same command again reuses the key, says nothing about backups, and signs a key the first public key accepts.
    assert command.main(["sign", code.lower(), "--until", (date.today() + timedelta(days=9)).isoformat()]) == 0
    second = capsys.readouterr()
    assert private.read_text() == first_private and "BACK UP" not in second.err
    public = licensing.public_key_of((folder / "public.key").read_text().strip())
    parsed = licensing.read_key(key, public)
    assert parsed.mac == code and parsed.name == "Sam" and parsed.expires == date.today() + timedelta(days=30)


def test_the_command_refuses_a_private_key_other_users_can_read(command, tmp_path, capsys):
    command.main(["init"])
    capsys.readouterr()
    os.chmod(tmp_path / "home" / "axiom-license" / "private.key", 0o644)
    with pytest.raises(SystemExit, match="chmod 600"):
        command.main(["sign", mac_code_for(MY_MAC), "--days", "3"])


def test_a_key_the_command_signs_is_accepted_by_a_build_with_the_public_key_it_prints(command, tmp_path, capsys, monkeypatch):
    command.main(["sign", mac_code_for(MY_MAC), "--days", "10"])
    key = capsys.readouterr().out.strip()
    command.main(["public"])
    public = capsys.readouterr().out.strip()
    monkeypatch.setattr(licensing, "load_build", lambda: licensing.Build(True, public))
    monkeypatch.setattr(licensing, "hardware_uuid", lambda: MY_MAC)
    assert Licensing(tmp_path / "data").install(key)["state"] == "valid"


def test_the_command_refuses_a_date_in_the_past_and_a_bad_code(command, capsys):
    with pytest.raises(SystemExit, match="passed"):
        command.main(["sign", mac_code_for(MY_MAC), "--until", "2020-01-01"])
    assert command.main(["sign", "nonsense", "--days", "3"]) == 1


def test_the_repository_holds_no_private_key_and_no_build_setting():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "jobpilot" / "license_build.json").exists()  # Off by default: a checkout asks for nothing.
    assert "license_build.json" in (root / ".gitignore").read_text()
