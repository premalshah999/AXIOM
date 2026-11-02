"""TikTok (2026-11-01): "you can submit up to 2 job applications (including referrals) within the "Bachelor/Master Graduate
- 2027 Start" recruitment project. You've reached the application limit." The limit was saved against the employer, so all
of its jobs waited until 2027, a Senior Data Scientist included. A limit that names a recruitment project holds that
project's postings; an employer's other postings are tried."""

from datetime import UTC, datetime

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.application_pacing import EMPLOYER_CAP_PREFIX, application_cap, employer_identity, record_employer_cap, require_company_limit
from jobpilot.db import Job, Run, Setting
from jobpilot.employer_limit import covers
from jobpilot.loop_guard import learn_from_history, scope_saved_caps, stop_of

SAID = (
    "From 2026-07-01 to 2026-12-31, you can submit up to 2 job applications (including referrals) within the "
    "“Bachelor/Master Graduate - 2027 Start” recruitment project. You've reached the application limit"
)
PLAIN = "You've reached the application limit"


def test_the_project_is_read_from_the_refusal():
    cap = application_cap(SAID)
    assert cap["limit"] == 2 and cap["project"] == "Bachelor/Master Graduate - 2027 Start"
    assert cap["until"].startswith("2027-01-01")
    straight = SAID.replace("“", '"').replace("”", '"')
    assert application_cap(straight)["project"] == "Bachelor/Master Graduate - 2027 Start"
    assert "project" not in application_cap(PLAIN)


@pytest.mark.parametrize(
    "title, held",
    [
        ("New Grad 2027: Machine Learning Engineer (Ads)", True),
        ("Backend Software Engineer Graduate (Creation Platform) - 2027 Start", True),
        ("Research Engineer, Monetization Technology (Master's)", True),
        ("Senior Data Scientist, Global E-commerce Seller Business", False),
        ("Senior Software Engineer, Ads Measurement", False),
    ],
)
def test_a_project_limit_holds_only_that_projects_postings(title, held):
    assert covers(application_cap(SAID), title) is held


def test_a_limit_naming_no_project_holds_every_posting():
    assert covers(application_cap(PLAIN), "Senior Data Scientist") is True
    assert covers(None, "Anything") is True


def _jobs(service):
    with service.db.exclusive() as s:
        for job_id, title in (("grad", "New Grad 2027: Backend Engineer"), ("senior", "Senior Data Scientist")):
            s.add(Job(id=job_id, identity=job_id, url=f"https://jobs.example.test/{job_id}", ats="custom", company="Example Co",
                      title=title, status="ready"))


def test_a_saved_project_limit_holds_the_graduate_job_and_not_the_senior_one(service):
    _jobs(service)
    record_employer_cap(service.db, {"company": "Example Co", "url": "https://jobs.example.test/grad"}, application_cap(SAID))
    with service.db.session() as s:
        with pytest.raises(QueueBlocked) as blocked:
            require_company_limit(s, s.get(Job, "grad"))
        assert blocked.value.code == "employer_cap" and "matching jobs wait until January 1, 2027" in str(blocked.value)
        require_company_limit(s, s.get(Job, "senior"))
        assert stop_of(s, s.get(Job, "grad")) is not None
        assert stop_of(s, s.get(Job, "senior")) is None


def test_a_limit_saved_before_projects_were_read_is_scoped_from_its_refusal(service):
    _jobs(service)
    now = datetime.now(UTC).isoformat()
    with service.db.exclusive() as s:
        s.add(Run(id="refused", job_id="grad", state="closed", mode="submit", packet={}, receipt={}, reason=SAID,
                  elapsed=0, cost=0, model_calls=0, created_at=now))
        key = EMPLOYER_CAP_PREFIX + employer_identity(s, s.get(Job, "grad"))
        s.add(Setting(key=key, value={
            "limit": 2, "days": None, "until": "2027-01-01T12:00:00+00:00", "company": "Example Co", "run_id": "refused",
            "sentence": "Example Co says you've reached its limit of 2 applications, so its jobs wait until January 1, 2027."}))
    assert scope_saved_caps(service.db) == 1
    assert scope_saved_caps(service.db) == 0
    with service.db.session() as s:
        saved = s.get(Setting, EMPLOYER_CAP_PREFIX + employer_identity(s, s.get(Job, "grad"))).value
        assert saved["project"] == "Bachelor/Master Graduate - 2027 Start"
        assert stop_of(s, s.get(Job, "grad")) is not None
        assert stop_of(s, s.get(Job, "senior")) is None
