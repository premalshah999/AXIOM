"""Phone group fields are deterministic on every ATS: calling-code picker before the number,
national digits behind a separate code, phone type Mobile when offered, and an optional
extension that stays empty and never receives the phone number."""

import pytest

from jobpilot import phone_fields
from jobpilot.forms import FormSession as Form
from jobpilot.answers import Resolver
from jobpilot.schemas import Address, Profile

PHONE_GROUP = """<section><h2>Phone</h2>
<label for=cc>Country code</label>
<select id=cc><option value="">-- Select --</option><option value=us>United States of America (+1)</option>
  <option value=gb>United Kingdom (+44)</option></select>
<label for=ph>Phone</label><input id=ph type=tel>
<label for=ext>Phone Extension</label><input id=ext type=text>
<label for=pt>Phone Device Type</label>
<select id=pt><option value="">-- Select --</option><option>Home</option><option>Mobile</option><option>Work</option></select>
</section>"""


def _profile():
    return Profile(
        phone="+1 555-555-0100",
        phone_country_code="+1",
        phone_type="Mobile",
        address=Address(country="United States"),
    )


def _by_label(form, label):
    return next(f for f in form.fields.values() if f["label"] == label)


# Pure classification: no browser needed.


def test_extension_is_never_a_phone_number_field():
    ext = {"label": "Phone Extension", "type": "text", "section": "Phone"}
    assert phone_fields.is_phone_extension(ext)
    assert not phone_fields.is_phone_number(ext)
    assert phone_fields.is_phone_extension({"label": "Extension", "type": "text", "section": "Phone"})
    assert not phone_fields.is_phone_extension({"label": "Phone Number (Optional)", "type": "tel"})
    assert phone_fields.is_phone_number({"label": "Phone Number (Optional)", "type": "tel"})


def test_country_code_and_device_type_are_not_numbers():
    assert phone_fields.is_phone_country({"label": "Country / Territory Phone Code", "type": "combobox"})
    assert phone_fields.is_phone_country({"label": "Country", "type": "combobox", "section": "Phone"})
    assert not phone_fields.is_phone_country({"label": "Country", "type": "combobox", "section": "Address"})
    assert phone_fields.is_phone_type({"label": "Phone Device Type", "type": "combobox"})
    assert not phone_fields.is_phone_number({"label": "Phone Device Type", "type": "text"})


def test_phone_country_is_ordered_before_the_number():
    number = {"id": "n", "label": "Phone", "type": "tel"}
    code = {"id": "c", "label": "Country code", "type": "combobox"}
    other = {"id": "o", "label": "Address line 1", "type": "text"}
    assert [f["id"] for f in phone_fields.order_phone_fields([number, other, code])] == ["o", "c", "n"]


def test_phone_type_prefers_mobile_synonyms_only_when_offered():
    assert phone_fields.phone_type_option(["Home", "Mobile", "Work"], "Mobile") == "Mobile"
    assert phone_fields.phone_type_option([{"text": "Cell Phone"}, "Work"], "Mobile") == "Cell Phone"
    assert phone_fields.phone_type_option(["Home", "Work"], "Mobile") is None
    assert phone_fields.phone_type_option(["Home", "Work"], "") is None


# Browser behaviour through the real form driver.


@pytest.fixture
def form_on_phone_group(page, service, tmp_path):
    async def build():
        await page.set_content(PHONE_GROUP)
        form = Form(
            page, Resolver(_profile(), service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
        )
        await form.scan()
        return form

    return build


async def test_extension_stays_empty_and_never_receives_the_phone(page, form_on_phone_group):
    form = await form_on_phone_group()
    ext = _by_label(form, "Phone Extension")
    await form.fill(ext, "+1 555-555-0100")
    assert await page.locator("#ext").input_value() == ""
    assert form.expected[ext["id"]] == ""


async def test_country_code_picks_plus_one_before_the_number_is_typed(page, form_on_phone_group):
    form = await form_on_phone_group()
    code = _by_label(form, "Country code")
    answer = form.phone_answer(code)
    assert answer is not None and answer.value == "United States of America (+1)"
    await form.fill_current(accept_prefilled=False)
    assert await page.locator("#cc").input_value() == "us"
    # The number is typed after the selector commits, as national digits, and the extension stays empty.
    assert await page.locator("#ph").input_value() == "5555550100"
    assert await page.locator("#ext").input_value() == ""


async def test_phone_type_picks_mobile_when_offered(page, form_on_phone_group):
    form = await form_on_phone_group()
    device = _by_label(form, "Phone Device Type")
    answer = form.phone_answer(device)
    assert answer is not None and answer.value == "Mobile"
    await form.fill_current(accept_prefilled=False)
    assert await page.locator("#pt").input_value() == "Mobile"


async def test_number_is_national_digits_behind_a_separate_code(page, form_on_phone_group):
    form = await form_on_phone_group()
    number = _by_label(form, "Phone")
    assert number.get("phone_format") == "national"
    await form.fill(number, "+1 555-555-0100")
    assert await page.locator("#ph").input_value() == "5555550100"
