"""America's Tire (iCIMS, 2026-10-27/28): the profile refused every work-experience Description of 186-190 characters
("Invalid Data Error", boxes drawn red) and kept one of 182; the page itself takes 200, so no limit was ever shown.
Refused prose is shortened (whole sentences or words, never ending on "and.") and becomes the remembered answer."""

from jobpilot.answers import Resolver
from jobpilot.features.knowledge.writing import fit_length
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PROSE = ("Built a production hybrid search service over 3,309 state regulations, transforming official XML sources "
         "into searchable chunks so compliance teams, researchers, and analysts could find answers quickly")


def test_a_cut_sentence_never_ends_on_a_joining_word():
    assert fit_length("Built search so compliance teams, researchers, and analysts could find answers", 52) == (
        "Built search so compliance teams, researchers."
    )
    assert fit_length("One. Two three.", 40) == "One. Two three."


async def test_flagged_prose_is_shortened_and_remembered(page, service, tmp_path):
    await page.set_content(f"""<form>
      <label for=d>Description</label><input id=d type=text value="{PROSE}" data-jp-employer-flagged=1>
      <label for=r>Reason For Leaving</label><input id=r type=text value="The internship ended in June.">
      </form>""")
    form = FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path / "unused.pdf", lambda *a: None)
    fields = (await form.scan())["fields"]
    flagged = set(await page.evaluate(
        "() => [...document.querySelectorAll('[data-jp-employer-flagged]')].map(e => e.dataset.jpId)"))
    assert await form.shorten_refused_prose(flagged) == ["Description"]
    value = await page.locator("#d").input_value()
    assert len(value) <= int(len(PROSE) * 0.6) and value.endswith(".") and not value.endswith(" and.")
    assert PROSE.startswith(value[:-1])
    described = next(f for f in fields if f["label"] == "Description")
    assert form.expected[described["id"]] == value
    assert await page.locator("#r").input_value() == "The internship ended in June."
