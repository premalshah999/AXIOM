"""One account identity across layers: the host list that allows a password page and the tenant that scopes the account agree,
and a finished application at one tenant proves no account at another tenant on the same host."""

from types import SimpleNamespace

from jobpilot.accounts import AccountFlow, account_tenant
from jobpilot.accounts.tenants import AUTH_HOSTS, SF_HOSTS
from jobpilot.db import Job, Run

GOV = "https://career-hcm20.ns2cloud.com/career?company={}&loginFlowRequired=true"
EU = "https://career5.successfactors.eu/career?company={}&loginFlowRequired=true"


def test_every_successfactors_auth_host_scopes_its_tenant():
    for host in AUTH_HOSTS["successfactors"]:
        if host == "jobs2web.com":  # employer-branded career sites, no tenant in the URL
            continue
        assert any(("." + host).endswith(h) for h in SF_HOSTS), host


def test_two_tenants_on_the_government_cloud_have_different_accounts():
    assert account_tenant(GOV.format("AcmeA"), GOV.format("AcmeA")) == "company:AcmeA"
    assert account_tenant(GOV.format("AcmeB"), GOV.format("AcmeB")) == "company:AcmeB"


def seed_applied(service, url, state="confirmed"):
    with service.db.exclusive() as s:
        job = Job(identity="sf-" + url[-20:] + state, url=url, ats="successfactors", title="Engineer", company="X")
        s.add(job)
        s.flush()
        s.add(Run(job_id=job.id, state=state, mode="submit"))


def applied_before(service, url):
    profile = SimpleNamespace(allow_account_creation=True, accept_all_application_terms=False, email="alex@example.test")
    form = SimpleNamespace(resolver=SimpleNamespace(profile=profile), page=None)
    engine = SimpleNamespace(service=service, form=form, job={"url": url, "ats": "successfactors"})
    return AccountFlow(engine).applied_before()


def test_a_finished_application_at_another_tenant_on_the_same_host_proves_nothing(service):
    seed_applied(service, EU.format("TenantOne") + "&career_job_req_id=1")
    assert applied_before(service, EU.format("TenantTwo") + "&career_job_req_id=9") is False


def test_a_finished_application_at_the_same_tenant_still_proves_the_account(service):
    seed_applied(service, EU.format("TenantOne") + "&career_job_req_id=1")
    assert applied_before(service, EU.format("TenantOne") + "&career_job_req_id=9") is True


def test_the_same_pair_on_the_government_cloud(service):
    seed_applied(service, GOV.format("AcmeA") + "&career_job_req_id=1", "already_applied")
    assert applied_before(service, GOV.format("AcmeB") + "&career_job_req_id=2") is False
    assert applied_before(service, GOV.format("AcmeA") + "&career_job_req_id=3") is True


def test_an_employers_own_career_site_is_its_own_account(service):
    seed_applied(service, "https://jobs.example.test/job/City-Engineer/1/")
    assert applied_before(service, "https://jobs.example.test/job/City-Engineer/2/") is True
    assert applied_before(service, "https://careers.other.test/job/City-Engineer/2/") is False
