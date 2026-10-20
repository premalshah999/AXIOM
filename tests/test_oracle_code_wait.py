"""Oracle's verification-code page cannot be reopened: a parked run's continuation lands on the email step, which
sends a new code. GM Financial (2026-10-18) took 65 s per code and parked twice. Oracle runs wait up to 150 s in
the browser; other platforms keep the 30 s inline wait before parking."""

from types import SimpleNamespace

import pytest

from jobpilot.adapters.oracle import Oracle
from jobpilot.adapters.workday import Workday


@pytest.mark.parametrize("adapter,inline", [(Oracle, 150), (Workday, 30)])
async def test_code_waits_inline_as_long_as_the_platform_needs(adapter, inline):
    waits, extended = [], []

    async def wait(challenge, *, max_wait_seconds):
        waits.append(max_wait_seconds)
        return {"kind": "code", "value": "123456"}

    step = object.__new__(adapter)
    step.challenge = {"id": "window"}
    step.inbox = SimpleNamespace(live=lambda challenge: True, wait=wait)
    step.service = SimpleNamespace(mail=SimpleNamespace(narrow=lambda *args: None))
    step.config = SimpleNamespace(mail_wait_seconds=90)
    step.e = SimpleNamespace(extend=extended.append, emit=lambda *args, **kwargs: None)
    step.emit = lambda *args, **kwargs: None
    assert (await step.wait_email("code"))["value"] == "123456"
    assert waits == [inline] and extended == [max(90, inline)]
    waits.clear()
    await step.wait_email("link")
    assert waits == [3]
