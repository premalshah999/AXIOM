"""A real page and the real solver delivery: on Phenom's review step the reCAPTCHA token's callback is what posts the application. Outside the
commit no token is delivered; inside it exactly one is, the application is posted once, and nothing clicks the button after it."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest

from jobpilot.answers import Resolver
from jobpilot.browser import RECEIVED_REASON, BrowserEngine
from jobpilot.captcha import HOOK
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

pytestmark = [pytest.mark.browser, pytest.mark.integration]

JOB = "https://careers.acme-health.test/us/en/job/ACMACMUSR434028EXTERNALENUS/AI-Engineer"
REVIEW = "https://careers.acme-health.test/us/en/apply?jobSeqNo=ACMACMUSR434028EXTERNALENUS&step=6&stepname=applicationReview"
THANKS = "https://careers.acme-health.test/us/en/applythankyou?status=success&jobSeqNo=ACMACMUSR434028EXTERNALENUS&jobId=R-434028"
PAGE = """<!doctype html><title>Review</title><h1>AI Engineer (R-434028)</h1><h2>My Information</h2><p>Alex Rivera</p>
  <div id=widget class=g-recaptcha data-sitekey=fixture></div><textarea name=g-recaptcha-response hidden></textarea>
  <form onsubmit="return false"><button type=button id=previous class="btn btn-navigate btn-prev">Previous Step</button>
  <button type=submit id=go class="btn primary-button btn-submit" atm-id=submit-button aria-label=Continue>Continue</button></form>"""


async def review_page(page, service, monkeypatch, tmp_path, frame_stays=False):
    posts = []

    async def route(request):
        if request.request.method == "POST":
            posts.append(urlsplit(request.request.url).path)
            await request.fulfill(status=200, content_type="application/json", body="{}")
        elif "applythankyou" in request.request.url:
            await request.fulfill(status=200, content_type="text/html", body="<h1>One Last Step!</h1><p>There's just one last step.</p>")
        else:
            await request.fulfill(status=200, content_type="text/html", body=PAGE)

    await page.route("**/*", route)
    await page.goto(REVIEW)
    await page.evaluate(HOOK)
    await page.evaluate(
        """({thanks, frameStays}) => {
        // A press of the button is a post of its own, so `posts` shows any click, whatever page the run ended on.
        document.querySelector('#go').addEventListener('click', () => { fetch('/pressed', {method: 'POST', body: '{}'}); });
        if (frameStays) {
          const f = document.createElement('iframe'); f.title = 'recaptcha challenge expires in two minutes';
          f.style.cssText = 'width:300px;height:200px'; f.srcdoc = 'Challenge'; document.body.append(f);
        }
        window.grecaptcha = {render: () => 1};
        grecaptcha.render('widget', {sitekey: 'fixture', callback: async () => {
          if (!frameStays) document.querySelector('#go').disabled = true;
          await fetch('/applySubmit', {method: 'POST', body: '{}'});
          if (!frameStays) setTimeout(() => { location.href = thanks; }, 300);
        }}); }""",
        {"thanks": THANKS, "frameStays": frame_stays},
    )
    service.config.twocaptcha_api_key = "synthetic-key"
    bought = AsyncMock(return_value={"token": "synthetic-token"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", bought)
    engine = BrowserEngine(service, "synthetic-run")
    engine.page, engine.job = page, {"id": "phenom-job", "url": JOB, "demo": False}
    engine.form = FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path / "resume.pdf", lambda *a, **k: None)
    await engine.form.scan()
    return engine, posts, bought


async def test_no_token_outside_the_commit_and_exactly_one_inside_it(page, service, monkeypatch, tmp_path):
    engine, posts, bought = await review_page(page, service, monkeypatch, tmp_path)
    final = await engine.form.application_submit_controls()
    assert [c["label"] for c in final] == ["Continue"]  # The form's own submit list reads the mark.
    assert await engine.browser_challenge() is None and posts == [] and not bought.called
    with pytest.raises(ValueError, match="submit action"):  # A plain click (the model's) is refused until the commit.
        await engine.form.click(final[0]["id"])
    assert posts == []

    engine.armed = True
    assert await engine.commit_by_token(final[0]) is True
    await page.wait_for_url("**/applythankyou**")
    assert await engine.browser_challenge() is None and bought.await_count == 1  # A second token would be a second application.
    assert posts == ["/applySubmit"]  # One post, and no press of the button after it.


async def test_a_run_that_ends_on_the_thank_you_page_is_settled_as_sent(page, service, monkeypatch, tmp_path):
    engine, posts, _ = await review_page(page, service, monkeypatch, tmp_path)
    engine.armed = True
    assert await engine.commit_by_token((await engine.form.application_submit_controls())[0]) is True
    await page.wait_for_url("**/applythankyou**")
    result = {"state": "waiting_browser", "reason": "Browser control failed", "recovery_cause": "control_timeout"}
    engine.result = result
    engine.settle_on_receipt_page()
    assert result["state"] == "submission_unknown" and result["reason"] == RECEIVED_REASON and "recovery_cause" not in result
    assert service.db.get_setting("unknown_triage:phenom-job")["verdict"] == "submitted"
    assert SimpleNamespace(**result).state == "submission_unknown"


async def test_a_token_delivered_under_a_frame_that_stays_drawn_is_a_possible_send(page, service, monkeypatch, tmp_path):
    """Real solver, real page: the provider's frame stays drawn after the callback posted, and the solver reports a check. That report is
    no proof of nothing sent, so the commit stays armed and nothing follows it."""
    engine, posts, bought = await review_page(page, service, monkeypatch, tmp_path, frame_stays=True)
    engine.armed = True
    final = (await engine.form.application_submit_controls())[0]
    assert await engine.commit_by_token(final) is True
    assert engine.armed is True and engine.commit_token_sent is True and bought.await_count == 1
    assert await engine.browser_challenge() is None and bought.await_count == 1
    assert posts == ["/applySubmit"]
