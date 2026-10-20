"""Generic stall breaker (2026-10-16): an action that changed nothing is diagnosed before it repeats."""

from types import SimpleNamespace

from jobpilot.answers import Resolver
from jobpilot.browser import BrowserEngine
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """
<div role="alert">Your application wasn't submitted. Try again later.</div>
<label><input type="checkbox" id="ack" onchange="document.getElementById('go').disabled=!this.checked">
I agree to the Candidate Acknowledgement</label>
<button id="go" type="button" disabled>Next</button>
"""


async def engine_for(page, tmp_path, html):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(html)
    form = FormSession(page, Resolver(Profile(accept_all_application_terms=True), None, None), resume, lambda *a: None)
    await form.scan()
    events = []

    async def no_challenge(**kwargs):
        return None

    async def fill_current(**kwargs):
        await page.check("#ack")
        return {"ok": True}

    form.fill_current = fill_current
    e = SimpleNamespace(form=form, page=page, armed=False, result=None, captcha=SimpleNamespace(token_delivered_at={}),
                        browser_challenge=no_challenge, emit=lambda *a, **k: events.append(a))
    return e, events


async def test_disabled_next_is_completed_and_unread_messages_reach_the_navigator(page, tmp_path):
    e, events = await engine_for(page, tmp_path, PAGE)
    decision = SimpleNamespace(action="click")
    finding = await BrowserEngine.break_stall(e, decision, {"label": "Next"})
    assert "'Next' was disabled; completed the page and it is enabled now" in finding
    assert "Your application wasn't submitted" in finding
    assert events and events[0][0] == "stall_breaker"


async def test_nothing_to_diagnose_returns_no_finding(page, tmp_path):
    e, events = await engine_for(page, tmp_path, "<button>Next</button>")
    assert await BrowserEngine.break_stall(e, SimpleNamespace(action="fill"), {"label": "Email"}) == ""
    assert not events


async def test_a_captcha_solved_during_the_diagnosis_is_reported_and_a_blocking_one_stops_the_run(page, tmp_path):
    e, _ = await engine_for(page, tmp_path, "<button>Next</button>")

    async def solves(**kwargs):
        e.captcha.token_delivered_at["recaptcha"] = 5.0
        return None

    e.browser_challenge = solves
    assert "A pending CAPTCHA was solved" in await BrowserEngine.break_stall(e, SimpleNamespace(action="click"), {})

    async def blocks(**kwargs):
        return {"state": "waiting_browser", "reason": "Website security check was not accepted"}

    e.browser_challenge = blocks
    assert await BrowserEngine.break_stall(e, SimpleNamespace(action="click"), {}) == "Website security check was not accepted"
    assert e.result["state"] == "waiting_browser"


async def test_live_region_counts_and_static_hints_are_not_findings(page, tmp_path):
    e, events = await engine_for(page, tmp_path, """<div aria-live="polite">248 matches found.</div>
      <div role="status">Required fields are indicated with *</div><button>Next</button>""")
    assert await BrowserEngine.break_stall(e, SimpleNamespace(action="fill"), {"label": "Email"}) == ""
    assert not events
