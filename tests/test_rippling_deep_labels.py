"""Go Fish Digital (Rippling, 2026-10-27): the searchable select of "What is your desired compensation range?" sits 9
levels below its question and was labelled "Select", so its required answer was never resolved and Apply stayed
disabled; the SMS opt-in radio group read "field-113" with choices "true"/"false" because its native radios are named
by aria-labelledby."""

from jobpilot.forms import SCAN

PAGE = """<form>
<div><div><div><p>What is your desired compensation range?</p></div></div>
<div data-testid="field"><div><div data-testid="customQuestions.q1"><div><div data-testid="select-controller">
  <div><div><div data-testid="select-search-input"><input id="field-81" aria-required="true" placeholder="Select"
    aria-label="Select" role="combobox" aria-autocomplete="list" aria-haspopup="listbox" aria-expanded="false" value="">
  </div></div></div><div><span aria-hidden="true">v</span></div></div></div></div></div></div>
<div><p>Check <strong>Yes</strong> or <strong>No</strong> to indicate your agreement to receive text message updates from
  the employer's recruiting team regarding your job application and interviews. Frequency may vary. Message and data rates may apply. Reply HELP for
  assistance. Reply STOP to opt out of future messaging.</p>
<div data-testid="field"><div><div data-testid="sms_opt_in" id="field-113" role="radiogroup">
  <div data-value="true" role="radio" tabindex="0" aria-checked="false"><div><input aria-labelledby="label-116"
    type="radio" value="true" name="sms_opt_in"></div><div id="label-116"><p>Yes - I consent to receiving text messages</p></div></div>
  <div data-value="false" role="radio" tabindex="-1" aria-checked="false"><div><input aria-labelledby="label-117"
    type="radio" value="false" name="sms_opt_in"></div><div id="label-117"><p>No - I do not consent to receiving text messages</p></div></div>
</div></div></div></div>
</form>"""


async def test_rippling_deep_select_and_sms_consent_are_labelled(page):
    await page.set_content(PAGE)
    data = await page.main_frame.evaluate(SCAN)
    labels = {f["label"]: f for f in data["fields"]}
    assert "What is your desired compensation range?" in labels
    sms = next(f for f in data["fields"] if f["type"] == "radiogroup")
    assert sms["options"] == ["Yes - I consent to receiving text messages",
                              "No - I do not consent to receiving text messages"]
    # The caption is longer than a label; the group is named by its Yes choice.
    assert sms["label"] == "I consent to receiving text messages"


def test_the_readable_sms_choices_resolve_to_the_decline(service):
    # TheGuarantors (Rippling, 2026-10-27): Apply stayed disabled while this group was unanswered.
    from jobpilot.answers import Resolver
    from jobpilot.schemas import Profile

    field = {"id": "s", "label": "Tick Yes or No to indicate your agreement to receive text message updates from the "
             "employer regarding your job application.", "type": "radiogroup", "required": False, "value": "",
             "valid": True, "group": "", "section": "",
             "options": ["Yes - I consent to receiving text messages", "No - I do not consent to receiving text messages"]}
    answer = Resolver(Profile(), service.db, service.config).local(field)
    assert answer.disposition == "answer" and answer.value == "No - I do not consent to receiving text messages"


COLLIER = """<form><div>
<div><div><div><p>Due to the nature of our collaboration and consulting services with various secure and restricted
  company projects, this position requires that all applicable candidates can verify they are eligible to work in the US.
  At this time, we cannot sponsor US work visas. Can you confirm you are eligible to work in the US through US
  citizenship?<span></span></p></div></div>
<div data-testid="field"><div><div data-testid="customQuestions.q2" id="field-44" role="radiogroup">
  <div data-value="Yes" role="radio" tabindex="0" aria-checked="false"><div><input aria-labelledby="label-47" type="radio"
    value="Yes" name="customQuestions.q2"></div><div id="label-47"><p>Yes</p></div></div>
  <div data-value="No" role="radio" tabindex="-1" aria-checked="false"><div><input aria-labelledby="label-48" type="radio"
    value="No" name="customQuestions.q2"></div><div id="label-48"><p>No</p></div></div>
</div></div></div></div></form>"""


async def test_a_long_question_before_a_rippling_radio_group_is_its_label(page):
    """Collier Aerospace (Rippling, 2026-10-27): the custom question's text is 330 characters long, so the group read
    "field-44", the engine could not answer it, and the owner was asked about "field-44"."""
    await page.set_content(COLLIER)
    data = await page.main_frame.evaluate(SCAN)
    group = next(f for f in data["fields"] if f["type"] == "radiogroup")
    assert group["label"].endswith("Can you confirm you are eligible to work in the US through US citizenship?")
    assert group["options"] == ["Yes", "No"]
