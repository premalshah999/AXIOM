"""Qualtrics (Greenhouse) 2026-10-18: its chatbot panel is a role=dialog in the corner, so every click was refused as
"Control is covered by the active dialog or inert" and three runs stalled on "Apply now". A modal still blocks."""

import pytest

from jobpilot import widgets

PAGE = """<main><h1>Data Scientist</h1><button id=apply style="margin:40px">Apply now</button></main>%s"""
CHAT = """<div role=dialog aria-label="Chatbot" class="qualtrics-chatbot" style="position:fixed;right:0;bottom:0;width:300px;height:200px">
  <button>Open Chatbot</button></div>"""
SIDE = """<div role=dialog aria-label="Saved jobs" style="position:fixed;right:0;top:0;width:200px;height:120px">Saved</div>"""
MODAL = """<div role=dialog aria-modal=true aria-label="Confirm" style="position:fixed;right:0;bottom:0;width:200px;height:100px">
  <button>OK</button></div>"""


@pytest.mark.parametrize("overlay,blocked", [(CHAT, False), (SIDE, False), (MODAL, True)])
async def test_only_a_modal_or_a_covering_dialog_blocks(page, overlay, blocked):
    await page.set_content(PAGE % overlay)
    if blocked:
        with pytest.raises(ValueError, match="covered"):
            await widgets.ensure_uncovered(page.locator("#apply"))
    else:
        await widgets.ensure_uncovered(page.locator("#apply"))
