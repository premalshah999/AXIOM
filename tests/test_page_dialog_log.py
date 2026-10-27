"""A page's alert()/confirm() is recorded before it is dismissed: iCIMS's required-field check is an alert, and
iPipeline's "Submit Profile" (2026-10-19) silently did not submit while Playwright auto-dismissed it."""

from jobpilot.browser import BrowserEngine


async def test_a_dialog_is_recorded_then_dismissed(page):
    events = []
    engine = BrowserEngine.__new__(BrowserEngine)
    engine.emit = lambda kind, message, data=None: events.append((kind, data))
    page.on("dialog", engine.page_dialog)
    await page.set_content("<p>form</p>")
    await page.evaluate("() => { alert('Please upload your resume is required.'); return 1; }")
    assert events == [("browser_dialog", {"type": "alert", "message": "Please upload your resume is required."})]
    assert engine.last_dialog["message"].endswith("is required.")


async def test_leave_site_is_accepted_so_our_navigation_proceeds(page):
    """Restarting from a kept iCIMS tab (dirty form, beforeunload) failed net::ERR_ABORTED while the prompt was
    dismissed (Schwab, Iridium 2026-10-21)."""
    engine = BrowserEngine.__new__(BrowserEngine)
    engine.emit = lambda *args: None
    page.on("dialog", engine.page_dialog)
    await page.set_content("<input id=x><script>addEventListener('beforeunload', e => { e.preventDefault(); e.returnValue = ''; })</script>")
    await page.click("#x")  # Chrome shows beforeunload only after a user gesture.
    await page.keyboard.type("dirty")
    await page.goto("data:text/html,<p>next</p>")
    assert engine.last_dialog["type"] == "beforeunload"
    assert await page.inner_text("p") == "next"


async def test_an_expected_confirmation_is_accepted_once(page):
    """iCIMS's "Delete Phones (2)" asks for confirmation; the adapter arms exactly that click (Emory 2026-10-27)."""
    engine = BrowserEngine.__new__(BrowserEngine)
    engine.emit = lambda *args: None
    page.on("dialog", engine.page_dialog)
    await page.set_content("<p>form</p>")
    engine.accept_confirms = 1
    assert await page.evaluate("() => confirm('Delete this phone?')") is True
    assert engine.accept_confirms == 0
    assert await page.evaluate("() => confirm('Withdraw your application?')") is False
