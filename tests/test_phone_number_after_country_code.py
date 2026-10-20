"""iCIMS PowerSchool (2026-10-17): "Phone Country Code" then an input labelled only "Number". It stayed empty and the page
answered "Phone Number: required"; a bare Number after a calling-code picker is the phone number."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<label for="email">Primary Email</label><input id="email" type="email">
<label for="cc">Country Code</label><select id="cc"><option>Make a Selection</option><option selected>(+1) United States</option></select>
<label for="num">Number</label><input id="num" type="text">
<label for="other">Number of years of experience</label><input id="other" type="text">"""


async def test_a_bare_number_after_a_calling_code_is_the_phone(page, service, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(phone="(240) 555-0100"), service.db, service.config), resume, lambda *a, **k: None)
    obs = await form.scan()
    labels = [f["label"] for f in obs["fields"]]
    assert "Phone Number" in labels and "Number of years of experience" in labels
    phone = next(f for f in obs["fields"] if f["label"] == "Phone Number")
    answer = form.resolver.local(phone)
    assert answer and "".join(c for c in answer.value if c.isdigit()).endswith("2405550100")
