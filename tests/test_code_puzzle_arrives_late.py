"""ByteDance (2026-11-01, 19 runs on 15 jobs, none ever confirmed): "Get code" draws its picture puzzle a few seconds after the
click, not at it. The code step looked for the puzzle for 3 s, found none, and parked for the 20-minute code wait with the
puzzle open on the page; the code was never sent, and the parked wait held the email sender that seven other applications
then timed out on. The page here is a local copy of that step: an email box, a code box, a "Get code" button disabled until
an email is typed, and a puzzle that appears LATE seconds after the press. The "server" mails the code only when the puzzle
is solved (it sets data-sent). The solver (a paid service) is replaced by one that answers the two points. Synthetic data."""

import asyncio
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from jobpilot import captcha_visual
from jobpilot.adapters.base import Adapter
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

LATE = 4000  # ms between the press and the puzzle

STEP = """<html><body><main><h1>Create account</h1>
<input id=email type=email placeholder="Email" oninput="document.getElementById('get').disabled=!this.value">
<input id=code placeholder="Enter Email Verification Code">
<button id=get type=button disabled>Get code</button><button id=go type=button>Continue</button></main>
<div id=host></div>
<script>
 document.getElementById('get').addEventListener('click', () => setTimeout(() => {
  document.getElementById('host').innerHTML = `<div id=mask style="position:fixed;inset:0;background:rgba(0,0,0,.4)">
   <div style="position:absolute;left:100px;top:60px;width:380px;height:380px;background:#fff;padding:16px">
    <div>Select 2 objects that are the same shape:</div><canvas id=scene width=340 height=212></canvas>
    <div><span>Refresh</span><button id=confirm type=button>Confirm</button></div></div></div>`;
  let clicks = 0;
  document.getElementById('scene').addEventListener('click', () => clicks++);
  document.getElementById('confirm').addEventListener('click', () => {
   if (clicks === 2) { document.getElementById('mask').remove(); document.body.dataset.sent = '1'; } else clicks = 0; });
 }, %d));
</script></body></html>""" % LATE

CONFIG = SimpleNamespace(twocaptcha_api_key="synthetic", captcha_timeout=30, captcha_max_attempts=2, captcha_image_rounds=3)


class Probe(Adapter):
    mail_rule_known = True


async def run_request_code(page, tmp_path, monkeypatch):
    async def task_result(base, key, task, timeout, emit):
        return {"coordinates": [{"x": 60, "y": 50}, {"x": 250, "y": 150}]}, None

    monkeypatch.setattr(captcha_visual, "task_result", task_result)
    await page.set_content(STEP)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a: None)
    obs = await form.scan()
    value = Probe.__new__(Probe)
    value.e = SimpleNamespace(form=form, emit=lambda *a, **k: None)
    value.page, value.config = page, CONFIG
    value.inbox = SimpleNamespace(configured=True, live=lambda _c: True)
    value.accounts = SimpleNamespace(email="alex@example.test", password_allowed=lambda *a: True)
    value.attempts = Counter()
    value.arm_mail = AsyncMock(return_value=True)
    value.after_auth_click = AsyncMock(return_value=None)
    value.emit = MagicMock()
    value.click = lambda b, **k: form.click(b["id"])
    return await Adapter.request_code(value, obs), value


async def test_a_puzzle_drawn_seconds_after_get_code_is_solved_and_the_code_sent(page, tmp_path, monkeypatch):
    result, value = await run_request_code(page, tmp_path, monkeypatch)
    assert result is True
    assert value.attempts["inpage_puzzle"] == 1
    assert await page.evaluate("document.body.dataset.sent") == "1"  # the site mailed its code only once the puzzle was solved
    assert not await page.locator("#mask").count()


async def test_a_step_that_never_draws_a_puzzle_still_goes_on_to_wait(page, tmp_path, monkeypatch):
    from jobpilot.adapters import base

    monkeypatch.setattr(base, "PUZZLE_WATCH_SECONDS", 1)
    await page.set_content(STEP.replace("setTimeout(", "setTimeout(() => {}, 0) && (() => 0)("))  # no puzzle ever
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a: None)
    obs = await form.scan()
    value = Probe.__new__(Probe)
    value.e = SimpleNamespace(form=form, emit=lambda *a, **k: None)
    value.page, value.config = page, CONFIG
    value.inbox = SimpleNamespace(configured=True, live=lambda _c: True)
    value.accounts = SimpleNamespace(email="alex@example.test", password_allowed=lambda *a: True)
    value.attempts = Counter()
    value.arm_mail = AsyncMock(return_value=True)
    value.after_auth_click = AsyncMock(return_value=None)
    value.emit = MagicMock()
    value.click = lambda b, **k: form.click(b["id"])
    assert await Adapter.request_code(value, obs) is True
    assert value.attempts["inpage_puzzle"] == 0
