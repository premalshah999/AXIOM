"""Greenhouse application embedded on the employer's own site: the emailed code goes into the board iframe.

Roblox, Stripe, Waymo and Toast (2026-10-10 to 2026-10-19) open the employer's page and render the application in a
job-boards.greenhouse.io iframe. The page-level origin check compared the employer's URL with the mail rule's email
link origins, so 13 of 21 such jobs never submitted ("Add this employer's login origin to its email verification
rule", or an unexplained unknown a few seconds after the commit). The code may be typed only into inputs whose own
frame is this job's Greenhouse board or embed URL.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest

from jobpilot.answers import Resolver
from jobpilot.browser import GUARD, BrowserEngine
from jobpilot.email_browser import EmailBrowser
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile
from support_mail import configured, message, provider

JOB = "https://job-boards.greenhouse.io/example/jobs/1"
EMBED = "https://job-boards.greenhouse.io/embed/job_app?for=example&token=1"
EMPLOYER_PAGE = "https://careers.example.test/start"
PROMPT = {"text": "To submit your application, enter the 8-character security code sent to your email."}


def browser(frames, *, armed=True, ats="greenhouse", page=EMPLOYER_PAGE):
    b = object.__new__(EmailBrowser)
    b.engine = SimpleNamespace(
        job={"ats": ats, "url": JOB, "company": "Example"},
        armed=armed,
        page=SimpleNamespace(url=page),
        form=SimpleNamespace(frames={k: SimpleNamespace(url=v) for k, v in frames.items()}),
    )
    b.rule = {"link_origins": ["https://notify.example.test"]}
    return b


FIELDS = [{"id": "c1"}, {"id": "c2"}]


def test_employer_page_is_not_a_greenhouse_origin_but_its_board_iframe_is():
    b = browser({"c1": EMBED, "c2": EMBED})
    assert not b.code_origin_allowed(EMPLOYER_PAGE, PROMPT)
    assert b.embedded_code_frames_allowed(FIELDS, PROMPT)


def test_eu_board_iframe_is_accepted():
    eu = "https://job-boards.eu.greenhouse.io/embed/job_app?for=example&token=1"
    assert browser({"c1": eu, "c2": eu}).embedded_code_frames_allowed(FIELDS, PROMPT)


@pytest.mark.parametrize(
    "other",
    [
        "https://careers.example.test/start",  # the employer's own document is not a code frame
        "https://evil.example.test/embed/job_app?for=example&token=1",  # not a Greenhouse host
        "https://job-boards.greenhouse.io/embed/job_app?for=other&token=999",  # another employer's job
        "http://job-boards.greenhouse.io/embed/job_app?for=example&token=1",  # not https
        "https://user:pw@job-boards.greenhouse.io/embed/job_app?for=example&token=1",  # credentials in the URL
        "https://job-boards.greenhouse.io:8443/embed/job_app?for=example&token=1",  # unexpected port
    ],
)
def test_one_foreign_frame_among_the_code_boxes_refuses(other):
    assert not browser({"c1": EMBED, "c2": other}).embedded_code_frames_allowed(FIELDS, PROMPT)


def test_requires_the_post_submit_prompt_an_armed_commit_and_greenhouse():
    frames = {"c1": EMBED, "c2": EMBED}
    assert not browser(frames).embedded_code_frames_allowed(FIELDS, {"text": "Security code"})
    assert not browser(frames, armed=False).embedded_code_frames_allowed(FIELDS, PROMPT)
    assert not browser(frames, ats="eightfold").embedded_code_frames_allowed(FIELDS, PROMPT)
    assert not browser(frames).embedded_code_frames_allowed([], PROMPT)
    assert not browser({"c1": EMBED}).embedded_code_frames_allowed(FIELDS, PROMPT)  # c2 has no frame


async def embedded_engine(service, page, tmp_path, resume_pdf):
    """An armed Greenhouse run whose employer page embeds the board iframe; the mail rule lists another origin."""
    from jobpilot.db import Run

    # Greenhouse jobs are keyed by the board's origin, never by the employer's own site.
    box, run, rule = await configured(service, employer="https://job-boards.greenhouse.io/example")
    assert "https://careers.example.test" not in rule["link_origins"]
    cfg = service.config
    cfg.mail_poll_seconds = 0.1
    cfg.mail_wait_seconds = 60  # the request window opens at the run's start; browser setup eats into it
    resume = tmp_path / "fixture-resume.pdf"
    resume.write_bytes(resume_pdf)
    profile = Profile(
        name="Alex Example", email="alex@example.test", facts={"first_name": "Alex", "last_name": "Example"}
    )
    run["packet"] = {"profile": profile.model_dump(), "resume_path": str(resume)}
    e = BrowserEngine(service, run["id"])
    e.job = {"url": JOB, "demo": False, "ats": "greenhouse", "company": "Example"}
    e.run_record, e.page, e.dns_cache = run, page, set()
    e.form = FormSession(page, Resolver(profile, service.db, cfg, run["id"], "Example"), resume, e.emit)
    e.email_verification = EmailBrowser(e)

    employer = f'<h1>Careers</h1><iframe src="{EMBED}" width="900" height="600"></iframe>'
    application = """<p>To submit your application, enter the security code sent to your email.</p>
    <label>Full name<input value="Alex Example"></label>
    <label>Security code<input name="code" autocomplete="one-time-code" oninput="document.querySelector('button').disabled=false"></label>
    <button disabled onclick="document.body.innerHTML='<h1>Application submitted</h1>'">Submit application</button>"""

    async def fixture(route):
        url = route.request.url
        if url == EMPLOYER_PAGE:
            return await route.fulfill(body=employer, content_type="text/html")
        if url.startswith("https://job-boards.greenhouse.io/embed/job_app"):
            return await route.fulfill(body=application, content_type="text/html")
        return await route.fulfill(status=404, body="Unexpected request")

    await page.context.add_init_script(GUARD)
    await page.context.route("**/*", fixture)
    await page.context.route("**/*", e.network_guard)
    await page.goto(EMPLOYER_PAGE)
    await page.wait_for_selector("iframe")
    await asyncio.sleep(0.5)  # the iframe document loads after the parent
    e.armed = True
    with service.db.session() as s:
        s.get(Run, e.run_id).state = "submitting"
    # A shared Greenhouse sender is attributed only when the mail names the employer.
    service.mail.transport = provider(
        [message("Example: your verification code is 483921", received=time.time() + 3)]
    )
    return e


async def test_code_is_entered_in_the_iframe_when_the_rule_does_not_list_the_employer_page(
    service, page, tmp_path, resume_pdf
):
    e = await embedded_engine(service, page, tmp_path, resume_pdf)
    poller = asyncio.create_task(service.mail.poll())
    try:
        assert not e.email_verification.code_origin_allowed(page.url, PROMPT)
        assert await e.email_verification.handle()
        frame = next(f for f in page.frames if f.url.startswith("https://job-boards.greenhouse.io/embed/job_app"))
        assert await frame.locator("h1").inner_text() == "Application submitted"
    finally:
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
        e.email_verification.close()


async def test_without_the_iframe_allowance_the_original_error_is_raised(
    service, page, tmp_path, resume_pdf, monkeypatch
):
    # Proves the fixture reproduces the production failure (Roblox 50e9bd1d, 2026-10-18) rather than passing trivially.
    monkeypatch.setattr(EmailBrowser, "embedded_code_frames_allowed", lambda self, fields, obs: False)
    e = await embedded_engine(service, page, tmp_path, resume_pdf)
    try:
        with pytest.raises(ValueError, match="Add this employer's login origin"):
            await e.email_verification.handle()
    finally:
        e.email_verification.close()
