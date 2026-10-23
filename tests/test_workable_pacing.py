"""Workable throttles a candidate: after three applications in one minute every apply POST answered HTTP 412 and
"Something went wrong. We are working on this, please try again later." (2026-10-23, 22 refused). Workable now takes
one application at a time with a five-minute gap, like Ashby, and the banner reads as nothing accepted."""

import time
from datetime import UTC, datetime

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.application_pacing import START_GAPS, may_start, record_start, require_ats_admission
from jobpilot.db import Job, Run


def add(service, state, n, ats="workable"):
    with service.db.exclusive() as s:
        job = Job(identity=f"wk-{n}", url=f"https://apply.workable.com/acme/j/{n}/", company="Acme",
                  title="Data Engineer", ats=ats)
        s.add(job)
        s.flush()
        run = Run(job_id=job.id, state=state, mode="submit", packet={}, created_at=datetime.now(UTC).isoformat())
        s.add(run)
        s.flush()
        return job.id, run.id


def test_a_second_workable_application_waits_its_turn(service):
    add(service, "running", 1)
    job_id, run_id = add(service, "queued", 2)
    with service.db.session() as s:
        with pytest.raises(QueueBlocked) as blocked:
            require_ats_admission(s, s.get(Job, job_id), run_id="other")
        assert blocked.value.outcome == "deferred" and blocked.value.code == "ats_pacing"
        assert not may_start(s, s.get(Job, job_id), s.get(Run, run_id))


def test_workable_starts_are_spaced(service):
    first_job, first_run = add(service, "queued", 1)
    with service.db.exclusive() as s:
        record_start(s, s.get(Job, first_job), s.get(Run, first_run), at=time.time())
        s.get(Run, first_run).state = "confirmed"
    job_id, run_id = add(service, "queued", 2)
    with service.db.session() as s:
        assert not may_start(s, s.get(Job, job_id), s.get(Run, run_id), at=time.time() + 60)
        assert may_start(s, s.get(Job, job_id), s.get(Run, run_id), at=time.time() + START_GAPS["workable"] + 1)


def test_other_platforms_keep_their_own_pace(service):
    add(service, "running", 1)
    job_id, run_id = add(service, "queued", 2, ats="greenhouse")
    with service.db.session() as s:
        require_ats_admission(s, s.get(Job, job_id))
        assert may_start(s, s.get(Job, job_id), s.get(Run, run_id))


WORKABLE_412 = """<main><h1>Software Engineer - Infrastructure</h1>
<div role="alert"><strong>Something went wrong. We are working on this, please try again later.</strong></div>
<form><label>First name <input name="firstname" value="Ada"></label></form></main>"""


async def test_the_throttle_banner_is_a_temporary_refusal(page, tmp_path):
    from jobpilot.answers import Resolver
    from jobpilot.forms import FormSession
    from jobpilot.schemas import Profile

    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(WORKABLE_412)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    rejection = await form.rejection()
    assert rejection and rejection["category"] == "temporary"
