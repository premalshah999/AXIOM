"""Dayforce (NCMEC 2026-10-17): the submitted page says "Congratulations, your application has been submitted. The
confirmation number is ..." outside the generic status regions; every Dayforce success had ended unknown."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession, dayforce_tenant
from jobpilot.schemas import Profile

JOB = "https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL/jobs/3424"
DONE = JOB + "/apply/manualApplication?step=3"
PAGE = """<!doctype html><html><head><script>window.i18n={"submit-application-submitted":
"Congratulations, your application has been submitted!"}</script></head><body>
<header><input type="search" class="ant-select-selection-search-input" readonly> Sign In</header>
<div class="ant-card"><h1>Software Engineer</h1><div class="result"><h4 class="ant-typography">Congratulations, your
application has been submitted. The confirmation number is iJwgHsPg. You must save this confirmation number now.</h4></div></div>
</body></html>"""
FORM = """<!doctype html><html><head><script>window.i18n={"submit-application-submitted":
"Congratulations, your application has been submitted!"}</script></head><body>
<label><input type="checkbox"> I agree to the Candidate Acknowledgement</label><button>Submit</button></body></html>"""


async def form_on(page, tmp_path, url, html, job_url=JOB):
    await page.route(url, lambda route: route.fulfill(content_type="text/html", body=html))
    await page.goto(url)
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    resolver = Resolver(Profile(), None, None, job={"ats": "dayforce", "url": job_url})
    return FormSession(page, resolver, resume, lambda *a, **k: None)


async def test_the_dayforce_submitted_page_is_proof(page, tmp_path):
    form = await form_on(page, tmp_path, DONE, PAGE)
    receipt = await form.proof()
    assert receipt and receipt["evidence_selector"] == "dayforce_application_submitted"
    assert "confirmation number is iJwgHsPg" in receipt["confirmation"]


async def test_the_form_page_with_the_same_string_in_its_bundle_is_not(page, tmp_path):
    form = await form_on(page, tmp_path, DONE, FORM)
    assert not await form.proof()


async def test_another_tenants_page_is_not_this_jobs_proof(page, tmp_path):
    form = await form_on(page, tmp_path, DONE, PAGE, job_url="https://jobs.dayforcehcm.com/en-US/other/CANDIDATEPORTAL/jobs/9")
    receipt = await form.proof()
    assert not receipt or receipt.get("evidence_selector") != "dayforce_application_submitted"


def test_tenant_parsing():
    assert dayforce_tenant(JOB) == "acme"
    assert dayforce_tenant("https://jobs.dayforcehcm.com/ncmec/CANDIDATEPORTAL/jobs/3424/apply?x=1") == "ncmec"
    assert dayforce_tenant("https://example.test/acme") is None
