"""Career sites that hand off to their iCIMS portal (Planview careers.planview.com/jobs/5080 -> careers2-planview.icims.com;
Noblis, Rivian, America's Tire, 2026-10-27). The run kept the career site as the job: the signed-in-session checks
never matched the portal, the returning-candidate login and the candidate dashboard opened on the career site, and
"return to job" left the portal. After the handoff the portal's posting is the run's job URL and entry, as for Workday,
and account records saved under the career site are carried over to the portal's key."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot.accounts import Accounts
from jobpilot.adapters.icims import ICIMS
from jobpilot.browser import BrowserEngine
from jobpilot.schemas import Profile

SITE = "https://careers.acme.example/jobs/5080?icims=1"
PORTAL = "https://careers2-acme.icims.com/jobs/5080/login"
POSTING = "https://careers2-acme.icims.com/jobs/5080/job"
CENTRAL = "https://login.icims.com/u/login/password?state=synthetic"
LOGIN = {"email": "alex@example.test", "password": "Synthetic9!x"}


@pytest.mark.parametrize("url,posting", [
    (PORTAL, POSTING),
    ("https://careers2-acme.icims.com/jobs/5080/software-engineer/candidate?mode=apply", POSTING),
    ("https://careers2-acme.icims.com/jobs/login?loginOnly=1", None),
    ("https://login.icims.com/jobs/5080/x", None),
    ("https://careers.acme.example/jobs/5080", None),
    ("http://careers2-acme.icims.com/jobs/5080/job", None),
])
def test_the_posting_behind_a_portal_page(url, posting):
    assert ICIMS.posting_url(url) == posting


def engine(service, **extra):
    value = BrowserEngine.__new__(BrowserEngine)
    value.job = {"id": "job-acme", "url": SITE, "ats": "custom", "demo": False}
    value.service, value.db = service, service.db
    value.profile = Profile(email="alex@example.test", allow_account_creation=True)
    value.application_entry_url = SITE
    value.emit = lambda *a, **k: None
    value.__dict__.update(extra)
    return value


async def test_the_embedded_handoff_adopts_the_portal(service, monkeypatch):
    page = SimpleNamespace(url=SITE, wait_for_load_state=AsyncMock(), evaluate=AsyncMock(return_value=[]),
                           goto=AsyncMock())
    monkeypatch.setattr("jobpilot.browser.detect_embedded", lambda entries: ("icims", PORTAL))
    value = engine(service, page=page)
    assert await value.detect_ats(page, "custom") == "icims"
    assert value.job["url"] == POSTING and value.job["ats"] == "icims"
    assert value.application_entry_url == POSTING + "?in_iframe=1"
    assert value.listing_url == SITE and value.handoff_origin == "https://careers2-acme.icims.com"


def test_the_central_login_saved_under_the_career_site_is_carried_over(service):
    before = Accounts(service, Profile(email="alex@example.test"), SITE)
    before.mark(CENTRAL, "authenticated", LOGIN, reset_submitted_at="2026-10-27T20:48:06+00:00")
    after = Accounts(service, Profile(email="alex@example.test"), POSTING)
    assert after.record(CENTRAL) == {}  # keyed by the portal now
    value = engine(service, page=SimpleNamespace(url=PORTAL))
    value.adopt_handoff("icims", PORTAL)
    for url in (CENTRAL, POSTING, PORTAL):
        record = after.record(url)
        assert record["state"] == "authenticated" and after.saved(url) == LOGIN
    assert after.record(CENTRAL)["tenant"] == "employer:https://careers2-acme.icims.com"
    assert before.record(CENTRAL)["state"] == "authenticated"  # the career site's record is kept


def test_a_portal_record_is_never_overwritten(service):
    Accounts(service, Profile(email="alex@example.test"), SITE).mark(CENTRAL, "credentials_rejected", LOGIN)
    portal = Accounts(service, Profile(email="alex@example.test"), POSTING)
    portal.mark(PORTAL, "authenticated", {**LOGIN, "password": "Newer9!x"})
    assert portal.store.adopt_listing(SITE) is False
    assert portal.record(CENTRAL)["state"] == "authenticated"


def test_a_job_already_on_its_portal_is_left_alone(service):
    value = engine(service, page=SimpleNamespace(url=PORTAL))
    value.job["url"], value.job["ats"] = POSTING, "icims"
    assert value.adopt_icims_portal(PORTAL) is False
    assert not hasattr(value, "listing_url")


def test_a_resumed_tab_finds_the_portal_its_run_adopted(service):
    service.db.set_setting("desktop_job:job-acme", {"target_id": "t1"})
    value = engine(service, page=SimpleNamespace(url=PORTAL))
    value.adopt_handoff("icims", PORTAL)
    assert service.db.get_setting("desktop_job:job-acme")["platform_url"] == POSTING


def test_portal_handoff_updates_the_resolvers_receipt_identity(service):
    from jobpilot.forms import icims_receipt

    value = engine(service, page=SimpleNamespace(url=PORTAL))
    resolver = SimpleNamespace(job=value.job)
    value.adopt_handoff("icims", PORTAL)
    assert resolver.job is value.job
    assert resolver.job["ats"] == "icims"
    assert icims_receipt(resolver.job["url"], POSTING,
                         "Your application was submitted successfully. Thank you for applying.")
    assert icims_receipt(resolver.job["url"], POSTING.replace('/5080/', '/5081/'),
                         "Your application was submitted successfully. Thank you for applying.") is None


def test_workday_handoff_keeps_shared_job_identity(service):
    page = SimpleNamespace(url="https://acme.wd1.myworkdayjobs.com/en-US/jobs/job/Analyst_R1/apply")
    value = engine(service, page=page)
    resolver = SimpleNamespace(job=value.job)
    value.adopt_handoff("workday")
    assert resolver.job is value.job
    assert resolver.job["ats"] == "workday"
    assert resolver.job["url"].endswith('/Analyst_R1')
