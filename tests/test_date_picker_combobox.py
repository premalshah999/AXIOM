"""PayPal (Eightfold) 2026-10-14: a date picker exposed as role=combobox has no list options."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PICKER = """<label for=d>Date</label>
<input id=Application_questions_english_date role=combobox aria-haspopup=dialog aria-expanded=false value="{value}">"""


async def form_for(page, tmp_path, value):
    await page.set_content(PICKER.format(value=value))
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    field = next(f for f in form.fields.values() if f["label"] == "Date")
    return form, field


async def test_prefilled_valid_date_is_kept(page, tmp_path):
    form, field = await form_for(page, tmp_path, "14 Oct 2026")
    await form.fill(field, "10/14/2026")
    assert await page.locator("input").input_value() == "14 Oct 2026"


async def test_empty_picker_gets_the_date_typed(page, tmp_path):
    form, field = await form_for(page, tmp_path, "")
    await form.fill(field, "10/14/2026")
    assert await page.locator("input").input_value() == "10/14/2026"
