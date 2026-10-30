"""Found applying by hand (2026-10-29): a resume parser pre-filled the Email box with the address printed on the resume,
the LinkedIn box with the GitHub URL and the Major box with the degree name. All pages here are synthetic."""

from unittest.mock import AsyncMock

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Answer, EducationEntry, Profile

PROFILE = {
    "email": "alexample.apply@gmail.com",
    "linkedin": "https://www.linkedin.com/in/alex-example",
    "github": "https://github.com/alexexample",
    "education": [EducationEntry(school="Example University", degree="Master of Science", field_of_study="Data Science")],
}


def session(page, service, tmp_path, employer="ExampleSoft Inc", **profile):
    resolver = Resolver(
        Profile(**{**PROFILE, **profile}), service.db, service.config, employer=employer,
        job={"ats": "custom", "url": "https://x.test"},
    )
    return FormSession(page, resolver, tmp_path / "unused.pdf", lambda *a: None)


def unknown_to_the_model(form):
    """No profile answer: the model has none either (these tests make no model call)."""

    async def skip(fields, **_):
        return [Answer(field_id=f["id"], disposition="skip") for f in fields]

    form.resolver.resolve = AsyncMock(side_effect=skip)


PREFILLED = """<form>
  <label for=e>Email Address</label><input id=e type=email value="printed.on.resume@example.test" required>
  <label for=l>LinkedIn</label><input id=l type=url value="https://github.com/alexexample">
  <label for=m>Major</label><input id=m type=text value="Master of Science">
</form>"""


async def test_a_resume_parsers_wrong_values_are_replaced_by_the_profiles(page, service, tmp_path):
    await page.set_content(PREFILLED)
    form = session(page, service, tmp_path)
    await form.scan()
    await form.fill_current(accept_prefilled=True)
    assert await page.locator("#e").input_value() == "alexample.apply@gmail.com"
    assert await page.locator("#l").input_value() == "https://www.linkedin.com/in/alex-example"
    assert await page.locator("#m").input_value() == "Data Science"


async def test_a_wrong_value_the_profile_cannot_replace_is_cleared_not_kept(page, service, tmp_path):
    await page.set_content(PREFILLED)
    form = session(page, service, tmp_path, linkedin="", education=[])
    unknown_to_the_model(form)
    await form.scan()
    await form.fill_current(accept_prefilled=True)
    assert await page.locator("#l").input_value() == ""  # the GitHub URL is not a LinkedIn address
    assert await page.locator("#m").input_value() == ""  # a degree name is not a major


async def test_a_required_prefilled_value_that_contradicts_the_profile_is_not_verified(page, service, tmp_path):
    await page.set_content(PREFILLED)
    form = session(page, service, tmp_path)
    await form.scan()
    form.accept_prefilled = True
    report = await form.verify()
    assert any("contradicts" in problem and "Email Address" in problem for problem in report["problems"])
    # The same mailbox under a plus-address is accepted as pre-filled.
    await page.fill("#e", "alexample.apply+icims@gmail.com")
    await form.scan()
    report = await form.verify()
    assert not any("contradicts" in problem for problem in report["problems"])
