"""Upstart (2026-10-23/25): its closed Greenhouse posting led to the career site's job search, whose talent-network
form (resume, standard questions, "Please choose the category/categories" and "location(s)") was submitted as the
application. A job-alert form on a job search page is a signup, even with a resume upload."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

FORM = """<form action="/call_to_actions/x/form_submissions" method=post>
<label for=f>First Name</label><input id=f name=first required>
<label for=e>Email</label><input id=e name=email required>
<label for=r>Resume</label><input id=r type=file name=resume>
<label for=c>Please choose the category/categories</label><select id=c name=cat><option>Engineering</option></select>
<label for=l>Please choose the location(s)</label><select id=l name=loc><option>Remote</option></select>
<button type=submit>Submit</button></form>"""


async def open_at(page, url, body):
    await page.route(url, lambda route: route.fulfill(body=body, content_type="text/html"))
    await page.goto(url)


def session(page, service, tmp_path):
    resolver = Resolver(Profile(), service.db, service.config, job={"ats": "greenhouse", "url": "https://x.test"})
    return FormSession(page, resolver, tmp_path / "unused.pdf", lambda *a: None)


async def test_a_job_alert_form_on_a_job_search_page_is_a_signup(page, service, tmp_path):
    await open_at(page, "https://careers.example.test/jobs/search", FORM)
    form = session(page, service, tmp_path)
    await form.scan()
    final = await form.application_submit_controls()
    assert await form.signup_form(final[0]["id"]) is True


async def test_the_same_questions_on_a_posting_page_are_the_application(page, service, tmp_path):
    await open_at(page, "https://careers.example.test/jobs/12345-data-engineer", FORM)
    form = session(page, service, tmp_path)
    await form.scan()
    final = await form.application_submit_controls()
    assert await form.signup_form(final[0]["id"]) is False
