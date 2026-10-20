"""Taleo (Bealls 2026-10-17): Personal Information came back with 'You must select ALL possible values in the section
Place of Residence' and 'you must enter a number in the "Cellular Number" field'. Both fields were optional, the
page-wide fill skipped them, and "Cellular Number" was not even recognized as a phone input."""

from jobpilot import phone_fields
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

ERRORS = (
    "Errors occurred on this page, could not proceed. You must select ALL possible values in the section Place of "
    'Residence. You have chosen "Cellular Phone" as the primary contact number. Therefore, you must enter a number '
    'in the "Cellular Number" field.'
)
PAGE = f"""<div role="alert">{ERRORS}</div>
<label for="country">Country *</label><select id="country" required><option></option><option selected>United States</option></select>
<label for="state">State/Province *</label><select id="state" required><option></option><option selected>Maryland</option></select>
<label for="region">Region</label><select id="region"><option></option><option>Anne Arundel County</option><option>Howard County</option></select>
<label for="primary">Primary Number *</label><select id="primary" required><option></option><option>Home Phone</option><option>Cellular Phone</option><option>Work Phone</option></select>
<label for="home">Home Phone Number</label><input id="home">
<label for="cell">Cellular Number</label><input id="cell">
<label for="pay">Expected Pay Rate</label><input id="pay">"""


async def test_page_errors_name_the_fields_the_fill_must_complete(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    await form.scan()
    labels = {form.fields[i]["label"] for i in form.error_named_fields()}
    assert labels == {"Region", "Cellular Number"}


def test_taleo_phone_labels_are_phone_fields():
    assert phone_fields.is_phone_number({"label": "Cellular Number", "type": "text"})
    assert phone_fields.is_phone_type({"label": "Primary Number .", "type": "select"})
    options = ["Home Phone", "Cellular Phone", "Work Phone"]
    assert phone_fields.phone_type_option(options, "Mobile") == "Cellular Phone"


def test_phone_masks_come_from_format_notes_and_pattern_errors():
    assert phone_fields.phone_mask("Cellular Number Format: 555-555-5555") == "555-555-5555"
    assert phone_fields.phone_mask("The value does not match the pattern 999-999-9999.") == "999-999-9999"
    assert phone_fields.apply_mask("(555) 555-0100", "555-555-5555") == "555-555-0100"
    assert phone_fields.apply_mask("+1 555-555-0100", "(999) 999-9999") == "(555) 555-0100"
    assert phone_fields.phone_mask("Call 555-555-0100") is None


MASKED = """<div role="alert">Errors occurred on this page. The value does not match the pattern 999-999-9999.</div>
<span><label for="cell">Cellular Number</label><input id="cell" value="(555) 555-0100"></span><span>Format: 555-555-5555</span>"""


async def test_a_phone_in_another_shape_is_rewritten_in_the_mask(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(MASKED)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    await form.scan()
    cell = next(f for f in form.fields.values() if f["label"] == "Cellular Number")
    assert cell["id"] in form.error_named_fields()
    await form.fill(cell, "(555) 555-0100")
    assert await page.locator("#cell").input_value() == "555-555-0100"


async def test_fields_named_by_the_error_become_required_so_unknown_answers_reach_the_owner(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    await form.scan()
    region = next(f for f in form.fields.values() if f["label"] == "Region")
    assert region["id"] in form.error_named_fields()
    assert region["required"]


def test_personal_phone_variants_are_answered_with_the_profile_phone(service):
    resolver = Resolver(Profile(phone="(240) 555-0100"), service.db, service.config)
    for label in ("Cellular Number", "Home Phone Number", "Cell Phone", "Mobile Number", "Phone"):
        answer = resolver.local({"id": "x", "label": label, "type": "text"})
        assert answer and "".join(c for c in answer.value if c.isdigit()).endswith("2405550100"), label
    work = resolver.local({"id": "x", "label": "Work Phone Number", "type": "text"})
    assert not work or work.disposition != "answer" or "profile:phone" not in work.evidence_ids


async def test_a_page_demanded_field_without_an_answer_becomes_an_owner_question(page, service, tmp_path):
    from jobpilot.schemas import Answer

    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE)
    profile = Profile(phone="(240) 555-0100", address={"city": "Springfield", "state": "MD", "country": "United States"})
    form = FormSession(page, Resolver(profile, service.db, service.config), resume, lambda *a, **k: None)

    async def cannot_answer(fields, **kwargs):
        return [
            Answer(field_id=f["id"], value="", disposition="review", blocker_type="missing_fact", reason="Not stated")
            for f in fields
        ]

    form.resolver.resolve = cannot_answer
    report = await form.fill_current()
    region = next((p for p in report["pending"] if p.get("question", "").startswith("Region")), {})
    assert region.get("blocker_type") == "missing_fact"
    assert "Howard County" in region.get("options", [])


async def test_a_gpa_the_page_calls_invalid_is_written_as_a_number(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content("""<div role="alert">Errors occurred on this page, could not proceed. The field "GPA" contains an
      invalid value.</div><label for="gpa">GPA</label><input id="gpa" value="3.9/4.0">""")
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    await form.scan()
    gpa = next(f for f in form.fields.values() if f["label"] == "GPA")
    assert gpa["id"] in form.error_named_fields() and "invalid" in gpa["error"]
    await form.fill(gpa, "3.9/4.0")
    assert await page.locator("#gpa").input_value() == "3.9"


async def test_alert_text_outside_the_scanned_errors_still_names_fields(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content("""<label for="gpa">GPA</label><input id="gpa" value="3.9/4.0">""")
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    await form.scan()
    form.last_observation["errors"] = []
    gpa = next(f for f in form.fields.values() if f["label"] == "GPA")
    assert gpa["id"] in form.error_named_fields(['The field "GPA" contains an invalid value.'])
