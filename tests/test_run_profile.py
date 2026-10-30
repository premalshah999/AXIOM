"""Runs keep one copy of each profile version and can always rebuild the exact profile they started with."""

import pytest
from sqlalchemy import func, select

from jobpilot import run_profile
from jobpilot.db import Job, ProfileSnapshot, Run
from jobpilot.run_profile import full, keep
from jobpilot.schemas import Profile

PROFILE = {"name": "Alex Example", "email": "alex+ats@example.test", "autonomous": True, "location": "Boston, MA",
           "facts": {"first_name": "Alex"}, "reviewed_answers": {"q" * 40: "a" * 2000}}


@pytest.fixture(autouse=True)
def forget_remembered():
    run_profile._REMEMBERED.clear()


def test_a_run_keeps_only_what_is_read_inline_and_rebuilds_the_exact_profile(service):
    db = service.db
    with db.session() as s:
        first = keep(s, PROFILE)
        second = keep(s, {**PROFILE})
    assert first == second, "one version is stored once"
    assert first["profile"] == {"email": PROFILE["email"], "autonomous": True}
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(ProfileSnapshot)) == 1
    assert full(first, db) == PROFILE
    with db.session() as s:
        assert full(first, s) == PROFILE


def test_a_changed_profile_does_not_change_a_run_already_holding_the_old_one(service):
    db = service.db
    with db.session() as s:
        old = keep(s, PROFILE)
        new = keep(s, {**PROFILE, "location": "Denver, CO"})
    assert old["profile_snapshot"] != new["profile_snapshot"]
    assert full(old, db)["location"] == "Boston, MA" and full(new, db)["location"] == "Denver, CO"


def test_what_full_returns_cannot_change_the_snapshot_for_the_next_reader(service):
    with service.db.session() as s:
        packet = keep(s, PROFILE)
    full(packet, service.db)["facts"]["first_name"] = "Changed"
    assert full(packet, service.db)["facts"]["first_name"] == "Alex"


def test_a_packet_from_before_the_change_reads_as_it_always_did(service):
    assert full({"profile": PROFILE}, service.db) == PROFILE
    assert full({}, service.db) == {} and full(None, service.db) == {}


def test_a_missing_snapshot_is_an_error_never_the_current_profile(service):
    with pytest.raises(RuntimeError, match="missing"):
        full({"profile": {"email": "a@b"}, "profile_snapshot": "nope"}, service.db)


def test_the_browser_path_validates_the_snapshot_not_the_inline_part(service):
    with service.db.session() as s:
        packet = keep(s, Profile(name="Alex", email="a@example.test", location="Boston, MA").model_dump())
    assert "location" not in packet["profile"]
    assert Profile.model_validate(full(packet, service.db)).location == "Boston, MA"


def test_two_writers_of_one_new_version_do_not_collide(service):
    with service.db.session() as s:
        run_profile._store(s, "v", {"email": "a@b"})
        run_profile._store(s, "v", {"email": "a@b"})
        assert s.scalar(select(func.count()).select_from(ProfileSnapshot)) == 1


async def queued_packets(service, tmp_path, jobs):
    from jobpilot.db import Resume

    db = service.db
    service.config.mimo_api_key = "test-key"
    pdf = tmp_path / "resumes" / "a.pdf"
    pdf.parent.mkdir(exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 resume")
    db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.test", "phone": "2025550100",
                               "location": "Boston, MA", "autonomous": True, "reviewed_answers": [{"id": "1", "question": "q", "answer": "a" * 5000}]})
    with db.exclusive() as s:
        s.add(Resume(id="r1", name="Main", filename="a.pdf", sha256="sha", text="resume text"))
        for job in jobs:
            s.add(Job(id=job, identity=job, url=f"https://{job}.example.test/job", company=job, ats="greenhouse", title="Software Engineer", status="ready"))
    return [(await service.queue(job, "dry_run", "r1"))["id"] for job in jobs]


async def test_runs_queued_on_one_version_share_one_snapshot(service, tmp_path):
    ids = await queued_packets(service, tmp_path, ["a", "b", "c"])
    db = service.db
    with db.session() as s:
        packets = [s.get(Run, run_id).packet for run_id in ids]
        assert s.scalar(select(func.count()).select_from(ProfileSnapshot)) == 1
    for packet in packets:
        assert set(packet["profile"]) == {"email", "autonomous"}, "no copy of the answers on the run"
        assert packet["profile"]["email"] == "alex@example.test" and packet["profile_snapshot"] == packet["profile_version"]
        started_with = full(packet, db)
        assert started_with["reviewed_answers"][0]["answer"] == "a" * 5000 and started_with["location"] == "Boston, MA"
        Profile.model_validate(started_with)


async def test_a_run_keeps_its_profile_after_the_owner_edits_it(service, tmp_path):
    (first,) = await queued_packets(service, tmp_path, ["a"])
    db = service.db
    db.set_setting("profile", {**db.get_setting("profile"), "location": "Denver, CO"})
    second = (await service.queue(_another_job(db), "dry_run", "r1"))["id"]
    with db.session() as s:
        old, new = s.get(Run, first).packet, s.get(Run, second).packet
        assert s.scalar(select(func.count()).select_from(ProfileSnapshot)) == 2
    assert full(old, db)["location"] == "Boston, MA" and full(new, db)["location"] == "Denver, CO"


def _another_job(db, job="later"):
    with db.exclusive() as s:
        s.add(Job(id=job, identity=job, url=f"https://{job}.example.test/job", company=job, ats="greenhouse", title="Software Engineer", status="ready"))
    return job


def test_the_email_stays_readable_by_sql(service):
    with service.db.session() as s:
        packet = keep(s, Profile(name="Alex", email="a@example.test").model_dump())
        s.add(Job(id="j", identity="j", url="https://example.test/j"))
        s.add(Run(id="r", job_id="j", state="running", packet=packet))
    with service.db.session() as s:
        assert s.scalar(select(Run.packet["profile"]["email"].as_string()).where(Run.id == "r")) == "a@example.test"
