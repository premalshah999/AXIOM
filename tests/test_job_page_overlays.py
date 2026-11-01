"""A job page's Apply control reached nothing when the page's own furniture sat on top of it (2026-10-31, measured live on
careers sites): a floating chat assistant (role=dialog, "I'm interested"), a cookie bar announced as a dialog, and a
"Get notified for similar jobs" box with a required email field. None of them is the application."""

from unittest.mock import MagicMock

from jobpilot.adapters.base import Adapter, Step
from jobpilot.forms import SCAN, FormSession

APPLY_LINK = '<main><h1>Software Engineer</h1><a class=btn role=button href="/apply?job=1">Apply Now</a></main>'

CHAT = """<div id=example-chatbot-wrapper style="position:fixed;right:10px;bottom:10px;width:360px;height:400px">
<div role=dialog aria-label="Example Careers"><input aria-label="Chatbot user input box"><button>I'm interested</button>
<button>Similar Jobs</button></div></div>"""

COOKIE_BAR = """<div role=dialog aria-label=Privacy style="position:fixed;bottom:0;left:0;width:100%;height:60px">
We use cookies to personalize your experience. <a href="/privacy">Cookie Privacy Statement</a>
<a href="/prefs">To manage your preferences, click here</a><button>Close</button></div>"""

ALERT_BOX = """<section class=widget><h3>Get notified for similar jobs</h3><form><div><label for=n>Enter Email address (Required)</label>
<input id=n type=email required placeholder="you@example.test"><button type=button>Activate</button></div></form></section>"""

APPLICATION_EMAIL_STEP = """<form><label for=e>Email address *</label><input id=e type=email required>
<button type=button>Continue</button></form>"""


def labels(data, kind):
    return [x["label"] for x in data[kind]]


async def test_a_floating_chat_assistant_is_not_the_page(page):
    await page.set_content(APPLY_LINK + CHAT)
    data = await page.main_frame.evaluate(SCAN)
    assert "Apply Now" in labels(data, "buttons")
    assert "I'm interested" not in labels(data, "buttons")
    assert not data["fields"] and not data["dialogs"]


async def test_a_chat_widget_still_hides_nothing_when_named_in_a_class(page):
    await page.set_content(APPLY_LINK + CHAT.replace("id=example-chatbot-wrapper", "class=widget-chatbot-launcher"))
    data = await page.main_frame.evaluate(SCAN)
    assert "I'm interested" not in labels(data, "buttons") and "Apply Now" in labels(data, "buttons")


async def test_a_page_wrapper_named_for_chat_is_still_the_page(page):
    """Only a floating (position: fixed) chat element is set aside; a site that classes its body "chatbot-enabled" keeps its page."""
    await page.set_content(f"<body class=chatbot-enabled>{APPLY_LINK}</body>")
    assert "Apply Now" in labels(await page.main_frame.evaluate(SCAN), "buttons")


async def test_a_cookie_bar_dialog_does_not_hide_the_page(page):
    await page.set_content(APPLY_LINK + COOKIE_BAR)
    session = FormSession(page, MagicMock(employer=""), None, lambda *a, **k: None)
    obs = await session.scan()
    assert "Apply Now" in [c["label"] for c in obs["controls"]]


async def test_a_job_alert_signup_box_is_not_an_application_field(page):
    await page.set_content(APPLY_LINK + ALERT_BOX)
    session = FormSession(page, MagicMock(employer=""), None, lambda *a, **k: None)
    obs = await session.scan()
    assert obs["fields"] == []
    assert "Apply Now" in [c["label"] for c in obs["controls"]]


async def test_an_email_step_with_a_continue_button_stays_a_field(page):
    await page.set_content(APPLICATION_EMAIL_STEP)
    session = FormSession(page, MagicMock(employer=""), None, lambda *a, **k: None)
    obs = await session.scan()
    assert [f["label"] for f in obs["fields"]] == ["Email address *"] or len(obs["fields"]) == 1


def test_enter_job_title_or_location_is_a_site_filter():
    from jobpilot.adapters.base import site_filter

    assert site_filter({"label": "Enter Job Title or Location", "type": "combobox"})
    assert not site_filter({"label": "Job Title Applied For", "type": "text", "required": True})
