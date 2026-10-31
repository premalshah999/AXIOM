"""Radios with no name and no fieldset are one question when they sit together (Nuvo on Gem, 2026-10-31).

Gem prints each Yes/No question as a span with two nameless radios under it. Every radio was read as its own question
labelled "Yes" or "No", the required question stayed blank ("Please select an option."), and the run repeated one click
until it stopped with "No final submit control". Synthetic markup."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<form><div><span>Are you graduating from a degree program in 2027?<span> *</span></span></div>
<div><div><input type=radio id=a1><label for=a1><div>Yes</div></label></div>
<div><input type=radio id=a2><label for=a2><div>No</div></label></div></div>
<div><span>Is your degree in Computer Science?<span> *</span></span></div>
<div><div><input type=radio id=b1><label for=b1><div>Yes</div></label></div>
<div><input type=radio id=b2><label for=b2><div>No</div></label></div></div></form>"""


async def test_nameless_radios_under_a_caption_are_one_question_each(page, service, tmp_path):
    await page.set_content(PAGE)
    resolver = Resolver(Profile(), service.db, service.config, job={"ats": "gem", "url": "https://example.test/job"})
    form = FormSession(page, resolver, tmp_path / "unused.pdf", lambda *a: None)
    fields = [f for f in (await form.scan())["fields"] if f["type"] == "radio"]
    assert [(f["label"], f["options"]) for f in fields] == [
        ("Are you graduating from a degree program in 2027?", ["Yes", "No"]),
        ("Is your degree in Computer Science?", ["Yes", "No"]),
    ]
    await form.fill(fields[1], "No")
    assert await page.locator("#b2").is_checked() and not await page.locator("#a2").is_checked()
