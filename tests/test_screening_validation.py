"""Screening questions that production refused or answered wrongly (2026-10-14 to 2026-10-19).

Every label below is employer text seen in production. The applicant is synthetic. The facts mirror an F-1 OPT
applicant who needs no visa sponsorship and records "No" for the government, relative and restriction questions.
"""

import pytest

from jobpilot.answer_context import (
    evaluate_eligibility,
    question_head,
    status_defined_answer,
    us_person,
    us_person_question,
)
from jobpilot.answers import Resolver, critical_question
from jobpilot.knowledge import relevant_fact, topic
from jobpilot.schemas import Answer, Profile

OPT_FACTS = {
    "us_citizen": False,
    "citizenship": "Canada",
    "country_of_citizenship": "Canada",
    "current_immigration_status_US": "OPT; expiration July 2029",
    "requires_future_sponsorship_US": False,
    "work_authorization_US": "Yes, authorized to work for any employer in the USA (applicant confirmed)",
    "has_asylee_status": False,
    "government_employment_history": "No (confirmed by applicant)",
    "government_official": False,
    "has_non_compete": False,
    "has_non_solicitation": False,
    "export_control_restricted_country_history": "No: never a citizen of or traveled to restricted countries",
}
FACTS = {k.lower(): v for k, v in OPT_FACTS.items()}

UCHICAGO = (
    "Will you now or in the future require employer sponsorship for an employment-based visa or work authorization "
    'in the United States? Select "no" if you are a U.S. citizen or legal permanent resident. Select "yes" if you '
    "are in the U.S. on a nonimmigrant or dual-intent visa (e.g. F-1 (including OPT), J-1, J-2, H-1B, E-3, TN, O-1, "
    "etc.) Please note: the University of Chicago is not an eligible employer for F-1 visa holders with approved "
    "periods of STEM OPT."
)
INTEL = (
    "11)* Do you now, or in the future, require Intel to sponsor you for a visa to work in the United States? NOTE: "
    "Individuals with work authorization issued pursuant to their student visa (e.g., F-1, J-1, M-1) or currently on "
    "work visas specific to their current employer (e.g., H-1B, O-1, TN, H-1B1, E-3), should answer “Yes” because "
    "Intel must file a visa petition on your behalf now or in the future. Intel immigration sponsorship refers to "
    "Intel filing employment-based visa and permanent resident applications on behalf of the employee in order for "
    "the individual to have the legal right to work in the U.S. Individuals with work authorization not dependent on "
    "Intel sponsorship should respond “No” (e.g., U.S. citizens, permanent residents, asylees, refugees, DACA)."
)
OPT_SAYS_NO = (
    "Are you legally able to work in the country for which you are applying without sponsorship now or in the "
    "future? (OPT and CPT participants should answer “No”)"
)
BRIDGING = (
    "Will you now or could you in the future require sponsorship to obtain work authorization or to transfer or "
    "extend your current visa? (You must answer “Yes” if your current visa is tied to your spouse or partner’s visa "
    "or if you are on a bridging visa or working holiday visa)"
)
US_PERSON = (
    "Are you a U.S. Citizen, U.S. National, Lawful Permanent Resident (i.e., Green Card Holder), Asylee, or Refugee? "
    "Please note that people in the classification of U.S. Citizen, U.S. National, Lawful Permanent Resident (i.e., "
    "Green Card Holder), Asylee, or Refugee are legally authorized to work permanently in the U.S. and have "
    "unrestricted U.S. work authorization. They do not need employment support or sponsorship."
)
US_WORKER = (
    "Micron is required to comply with federal reporting regulations regarding those who seek employment with the "
    "company. We need to know whether you are a U.S. worker. A U.S. worker is defined by the U.S. Department of Labor "
    "as a 1) U.S. citizen, 2) U.S. national, 3) U.S. legal permanent resident, 4) refugee, or 5) person granted "
    "asylum. Are you in one of the above five groups?"
)
EXPORT_OPTIONS = [
    "A United States Citizen",
    "A lawful permanent resident of the United States (i.e. Greencard holder)",
    "A protected individual",
    "Other",
]
EXPORT = (
    "This position may require access to information and technology that is subject to export controls, such as "
    "EAR. Are you any of the following?"
)


def resolver(service, facts=OPT_FACTS, **profile):
    return Resolver(
        Profile(
            name="Alex Doe",
            first_name="Alex",
            last_name="Doe",
            facts=dict(facts),
            accept_all_application_terms=True,
            **profile,
        ),
        service.db,
        service.config,
    )


def field(label, options=("Yes", "No"), kind="select"):
    return {
        "id": "q",
        "label": label,
        "type": kind,
        "required": True,
        "options": list(options),
        "value": "",
        "valid": True,
    }


def validate(r, f, value, evidence):
    facts, sources, learned = r.answer_sources()
    proposal = Answer(field_id="q", value=value, disposition="answer", evidence_ids=list(evidence))
    return r._validate_answer(f, proposal, facts, sources, learned)


# Questions that define their own answer for the applicant's status (owner rule: Yes only when the question tells
# F-1 OPT holders to answer Yes).


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        (UCHICAGO, True),
        (INTEL, True),
        (OPT_SAYS_NO, False),
        (
            "Do you now, or will you in the future, need sponsorship from an employer in order to obtain, extend or "
            "renew your authorization to work in the United States? Please note: if you are on F-1 Optional "
            "Practical Training (OPT) answer “Yes”.",
            True,
        ),
        (BRIDGING, None),
        ("Will you now or in the future require sponsorship? Answer ‘Yes/No’ based on your status.", None),
    ],
)
def test_status_defined_answers(label, expected):
    found = status_defined_answer(label, FACTS)
    assert (found[0] if found else None) is expected


def test_status_definitions_do_not_apply_to_other_statuses():
    assert status_defined_answer(UCHICAGO, {"current_immigration_status_us": "U.S. citizen"}) is None
    # "opt out" is not OPT.
    assert status_defined_answer(
        "Select “Yes” if you opt out of text messages.", {"current_immigration_status_us": "OPT"}
    ) is None


def test_sponsorship_clauses_are_read_as_sponsorship():
    # The second clause sponsors the authorization; reading "authorized or sponsored" forced a Yes.
    label = (
        "Do you require sponsorship, or will you require sponsorship in the future for employment authorization "
        "in the United States?"
    )
    assert evaluate_eligibility(question_head(label), FACTS)[0] is False
    # An employer's opening statement is not a proposition about the applicant.
    statement = (
        "This position requires authorization to work in the US without sponsorship. Will you now or in the future "
        "require sponsorship (or transfer of sponsorship) to work in the US?"
    )
    assert (evaluate_eligibility(question_head(statement), FACTS) or (False,))[0] is False
    confirm = (
        "This position does not sponsor any Visa assistance. Please confirm you are authorized to work in the US for "
        "the entire duration of the 2-year term and for the future in case of an extension."
    )
    assert (evaluate_eligibility(question_head(confirm), FACTS) or (True,))[0] is True


def test_resolver_follows_the_question_definition(service):
    r = resolver(service)
    assert r.local(field(INTEL)).value == "Yes"
    assert r.local(field(UCHICAGO)).value == "Yes"
    # The DACA shortcut answers a DACA question, not a sponsorship question listing DACA among its examples.
    daca = "Are you currently in the U.S. under Deferred Action for Childhood Arrivals (DACA)?"
    assert r.local(field(daca)).value == "No"


def test_validator_accepts_the_defined_yes(service):
    r = resolver(service)
    accepted = validate(r, field(UCHICAGO), "Yes", ["fact:current_immigration_status_US"])
    assert accepted.disposition == "answer"


# U.S.-person status.


def test_us_person_questions_are_recognised():
    assert us_person_question(US_PERSON)
    assert us_person_question(US_WORKER)
    assert us_person_question(EXPORT + " " + " ".join(EXPORT_OPTIONS))
    assert not us_person_question("Are you a U.S. citizen?")
    assert us_person(FACTS)[0] is False
    assert us_person({"us_citizen": True})[0] is True
    assert us_person({"us_citizen": False}) is None  # No recorded status: unknown, not "No".


def test_us_person_answers_from_facts(service):
    r = resolver(service)
    assert r.local(field(US_PERSON)).value == "No"
    assert r.local(field(US_WORKER)).value == "No"
    assert r.local(field(EXPORT, EXPORT_OPTIONS)).value == "Other"
    citizen = resolver(service, {"us_citizen": True})
    assert citizen.local(field(US_WORKER)).value == "Yes"


def test_validator_refuses_a_wrong_us_person_answer(service):
    r = resolver(service)
    assert validate(r, field(US_WORKER), "Yes", ["fact:us_citizen"]).disposition != "answer"
    assert validate(r, field(US_WORKER), "No", ["fact:us_citizen"]).disposition == "answer"
    wrong = validate(r, field(EXPORT, EXPORT_OPTIONS), "A United States Citizen", ["fact:us_citizen"])
    assert wrong.disposition != "answer"


# The critical-question filter and the topic classifier.


@pytest.mark.parametrize(
    "label",
    [
        "Please provide your full legal name as it appears on your government ID",
        "[Optional] Preferred Full Name (The name that you would like to be referred to as. Please input your legal "
        "name in the first and last name fields above.)",
        "Do you possess 2 years of experience implementing Information Security standards of Encryption, "
        "Authentication, Authorization, Privileged user management, and Integration Service User maintenance as "
        "they apply to Workday?",
        "Provide a link to a GitHub repo, deployed application, or demo that shows something you built using AI "
        "coding agents. Personal projects and side experiments count. If your work is under NDA, describe what you "
        "built, the AI tools you used, and your role.",
        "Are you a person who is licensed to provide health care and/or authorized to prescribe, dispense or purchase "
        "prescription drugs, biologics or medical devices in the commonwealth of Massachusetts?",
        "Do you currently have valid, government-issued identification that meets TSA requirements for domestic air "
        "travel?",
        "Please review the meeting agenda before your interview.",
    ],
)
def test_not_disclosures(label):
    assert not critical_question(label)


@pytest.mark.parametrize(
    "label",
    [
        "Are you legally authorized to work in the United States?",
        "Do you require work authorization sponsorship?",
        "Are you a U.S. citizen?",
        "Have you ever been convicted of a felony?",
        "Are you bound by a non-compete agreement?",
        "Have you signed an NDA that would restrict your work here?",
        "Do you now, or did you at any time in the past 24 months, serve as an official or employee of the DoD?",
        "Have you ever been employed by a government agency?",
    ],
)
def test_still_disclosures(label):
    assert critical_question(label)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        (
            "By checking this box, I consent to receive text messages in regards to my application, and understand "
            "that I can opt out at any time by replying STOP to the texts that I receive.",
            "consent",
        ),
        ("Are you a current active Citizens contractor?", "prior_employment"),
        (
            "Are you related to or have a personal relationship (this includes, but is not limited to those existing "
            "by virtue of blood, marriage, domestic partnership, adoption or cohabitation) to a colleague of Citizens "
            "Bank, N.A. and its respective affiliates?",
            "referral",
        ),
        (
            "2. Section 847 of the National Defense Authorization Act for Fiscal Year 2008 requires certain current "
            "and former Department of Defense (DoD) officials or employees to seek and obtain a written ethics "
            "opinion regarding the applicability of post-employment restrictions. Do you now, or did you at any "
            "time in the past 24 months, serve as an official or employee of the DoD?",
            "government",
        ),
        (
            "Regardless of your citizenship or nationality, are you personally participating in foreign country of "
            "risk entity sponsored or affiliated activities?",
            "export_control",
        ),
        (
            "Are you legally able to work in the location of the role you applied for, and if hired, able to present "
            "evidence of your citizenship or proof of your legal right to live and work in the country?",
            "authorization",
        ),
        (
            "In your current role, do you engage with Mastercard employees to negotiate, influence and/or sign "
            "commercial contracts or government contracts?",
            "client_relationship",
        ),
        (
            "Have you signed an agreement with any employer(s) that you will not, (a) engage in competition with that "
            "former employer, or (b) contact customers of that employer? If answering yes, you agree to provide a "
            "copy of any such agreement to a representative of ABB Inc. upon request.",
            "restriction",
        ),
    ],
)
def test_topics(label, expected):
    assert topic(label) == expected


def test_relevant_facts_for_status_lists_and_officials():
    fis = (
        "Please indicate if you are a national or permanent resident of any of the following countries: Cuba, Iran, "
        "North Korea, Syria, and the Crimea Regions of Ukraine or Russia."
    )
    assert relevant_fact(fis, "country_of_citizenship")
    assert relevant_fact(US_PERSON, "us_citizen")
    official = (
        "Please answer the next two questions to the best of your knowledge. For purposes of these questions: The "
        "term Senior Government Official refers to an official of a foreign (i.e., non-U.S.) government entity. Are "
        "you a Senior Government Official?"
    )
    assert relevant_fact(official, "government_official")


# Consents, signatures and declines.


def test_background_check_willingness_is_consent_not_a_record(service):
    label = (
        "A comprehensive background check may consist of prior employment verification, education confirmation, "
        "criminal record check and drug testing. If extended a job offer, are you willing to submit to a "
        "comprehensive background check?"
    )
    r = resolver(service, {**OPT_FACTS, "criminal_conviction": False})
    assert r.local(field(label)).value == "Yes"
    without_policy = Resolver(
        Profile(facts={"criminal_conviction": False}, accept_all_application_terms=False), service.db, service.config
    )
    answer = without_policy.local(field(label))
    assert answer is None or answer.value != "No"


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("By selecting Yes, I am consenting to the use of AI for evaluating my candidacy.", "Yes"),
        (
            "By checking this box, I consent to receive text messages in regards to my application, and understand "
            "that I can opt out at any time by replying STOP.",
            "I understand and agree",
        ),
        (
            "Are you able to, and do you agree to, comply with our in-office policy, which currently requires "
            "employees to work from their assigned office location at least four days per week?",
            "Yes",
        ),
    ],
)
def test_routine_consents_follow_the_policy(service, label, value):
    r = resolver(service)
    options = ("Yes", "No") if value == "Yes" else (value,)
    assert validate(r, field(label, options), value, ["fact:application_consent_policy"]).disposition == "answer"


def test_choosing_an_option_is_the_consent(service):
    label = (
        "Opportunities are available in our below business groups, please select those you are most interested in "
        "applying to. By selecting below, you consent to Blackstone and its affiliates considering you for relevant "
        "opportunities within other business units."
    )
    r = resolver(service)
    f = field(label, ["Credit", "Finance", "Technology"])
    assert validate(r, f, "Technology", ["fact:application_consent_policy"]).disposition == "answer"


@pytest.mark.parametrize(
    ("label", "value", "ok"),
    [
        ("By entering your name into the field below, you certify that this application is truthful and complete.",
         "Alex Doe", True),
        ("I certify that the facts set forth in this Application for Employment are true and complete to the best of "
         "my knowledge.", "Alex Doe 10/18/2026", True),
        ("By entering your name into the field below, you certify that this application is truthful and complete.",
         "Jordan Roe", False),
        ("I certify that I have never been convicted of a felony. Sign below.", "Alex Doe", False),
    ],
)
def test_typed_signatures(service, label, value, ok):
    r = resolver(service)
    result = validate(r, field(label, (), kind="text"), value, ["profile:name"])
    assert (result.disposition == "answer") is ok


def test_an_option_asserting_the_status_is_not_a_decline(service):
    label = "Protected veteran status: please select the option that applies to you."
    options = [
        "I identify as one or more of the classifications of protected veteran",
        "I am a protected veteran, but I choose not to self-identify the classifications to which I belong",
        "I am not a protected veteran",
        "I don't wish to answer",
    ]
    r = resolver(service, decline_demographics=True)
    answer = r.local(field(label, options))
    assert answer is None or not answer.value.startswith("I am a protected veteran")
