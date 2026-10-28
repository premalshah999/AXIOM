"""Pinpoint (Kenway Consulting, Wolverine Trading 2026-10-27): every Yes/No question of a section sits in one fieldset
("3.Questions"); each question has its own <label for="…_boolean_answer"> over pill-style radios "…_true"/"…_false"
whose accessible names are hidden "<question> yes" texts. Named by the section legend and read as optional, the
required work authorization and sponsorship answers were left blank: "Yes / no answers to questions can't be blank"."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile


def question(index, title):
    stem = f"application_form_application_answers_attributes_{index}_boolean_answer"
    choices = "".join(
        f"""<div class="checkable-input"><div id="answer-label-{index}-{value}" style="display:none">{title}  {word}</div>
        <input class="checkable-input__input" type="radio" id="{stem}_{value}" value="{value}"
         name="application_form[application][answers_attributes][{index}][boolean_answer]"
         aria-labelledby="answer-label-{index}-{value}">
        <label class="checkable-input__label" for="{stem}_{value}">{word.title()}</label></div>"""
        for value, word in (("true", "yes"), ("false", "no"))
    )
    return f"""<div class="pad-v-3"><label class="external-form__label" for="{stem}">
      <span class="external-form__label--title external-form__label--required">{title} </span></label>{choices}</div>"""


PAGE = f"""<form><fieldset id="application-fieldset-questions"><legend><span>3.</span>Questions</legend>
  {question(0, "Are you authorized to work for any employer in the United States?")}
  {question(1, "Do you require sponsorship now or at any point in the future?")}
</fieldset></form>"""


async def test_each_yes_no_question_is_named_by_its_own_label_and_required(page, service, tmp_path):
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path / "unused.pdf", lambda *a: None)
    radios = [f for f in (await form.scan())["fields"] if f["type"] == "radio"]
    assert [(f["label"], f["required"], f["options"]) for f in radios] == [
        ("Are you authorized to work for any employer in the United States?", True, ["Yes", "No"]),
        ("Do you require sponsorship now or at any point in the future?", True, ["Yes", "No"]),
    ]
    await form.fill(radios[0], "Yes")
    await form.fill(radios[1], "No")
    assert await page.locator("#application_form_application_answers_attributes_0_boolean_answer_true").is_checked()
    assert await page.locator("#application_form_application_answers_attributes_1_boolean_answer_false").is_checked()
    again = [f for f in (await form.scan())["fields"] if f["type"] == "radio"]
    assert [f["value"] for f in again] == ["Yes", "No"]


async def test_a_fieldset_with_one_question_keeps_its_legend(page, service, tmp_path):
    await page.set_content("""<form><fieldset><legend>Are you 18 or older?</legend>
      <label><input type=radio name=adult value=y>Yes</label><label><input type=radio name=adult value=n>No</label>
      </fieldset></form>""")
    form = FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path / "unused.pdf", lambda *a: None)
    radios = [f for f in (await form.scan())["fields"] if f["type"] == "radio"]
    assert [(f["label"], f["options"]) for f in radios] == [("Are you 18 or older?", ["Yes", "No"])]


async def test_a_react_select_mirror_select_is_not_a_question(page, service, tmp_path):
    # The widget's hidden input carries the value; the nameless <select> after it is the small-screen mirror.
    await page.set_content("""<form><fieldset><legend>1.Personal Details</legend>
      <label class="external-form__label external-form__label--required" for="town">State</label>
      <div class="field"><div class="react-select"><input id="town" role="combobox" aria-haspopup="true"
        aria-expanded="false"><input name="application_form[application][town]" type="hidden" value="MD"></div>
      <select class="hide-at-sm-block" id="town"><option value="">Select</option><option value="MD">Maryland</option>
      </select></div>
      <label for=c>Comments</label><select id=c name=comments><option>A</option><option>B</option></select>
      </fieldset></form>""")
    form = FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path / "unused.pdf", lambda *a: None)
    fields = (await form.scan())["fields"]
    assert [f["type"] for f in fields if f["type"] == "select"] == ["select"]
    assert not any(f["label"].startswith("1.Personal Details") for f in fields)
