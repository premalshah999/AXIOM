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
