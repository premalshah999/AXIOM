"""Clicks keep real pointer movement but no sleeps (2026-10-18). The owner removed the human-like pauses for speed;
SmartRecruiters (DataDome) and Greenhouse/Ashby (reCAPTCHA Enterprise v3) score pointer telemetry, so a native click
still moves the pointer to its target first. That costs a few CDP calls, not the old 40-160 ms sleep."""

import time

from jobpilot import widgets


async def test_a_native_click_moves_the_pointer_without_sleeping(page):
    await page.set_content("""<button id=b style="margin:200px;width:120px;height:40px"
      onclick="window.clicked=(window.clicked||0)+1">Next</button>
      <script>window.moves=0;document.addEventListener('mousemove',()=>window.moves++);</script>""")
    handle = await page.locator("#b").element_handle()
    started = time.monotonic()
    await widgets.safe_click(handle)
    assert time.monotonic() - started < 1.0
    assert await page.evaluate("window.clicked") == 1
    assert await page.evaluate("window.moves") >= 2
