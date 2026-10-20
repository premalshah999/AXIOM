"""Ashby employers state their own application limits when they refuse (2026-10-18/19). They were recorded as generic
failures and retried: Benchling three times, OpenAI and Aurora twice each. The refusal now closes the job with the
stated limit, and the employer's other jobs wait (or are excluded) instead of being refused one by one."""

from datetime import UTC, datetime, timedelta

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.answers import Resolver
from jobpilot.application_pacing import application_cap, record_employer_cap, require_company_limit
from jobpilot.db import Job
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

BENCHLING = "You've reached the maximum of 3 active applications at Benchling."
AURORA = "To ensure a thorough review of all profiles, candidates are limited to 3 applications every 30 days."
OPENAI = ("Please Note: we have set up limits for applications across roles. Candidates may not apply more than 5 times "
          "in any 180 day span.")


def test_stated_limits_are_read():
    assert application_cap(BENCHLING) == {"limit": 3, "days": None}
    assert application_cap(AURORA) == {"limit": 3, "days": 30}
    assert application_cap(OPENAI) == {"limit": 5, "days": 180}
    assert application_cap("Please correct the errors below") is None


def _jobs(service, company, n, status="confirmed"):
    with service.db.exclusive() as s:
        for i in range(n):
            s.add(Job(id=f"{company}{i}", identity=f"{company}{i}", url=f"https://jobs.ashbyhq.com/{company}/{i}",
                      ats="ashby", company=company, title="Engineer", status=status))
        s.add(Job(id=f"{company}-next", identity=f"{company}-next", url=f"https://jobs.ashbyhq.com/{company}/next",
                  ats="ashby", company=company, title="Engineer", status="ready"))


def test_a_remembered_cap_holds_the_employers_other_jobs(service):
    _jobs(service, "Aurora", 3)
    record_employer_cap(service.db, {"company": "Aurora", "url": "https://jobs.ashbyhq.com/Aurora/0"}, application_cap(AURORA))
    with service.db.session() as s:
        with pytest.raises(QueueBlocked) as blocked:
            require_company_limit(s, s.get(Job, "Aurora-next"))
    assert blocked.value.code == "employer_cap" and blocked.value.outcome == "deferred"
    assert datetime.fromisoformat(blocked.value.not_before) - datetime.now(UTC) > timedelta(days=29)
    assert "at most 3 applications every 30 days" in str(blocked.value)


def test_an_active_cap_excludes_until_the_owner_acts(service):
    _jobs(service, "Benchling", 3)
    record_employer_cap(service.db, {"company": "Benchling", "url": "https://jobs.ashbyhq.com/Benchling/0"},
                        application_cap(BENCHLING))
    with service.db.session() as s:
        with pytest.raises(QueueBlocked) as blocked:
            require_company_limit(s, s.get(Job, "Benchling-next"))
    assert blocked.value.outcome == "excluded"


def test_below_the_cap_the_employer_is_open(service):
    _jobs(service, "OpenAI", 2)
    record_employer_cap(service.db, {"company": "OpenAI", "url": "https://jobs.ashbyhq.com/OpenAI/0"}, application_cap(OPENAI))
    with service.db.session() as s:
        require_company_limit(s, s.get(Job, "OpenAI-next"))


async def test_the_refusal_page_is_an_employer_limit(page, tmp_path):
    await page.set_content(f"<main><h2>We couldn't submit your application</h2><p>{BENCHLING}</p></main>")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a: None)
    rejection = await form.rejection()
    assert rejection["category"] == "employer_limit" and rejection["cap"] == {"limit": 3, "days": None}
