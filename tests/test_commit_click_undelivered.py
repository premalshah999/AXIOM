"""A commit click that provably reached nothing is not an uncertain submission, and any doubt keeps it uncertain.

Two Sigma (Avature, 2026-10-30): signing in landed on a blank /careers/ApplicationFormWidget with one hidden Submit. The
engine committed on it, Playwright's click timed out, and the run ended submission_unknown (no automatic retry) although
the employer's site showed the application as Incomplete. A timeout can also follow a click that did go out, so the page
is the witness. A false "not sent" could submit a real application twice: most of these tests are the other direction.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import jobpilot.browser as browser
import jobpilot.widgets as widgets
from jobpilot.adapters.base import Adapter
from jobpilot.answers import Resolver
from jobpilot.browser import BrowserEngine
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

APPLY = "https://careers.example.test/apply"
HITS = """<script>window.hits=[];for(const t of ['pointerdown','mousedown','mouseup','click','submit'])
  document.addEventListener(t,()=>window.hits.push(t),true);</script>"""
VEIL = '<div id="veil" class="loading-mask" style="position:fixed;inset:0;background:#fff;z-index:9"></div>'
SUBMIT = '<form action="/post" method="post"><button id="go" type="submit">Submit application</button></form>'


@pytest.fixture(autouse=True)
def no_grace(monkeypatch):
    monkeypatch.setattr(browser, "COMMIT_INPUT_GRACE_SECONDS", 0.05)


async def serve(page, body, url=APPLY):
    async def route(route):
        if not route.request.url.endswith("/post"):  # An application post is left unanswered.
            await route.fulfill(status=200, content_type="text/html", body=body)

    await page.context.route("**/*", route)
    await page.goto(url)


async def session(page, service, tmp_path, job=None):
    resolver = Resolver(Profile(), service.db, service.config)
    resolver.job = job or {"id": "j", "ats": "custom", "title": "Staff Example Engineer"}
    form = FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a, **k: None)
    await form.scan()
    return form


def engine_on(page, form):
    engine = object.__new__(BrowserEngine)
    engine.page, engine.form, engine.armed = page, form, True
    engine.commit_requests, engine.commit_tracking, engine.commit_inflight = [], 0, set()
    engine.commit_url = page.url
    engine.run_id = "r"
    engine.job = {"id": "j", "demo": False, "ats": "custom", "url": APPLY}
    engine.events = []
    engine.emit = lambda kind, message, data=None: engine.events.append((kind, data))
    engine.service = SimpleNamespace(release_submission=lambda run_id: engine.events.append(("released", run_id)))
    return engine


def only_control(form):
    return next(b for b in form.buttons.values() if "submit" in b["label"].lower())


async def timed_out_click(form, control, timeout=400):
    with pytest.raises(PlaywrightTimeoutError):
        await form.locator(control["id"]).click(timeout=timeout)


# ----- the click that never went out ----------------------------------------------------------------------------------


async def test_a_click_blocked_by_a_cover_delivered_nothing_and_says_so(page, service, tmp_path):
    await serve(page, HITS + SUBMIT + VEIL)
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    watch = await engine.watch_commit_click(control["id"])
    await timed_out_click(form, control)
    assert await engine.commit_click_undelivered(watch) is True
    assert await page.evaluate("window.hits") == []

    await page.evaluate(browser.ARM_JS)
    result = await engine.commit_not_delivered(control, PlaywrightTimeoutError("Locator.click: Timeout"))
    assert result["state"] == "waiting_browser" and result["recoverable"] is True
    assert "Submit application" in result["reason"] and "covered by div#veil" in result["reason"]
    assert engine.armed is False and ("released", "r") in engine.events
    assert await page.evaluate("window.__jpArmed") is False, "a later plain click must not pass the page's guard"


async def test_the_incident_shape_a_lone_hidden_submit_never_took_the_click(page, service, tmp_path):
    """The scan counts a zero-size button as visible, because it has a client rect. Playwright does not."""
    await serve(page, HITS + '<form action="/post" method="post"><button id="go" type="submit" '
                      'style="width:0;height:0;padding:0;border:0;overflow:hidden">Submit</button></form>')
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    assert control["label"] == "Submit"
    watch = await engine.watch_commit_click(control["id"])
    await timed_out_click(form, control)
    assert await engine.commit_click_undelivered(watch) is True
    assert "no size" in await form.control_block_reason(control["id"])


async def test_a_control_in_an_iframe_is_witnessed_in_its_own_document(page, service, tmp_path):
    inner = (HITS + SUBMIT + VEIL).replace('"', "&quot;")
    await serve(page, f'<iframe srcdoc="{inner}" style="width:600px;height:300px"></iframe>')
    await page.wait_for_timeout(100)
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    watch = await engine.watch_commit_click(control["id"])
    assert len(watch["frames"]) == 2
    await timed_out_click(form, control)
    assert await engine.commit_click_undelivered(watch) is True


async def test_tracking_calls_alone_do_not_make_a_blocked_click_uncertain(page, service, tmp_path):
    await serve(page, SUBMIT + VEIL)
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    watch = await engine.watch_commit_click(control["id"])
    await timed_out_click(form, control)
    engine.commit_requests = [{"method": "POST", "host": "careers.example.test", "path": "/analytics/track", "status": 200}]
    assert await engine.commit_click_undelivered(watch) is True


# ----- the click that did go out: never "not sent" ----------------------------------------------------------------------


async def test_a_click_delivered_before_a_slow_handler_timed_out_stays_uncertain(page, service, tmp_path):
    """The dangerous direction: Playwright reports the same timeout, and the click is real."""
    await serve(page, HITS + """<button id="go" type="button" onclick="const t=Date.now();while(Date.now()-t<1500);">
        Submit application</button>""")
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    watch = await engine.watch_commit_click(control["id"])
    await timed_out_click(form, control, timeout=300)
    assert await engine.commit_click_undelivered(watch) is False
    assert "click" in await page.evaluate("window.hits")


async def test_a_click_whose_form_post_has_not_answered_stays_uncertain(page, service, tmp_path):
    """Playwright waits for the navigation the click started, and times out while the application is on its way."""
    await serve(page, HITS + SUBMIT)
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    page.on("request", engine.commit_request)
    watch = await engine.watch_commit_click(control["id"])
    await timed_out_click(form, control, timeout=500)
    assert await engine.commit_click_undelivered(watch) is False
    # The witness alone is enough: a page that sent nothing we could log still shows the click.
    engine.commit_inflight = set()
    assert await engine.commit_click_undelivered(watch) is False


async def test_a_click_that_landed_in_a_control_inside_an_iframe_stays_uncertain(page, service, tmp_path):
    inner = (HITS + '<button id="go" type="button" onclick="const t=Date.now();while(Date.now()-t<1500);">'
             "Submit application</button>").replace('"', "&quot;")
    await serve(page, f'<iframe srcdoc="{inner}" style="width:600px;height:300px"></iframe>')
    await page.wait_for_timeout(100)
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    watch = await engine.watch_commit_click(control["id"])
    await timed_out_click(form, control, timeout=300)
    assert await engine.commit_click_undelivered(watch) is False


async def test_a_real_click_is_witnessed_even_when_it_succeeds(page, service, tmp_path):
    await serve(page, '<button id="go" type="button">Submit application</button>')
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    watch = await engine.watch_commit_click(control["id"])
    await form.locator(control["id"]).click(timeout=2000)
    assert await engine.commit_click_undelivered(watch) is False


@pytest.mark.parametrize(
    "change",
    [
        "document.querySelector('#go').disabled=true",  # A site disables its submit while it posts.
        "document.querySelector('#go').textContent='Submitting...'",
        "document.querySelector('#go').className='is-loading'",
        "document.querySelector('#go').setAttribute('aria-busy','true')",
        "const o=document.querySelector('#go'),n=o.cloneNode(true);o.replaceWith(n)",  # Same tag and text, another node.
        "document.querySelector('#go').remove()",
        "history.pushState({},'','/apply/done')",
        "window.dispatchEvent(new Event('beforeunload'))",
        "document.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true}))",
        "delete window.__jpCommitWatch",
    ],
)
async def test_any_change_on_the_page_since_the_attempt_stays_uncertain(page, service, tmp_path, change):
    await serve(page, SUBMIT + VEIL)
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    watch = await engine.watch_commit_click(control["id"])
    await timed_out_click(form, control)
    await page.evaluate(change)
    assert await engine.commit_click_undelivered(watch) is False


async def test_a_replaced_document_stays_uncertain(page, service, tmp_path):
    await serve(page, SUBMIT + VEIL)
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    watch = await engine.watch_commit_click(control["id"])
    await timed_out_click(form, control)
    await page.reload()
    await form.scan()
    assert await engine.commit_click_undelivered(watch) is False


@pytest.mark.parametrize(
    "writes",
    [
        {"commit_requests": [{"method": "POST", "host": "api.ats.example", "path": "/v1/applications", "status": 200}]},
        {"commit_requests": [{"method": "POST", "host": "api.ats.example", "path": "/v1/applications", "status": 400}]},
        {"commit_requests": [{"method": "POST", "host": "api.ats.example", "path": "/v1/applications", "status": 0}]},
        {"commit_requests": [{"method": "POST", "host": "a.example", "path": "/track", "status": 200}] * 40},
        {"commit_inflight": {1}},
    ],
)
async def test_any_write_since_the_attempt_stays_uncertain(page, service, tmp_path, writes):
    await serve(page, SUBMIT + VEIL)
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    watch = await engine.watch_commit_click(control["id"])
    await timed_out_click(form, control)
    for name, value in writes.items():
        setattr(engine, name, value)
    assert await engine.commit_click_undelivered(watch) is False


async def test_a_new_receipt_or_refusal_stays_uncertain_and_an_old_one_does_not(page, service, tmp_path):
    await serve(page, SUBMIT + VEIL)
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    old = {"type": "explicit_rejection_page", "category": "validation"}
    watch = await engine.watch_commit_click(control["id"], rejection=old)
    await timed_out_click(form, control)
    form.rejection = AsyncMock(return_value=old)
    assert await engine.commit_click_undelivered(watch) is True
    await page.evaluate(browser.COMMIT_WATCH, watch["token"])
    form.rejection = AsyncMock(return_value={"type": "explicit_rejection_page", "category": "temporary"})
    assert await engine.commit_click_undelivered(watch) is False
    form.rejection = AsyncMock(return_value=None)
    form.proof = AsyncMock(return_value={"type": "explicit_confirmation_page"})
    assert await engine.commit_click_undelivered(watch) is False


async def test_a_page_too_busy_to_answer_stays_uncertain(page, service, tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "COMMIT_WATCH_SECONDS", 0.2)
    await serve(page, SUBMIT + VEIL)
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    watch = await engine.watch_commit_click(control["id"])
    await timed_out_click(form, control)
    busy = asyncio.ensure_future(page.evaluate("(()=>{const t=Date.now();while(Date.now()-t<800);})()"))
    await asyncio.sleep(0.05)
    assert await engine.commit_click_undelivered(watch) is False
    await busy


async def test_no_witness_means_no_proof():
    engine = object.__new__(BrowserEngine)
    assert await engine.commit_click_undelivered(None) is False
    engine.page = SimpleNamespace(frames=[])  # no main_frame: the witness cannot be set
    engine.form = SimpleNamespace()
    assert await engine.watch_commit_click("submit") is None


async def test_a_frame_that_refuses_the_witness_means_no_proof(page, service, tmp_path):
    await serve(page, SUBMIT + VEIL)
    form = await session(page, service, tmp_path)
    engine, control = engine_on(page, form), only_control(form)
    form.frames[control["id"]] = SimpleNamespace(evaluate=AsyncMock(side_effect=PlaywrightError("Frame was detached")))
    assert await engine.watch_commit_click(control["id"]) is None


# ----- before the commit: the actionability check ----------------------------------------------------------------------


async def test_the_actionability_check_presses_nothing(page, service, tmp_path):
    """It runs on the live Submit, so this is the test that it never submits. The page's own listeners on window, which
    come before anything Playwright could use to stop an input, see no press at all."""
    await serve(page, """<script>window.seen=[];for(const t of ['pointerdown','mousedown','pointerup','mouseup','click',
          'dblclick','auxclick','keydown','submit'])window.addEventListener(t,()=>window.seen.push(t),true);</script>
        <form action="/post" method="post" onsubmit="window.sent=true">
        <button id="go" type="submit" onclick="window.clicked=true">Submit application</button></form>""")
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    form = await session(page, service, tmp_path)
    assert await form.final_control_blocked(only_control(form)["id"], timeout=2000) == ""
    await page.wait_for_timeout(200)
    assert await page.evaluate("[window.clicked, window.sent]") == [None, None]
    assert await page.evaluate("window.seen") == [] and posts == [] and page.url == APPLY


async def test_a_control_that_turned_disabled_is_named_as_disabled(page, service, tmp_path):
    await serve(page, SUBMIT)
    form = await session(page, service, tmp_path)
    await page.evaluate("document.querySelector('#go').disabled=true")
    assert await form.final_control_blocked(only_control(form)["id"], timeout=300) == "it is disabled"


async def test_a_control_that_appears_in_time_is_not_blocked(page, service, tmp_path):
    await serve(page, SUBMIT + VEIL + "<script>setTimeout(()=>document.querySelector('#veil').remove(),300)</script>")
    form = await session(page, service, tmp_path)
    assert await form.final_control_blocked(only_control(form)["id"], timeout=3000) == ""


@pytest.mark.parametrize(
    "body,why",
    [
        (SUBMIT + VEIL, "covered by div#veil"),
        ('<form><button id="go" style="width:0;height:0;padding:0;border:0;overflow:hidden">Submit</button></form>', "no size"),
        ('<form><button id="go" style="pointer-events:none">Submit</button></form>', "ignores the pointer"),
        ('<form><button id="go" style="position:fixed;left:-500px;top:-500px">Submit</button></form>', "outside"),
    ],
)
async def test_a_control_no_one_can_click_is_named_before_anything_is_reserved(page, service, tmp_path, body, why):
    await serve(page, body)
    form = await session(page, service, tmp_path)
    assert why in await form.final_control_blocked(only_control(form)["id"], timeout=300)


class Review:
    """A verified page at its one final submit: only the real click's outcome varies."""

    def __init__(self, click=None, label="Submit application"):
        self.label, self.on_click = label, click
        self.clicks = 0
        self.frames = {"submit-1": None}

    async def verify(self):
        return {"ok": True, "resume_attached": True, "verified_fields": 5, "problems": [], "pending": []}

    async def scan(self):
        return {"fields": [], "text": "", "dialogs": []}

    async def application_submit_controls(self):
        return [{"id": "submit-1", "label": self.label, "disabled": False, "section": ""}]

    async def settle(self, **kwargs):
        return None

    async def proof(self):
        return None

    async def rejection(self):
        return None

    def locator(self, control_id):
        form = self

        class Control:
            async def click(self, **kwargs):
                form.clicks += 1
                if form.on_click:
                    raise form.on_click

            async def scroll_into_view_if_needed(self, **kwargs):
                return None

        return Control()


async def noop(*args, **kwargs):
    return None


def engine_for(monkeypatch, form):
    monkeypatch.setattr(widgets, "ensure_uncovered", noop)
    engine = object.__new__(BrowserEngine)
    engine.form, engine.armed, engine.result = form, False, None
    engine.run_id = "r"
    engine.job = {"id": "j", "demo": False, "ats": "custom", "url": APPLY}
    engine.run_record = {"mode": "submit", "packet": {"resume_sha": "sha"}}
    engine.page = SimpleNamespace(frames=[], url=APPLY)
    engine.events = []
    engine.emit = lambda kind, message, data=None: engine.events.append(kind)
    engine.email_verification = SimpleNamespace(prepare_submission=noop, state=lambda observation: (False, None))
    engine.quality_gate = noop
    engine.browser_challenge = noop
    engine.reservations = []
    engine.service = SimpleNamespace(
        reserve_submission=engine.reservations.append,
        release_submission=lambda run_id: engine.events.append("released"),
    )
    return engine


async def test_finish_ends_a_proven_undelivered_click_as_not_sent(monkeypatch):
    form = Review(click=PlaywrightTimeoutError("Locator.click: Timeout 10000ms exceeded."))
    engine = engine_for(monkeypatch, form)
    engine.watch_commit_click = AsyncMock(return_value={"token": "t"})
    engine.commit_click_undelivered = AsyncMock(return_value=True)
    result = await engine.finish()
    assert result["state"] == "waiting_browser" and "Submit application" in result["reason"]
    assert result["recoverable"] is True and engine.armed is False and form.clicks == 1
    assert engine.reservations == ["r"] and "released" in engine.events and "commit_not_delivered" in engine.events
    assert engine.recoverable_result(result) is True


@pytest.mark.parametrize("error", [PlaywrightTimeoutError("Locator.click: Timeout 10000ms exceeded."),
                                   PlaywrightError("Target page, context or browser has been closed")])
async def test_finish_keeps_every_unproven_click_failure_uncertain(monkeypatch, error):
    """It raises as before; the navigator's own handler records submission_unknown because the run stays armed."""
    form = Review(click=error)
    engine = engine_for(monkeypatch, form)
    engine.watch_commit_click = AsyncMock(return_value={"token": "t"})
    engine.commit_click_undelivered = AsyncMock(return_value=False)
    with pytest.raises(PlaywrightError):
        await engine.finish()
    assert engine.armed is True and "released" not in engine.events and "commit_not_delivered" not in engine.events
    assert form.clicks == 1, "a failed commit click is never repeated"


async def test_finish_without_a_witness_stays_uncertain(monkeypatch):
    """engine_for's page has no main frame, so the real witness cannot be set."""
    form = Review(click=PlaywrightTimeoutError("Locator.click: Timeout 10000ms exceeded."))
    engine = engine_for(monkeypatch, form)
    with pytest.raises(PlaywrightError):
        await engine.finish()
    assert engine.armed is True and "released" not in engine.events


async def test_a_frame_lost_while_arming_is_not_an_attempted_submission(monkeypatch):
    form = Review()
    engine = engine_for(monkeypatch, form)
    engine.page.frames = [SimpleNamespace(evaluate=AsyncMock(side_effect=PlaywrightError("Frame was detached")))]
    result = await engine.finish()
    assert result["state"] == "waiting_browser" and engine.armed is False
    assert form.clicks == 0 and "released" in engine.events
    assert "submitting" not in engine.events, "nothing started: the durable commit mark must not be left behind"


async def test_finish_refuses_before_the_reservation_when_the_control_cannot_be_clicked(monkeypatch):
    form = Review()
    form.final_control_blocked = AsyncMock(return_value="it has no size on the page")
    engine = engine_for(monkeypatch, form)
    result = await engine.finish()
    assert result["state"] == "waiting_browser" and "no size" in result["reason"] and "Submit application" in result["reason"]
    assert result["recoverable"] is True and engine.armed is False
    assert engine.reservations == [] and form.clicks == 0 and "submitting" not in engine.events


async def test_a_validation_retry_after_a_real_commit_is_not_held_by_the_pre_checks(monkeypatch):
    """Armed means a commit already happened on this form. Its bounded resubmit keeps going as before."""
    form = Review()
    form.final_control_blocked = AsyncMock(return_value="it is hidden")
    form.final_step_evidence = lambda obs, control: ""
    engine = engine_for(monkeypatch, form)
    engine.armed = True
    engine.watch_submission = AsyncMock(return_value={"state": "submission_unknown", "reason": "no receipt"})
    result = await engine.finish(reserved=True, validation_attempt=1)
    assert result["state"] == "submission_unknown" and form.clicks == 1
    form.final_control_blocked.assert_not_awaited()


# ----- the adapters' commit --------------------------------------------------------------------------------------------


def adapter_for(form, undelivered):
    adapter = object.__new__(Adapter)
    engine = SimpleNamespace(
        armed=False,
        form=form,
        emit=lambda *a, **k: None,
        job={"id": "j", "demo": False, "ats": "custom"},
        run_id="r",
        quality_gate=noop,
        try_reserve_commit=noop,
        emit_commit_requests=lambda: None,
        watch_commit_click=AsyncMock(return_value={"token": "t"}),
        commit_click_undelivered=AsyncMock(return_value=undelivered),
        commit_not_delivered=AsyncMock(return_value={"state": "waiting_browser", "recoverable": True, "reason": "x"}),
        not_the_final_step=noop,
    )
    adapter.e = engine
    adapter.committed = False
    adapter.commit_pages = set()
    adapter.solve_captcha = noop
    adapter.signature = lambda step, obs: "review"
    adapter.observed_final = AsyncMock(return_value={"id": "submit-1", "label": "Submit application"})
    return adapter


class AdapterForm(Review):
    def __init__(self, error):
        super().__init__()
        self.error = error
        self.page = SimpleNamespace(url=APPLY, frames=[])

    async def click(self, control_id, **kwargs):
        self.clicks += 1
        raise self.error


@pytest.mark.parametrize("error", [PlaywrightTimeoutError("Locator.click: Timeout 6000ms exceeded."),
                                   ValueError("Control is covered by the active dialog or inert")])
async def test_an_adapter_commit_whose_click_never_landed_is_not_sent(monkeypatch, error):
    monkeypatch.setattr(widgets, "ensure_uncovered", noop)
    form = AdapterForm(error)
    adapter = adapter_for(form, undelivered=True)
    result = await adapter.commit({"id": "submit-1", "label": "Submit application"})
    assert result["state"] == "waiting_browser" and adapter.committed is False and form.clicks == 1
    adapter.e.commit_not_delivered.assert_awaited_once()


async def test_an_adapter_commit_with_any_doubt_stays_attempted(monkeypatch):
    monkeypatch.setattr(widgets, "ensure_uncovered", noop)
    form = AdapterForm(PlaywrightTimeoutError("Locator.click: Timeout 6000ms exceeded."))
    adapter = adapter_for(form, undelivered=False)
    with pytest.raises(PlaywrightError):
        await adapter.commit({"id": "submit-1", "label": "Submit application"})
    assert adapter.e.armed is True and adapter.committed is True and form.clicks == 1
    adapter.e.commit_not_delivered.assert_not_awaited()


async def test_an_adapter_refuses_a_page_that_is_not_the_final_step(monkeypatch):
    monkeypatch.setattr(widgets, "ensure_uncovered", noop)
    form = AdapterForm(AssertionError("must not click"))
    adapter = adapter_for(form, undelivered=False)
    adapter.e.not_the_final_step = AsyncMock(return_value={"state": "waiting_browser", "recoverable": True, "reason": "x"})
    assert (await adapter.commit({"id": "submit-1", "label": "Submit"}))["state"] == "waiting_browser"
    assert form.clicks == 0 and adapter.e.armed is False and adapter.committed is False
