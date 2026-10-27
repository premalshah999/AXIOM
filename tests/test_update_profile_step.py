"""Rivian (iCIMS 2026-10-27): the candidate profile's type=submit "Update Profile" was refused as a bare HTML submit,
and a fully filled profile stopped at step 1 of 4."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<main><h1>Candidate Profile</h1><form onsubmit="event.preventDefault();document.body.dataset.saved=1">
<label>First Name<input id=first value="Alex"></label>
<button type=submit>Update Profile</button></form></main>"""


async def test_update_profile_is_a_step(page, tmp_path):
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a, **k: None)
    await form.scan()
    button = next(b for b in form.buttons.values() if b["label"].strip() == "Update Profile")
    await form.click(button["id"])
    assert await page.evaluate("document.body.dataset.saved") == "1"
