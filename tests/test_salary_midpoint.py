"""A box that takes one amount gets the middle of the applicant's own saved range (Workable "Salary Range", a number
question drawn as a digits-only text input, read back "90,000,120,000" and stopped four runs, 2026-11-01)."""

import pytest

from jobpilot import widgets
from jobpilot.answers import Resolver
from jobpilot.schemas import Profile


def resolver(service, stance="$70,000 - $90,000", expectation=""):
    profile = Profile(first_name="Alex", salary_expectation=expectation, autonomous=True,
                      answer_rules={"salary_stance": stance})
    return Resolver(profile, service.db, service.config, job={"ats": "x", "url": "https://x.example.test"})


def field(label, kind="text", options=()):
    return {"id": "s", "label": label, "type": kind, "required": True, "options": list(options), "value": "",
            "valid": True}


@pytest.mark.parametrize(
    "text,expected",
    [("$70,000 - $90,000", "80000"), ("70000 - 90000", "80000"), ("$60k-$100k", "80000"),
     ("$100,000+", None), ("Negotiable", None), ("90000", None)],
)
def test_midpoint_of_a_closed_range(text, expected):
    assert widgets.range_midpoint(text) == expected


def test_number_input_gets_the_midpoint_of_the_saved_range(service):
    a = resolver(service).local(field("Salary Range", "number"))
    assert a is not None and a.value == "80000"


def test_one_amount_text_box_gets_the_midpoint_when_only_a_range_is_saved(service):
    a = resolver(service).local(field("What is your desired salary?"))
    assert a is not None and a.value == "80000"


def test_a_range_text_box_still_gets_the_range(service):
    a = resolver(service).local(field("Please indicate the salary range you are targeting"))
    assert a.value == "$70,000 - $90,000"


def test_a_saved_range_expectation_is_halved_for_a_single_amount_box(service):
    a = resolver(service, expectation="$70,000 - $90,000").local(field("Expected salary", "number"))
    assert a is not None and a.value == "80000"


def test_a_menu_of_bands_takes_the_band_holding_the_midpoint(service):
    options = ["Under $60,000", "$60,000 - $75,000", "$75,000 - $85,000", "$85,000 - $100,000"]
    f = field("Desired salary", "select", options)
    assert resolver(service).policy_choice(f, options) == "$75,000 - $85,000"


# A digits-only mask as a number question draws it (type=text; every other character is dropped, thousands are shown).
MASKED = """<label for=pay>Salary Range</label><input id=pay type=text>
<label for=note>Salary notes</label><input id=note type=text>
<script>
pay.addEventListener('input', () => {
  const d = pay.value.replace(/\\D/g, '');
  pay.value = d ? Number(d).toLocaleString('en-US') : '';
});
</script>"""


@pytest.mark.browser
async def test_a_digits_only_box_gets_the_midpoint_instead_of_a_mangled_range(page, service, tmp_path):
    from jobpilot.forms import FormSession

    await page.set_content(MASKED)
    form = FormSession(page, Resolver(Profile(first_name="Alex"), service.db, service.config), tmp_path / "r.pdf",
                       lambda *a: None)
    fields = {f["label"]: f for f in (await form.scan())["fields"]}
    await form.fill(fields["Salary Range"], "$70,000 - $90,000")
    assert await page.locator("#pay").input_value() == "80,000"
    assert widgets.same_value(fields["Salary Range"], "80,000", form.expected[fields["Salary Range"]["id"]])
    await form.fill(fields["Salary notes"], "$70,000 - $90,000")  # a plain box keeps the range
    assert await page.locator("#note").input_value() == "$70,000 - $90,000"


def test_prose_boxes_and_range_boxes_keep_the_range_and_two_box_ranges_get_their_halves(service):
    r = resolver(service)
    assert r.local(field("Please describe your desired salary expectations", "textarea")).value == "$70,000 - $90,000"
    assert r.local(field("What is your desired salary? Ranges are welcomed")).value == "$70,000 - $90,000"
    assert r.local(field("Annual Gross Salary Expectation - From")).value == "70000"
    assert r.local(field("Annual Gross Salary Expectation - To")).value == "90000"
