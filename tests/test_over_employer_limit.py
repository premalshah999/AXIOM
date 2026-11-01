"""Jobs › Over employer limit (owner, 2026-11-01: "per employer limit stays as 25, or add them to a new section, like
reserved, so I can approve").

The automatic limit stays what the user set. Ready jobs held only because their employer reached it are listed in their
own section, each saying how many applications the employer already has; the user approves one (or all) and that is the
owner's own Apply, which skips the autopilot's cap and no other guard. Synthetic data only.
"""

from datetime import UTC, datetime

import pytest

from jobpilot import reserved, startable
from jobpilot.admission import QueueBlocked
from jobpilot.application_pacing import EMPLOYER_CAP_PREFIX, LIMIT_KEY
from jobpilot.db import Job, JobListing, Run, Setting
from jobpilot.schemas import Profile

pytestmark = pytest.mark.integration


def setup(service, limit=2):
    service.db.set_setting("profile", Profile(target_roles=["Software Engineer"]).model_dump())
    service.db.set_setting("job_board_settings", {"minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": False})
    service.db.set_setting(LIMIT_KEY, {"limit": limit})


def applied(service, company, n):
    """`n` confirmed applications at one employer."""
    for k in range(n):
        jid = f"{company[:3].lower()}done{k}"
        with service.db.exclusive() as s:
            s.add(Job(id=jid, identity=jid, url=f"https://boards.example.test/{company}/jobs/{k}", ats="greenhouse",
                      company=company, title="Engineer", status="confirmed", demo=False))
            s.add(Run(id=f"run-{jid}", job_id=jid, state="confirmed", mode="submit", packet={}))


def ready(service, jid, company, score=80, url=None):
    seen = datetime.now(UTC).isoformat()
    with service.db.exclusive() as s:
        s.add(Job(id=jid, identity=jid, url=url or f"https://boards.example.test/{company}/jobs/{jid}", ats="greenhouse",
                  company=company, title="Software Engineer", status="ready", score=score, demo=False,
                  location="Austin, TX", created_at=seen, description="Build things. " * 40))
        s.add(JobListing(job_id=jid, source_id="src", posted_at=seen, date_basis="Employer first_published",
                         location="Austin, TX", country="US", career_level="New grad", match_reason="x",
                         first_seen=seen, last_seen=seen, active=True))
    return jid


def test_each_job_says_how_many_applications_its_employer_has_and_the_tab_counts_the_rows(service):
    setup(service, limit=2)
    applied(service, "Capped Example", 3)
    low, high = ready(service, "low", "Capped Example", score=50), ready(service, "high", "Capped Example", score=90)
    free = ready(service, "free", "Open Example")
    got = startable.read(service)
    assert got["jobs"][high]["code"] == "employer_limit"
    assert got["jobs"][high]["reason"] == "Capped Example already has 3 applications; your limit is 2."
    assert (got["jobs"][high]["count"], got["jobs"][high]["limit"]) == (3, 2)
    assert free not in got["jobs"] and got["startable"] == 1
    shown = startable.over_limit(service.db, got)
    assert [r["id"] for r in shown] == [high, low]  # best fit first
    assert startable.over_limit_count(got) == len(shown) == 2


def test_the_section_follows_the_users_limit_for_any_value(service):
    applied_to = 5
    setup(service, limit=5)
    applied(service, "Capped Example", applied_to)
    job = ready(service, "j1", "Capped Example")
    assert startable.over_limit_count(startable.read(service)) == 1  # 5 of 5
    service.db.set_setting(LIMIT_KEY, {"limit": 6})
    got = startable.read(service)
    assert startable.over_limit_count(got) == 0 and job in got["ids"]
    service.db.set_setting(LIMIT_KEY, {"limit": 1})
    got = startable.read(service)
    assert got["jobs"][job]["reason"] == "Capped Example already has 5 applications; your limit is 1."


def test_the_employers_own_stated_cap_is_not_a_limit_the_user_can_approve_past(service):
    setup(service, limit=25)
    with service.db.exclusive() as s:
        s.add(Setting(key=EMPLOYER_CAP_PREFIX + "name:strict example", value={
            "limit": 1, "days": None, "company": "Strict Example", "at": datetime.now(UTC).isoformat()}))
    applied(service, "Strict Example", 1)
    job = ready(service, "s1", "Strict Example")
    got = startable.read(service)
    assert got["jobs"][job]["code"] == "employer_cap"
    assert startable.over_limit_count(got) == 0


def test_a_reserved_job_over_the_limit_stays_in_reserved(service, monkeypatch):
    monkeypatch.setattr(reserved, "DEFAULT", {**reserved.DEFAULT, "enabled": True})
    reserved._compiled.cache_clear()
    try:
        setup(service, limit=1)
        applied(service, "Amazon", 1)
        job = ready(service, "amz", "Amazon", url="https://www.amazon.jobs/en/jobs/1")
        got = startable.read(service)
        assert got["jobs"][job]["code"] == "reserved"
        assert startable.over_limit_count(got) == 0
    finally:
        reserved._compiled.cache_clear()


def test_dropping_approved_jobs_updates_every_count_at_once(service):
    setup(service, limit=1)
    applied(service, "Capped Example", 1)
    a, b = ready(service, "a", "Capped Example"), ready(service, "b", "Capped Example")
    held = startable.Reading()
    value = held.get(service)
    assert startable.over_limit_count(value) == 2 and value["ready"] == 2
    held.drop([a])
    now = held.peek()
    assert startable.over_limit_count(now) == 1 and now["ready"] == 1
    assert [h["count"] for h in now["held"]] == [1]
    # A reading that was already under way when the job was approved does not bring it back.
    held._load(service)
    assert startable.over_limit_count(held.peek()) == 1 and a not in held.peek()["jobs"]
    assert b in held.peek()["jobs"]


def test_the_api_lists_the_section_and_approving_is_the_owners_apply(client, service, monkeypatch):
    setup(service, limit=1)
    applied(service, "Capped Example", 1)
    a, b = ready(service, "a", "Capped Example", score=70), ready(service, "b", "Capped Example", score=90)
    ready(service, "c", "Open Example")
    page = client.get("/api/over-limit").json()
    assert page["count"] == page["total"] == 2 and [r["id"] for r in page["rows"]] == [b, a]
    assert page["rows"][0]["hold"]["reason"] == "Capped Example already has 1 application; your limit is 1."
    assert client.get("/api/over-limit?q=nothing-like-it").json()["total"] == 0
    assert client.get("/api/over-limit?q=nothing-like-it").json()["count"] == 2  # the tab's number is the section, not the search

    service.dispatch = object()
    started = []

    async def apply(svc, job_id):
        started.append(job_id)
        with svc.db.exclusive() as s:  # what the one-click Apply does to a job it starts
            s.get(Job, job_id).status = "queued"
        return {"job_id": job_id, "state": "queued", "run_id": "r1", "message": "Application queued."}

    monkeypatch.setattr("jobpilot.owner_apply.apply", apply)
    assert client.post(f"/api/jobs/{b}/approve-over-limit", json={}).json()["state"] == "queued"
    assert started == [b]
    after = client.get("/api/over-limit").json()
    assert after["count"] == 1 and [r["id"] for r in after["rows"]] == [a]
    # A job that is not waiting over the limit is not applied to through this door.
    assert client.post("/api/jobs/missing/approve-over-limit", json={}).status_code == 400
    assert client.post(f"/api/jobs/{b}/approve-over-limit", json={}).status_code == 400 and started == [b]


def test_approve_all_applies_each_job_through_the_single_click_path_and_keeps_refused_ones(client, service, monkeypatch):
    setup(service, limit=1)
    applied(service, "Capped Example", 1)
    ids = [ready(service, f"j{n}", "Capped Example", score=90 - n) for n in range(3)]
    service.dispatch = object()
    seen = []

    async def apply(svc, job_id):
        seen.append(job_id)
        if job_id == ids[1]:
            raise QueueBlocked("This job is submitted or has an uncertain submission.", code="submitted_or_uncertain", outcome="protected")
        with svc.db.exclusive() as s:
            s.get(Job, job_id).status = "queued"
        return {"job_id": job_id, "state": "queued", "run_id": "r", "message": "Application queued."}

    monkeypatch.setattr("jobpilot.owner_apply.apply", apply)
    answer = client.post("/api/over-limit/approve-all", json={}).json()
    assert seen == ids and answer["approved"] == 2 and answer["remaining"] == 0
    assert answer["summary"] == {"queued": 2, "refused": 1}
    left = client.get("/api/over-limit").json()
    assert [r["id"] for r in left["rows"]] == [ids[1]]  # the refused one stays, with the guard deciding it


async def test_the_owners_pick_skips_the_cap_and_only_the_cap(service):
    """The real queue path: admission refuses the autopilot for the limit and does not refuse the owner's pick for it."""
    setup(service, limit=1)
    applied(service, "Capped Example", 1)
    job = ready(service, "j", "Capped Example")
    with pytest.raises(QueueBlocked) as err:
        await service.queue(job, "submit")
    assert err.value.code == "company_limit" and err.value.detail == {"count": 1, "limit": 1}
    try:
        await service.queue(job, "submit", owner_pick=True)
    except ValueError as exc:  # a later guard may refuse this bare installation; the limit must not be what does
        assert getattr(exc, "code", "") != "company_limit"
