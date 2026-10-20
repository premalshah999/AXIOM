"""Eightfold date pickers exposed as role=combobox (Micron Self-ID "Date" 2026-10-16): the fill kept the shown
date but recorded True as the expected value, so every readback failed with "Value not accepted: Date"."""

from datetime import date

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile
from jobpilot.widgets import same_value

TODAY = date.today().strftime("%d %b %Y")
PAGE = f"""<div class="ocpicker-module_picker"><label for="Application_questions_Q___AUTO___Self_Id">Date</label>
<input id="Application_questions_Q___AUTO___Self_Id" role="combobox" aria-haspopup="dialog" aria-expanded="false"
 value="{TODAY}"></div>"""


async def test_a_kept_picker_date_is_verified_as_shown(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    obs = await form.scan()
    field = next(f for f in obs["fields"] if f["label"] == "Date")
    assert field["type"] == "combobox"
    assert await form.fill(field, True) == TODAY
    report = await form.verify()
    assert not [p for p in report["problems"] if p.endswith(": Date")]


def test_equal_dates_in_different_formats_are_the_same_value():
    field = {"type": "text", "label": "Date"}
    assert same_value(field, "16 Oct 2026", "10/16/2026")
    assert same_value(field, "2026-10-16", "Oct 16, 2026")
    assert not same_value(field, "16 Oct 2026", "10/17/2026")
    assert same_value(field, TODAY, "today")
