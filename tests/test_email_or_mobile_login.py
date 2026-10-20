"""ADP Workforce Now (ZincFive 2026-10-16): the guest login field "Email or Mobile Number" received the email
through the phone branch, which retyped only the value's digits and left the field empty on every run."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<label for="guestEmail">Email or Mobile Number</label><input id="guestEmail" type="text" class="vdl-textbox">
<button type="button">Continue</button>"""


async def test_an_email_in_an_email_or_mobile_field_is_typed_as_text(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    obs = await form.scan()
    field = next(f for f in obs["fields"] if f["label"] == "Email or Mobile Number")
    await form.fill(field, "alex.rivera@example.test")
    assert await page.locator("#guestEmail").input_value() == "alex.rivera@example.test"


async def test_a_phone_number_in_the_same_field_still_uses_the_phone_branch(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    obs = await form.scan()
    field = next(f for f in obs["fields"] if f["label"] == "Email or Mobile Number")
    await form.fill(field, "(240) 555-0100")
    assert "".join(c for c in await page.locator("#guestEmail").input_value() if c.isdigit()).endswith("2405550100")
