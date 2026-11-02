"""A saved password goes to the employer's own host or its application platform, and nowhere else.

The account flow's old deny-list passed idmsa.apple.com, secure.indeed.com and login.microsoft.com, and its questionnaire check needs a
launcher's own wording, so "Go to assessment" reached a vendor's login and typed. A saved password reached LinkedIn and an assessment
vendor (2026-11-02). The other half matters as much: an employer's own login, and every platform's, keeps working."""

from types import SimpleNamespace

import pytest

from jobpilot.accounts import AccountFlow
from jobpilot.accounts.credentials import Accounts
from jobpilot.accounts.tenants import AUTH_HOSTS
from jobpilot.answers import Resolver
from jobpilot.ats import AGGREGATORS, REGISTRY, VENDORS
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

CUSTOM_JOB = "https://careers.acme.test/job/1"
SIGN_IN = """<!doctype html><html><body><h1>Sign in</h1>
<label for=e>Email</label><input id=e type=email required>
<label for=p>Password</label><input id=p type=password required>
<button type=button onclick="window.sent=[e.value,p.value]">Sign in</button>
</body></html>"""


def accounts(allow_private=False):
    value = object.__new__(Accounts)
    value.config = SimpleNamespace(allow_private_urls=allow_private)
    return value


async def run_flow(page, service, tmp_path, url, job_url, *, ats="custom", company="Acme", **extra):
    await page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=SIGN_IN))
    await page.goto(url)
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    profile = Profile(first_name="Alex", last_name="Rivera", email="alex@example.test", allow_account_creation=True,
                      accept_all_application_terms=True)
    form = FormSession(page, Resolver(profile, service.db, service.config), resume, lambda *a, **k: None)

    async def nothing(*_a, **_k):
        return None

    engine = SimpleNamespace(
        service=service, form=form, page=page, job={"url": job_url, "ats": ats, "company": company}, emit=lambda *a, **k: None,
        email_verification=SimpleNamespace(prepare_submission=nothing), browser_challenge=nothing, result=None, **extra,
    )
    flow = AccountFlow(engine)
    # The worst case: the record production holds for a host like this one, with a login ready to be typed.
    flow.store.save(url, {"email": "alex@example.test", "password": "Synthetic-Pass1"}, "signing_in")
    assert await flow.handle()
    return engine


@pytest.mark.parametrize(
    "url,job_url,state,says",
    [
        # Apple's real password frame, on Apple's own posting: the same registrable domain as the job, and still an identity provider.
        ("https://idmsa.apple.com/appleauth/auth/signin", "https://jobs.apple.com/en-us/details/200000001", "waiting_browser", "idmsa.apple.com"),
        ("https://secure.indeed.com/auth", "https://www.indeed.com/viewjob?jk=abc123", "waiting_browser", "secure.indeed.com"),
        ("https://login.microsoft.com/common/oauth2/authorize", CUSTOM_JOB, "waiting_browser", "login.microsoft.com"),
        ("https://www.linkedin.com/checkpoint/lg/login", CUSTOM_JOB, "waiting_browser", "www.linkedin.com"),
        ("https://acme.okta.com/login/login.htm", CUSTOM_JOB, "waiting_browser", "acme.okta.com"),
        # "Go to assessment" is no launcher's wording, so no questionnaire is on record: the host alone decides.
        ("https://app.predictiveindex.com/login", CUSTOM_JOB, "closed", "personal test"),
        # On no list at all: an allow-rule stops what a deny-list has never heard of.
        ("https://portal.outsidevendor.test/login", CUSTOM_JOB, "waiting_browser", "portal.outsidevendor.test"),
    ],
)
async def test_nothing_is_typed_on_a_sign_in_that_is_not_the_employers(page, service, tmp_path, url, job_url, state, says):
    engine = await run_flow(page, service, tmp_path, url, job_url)
    assert await page.evaluate("window.sent") is None
    assert await page.locator("#p").input_value() == "" and await page.locator("#e").input_value() == ""
    assert engine.result["state"] == state
    assert says in engine.result["reason"], engine.result["reason"]
    assert "nothing was sent" in engine.result["reason"] or "nothing was typed" in engine.result["reason"] or engine.result["state"] == "closed"


@pytest.mark.parametrize(
    "url,job_url,ats",
    [
        ("https://careers.acme.test/login", CUSTOM_JOB, "custom"),  # A custom company site's own login on its own domain.
        ("https://sso.acme.test/login", CUSTOM_JOB, "custom"),  # Another host of the employer's own domain.
        ("https://acme.wd5.myworkdayjobs.com/External/login", CUSTOM_JOB, "custom"),  # Its career site handed off to its platform.
        ("https://career4.successfactors.com/career?company=acmeP", "https://jobs.acme.test/job/1", "custom"),
        ("https://acme.taleo.net/careersection/login.jsf", "https://acme.taleo.net/careersection/jobdetail.ftl?job=1", "taleo"),
        ("https://acme.avature.net/en_US/careers/Login", "https://acme.avature.net/en_US/careers/JobDetail/1", "avature"),
        ("https://atozpublic.idprism-auth.amazon.com/", "https://www.amazon.jobs/jobs/1/apply", "custom"),
    ],
)
async def test_the_employers_own_login_and_its_platforms_are_still_typed_into(page, service, tmp_path, url, job_url, ats):
    engine = await run_flow(page, service, tmp_path, url, job_url, ats=ats)
    assert engine.result is None, engine.result
    assert await page.evaluate("window.sent") == ["alex@example.test", "Synthetic-Pass1"]


async def test_the_employers_login_counts_when_the_jobs_own_link_landed_there(page, service, tmp_path):
    """A board's link that redirected to the employer: the address the job opened at is its own."""
    engine = await run_flow(page, service, tmp_path, "https://careers.other-name.test/login", "https://www.adzuna.com/land/ad/1",
                            ats="adzuna", company="Northwind", landing_url="https://careers.other-name.test/job/1")
    assert engine.result is None, engine.result
    assert await page.evaluate("window.sent") == ["alex@example.test", "Synthetic-Pass1"]


@pytest.mark.parametrize(
    "frame,job",
    [
        ("https://idmsa.apple.com/appleauth/auth/signin", "https://jobs.apple.com/en-us/details/1"),
        ("https://secure.indeed.com/auth", "https://www.indeed.com/viewjob?jk=1"),
        ("https://login.microsoft.com/common", "https://careers.microsoft.com/job/1"),
        ("https://accounts.google.com/signin", "https://www.google.com/about/careers/applications/jobs/results/1"),
        ("https://acme.b2clogin.com/acme.onmicrosoft.com/login", CUSTOM_JOB),
    ],
)
def test_the_adapters_own_check_never_passes_an_identity_provider(frame, job):
    assert not accounts().password_allowed("custom", frame, job)
    assert not accounts().password_allowed("workday", frame, job)


def test_the_adapters_own_check_still_passes_the_employer_and_its_platform():
    allowed = accounts().password_allowed
    assert allowed("custom", "https://sso.acme.test/login", CUSTOM_JOB)
    assert allowed("workday", "https://acme.wd5.myworkdayjobs.com/login", CUSTOM_JOB)
    assert allowed("custom", "https://atozpublic.idprism-auth.amazon.com/", "https://www.amazon.jobs/jobs/1")
    assert allowed("avature", "https://apply.deloitte.com/login", "https://deloitteus.avature.net/careers/1")
    assert not allowed("custom", "http://sso.acme.test/login", CUSTOM_JOB)  # Never over plain HTTP.
    assert not allowed("custom", "https://acme.test.phish.example/login", CUSTOM_JOB)


# ----- the rule itself, over every platform the app supports ----------------------------------------------------------------


def outside_kind(url):
    from urllib.parse import urlsplit

    from jobpilot.accounts.tenants import outside_sign_in

    return outside_sign_in(urlsplit(url).hostname)


def allowed(frame, *own, ats="custom", company="", allow_private=False):
    from jobpilot.accounts.credentials import account_host_allowed

    return account_host_allowed(ats, frame, own, company=company, allow_private=allow_private)


def supported_platform_hosts():
    for platform in REGISTRY:
        if platform.id in AGGREGATORS or platform.id in VENDORS:
            continue
        for domain in platform.domains:
            yield platform.id, domain
    for ats, domains in AUTH_HOSTS.items():
        for domain in domains:
            yield ats, domain


def test_every_supported_platforms_sign_in_keeps_working_from_a_custom_site():
    hosts = list(supported_platform_hosts())
    assert len(hosts) > 100
    for ats, domain in hosts:
        assert allowed(f"https://{domain}/login", CUSTOM_JOB), domain
        assert allowed(f"https://tenant.{domain}/login", CUSTOM_JOB), domain
        assert allowed(f"https://tenant.{domain}/login", f"https://tenant.{domain}/job/1", ats=ats), domain


def test_a_job_board_or_a_career_site_front_end_is_no_platform_of_the_employers():
    for platform in REGISTRY:
        if platform.id not in AGGREGATORS | VENDORS:
            continue
        for domain in platform.domains:
            assert not allowed(f"https://www.{domain}/login", CUSTOM_JOB), domain
            # Its own posting's sign-in is that site's own, as before: an account the job's own address asks for.
            assert allowed(f"https://www.{domain}/auth/login", f"https://www.{domain}/jobs/1"), domain


def test_a_look_alike_of_a_platform_or_the_employer_is_not_them():
    assert not allowed("https://myworkdayjobs.com.phish.test/login", CUSTOM_JOB)
    assert not allowed("https://notmyworkdayjobs.com/login", CUSTOM_JOB)
    assert not allowed("https://acme.test.phish.example/login", CUSTOM_JOB)


@pytest.mark.parametrize(
    "frame,company",
    [
        ("https://login.acme.test/signin", "Acme"),
        ("https://acme.test/signin", "Acme, Inc."),
        ("https://acmecareers.test/signin", "Acme Corp"),
        ("https://acme-jobs.test/signin", "Acme"),
        ("https://careers-acme-corp.test/signin", "Acme Corp"),
        ("https://bankofnorthwind.test/signin", "Bank of Northwind"),
    ],
)
def test_a_host_named_for_the_employer_is_the_employers_own(frame, company):
    assert allowed(frame, "https://www.adzuna.com/land/ad/1", company=company)


@pytest.mark.parametrize(
    "frame,company",
    [
        ("https://hireright.test/signin", "Hire"),  # A word in common is not the employer's name.
        ("https://acmeassessments.test/signin", "Acme"),
        ("https://signin.test/signin", ""),
        ("https://idmsa.apple.com/signin", "Apple"),  # The employer's name doesn't make its identity service a job site.
        ("https://login.microsoftonline.com/signin", "Microsoft"),
        ("https://www.linkedin.com/login", "LinkedIn"),
        ("https://secure.indeed.com/auth", "Indeed"),
        ("https://www.hirevue.com/login", "HireVue"),
    ],
)
def test_a_host_that_only_resembles_the_employers_name_is_not(frame, company):
    assert not allowed(frame, "https://www.adzuna.com/land/ad/1", company=company)


def test_a_tenant_named_for_the_employer_leads_to_its_own_domain_on_every_platform():
    assert allowed("https://apply.acme.test/login", "https://acme.bamboohr.com/careers/1")
    assert allowed("https://apply.acme.test/login", "https://acme.wd5.myworkdayjobs.com/External/job/1", ats="workday")
    assert not allowed("https://apply.other.test/login", "https://acme.bamboohr.com/careers/1")


def test_plain_http_and_an_address_with_no_host_are_never_typed_into():
    assert not allowed("http://careers.acme.test/login", CUSTOM_JOB)
    assert not allowed("about:blank", CUSTOM_JOB)
    assert not allowed("", "")
    assert allowed("http://127.0.0.1:8000/login", "http://127.0.0.1:8000/job/1", allow_private=True)  # The test rig's own server.


@pytest.mark.parametrize(
    "url,kind",
    [
        ("https://idmsa.apple.com/x", "identity"),
        ("https://appleid.apple.com/x", "identity"),
        ("https://secure.indeed.com/x", "identity"),
        ("https://login.microsoft.com/x", "identity"),
        ("https://login.microsoftonline.com/x", "identity"),
        ("https://login.live.com/x", "identity"),
        ("https://accounts.google.com/x", "identity"),
        ("https://www.linkedin.com/x", "identity"),
        ("https://x.com/x", "identity"),
        ("https://acme.okta.com/x", "identity"),
        ("https://acme.auth0.com/x", "identity"),
        ("https://app.predictiveindex.com/x", "assessment"),
        ("https://assess.vendor.test/x", "assessment"),
        ("https://wotc.adp.com/x", "assessment"),
        ("https://taxcredit.icims.com/x", "assessment"),
        ("https://acme.hirevue.com/x", "assessment"),
        ("https://fox.com/x", ""),  # x.com is a whole domain, not a tail.
        ("https://apple.com/x", ""),
        ("https://jobs.apple.com/x", ""),
        ("https://workforcenow.adp.com/x", ""),
        ("https://login.icims.com/x", ""),
        ("https://careers.acme.test/x", ""),
    ],
)
def test_what_is_never_typed_into(url, kind):
    assert outside_kind(url) == kind


def test_the_questionnaire_a_platform_hosts_is_still_refused_from_that_platform():
    assert not allowed("https://wotc.adp.com/login", "https://acme.wd5.myworkdayjobs.com/External/job/1", ats="workday")
    assert not allowed("https://taxcredit.icims.com/login", "https://careers-acme.icims.com/jobs/1/job", ats="icims")
    assert allowed("https://workforcenow.adp.com/login", "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=1", ats="adp")
