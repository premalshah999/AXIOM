"""Planview (2026-10-27): the job's URL is the employer's career site (careers.planview.com/jobs/5080?icims=1), which
handed off to its iCIMS portal (careers2-planview.icims.com/jobs/5080/login). The email step answered "already in
use", and the returning-candidate login was built on the job URL's host: careers.planview.com/jobs/login sent the run
to the career site's home page, where the guided agent searched jobs and filled another posting. The login goes to the
portal that showed the email step."""

from unittest.mock import AsyncMock

from support_icims import adapter

IN_USE = "The email address and phone number are already in use. Please login or use another email address."


def email_step(url, frame_url=None):
    field = {"id": "e", "name": "css_loginName", "type": "email", "label": "Email", "required": False, "value": ""}
    if frame_url:
        field["frame_url"] = frame_url
    return {"url": url, "errors": [], "headings": [], "controls": [], "fields": [field],
            "text": "Enter Your Information " + IN_USE}


async def returning(service, obs, job_url):
    value = adapter(service, obs)
    value.job["url"] = job_url
    value.e.form.page.goto = AsyncMock()
    value.form.settle = AsyncMock()
    assert await value.returning_candidate_login(obs) is True
    return value.e.form.page.goto.await_args.args[0]


async def test_a_career_site_handoff_signs_in_on_the_portal(service):
    obs = email_step("https://careers2-acme.icims.com/jobs/5080/login?mobile=false&needsRedirect=false")
    assert await returning(service, obs, "https://careers.acme.example/jobs/5080?icims=1") == (
        "https://careers2-acme.icims.com/jobs/login?loginOnly=1&in_iframe=1"
    )


async def test_a_portal_framed_in_the_career_site_is_found_by_its_fields(service):
    obs = email_step("https://careers.acme.example/jobs/5080?icims=1",
                     frame_url="https://careers2-acme.icims.com/jobs/5080/login?in_iframe=1")
    assert await returning(service, obs, "https://careers.acme.example/jobs/5080?icims=1") == (
        "https://careers2-acme.icims.com/jobs/login?loginOnly=1&in_iframe=1"
    )


async def test_a_job_on_the_portal_keeps_its_host(service):
    obs = email_step("https://eb-acme.icims.com/jobs/9917/ai-engineer/login?step=email")
    assert await returning(service, obs, "https://eb-acme.icims.com/jobs/9917/ai-engineer/job") == (
        "https://eb-acme.icims.com/jobs/login?loginOnly=1&in_iframe=1"
    )
