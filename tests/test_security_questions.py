"""Dayforce ID registration (2026-10-16) continues on /account/register/securityquestions: three question
pickers, each followed by an "Answer" input. The account flow answers them itself, saves the answers sealed
with the account record, and never treats an application page's questions this way."""

from types import SimpleNamespace

from jobpilot.accounts import AccountFlow
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

QUESTIONS = ["What was the name of your first pet?", "In what city were you born?", "What was your first car?"]


def page_html():
    options = "".join(f"<option>{q}</option>" for q in QUESTIONS)
    rows = "".join(
        f'<label for="q{i}">Security Question #{i}</label><select id="q{i}"><option value="">Select</option>{options}</select>'
        f'<label for="a{i}">Answer</label><input id="a{i}" type="text">'
        for i in (1, 2, 3)
    )
    return f"""<!doctype html><html><body><h1>Security Questions</h1>{rows}
<button type="button" id="back">Back</button>
<button type="button" id="next" onclick="window.sent = [q1.value, q2.value, q3.value, a1.value, a2.value, a3.value]">Next</button>
</body></html>"""


async def engine_for(page, service, tmp_path, url):
    await page.route(url, lambda route: route.fulfill(content_type="text/html", body=page_html()))
    await page.goto(url)
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    profile = Profile(first_name="Alex", last_name="Rivera", email="alex@example.test", allow_account_creation=True)
    form = FormSession(page, Resolver(profile, service.db, service.config), resume, lambda *a, **k: None)

    async def nothing(*_a, **_k):
        return None

    events = []
    engine = SimpleNamespace(
        service=service, form=form, page=page, job={"url": "https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL/jobs/1"},
        emit=lambda *a, **k: events.append(a[1]), email_verification=SimpleNamespace(prepare_submission=nothing),
        browser_challenge=nothing,
    )
    return AccountFlow(engine), events


async def test_registration_security_questions_are_answered_and_saved_with_the_account(page, service, tmp_path):
    url = "https://dfid.dayforcehcm.com/globalidentity/account/register/securityquestions"
    flow, events = await engine_for(page, service, tmp_path, url)
    flow.store.save(url, flow.store.shared(), "created_locally")
    assert await flow.handle()
    sent = await page.evaluate("window.sent")
    assert sorted(sent[:3]) == sorted(QUESTIONS)
    answers = sent[3:]
    assert all(len(a) == 10 for a in answers) and len(set(answers)) == 3
    record = service.db.get_setting(flow.store.key(url), {})
    saved = service.mail.vault.open(record["security_answers"])
    assert saved == dict(zip(sent[:3], answers))
    assert any("security questions" in str(m) for m in events)


async def test_an_application_page_with_security_questions_is_not_answered_this_way(page, service, tmp_path):
    url = "https://careers.example.test/apply/12345"
    flow, _ = await engine_for(page, service, tmp_path, url)
    obs = await flow.e.form.scan()
    assert not await flow.security_questions(obs)
    assert await page.evaluate("window.sent") is None
