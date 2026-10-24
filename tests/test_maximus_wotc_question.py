"""MAXIMUS's tax-credit survey (PepsiCo on iCIMS, 2026-10-24) shows one question per page as plain text above an
unlabelled Yes/No pair. The question read as the field name "ctl00$MainContent$multipleChoiceSingleAnswerInput100",
nothing answered it, and the run stalled."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<form method=post action="#"><div class="container body-content">
  <div class="text-right"><input type=submit name="ctl00$MainContent$RescreenButton$btnRescreen" value="Start Over"
    class="btn btn-lge btn-warning"><br><i>Please click the 'Start Over' button if you feel you need to make corrections
    in the data you have provided.</i></div>
  <div class="row"><div class="col-md-12"><span id="MainContent_lblQuestion" class="h2">Are you a resident of the State of
    California?</span></div></div>
  <div class="row"><div class="col-md-12">
  <table id="MainContent_multipleChoiceSingleAnswerInput100"><tbody>
    <tr><td><input id="MainContent_multipleChoiceSingleAnswerInput100_0" type=radio
      name="ctl00$MainContent$multipleChoiceSingleAnswerInput100" value="1"><label
      for="MainContent_multipleChoiceSingleAnswerInput100_0">Yes</label></td></tr>
    <tr><td><input id="MainContent_multipleChoiceSingleAnswerInput100_1" type=radio
      name="ctl00$MainContent$multipleChoiceSingleAnswerInput100" value="2"><label
      for="MainContent_multipleChoiceSingleAnswerInput100_1">No</label></td></tr>
  </tbody></table></div></div>
  <input type=submit name="ctl00$MainContent$btnNext" id="MainContent_btnNext" value="Next" class="btn btn-primary"
    style="display:none">
</div></form>"""


async def test_the_survey_question_is_read_and_answered_from_the_saved_state(page, service, tmp_path):
    await page.set_content(PAGE)
    profile = Profile.model_validate({"address": {"city": "Rockville", "state": "MD", "country": "United States"}})
    resolver = Resolver(profile, service.db, service.config, job={"ats": "icims", "url": "https://example.test/job"})
    form = FormSession(page, resolver, tmp_path / "unused.pdf", lambda *a: None)
    field = next(f for f in (await form.scan())["fields"] if f["type"] == "radio")
    assert field["label"] == "Are you a resident of the State of California?" and field["options"] == ["Yes", "No"]
    answer = resolver.local(field)
    assert answer.value == "No" and answer.evidence_ids == ["profile:address.state"]
    await form.fill(field, answer.value)
    assert await page.locator("#MainContent_multipleChoiceSingleAnswerInput100_1").is_checked()


def test_a_resident_of_the_saved_state_says_yes_and_a_city_is_left_to_other_rules(service):
    resolver = Resolver(Profile.model_validate({"address": {"state": "Maryland"}}), service.db, service.config)

    def ask(label):
        return resolver.local({"id": "r", "label": label, "type": "radio", "required": True, "value": "", "valid": True,
                               "group": "", "section": "", "options": ["Yes", "No"]})

    assert ask("Are you a resident of the State of Maryland?").value == "Yes"
    assert ask("Are you a resident of New York?").value == "No"
    assert ask("Are you a resident of New York City?") is None
