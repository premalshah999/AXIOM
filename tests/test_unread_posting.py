"""A run does not go past the posting page on text it never read (posting_read.py).

Two link-only jobs (empty description) said "U.S. citizenship is required"; the applicant is on F-1 OPT and the engine
spent days on password resets for them (2026-10-30)."""

from types import SimpleNamespace

import pytest

from jobpilot import discovery
from jobpilot.db import Job, Setting
from jobpilot.posting_read import refuse_unread_page, refuse_unread_posting

PROFILE = {
    "facts": {"us_citizen": False, "current_immigration_status_US": "F-1 OPT"},
    "education": [{"school": "Example University", "degree": "Master of Science", "end": "2025-05"}],
}
CITIZEN_POSTING = (
    "Example Corp builds software for its customers. Responsibilities include building services. "
    "U.S. citizenship is required. Apply through our careers site."
)


def engine_for(service, scan=None):
    form = SimpleNamespace(scan=scan) if scan else None
    return SimpleNamespace(db=service.db, config=SimpleNamespace(allow_private_urls=False), form=form)


def add_job(service, job_id="link", title="", description="", **extra):
    service.db.set_setting("profile", PROFILE)
    with service.db.exclusive() as s:
        s.add(Job(id=job_id, identity=job_id, url=f"https://example.test/job/{job_id}", title=title,
                  description=description, status="ready", **extra))
    return {"id": job_id, "title": title, "description": description, "demo": False}


def reading(monkeypatch, text, title=""):
    async def enrich(db, config, job_id):
        with db.session() as s:
            row = s.get(Job, job_id)
            row.description = text
            row.title = row.title or title

    monkeypatch.setattr(discovery, "enrich", enrich)


RUN = {"packet": {"profile": PROFILE}}


async def test_a_citizenship_requirement_in_an_unread_posting_closes_the_run_with_the_employers_words(service, monkeypatch):
    job = add_job(service)
    reading(monkeypatch, CITIZEN_POSTING, "Data Engineer")
    result = await refuse_unread_posting(engine_for(service), job, RUN)
    assert result["state"] == "closed" and result["blocker_type"] == "ineligible"
    assert "U.S. citizenship is required" in result["reason"] and "citizenship" in result["reason"]
    # The text and title were stored on the job for later runs and the dashboard.
    with service.db.session() as s:
        row = s.get(Job, "link")
        assert row.description == CITIZEN_POSTING and row.title == "Data Engineer"
    assert job["description"] == CITIZEN_POSTING and job["title"] == "Data Engineer"


@pytest.mark.parametrize(
    "text",
    [
        "Must be able to obtain and maintain a Secret level DoD security clearance. We build defense software for customers.",
        "Software Engineer Graduate (AI Compute) - 2027 Start (PhD). Minimum qualifications: completing or have recently "
        "completed a PhD in Computer Science or a related field. Join a large team of engineers.",
    ],
)
async def test_clearance_and_phd_wording_close_the_run_too(service, monkeypatch, text):
    job = add_job(service)
    reading(monkeypatch, text, "Engineer")
    result = await refuse_unread_posting(engine_for(service), job, RUN)
    assert result and result["state"] == "closed"


async def test_a_posting_the_applicant_can_meet_is_stored_and_the_run_goes_on(service, monkeypatch):
    job = add_job(service)
    reading(monkeypatch, "Build services for our customers. Bachelor's, Master's or PhD in Computer Science. " * 3, "Engineer")
    assert await refuse_unread_posting(engine_for(service), job, RUN) is None
    with service.db.session() as s:
        assert "Build services" in s.get(Job, "link").description


async def test_a_read_that_fails_leaves_the_platform_checks_as_they_were(service, monkeypatch):
    async def enrich(db, config, job_id):
        raise TimeoutError("slow site")

    monkeypatch.setattr(discovery, "enrich", enrich)
    assert await refuse_unread_posting(engine_for(service), add_job(service), RUN) is None


async def test_a_job_that_already_has_its_text_is_not_read_again(service, monkeypatch):
    async def enrich(db, config, job_id):
        raise AssertionError("read again")

    monkeypatch.setattr(discovery, "enrich", enrich)
    job = add_job(service, description=CITIZEN_POSTING)
    assert await refuse_unread_posting(engine_for(service), job, RUN) is None  # queue time already checked this text


async def test_demo_and_verification_only_runs_are_left_alone(service, monkeypatch):
    reading(monkeypatch, CITIZEN_POSTING)
    job = add_job(service)
    assert await refuse_unread_posting(engine_for(service), {**job, "demo": True}, RUN) is None
    verifying = {"packet": {**RUN["packet"], "verification_only": True}}
    assert await refuse_unread_posting(engine_for(service), job, verifying) is None


async def test_a_title_unknown_at_queue_time_meets_the_fit_filters_unless_the_owner_picked_it(service, monkeypatch):
    service.db.set_setting("job_board_settings", {"early_career_only": True})
    reading(monkeypatch, "Lead the platform team and mentor engineers across the organisation. " * 3, "Senior Software Engineer")
    job = add_job(service, "auto")
    result = await refuse_unread_posting(engine_for(service), job, RUN)
    assert result["state"] == "closed" and "career-level" in result["reason"]
    picked = add_job(service, "mine")
    with service.db.exclusive() as s:
        s.add(Setting(key="owner_pick:mine", value={"at": "2026-10-30T00:00:00"}))
    assert await refuse_unread_posting(engine_for(service), picked, RUN) is None


async def test_a_page_that_renders_in_the_browser_is_read_from_the_open_page(service):
    job = add_job(service)
    page = CITIZEN_POSTING + " More about the role and the team." * 8

    async def scan():
        return {"text": page, "headings": ["Data Engineer"], "fields": []}

    result = await refuse_unread_page(engine_for(service, scan), job, RUN)
    assert result["state"] == "closed" and "U.S. citizenship is required" in result["reason"]
    with service.db.session() as s:
        row = s.get(Job, "link")
        assert row.description == page and row.title == "Data Engineer"


async def test_a_login_page_is_not_stored_as_the_posting(service):
    job = add_job(service)

    async def scan():
        return {"text": "Sign in to continue. " * 20, "headings": ["Sign In"], "fields": [{"type": "password"}]}

    assert await refuse_unread_page(engine_for(service, scan), job, RUN) is None
    with service.db.session() as s:
        assert not s.get(Job, "link").description
