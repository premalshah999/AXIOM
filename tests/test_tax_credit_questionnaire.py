"""Tax-credit (WOTC) questionnaires an application requires, on any ATS.

Community Health Systems (Oracle, 2026-10-28): the required "Tax Credits" section's "Take Tax Credit Assessment" replaced
the application page with EY's survey, whose "Take Survey" the click guard refused as a possible application submit;
the section stayed untaken and Submit was refused "This info is required". Caesars (Oracle, 2026-10-27): Deloitte's
questionnaire asked "Are you under the age of 40?". The questionnaire is answered from the profile, finished with its own
controls, and the run returns to the application."""

from datetime import date
from types import SimpleNamespace

from jobpilot.adapters.base import Adapter
from jobpilot.answers import Resolver
from jobpilot.answers.local_rules import age_threshold, applicant_age
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

APPLY = "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1/apply/section/1"
SURVEY = "https://wotc.example.test/Public/Survey"

PAGES = {
    APPLY: """<html><body><h2>Tax Credits</h2>
      <p>To be considered for this job, you need to take a tax credit assessment on a partner's site. When you're done,
      you will be able to submit your job application.</p>
      <button class="partner-button" onclick="location.href='""" + SURVEY + """/Entry'">Take Tax Credit Assessment</button>
      <button type="submit">Submit</button></body></html>""",
    SURVEY + "/Entry": """<html><body><form action='""" + SURVEY + """/Questions' method="get">
      <label for="lang">Select a language</label><select id="lang" name="lang"><option value="">Select</option>
      <option>English</option><option>Español</option></select>
      <input type="submit" value="Take Survey"></form></body></html>""",
    SURVEY + "/Questions": """<html><body><form action='""" + SURVEY + """/Done' method="get">
      <fieldset><legend>Are you under the age of 40?</legend>
        <label><input type="radio" name="age" value="Y" required>Yes</label>
        <label><input type="radio" name="age" value="N" required>No</label></fieldset>
      <fieldset><legend>Has any member of your family received SNAP benefits (food stamps) in the last 6 months?</legend>
        <label><input type="radio" name="snap" value="Y" required>Yes</label>
        <label><input type="radio" name="snap" value="N" required>No</label></fieldset>
      <button type="submit">Submit</button></form></body></html>""",
    SURVEY + "/Done": "<html><body><h1>Thank you</h1><p>Your survey is complete. You may close this window.</p></body></html>",
}

FACTS = {"date_of_birth": "01/15/1995", "tax_credit_screening_no": True, "veteran": False}


async def serve(page):
    async def fulfil(route):
        url = route.request.url.split("?")[0]
        await route.fulfill(status=200, content_type="text/html", body=PAGES.get(url, "<html>not found</html>"))

    await page.context.route("https://acme.fa.us2.oraclecloud.com/**", fulfil)
    await page.context.route("https://wotc.example.test/**", fulfil)


def control(obs, label):
    return next(c for c in obs["controls"] if c["label"] == label)


async def test_the_questionnaire_is_answered_finished_and_left_for_the_application(page, service, tmp_path):
    await serve(page)
    await page.goto(APPLY)
    resolver = Resolver(Profile.model_validate({"facts": FACTS}), service.db, service.config,
                        job={"ats": "oracle", "url": APPLY})
    form = FormSession(page, resolver, tmp_path / "unused.pdf", lambda *a: None)
    obs = await form.scan()
    await form.click(control(obs, "Take Tax Credit Assessment")["id"])
    assert form.page.url.startswith(SURVEY) and form.on_questionnaire()
    report = await form.fill_current()
    assert report["ok"]
    # "Take Survey" is the questionnaire's own button, never the application's submit.
    await form.click(control(await form.scan(), "Take Survey")["id"])
    fields = (await form.scan())["fields"]
    answers = {f["label"]: resolver.local(f).value for f in fields}
    assert answers == {"Are you under the age of 40?": "Yes",
                       "Has any member of your family received SNAP benefits (food stamps) in the last 6 months?": "No"}
    assert (await form.fill_current())["ok"]
    after = await form.click(control(await form.scan(), "Submit")["id"])
    # Its thank-you page ends it: back on the application, where the employer's own Submit is the next step.
    assert form.page.url == APPLY and not form.on_questionnaire()
    assert any(c["label"] == "Submit" for c in after["controls"])


async def test_on_the_application_its_submit_is_still_refused_to_a_plain_click(page, service, tmp_path):
    await serve(page)
    await page.goto(APPLY)
    resolver = Resolver(Profile.model_validate({"facts": FACTS}), service.db, service.config,
                        job={"ats": "oracle", "url": APPLY})
    form = FormSession(page, resolver, tmp_path / "unused.pdf", lambda *a: None)
    obs = await form.scan()
    try:
        await form.click(control(obs, "Submit")["id"])
    except ValueError as exc:
        assert "submit" in str(exc).lower()
    else:
        raise AssertionError("the application's Submit passed a plain click")


class Oracleish(Adapter):
    id = "oracle"

    def __init__(self, url, controls, text):
        self.e = SimpleNamespace(form=SimpleNamespace(page=SimpleNamespace(url=url), click=self.form_click,
                                                      settle=self.settle))
        self.attempts, self.committed, self.events, self.launched = __import__("collections").Counter(), False, [], []
        self.obs = {"url": url, "controls": controls, "fields": [], "text": text}

    def emit(self, kind, message, data=None):
        self.events.append(kind)

    async def dismiss_overlays(self):
        return None

    async def settle(self, timeout=0):
        return None

    async def form_click(self, control_id, auth_control=False, step_control=False, launch=False):
        self.launched.append((control_id, launch))


async def test_an_oracle_tax_credits_section_is_launched_once_before_submit():
    text = "Tax Credits To be considered for this job, you need to take a tax credit assessment on a partner's site."
    adapter = Oracleish(APPLY, [{"id": "take", "label": "Take Tax Credit Assessment", "automation_id": ""},
                                {"id": "submit", "label": "Submit", "automation_id": ""}], text)
    assert await adapter.launch_assessment(adapter.obs) and adapter.launched == [("take", True)]
    assert adapter.assessment_from == "acme.fa.us2.oraclecloud.com"
    assert not await adapter.launch_assessment(adapter.obs)  # once per run
    adapter.e.form.page.url = SURVEY + "/Entry"
    assert adapter.in_assessment() and adapter.handoff(adapter.page.url) is None
    adapter.e.form.page.url = "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1"
    assert not adapter.in_assessment()


def test_age_thresholds_are_read_from_the_date_of_birth():
    assert applicant_age({"date_of_birth": "01/15/1995"}, today=date(2026, 10, 28)) == (31, "fact:date_of_birth")
    assert applicant_age({"age": 31}) == (31, "fact:age")
    assert age_threshold("are you under the age of 40", 31) is True
    assert age_threshold("are you 40 years of age or older", 31) is False
    assert age_threshold("are you between the ages of 16 and 24", 31) is False
    assert age_threshold("are you at least 18 years old", 31) is True
    # More than an age, or two thresholds: not answered from the age alone.
    assert age_threshold("are you under 25 and living in an empowerment zone", 22) is None
    assert age_threshold("are you under 18 or over 65 years of age", 70) is None
    assert age_threshold("have you worked over 40 hours a week", 31) is None


def test_the_state_of_work_is_the_postings_own(service):
    def ask(location):
        resolver = Resolver(Profile.model_validate({"facts": FACTS}), service.db, service.config,
                            job={"ats": "oracle", "url": APPLY, "location": location})
        return resolver.local({"id": "s", "label": "State in which you plan to work (choose Unsure if multiple options "
                               "exist or are not certain)", "type": "dropdown", "required": True, "value": "",
                               "valid": True, "group": "", "section": "",
                               "options": ["Select one...", "Maryland", "Nevada", "Unsure"]}).value

    assert ask("Las Vegas, NV") == "Nevada"
    assert ask("Multiple Locations") == "Unsure"
