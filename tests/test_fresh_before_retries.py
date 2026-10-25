"""2026-10-24: 247 resumed repeat-failures were queued as owner requests ahead of fresh postings and filled every
worker; their retries succeeded about one time in eight. Never-attempted jobs start first, retries take at most a
capped share, and an employer site whose CAPTCHA or account stops three different jobs waits while others continue."""

from types import SimpleNamespace

from jobpilot.db import Job, Run, Setting
from jobpilot.owner_apply import due
from jobpilot.recovery import open_blocked_site_circuit


def job(service, n, *, url=None, ats="oracle", company="Acme"):
    job_id = f"job{n}"
    with service.db.exclusive() as s:
        s.add(Job(id=job_id, identity=job_id, url=url or f"https://acme.fa.oraclecloud.com/jobs/{n}", ats=ats,
                  company=company, title="Engineer", status="ready", demo=False))
    return job_id


async def test_never_attempted_jobs_start_before_retries_and_retries_are_capped(service):
    old, fresh, older_fresh = job(service, 1), job(service, 2), job(service, 3)
    with service.db.exclusive() as s:
        s.add(Run(id="old-run", job_id=old, state="waiting_browser", mode="submit", packet={}))
    for job_id, at in [(old, "2026-10-24T10:00:00"), (fresh, "2026-10-24T11:00:00"), (older_fresh, "2026-10-24T10:30:00")]:
        service.db.set_setting("owner_apply:" + job_id, {"at": at})
    started = []

    async def queue(job_id, mode, **options):
        started.append(job_id)
        return {"id": "r-" + job_id}

    service.queue = queue
    assert await due(SimpleNamespace(db=service.db, queue=queue), 3, retries=0) == 2
    assert started == [older_fresh, fresh]  # fresh first, oldest first; the retry waits for its share
    assert service.db.get_setting("owner_apply:" + old)
    assert await due(SimpleNamespace(db=service.db, queue=queue), 3) == 1 and started[-1] == old


def blocked(service, run_id, job_id, blocker="captcha", finished="2999-01-01T00:00:00+00:00"):
    with service.db.exclusive() as s:
        s.add(Run(id=run_id, job_id=job_id, state="waiting_browser", mode="submit", finished_at=finished,
                  packet={"blocker_type": blocker}))


def circuit(service, run_id):
    with service.db.exclusive() as s:
        return open_blocked_site_circuit(s, s.get(Run, run_id), "waiting_browser")


def test_three_jobs_stopped_by_one_sites_captcha_pause_that_site_only(service):
    for n in range(3):
        blocked(service, f"r{n}", job(service, n))
        opened = circuit(service, f"r{n}")
    assert opened  # the third job opens it
    blocked(service, "elsewhere", job(service, 9, url="https://other.fa.oraclecloud.com/jobs/9"))
    assert not circuit(service, "elsewhere")
    with service.db.session() as s:
        reasons = [row.value.get("reason", "") for row in s.query(Setting).where(Setting.key.like("site_circuit:%"))]
    assert len(reasons) == 1 and "captcha" in reasons[0]


def test_two_jobs_or_job_local_blockers_never_pause_a_site(service):
    for n in range(2):
        blocked(service, f"r{n}", job(service, n))
        assert not circuit(service, f"r{n}")
    blocked(service, "r2", job(service, 2), blocker="control_failure")
    assert not circuit(service, "r2")


def test_ranked_ready_jobs_without_a_live_listing_are_autopilot_candidates(service):
    """122 imported, ranked-ready jobs had no discovery listing and never reached the autopilot (2026-10-24)."""
    from jobpilot.autopilot import unlisted_ready
    from jobpilot.jobboard import settings as board_settings

    imported, attempted, stale, listed, unranked = (job(service, n) for n in range(5))
    with service.db.exclusive() as s:
        s.add(Run(id="done", job_id=attempted, state="confirmed", mode="submit", packet={}))
        s.get(Job, stale).created_at = "2020-01-01T00:00:00+00:00"
        s.get(Job, unranked).status = "new"
    found = {j["id"] for j in unlisted_ready(service.db, board_settings(service.db), {listed})}
    assert found == {imported}


def test_three_jobs_stalled_on_the_same_required_field_pause_that_site(service):
    """Emory (iCIMS, 2026-10-24): eight retries failed on one unreadable menu while holding iCIMS's single slot."""
    menu = "What is the highest level of education you have completed?"
    for n in range(3):
        blocked(service, f"s{n}", job(service, 10 + n, ats="icims", url=f"https://careers-emoryx.icims.com/jobs/{n}"),
                blocker="control_failure")
        service.db.event(f"s{n}", "verification", "Read back", {"problems": [f"Required field incomplete: {menu}"]})
        opened = circuit(service, f"s{n}")
    assert opened
    with service.db.session() as s:
        reasons = [row.value.get("reason", "") for row in s.query(Setting).where(Setting.key.like("site_circuit:%"))]
    assert len(reasons) == 1 and "highest level of education" in reasons[0]


def test_different_stuck_fields_on_one_site_do_not_pause_it(service):
    for n, label in enumerate(["Phone Type", "Degree", "Start Date"]):
        blocked(service, f"d{n}", job(service, 20 + n, ats="icims", url=f"https://careers-acme.icims.com/jobs/{n}"),
                blocker="control_failure")
        service.db.event(f"d{n}", "verification", "Read back", {"problems": [f"Required field incomplete: {label}"]})
        assert not circuit(service, f"d{n}")
