"""Company-site jobs (a custom or unrecognised platform) have their own section and are never put in line by anything automatic.
Synthetic data only."""

from datetime import UTC, datetime

import pytest

from jobpilot import autopilot, board_view, owner_apply, review_batch, startable
from jobpilot.admission import QueueBlocked
from jobpilot.application_pacing import LIMIT_KEY
from jobpilot.company_sites import KEY, allow, allowed, is_company_site, leave_the_queue
from jobpilot.db import Job, JobListing, Run, Setting
from jobpilot.jobboard import BoardSettings
from jobpilot.schemas import Profile

pytestmark = pytest.mark.integration


def setup(service, auto=False):
    service.db.set_setting("profile", Profile(target_roles=["Software Engineer"]).model_dump())
    service.db.set_setting("job_board_settings", {"enabled": True, "auto_apply": auto, "minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": False})
    service.db.set_setting(LIMIT_KEY, {"limit": 25})
    service.db.set_setting("control", {"paused": False, "auto_submit": True})


def job(service, jid, ats="greenhouse", status="ready", company=None):
    company = company or "Example " + jid
    seen = datetime.now(UTC).isoformat()
    with service.db.exclusive() as s:
        s.add(Job(id=jid, identity=jid, url=f"https://boards.example.test/{jid}/jobs/{jid}", ats=ats, company=company, title="Software Engineer", status=status,
                  score=80, demo=False, location="Austin, TX", created_at=seen, description="Build things. " * 40, reason="Backend role matches Python."))
        s.add(JobListing(job_id=jid, source_id="src", posted_at=seen, date_basis="Employer first_published", location="Austin, TX", country="US",
                         career_level="New grad", match_reason="x", first_seen=seen, last_seen=seen, active=True))
    return jid


def board(service, **kw):
    startable.reading(service).reload(service)
    board_view.board(service).reload(service)
    return board_view.page(service, **{"fit": "all", **kw})


def test_which_labels_are_company_sites():
    for label in ("custom", "", None, "phenom", "somethingelse"):
        assert is_company_site(label)
    for label in ("greenhouse", "workday", "sap", "pinpoint", "icims"):
        assert not is_company_site(label)


def test_company_sites_leave_the_main_list_for_their_own_section(service):
    setup(service)
    job(service, "known")
    job(service, "own", ats="custom")
    job(service, "front", ats="phenom")
    main = board(service)
    assert [r["id"] for r in main["rows"]] == ["known"] and main["sites"] == 2
    sites = board(service, section="sites")
    assert sorted(r["id"] for r in sites["rows"]) == ["front", "own"] and sites["total"] == 2
    assert sorted(board(service, section="sites", want="ids")["ids"]) == ["front", "own"]
    # Relabelled with a platform the app knows: back on the main list by itself.
    with service.db.exclusive() as s:
        s.get(Job, "own").ats = "lever"
    main = board(service)
    assert sorted(r["id"] for r in main["rows"]) == ["known", "own"] and main["sites"] == 1


def test_select_all_on_the_main_list_and_the_review_batch_never_offer_one(service):
    setup(service)
    job(service, "known")
    job(service, "own", ats="custom")
    assert board(service, want="ids")["ids"] == ["known"]
    assert [r["id"] for r in review_batch.current(service)["rows"]] == ["known"]
    # Even handed the id, the main list's door refuses it, in words.
    out = owner_apply.enqueue_many(service, ["own", "known"])
    assert out["added"] == ["known"] and [x["job_id"] for x in out["refused"]] == ["own"]
    assert "Company sites" in out["refused"][0]["reason"]
    with pytest.raises(QueueBlocked):
        owner_apply.enqueue(service, "own")
    with service.db.session() as s:
        assert s.get(Setting, owner_apply.KEY + "own") is None


def test_the_ready_count_and_the_dispatcher_do_not_include_them(service):
    setup(service, auto=True)
    job(service, "known")
    job(service, "own", ats="custom")
    startable.reading(service).reload(service)
    value = startable.reading(service).get(service)
    assert "own" not in value["ids"] and "own" not in value["jobs"] and value["ready"] == 1
    state, _, _ = autopilot.screen_job(
        service.db, BoardSettings(auto_apply=True), Profile(target_roles=["Software Engineer"]), {"id": "own"}, frozenset(),
        board_ids={"own"}, integrated=False, use_master=True, data_dir=service.config.data_dir,
    )
    assert state == "company_site"


async def test_the_one_door_every_start_goes_through_refuses_them(service):
    setup(service)
    job(service, "own", ats="custom")
    for kwargs in ({}, {"owner_pick": True}):
        with pytest.raises(QueueBlocked) as caught:
            await service.queue("own", "submit", **kwargs)
        assert caught.value.code == "company_site"
    with pytest.raises(QueueBlocked):
        await owner_apply.apply(service, "own")
    out = await owner_apply.apply_many(service, ["own"])
    assert out["summary"] == {"refused": 1}
    with service.db.session() as s:
        assert s.query(Run).count() == 0 and s.get(Job, "own").status == "ready"


def test_the_owner_sends_one_on_purpose_from_the_section(client, service):
    setup(service)
    job(service, "own", ats="custom")
    job(service, "known")
    answer = client.post("/api/queue/company-sites", json={"job_ids": ["own"]}).json()
    assert answer["added"] == 1
    with service.db.session() as s:
        assert allowed(s, "own") and not allowed(s, "known")
        assert s.get(Setting, owner_apply.KEY + "own") is not None
    in_line = board(service, section="sites")["rows"]
    assert [(r["id"], r["state"]) for r in in_line] == [("own", "queue")]


def test_company_sites_waiting_in_line_leave_it_at_startup_and_are_not_deleted(service):
    setup(service)
    for jid in ("waits", "chosen", "running", "sent", "known"):
        job(service, jid, ats="greenhouse" if jid == "known" else "custom")
    with service.db.exclusive() as s:
        s.get(Job, "sent").status = "confirmed"
        s.add(Run(id="r1", job_id="running", state="running", mode="submit", packet={}))
    for jid in ("waits", "chosen", "running", "sent", "known"):
        service.db.set_setting(owner_apply.KEY + jid, {"at": "2026-11-01T10:00:00", "code": "queue"})
    allow(service.db, ["chosen"])
    assert leave_the_queue(service.db) == 1
    with service.db.session() as s:
        waiting = {j for j in ("waits", "chosen", "running", "sent", "known") if s.get(Setting, owner_apply.KEY + j)}
        assert waiting == {"chosen", "running", "sent", "known"}
        assert s.get(Job, "waits") is not None and s.get(Setting, KEY + "chosen") is not None
