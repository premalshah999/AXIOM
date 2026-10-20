"""Questions that reached the owner on 2026-10-12..18 although the profile or the owner's policies answer them
(owner: "it's disastrous if our agent cannot even counter these kind of questions")."""

from datetime import datetime
from zoneinfo import ZoneInfo

from jobpilot.answers import Resolver, residence_outside
from jobpilot.db import Job, Run
from jobpilot.schemas import Profile, WorkEntry

FACTS = {
    "has_non_compete": False,
    "has_non_solicitation": False,
    "disability": False,
    "veteran": False,
    "state_of_residence": "Maryland",
}


def profile(**extra):
    return Profile(
        name="Alex Example",
        location="Springfield, MD",
        accept_all_application_terms=True,
        work=[
            WorkEntry(company="Example School", title="AI Engineer", start="2025-12", current=True),
            WorkEntry(company="Example Lab", title="Research Engineer", start="2025-07", end="2026-05"),
            WorkEntry(company="Example AI", title="Data Science Intern", start="2023-08", end="2024-06"),
        ],
        **{"facts": dict(FACTS), **extra},
    )


def field(label, options=(), kind="select", **extra):
    return {"id": "q", "label": label, "type": kind, "required": True, "options": list(options), "value": "",
            "valid": False, **extra}


def local(service, f, **extra):
    return Resolver(profile(**extra), service.db, service.config).local(f)


def today(service):
    now = datetime.now(ZoneInfo(service.config.timezone)).date()
    return f"{now.month:02d}/{now.day:02d}/{now.year}"


def test_consent_to_be_considered_for_other_roles(service):
    label = ("I authorize the RV Tech Talent Acquisition team to consider me for other job opportunities within RV Tech "
             "in addition to the specific job I am applying for.")
    assert local(service, field(label, ["Yes", "No"], "radio")).value == "Yes"


def test_consent_request_for_ai_screening_and_its_date(service):
    label = ("Candidate Consent: Use of AI-Assisted Screening Tool (HiredScore) S&P Global requests your consent to use "
             "HiredScore, an AI-assisted screening tool. It does not consider race, gender, age or disability.")
    assert local(service, field(label, ["Yes, I consent", "No, I do not consent"], "radio")).value == "Yes, I consent"
    assert local(service, field(label, kind="checkbox")).value == "true"
    # The consent's own date input (Workday renders it under the consent text).
    assert local(service, field(label, kind="date_parts")).value == today(service)


def test_marketing_is_not_a_consent_request(service):
    # The contact-consent policy owns marketing; the consent-request rule never accepts it.
    label = "Do you consent to the use of your phone number for marketing text messages?"
    answer = local(service, field(label, ["Yes", "No"], "radio"), accept_required_marketing=False)
    assert answer is None or answer.evidence_ids != ["policy:consent"]


def test_the_label_names_the_acknowledging_option(service):
    label = ("Gas South is committed to providing access, equal opportunity, and reasonable accommodation for individuals "
             "with disabilities as defined by the Americans with Disabilities Act (ADA). Please select \"Yes\" in the "
             "dropdown to confirm that you have read this statement.")
    assert local(service, field(label, ["Yes"])).value == "Yes"
    assert local(service, field(label, kind="combobox")).value == "Yes"  # Options load when the list opens.


def test_a_generic_label_takes_its_topic_from_the_options(service):
    options = ["I Am Not a Protected Veteran", "I Choose Not to Self Identify",
               "I Identify as One or More of the Classifications of Protected Veteran Listed Above"]
    assert local(service, field("Please Select:", options)).value == "I Choose Not to Self Identify"


def test_an_availability_question_naming_disability_is_not_self_identification(service):
    label = ("Other than time off for reasons related to your religion, a disability, or a medical condition, are there "
             "any days or times when you are unavailable to work?")
    answer = local(service, field(label, kind="text"))
    assert answer is None or answer.reason != "Decline disclosure; never infer demographic identity"


def test_a_covenant_described_in_words_uses_both_covenant_facts(service):
    label = ("Have you signed an agreement with any employer(s) that you will not, (a) engage in competition with that "
             "former employer, or (b) contact customers of that employer? If answering yes, you agree to provide a copy "
             "of any such agreement to a representative of ABB Inc. upon request.")
    assert local(service, field(label, ["Yes", "No"])).value == "No"
    bound = Resolver(profile(), service.db, service.config)
    bound.profile.facts["has_non_solicitation"] = True
    assert bound.local(field(label, ["Yes", "No"])) is None  # "No non-compete" does not answer a recorded one.


def test_reason_for_leaving_states_only_the_recorded_history(service):
    label = "What is your reason for leaving your current/most recent employer?"
    assert local(service, field(label, kind="textarea")).value.startswith("I am still in this role")
    assert local(service, field("Reason for Leaving", kind="text", group="Work Experience 2")).value.startswith(
        "The role ended in May 2026"
    )
    assert local(service, field("Reason for Leaving", kind="text", group="Work Experience 3")).value.startswith(
        "The internship ended in June 2024"
    )
    assert local(service, field("Reason for leaving", ["Resigned", "Laid off", "Still employed"])) is None


def test_a_dayforce_answered_date_text_field_takes_today(service):
    assert local(service, field("Disability Questionnaire Answered Date", kind="text")).value == today(service)


JERRY = ("Where do you currently reside? Jerry.ai can only hire in the locations listed below. If your location is not "
         "listed, unfortunately we are unable to process your application.")
STATES = ["Arizona", "California", "Colorado", "Florida", "New York", "Texas", "Virginia", "Ontario"]


def test_residence_outside_the_listed_hiring_locations():
    assert residence_outside(JERRY, STATES, FACTS) == "Maryland"
    assert residence_outside(JERRY, STATES, {}, "Springfield, MD") == "Maryland"
    assert residence_outside(JERRY, [*STATES, "Maryland"], FACTS) is None
    assert residence_outside(JERRY, [*STATES, "Other"], FACTS) is None
    assert residence_outside("Where do you currently reside?", STATES, FACTS) is None  # No stated hiring limit.


def test_the_run_closes_instead_of_asking(service):
    with service.db.exclusive() as s:
        s.add(Job(id="jerry", identity="jerry", url="https://jobs.ashbyhq.com/jerry/1", ats="ashby", company="Jerry"))
        s.add(Run(id="jr", job_id="jerry", mode="submit", state="running",
                  packet={"profile": {"autonomous": True, "facts": FACTS, "location": "Springfield, MD"}}))
    service.complete("jr", {"state": "needs_review", "reason": "Required profile information is missing",
                            "reviews": [{"question": JERRY, "options": STATES, "key": "k", "blocker_type": "missing_fact"}]},
                     1.0)
    with service.db.session() as s:
        run, job = s.get(Run, "jr"), s.get(Job, "jerry")
        assert run.state == "closed" and "Maryland" in run.reason
        assert job.status == "closed"


def test_salary_in_local_currency_is_a_plain_number(service):
    label = "Minimum annual gross salary desired, in local currency:"
    assert local(service, field(label, kind="text"), salary_expectation="80000").value == "80000"
    # "Specify the amount and currency" still names the currency (TransUnion 2026-10-15).
    assert local(service, field("Desired salary. Please specify the amount and currency.", kind="text"),
                 salary_expectation="80000").value == "80,000 USD"


def test_a_free_text_identity_question_takes_the_owners_fact(service):
    # Sentry (Ashby) 2026-10-19 asked the owner for "Pronouns"; a text box offers no decline choice.
    facts = {**FACTS, "pronouns": "He/him", "gender_identity": "Cisgender man", "race_preference": ["Asian", "South Asian"]}
    r = Resolver(profile(facts=facts), service.db, service.config)
    assert r.local(field("Pronouns", kind="text")).value == "He/him"
    assert r.local(field("Race/Ethnicity", kind="combobox")).value == "Asian"  # Options not readable.
    assert r.local(field("Are you a veteran?", kind="text")).value == "No"
    # A list that offers a decline still declines first.
    assert r.local(field("Pronouns", ["He/him", "She/her", "Prefer not to say"])).value == "Prefer not to say"
    # Without a saved fact it still asks.
    bare = Resolver(profile(facts={}), service.db, service.config)
    assert bare.local(field("Pronouns", kind="text")).disposition == "review"


def test_a_required_text_consent_with_one_agreeing_option(service):
    # SeekWell (Workday) 2026-10-18: the model's "I understand and agree" failed validation four times.
    label = ("By checking this box, I consent to receive text messages in regards to my application, and understand "
             "that I can opt out at any time by replying STOP to the texts that I receive from SeekWell.")
    r = Resolver(profile(), service.db, service.config)
    assert r.required_contact_consent(field(label, ["I understand and agree"], "checkboxgroup")).value == "I understand and agree"
    assert r.required_contact_consent(field(label, ["I do not agree"], "checkboxgroup")) is None


def test_local_answers_the_single_option_text_consent(service):
    label = ("By checking this box, I consent to receive text messages in regards to my application, and understand "
             "that I can opt out at any time by replying STOP to the texts that I receive from SeekWell.")
    answer = local(service, field(label, ["I understand and agree"], "checkboxgroup"))
    assert answer is not None and answer.value == "I understand and agree"


def test_a_legal_name_prompt_naming_a_government_id_is_the_applicants_name(service):
    # Waymo (Greenhouse): "government" made the model's answer a scoped disclosure three times.
    label = "Please provide your full legal name as it appears on your government ID"
    r = Resolver(profile(first_name="Alex", last_name="Example"), service.db, service.config)
    assert r.local(field(label, kind="text")).value == "Alex Example"
    assert r.local(field("Emergency contact full legal name", kind="text")) is None or \
        r.local(field("Emergency contact full legal name", kind="text")).value != "Alex Example"


def test_a_province_only_work_location_is_outside_the_applicants_state():
    # NerdWallet (Ashby) 2026-10-19: "What province in Canada will you be working from?"
    provinces = ["Ontario", "Quebec", "Nova Scotia", "British Columbia", "Alberta"]
    assert residence_outside("What province in Canada will you be working from?", provinces, FACTS) == "Maryland"
    states = ["Maryland", "Virginia", "New York"]
    assert residence_outside("What state will you be working from?", states, FACTS) is None
