"""Columbia's posting (PageUp, 2026-11-01, job runs 33bc35d7/6ec991bf/fa4f48b3): "Apply" is the submit button of a form
holding only First name, Last name and Email, beside a job-alert widget with its own fields. It was refused as a final
submit ("This may submit the application") three runs in a row. The next page's "Login" button was refused the same way
because the guard's pattern read "log in" and not "login"."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

GATE = """<main>
<form id=alert onsubmit="event.preventDefault();document.body.dataset.alert=1">
  <label>Departments <select><option>Any</option></select></label><button type=submit>Submit</button></form>
<form id=gate onsubmit="event.preventDefault();document.body.dataset.gate=1">
  <label>First Name <input name=first_name></label><label>Last Name <input name=last_name></label>
  <label>Email <input type=email name=email></label><button type=submit>Apply</button></form></main>"""

NOT_A_GATE = """<main><form onsubmit="event.preventDefault();document.body.dataset.gate=1">
  <label>First Name <input name=first_name></label><label>Email <input type=email name=email></label>
  <label>Resume <input type=file name=resume></label><button type=submit>Apply</button></form></main>"""

# The everyday upload: a styled drop zone over a hidden file input. Nothing about the form says "gate" but its visible boxes.
HIDDEN_UPLOAD = """<main><form onsubmit="event.preventDefault();document.body.dataset.gate=1">
  <label>First Name <input name=first_name></label><label>Last Name <input name=last_name></label>
  <label>Email <input type=email name=email></label>
  <div class=dropzone>Drop your file here<input type=file name=upload style="display:none"></div>
  <button type=submit>Apply</button></form></main>"""

# A question built from divs: no input the visible-box check can read.
WIDGET_QUESTION = """<main><form onsubmit="event.preventDefault();document.body.dataset.gate=1">
  <label>First Name <input name=first_name></label><label>Email <input type=email name=email></label>
  <div role=radiogroup aria-label="Are you authorized to work here?"><div role=radio tabindex=0>Yes</div><div role=radio tabindex=0>No</div></div>
  <button type=submit>Apply</button></form></main>"""

# An upload button that opens a file chooser and holds no input at all until it is pressed.
UPLOAD_BUTTON = """<main><form onsubmit="event.preventDefault();document.body.dataset.gate=1">
  <label>First Name <input name=first_name></label><label>Email <input type=email name=email></label>
  <button type=button>Attach Resume/CV</button><button type=submit>Apply</button></form></main>"""

LOGIN = """<main><form onsubmit="event.preventDefault();document.body.dataset.login=1">
  <label>Email <input type=email name=email></label><label>Password <input type=password name=pw></label>
  <button type=submit>Login</button></form></main>"""


async def session(page, tmp_path, body):
    await page.set_content(f"<html><body>{body}</body></html>")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a: None)
    await form.scan()
    return form, {b["label"].strip(): b for b in form.buttons.values()}


async def test_apply_in_a_name_and_email_form_starts_the_application(page, tmp_path):
    form, buttons = await session(page, tmp_path, GATE)
    await form.click(buttons["Apply"]["id"])
    assert await page.evaluate("document.body.dataset.gate") == "1"


async def test_apply_in_a_form_with_an_upload_is_still_a_final_submit(page, tmp_path):
    form, buttons = await session(page, tmp_path, NOT_A_GATE)
    with pytest.raises(ValueError, match="submit_application"):
        await form.click(buttons["Apply"]["id"])
    assert await page.evaluate("document.body.dataset.gate") is None


@pytest.mark.parametrize("body", [HIDDEN_UPLOAD, WIDGET_QUESTION, UPLOAD_BUTTON])
async def test_apply_in_a_form_that_is_more_than_who_you_are_is_still_a_final_submit(page, tmp_path, body):
    """Sent as an "entry", the application left with no reservation, no `submitting` event and no duplicate protection."""
    form, buttons = await session(page, tmp_path, body)
    with pytest.raises(ValueError, match="submit_application"):
        await form.click(buttons["Apply"]["id"])
    assert await page.evaluate("document.body.dataset.gate") is None


async def test_apply_after_the_resume_went_up_is_still_a_final_submit(page, tmp_path):
    form, buttons = await session(page, tmp_path, GATE)
    form.uploaded = True
    with pytest.raises(ValueError, match="submit_application"):
        await form.click(buttons["Apply"]["id"])
    assert await page.evaluate("document.body.dataset.gate") is None


FORMLESS = """<main><label>First Name <input name=first_name></label><label>Last Name <input name=last_name></label>
  <label>Email <input type=email name=email></label><label>Resume <input type=file name=resume></label>
  <button type=submit onclick="document.body.dataset.gate=1">Apply</button></main>"""


async def test_apply_outside_any_form_on_the_application_page_is_a_final_submit(page, tmp_path):
    """Gem (Imagen Technologies 2026-10-28, Nuvo 10-31): no <form>, so a plain click sent it with no commit step."""
    form, buttons = await session(page, tmp_path, FORMLESS)
    with pytest.raises(ValueError, match="submit_application"):
        await form.click(buttons["Apply"]["id"])
    assert await page.evaluate("document.body.dataset.gate") is None
    form.uploaded = form.upload_verified = True
    assert [b["label"] for b in await form.application_submit_controls()] == ["Apply"]


async def test_login_is_a_sign_in_not_a_final_submit(page, tmp_path):
    form, buttons = await session(page, tmp_path, LOGIN)
    await form.click(buttons["Login"]["id"])
    assert await page.evaluate("document.body.dataset.login") == "1"
