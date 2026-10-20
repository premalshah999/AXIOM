"""Clicks keep real pointer movement but no sleeps (2026-10-18). The owner removed the human-like pauses for speed;
SmartRecruiters (DataDome) and Greenhouse/Ashby (reCAPTCHA Enterprise v3) score pointer telemetry, so a native click
still moves the pointer to its target first. That costs a few CDP calls, not the old 40-160 ms sleep."""

import asyncio

from jobpilot import widgets


class SleepLog:
    """widgets' view of asyncio with sleep() recorded, so "no sleeps" is asserted directly. A wall-clock bound on the
    click measured the host's load instead (1.2 s against a 1.0 s limit on a busy machine, 2026-10-20)."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, delay, result=None):
        self.calls.append(delay)
        return await asyncio.sleep(0, result)


async def test_a_native_click_moves_the_pointer_without_sleeping(page, monkeypatch):
    await page.set_content("""<button id=b style="margin:200px;width:120px;height:40px"
      onclick="window.clicked=(window.clicked||0)+1">Next</button>
      <script>window.moves=0;document.addEventListener('mousemove',()=>window.moves++);</script>""")
    handle = await page.locator("#b").element_handle()
    sleeps = SleepLog()
    monkeypatch.setattr(widgets, "asyncio", sleeps)
    await widgets.safe_click(handle)
    monkeypatch.undo()
    assert sleeps.calls == []
    assert await page.evaluate("window.clicked") == 1
    assert await page.evaluate("window.moves") >= 2
