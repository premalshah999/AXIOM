"""Dallas County's Oracle form says "Please enter your first name using ALL CAPITAL LETTERS (Example: JOHN)" in a
paragraph above its name fields. Mixed-case names were refused at submit with HTTP 400, twice, with no field error
shown, and the run ended unconfirmed (2026-10-25). Fields the form asks for in capital letters are written so."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession, mark_uppercase_fields
from jobpilot.schemas import Profile

PAGE = """<p>Please enter your first name using ALL CAPITAL LETTERS (Example: JOHN).</p>
<label for=first>First Name</label><input id=first>
<p>Please enter your last name using all capital letters.</p>
<label for=last>Last Name</label><input id=last>
<label for=city>City</label><input id=city>"""


def test_fields_named_by_a_capital_letters_instruction_are_marked():
    fields = [{"label": "First Name", "type": "text"}, {"label": "Last Name", "type": "text"},
              {"label": "City", "type": "text"}, {"label": "Employer", "type": "text", "hint": "Use block letters"}]
    mark_uppercase_fields(fields, "Please enter your first name using ALL CAPITAL LETTERS (Example: JOHN).\n"
                                  "Please enter your last name using all capital letters.")
    assert [f.get("letter_case") for f in fields] == ["upper", "upper", None, "upper"]


async def test_a_name_is_typed_in_capitals_and_reads_back_as_the_same_answer(page, service, tmp_path):
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(first_name="Alex"), service.db, service.config), tmp_path / "r.pdf",
                       lambda *a: None)
    fields = {f["label"]: f for f in (await form.scan())["fields"]}
    await form.fill(fields["First Name"], "Alex")
    await form.fill(fields["City"], "Springfield")
    assert await page.locator("#first").input_value() == "ALEX"
    assert await page.locator("#city").input_value() == "Springfield"
    from jobpilot.widgets import same_value

    assert same_value(fields["First Name"], "ALEX", "Alex")
    assert not same_value(fields["City"], "SPRINGFIELD", "Springfield")
