"""Work-authorization answers are read from the question's own definition (2026-10 audit of confirmed applications).

Synthetic applicants only. The F-1 OPT applicant's saved plain answers are "authorized" and "no future sponsorship".
"""

import pytest

from jobpilot.answers import Resolver
from jobpilot.answers.work_status import decide, false_claim, holds, standing
from jobpilot.schemas import Profile

OPT = {
    "us_citizen": False,
    "citizenship": "Freedonia",
    "country_of_citizenship": "Freedonia",
    "has_asylee_status": False,
    "work_authorization_US": "Yes, authorized to work for any employer in the USA (applicant confirmed)",
    "requires_future_sponsorship_US": False,
    "current_immigration_status_US": "OPT; expiration July 2029. Provide only when specifically asked for status or expiration.",
}
CITIZEN = {"us_citizen": True, "work_authorization_US": True, "requires_future_sponsorship_US": False}
GREEN_CARD = {
    "us_citizen": False,
    "permanent_resident": True,
    "work_authorization_US": True,
    "requires_future_sponsorship_US": False,
}
H1B = {
    "us_citizen": False,
    "work_authorization_US": True,
    "requires_future_sponsorship_US": True,
    "current_immigration_status_US": "H-1B",
}
YES_NO = ["Yes", "No"]


def field(label, options=YES_NO, kind="select", **extra):
    return {"id": "q", "label": label, "type": kind, "required": True, "options": options, "value": "", "valid": True, **extra}


def answer(label, facts, options=YES_NO, **extra):
    verdict = decide(field(label, options, **extra), facts)
    return verdict.value if verdict else None


# (question, F-1 OPT, citizen, green card)
AUDITED = [
    # Credence 22996081.
    ("Are you legally authorized to work in the U.S. as a U.S. citizen or Fully Naturalized U.S. Citizen?", "No", "Yes", "No"),
    ("Are you authorized to work in the US as a US citizen?", "No", "Yes", "No"),
    # ExxonMobil fd3420b3.
    (
        "* Do you have the indefinite right to work in the United States (i.e., you are a protected individual under 8 USC "
        "1324b – you are a US Citizen, US National, Permanent Resident, Conditional Permanent Resident, Temporary Resident, "
        "asylee, or refugee)?",
        "No", "Yes", "Yes",
    ),
    ("If so, do you have the indefinite right to work in the United States?", "No", "Yes", "Yes"),
    # Honeywell, Oshkosh.
    (
        "Are you currently lawfully authorized to work in the U.S. for any employer, as a (i) U.S. citizen or national; (ii) "
        "permanent resident; (iii) lawful temporary resident; (iv) foreign national admitted as a refugee; or (v) foreign "
        "national for whom asylum has been granted?",
        "No", "Yes", "Yes",
    ),
    (
        "Are you currently authorized to work permanently in the United States? (I.e. Are you one of the following: a United "
        "States citizen; a Lawful Permanent Resident (“green card” holder); a Conditional Permanent Resident; a Refugee; or an "
        "Asylee?)",
        "No", "Yes", "Yes",
    ),
    (
        "Select “Yes” if any one of the categories below applies to you. Select “No” only if none of these apply to you. A U.S. "
        "citizen (either by birth or naturalization), or A lawful permanent resident (green card holder), or An individual "
        "granted refugee or asylum status in the U.S.",
        "No", "Yes", "Yes",
    ),
    # A definition by valid work authorization: John Deere 7d724cb4 answered No to this.
    (
        "Are you legally eligible to work for John Deere and other employers in the United States on a regular or full-time "
        "basis? (You are legally eligible if you are a U.S. citizen or if you have a valid work authorization issued by the "
        "U.S. government, including authorization under the DACA program.)",
        "Yes", "Yes", "Yes",
    ),
    (
        "Are you a citizen or national of the United States, or an alien lawfully admitted for permanent residence, or an alien "
        "authorized by US Citizenship and Immigration Services to work in the United States?",
        "Yes", "Yes", "Yes",
    ),
    ("Are you a U.S citizen or authorized to work in the US?", "Yes", "Yes", "Yes"),
    # Plain citizenship and term questions.
    ("Are you a U.S. citizen?", "No", "Yes", "No"),
    ("Are you a Permanent Resident or U.S. Citizen?", "No", "Yes", "Yes"),
    ("Are you a Citizen or Permanent Resident?", "No", "Yes", "Yes"),
    ("Are you a lawful permanent resident of the United States?", "No", "No", "Yes"),
    ("Are you a U.S. Person? This includes a U.S. Citizen. It also includes a lawful permanent resident.", "No", "Yes", "Yes"),
    (
        "3. Are you currently legally authorized to work for Example Co on a full-time basis for an indefinite period of time "
        "in the country where this role is based?",
        "No", "Yes", "Yes",
    ),
    ("Do you have unrestricted work authorization in the U.S?", "No", "Yes", "Yes"),
    ("Can you, after employment, submit verification of your unrestricted legal right to work in the United States?", "No", "Yes", "Yes"),
    # Plain authorization stays Yes for all.
    ("Are you legally authorized to work in the United States?", "Yes", "Yes", "Yes"),
    # Status questions, whatever came before. Schwab, Mercury 1f100e4f answered No.
    ("If you will require sponsorship, do you currently hold Optional Practical Training (OPT)?", "Yes", "No", "No"),
    ("Do you currently hold OPT?", "Yes", "No", "No"),
    ("Are you currently in F-1 Visa status? (An F-1 Visa is granted to foreign students attending school)", "Yes", "No", "No"),
    ("Are you currently in a period of Optional Practical Training (OPT)?", "Yes", "No", "No"),
    ("Do you currently hold an H, L, E, J, or F nonimmigrant visa (examples: H-1B, H-4, J-1, F-1, etc.)?", "Yes", "No", "No"),
    ("Do you currently hold H-1B status?", "No", "No", "No"),
    # Sponsorship by the employer's own definition: Yes for OPT, the plain fact for everyone else.
    ("Do you now, or will you in the future, require sponsorship (e.g., H-1B visa, EAD, etc.) to work legally for the company?", "Yes", "No", "No"),
    ("Will you now or in the future require sponsorship for employment visa status (e.g. H-1B, O-1, TN, F-1 CPT letter or STEM OPT Training Plan signoff)?", "Yes", "No", "No"),
    (
        "Will you now require immigration sponsorship by our company (e.g., H-1B, E-3, or any immigration work authorization "
        "requiring a written submission from the company to a government agency)?",
        "Yes", "No", "No",
    ),
    (
        "Will you now or in the future require sponsorship for an employment visa? Specifically, this includes completing any "
        "employer forms or verifications required by a school, program sponsor, or government agency.",
        "Yes", "No", "No",
    ),
    ("Will you require support to complete ICE Form I-983 or other sponsorship?", "Yes", "No", "No"),
    # Intel Q11: the form tells F-1 holders to answer Yes.
    (
        "11)* Do you now, or in the future, require Example to sponsor you for a visa to work in the United States? NOTE: "
        "Individuals with work authorization issued pursuant to their student visa (e.g., F-1, J-1, M-1) should answer “Yes”.",
        "Yes", "No", "No",
    ),
    # The plain question keeps the owner's saved fact, whatever the status.
    ("Will you now or in the future require sponsorship?", "No", "No", "No"),
    ("Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status)?", "No", "No", "No"),
]


@pytest.mark.parametrize("label,opt,citizen,green", AUDITED)
def test_audited_wordings(label, opt, citizen, green):
    assert answer(label, OPT) == opt
    assert answer(label, CITIZEN) == citizen
    assert answer(label, GREEN_CARD) == green


def test_the_plain_fact_is_the_one_setting_for_the_plain_question():
    plain = "Will you now or in the future require visa sponsorship?"
    assert answer(plain, OPT) == "No"
    assert answer(plain, {**OPT, "requires_future_sponsorship_US": True}) == "Yes"
    # The definition does not depend on it.
    defined = "Will you require sponsorship (e.g., OPT, H-1B)?"
    assert answer(defined, {**OPT, "requires_future_sponsorship_US": True}) == "Yes"
    assert answer(defined, OPT) == "Yes"


def test_a_definition_that_excludes_opt_is_plain():
    assert answer("Will you require sponsorship (excluding OPT)?", OPT) == "No"
    assert answer("Will you require sponsorship for a visa other than OPT?", OPT) == "No"
    assert answer("Do not select Yes if you are only on OPT. Will you require visa sponsorship?", OPT) == "No"
    # Someone else's card.
    assert answer("Will you require visa sponsorship (e.g., H-1B visa, H-4 EAD, etc.)?", OPT) == "No"


def test_other_statuses_read_their_own_name():
    assert answer("Do you currently hold H-1B status?", H1B) == "Yes"
    assert answer("Do you currently hold OPT?", H1B) == "No"
    assert answer("Are you a U.S. citizen?", H1B) == "No"
    assert answer("Do you have the indefinite right to work in the United States?", H1B) == "No"


def test_an_unrecorded_stem_extension_is_left_open():
    assert answer("Are you on STEM OPT?", OPT) is None
    assert answer("Are you on STEM OPT?", {**OPT, "current_immigration_status_US": "STEM OPT"}) == "Yes"


@pytest.mark.parametrize(
    "label",
    [
        "Are you a protected veteran?",
        "Are you currently a member of the U.S. National Guard or Reserves?",
        "Are you an Indian Citizen",
        "Are you a citizen or permanent resident of Iran, Syria, North Korea, or Cuba?",
        "Do you hold Legal Permanent Resident Status in a country outside the US?",
        "Are you at least 18 years of age? (If not, you may be required to furnish a work permit).",
        "Do you meet the minimum age requirement for unrestricted employment in the country for which you are applying?",
        "What is your permanent legal address?",
        "In what countries do you have the unrestricted right to work?",
        "Do you have any relatives who work here?",
        "Are you a Citizen or Permanent Resident of the country where this role is located?",
        "Do you currently have an active immigration case (ex H-1B extension, green card)?",
        "If you currently hold OPT, are you eligible for a 24-month extension of your OPT, based upon a STEM degree?",
        "Do you need reasonable accommodation to apply at Visa?",
        "Do you currently have the legal right to work in Romania on a permanent basis without requiring sponsorship?",
    ],
)
def test_other_questions_are_not_touched(label):
    assert answer(label, OPT) is None


def test_silent_facts_answer_nothing():
    assert answer("Are you a U.S. citizen?", {}) is None
    assert answer("Will you require sponsorship?", {}) is None
    assert answer("Do you have the indefinite right to work in the US?", {"work_authorization_US": True}) is None


def test_offered_sentences_are_chosen_truthfully():
    sentences = ["No — I am a U.S. citizen, permanent resident or otherwise", "Yes — I will NOW need sponsorship", "Yes — I will need FUTURE sponsorship"]
    label = "Will you now or in the future (1-4+ yrs) require employer-sponsored immigration support (such as an H-1B, OPT, or green card)?"
    assert answer(label, OPT, sentences) == sentences[2]  # Lightcast: never the option claiming citizenship.
    assert answer(label, CITIZEN, sentences) == sentences[0]
    assert answer(label, GREEN_CARD, sentences) == sentences[0]
    picker = ["Yes, I am on an F1 Visa/CPT/OPT", "Yes, I am on an H1B Visa", "No, I do not require sponsorship", "No, I am on F1 Visa/CPT/OPT", "Other"]
    assert answer("Do you now OR in the future require visa sponsorship?", OPT, picker) == picker[3]
    assert answer("Are you legally eligible to work permanently in the country?", OPT, ["Yes", "Currently eligible to work, but will require sponsorship", "No - I require sponsorship"]) == (
        "Currently eligible to work, but will require sponsorship"
    )
    assert answer("Are you any of the following?", OPT, ["A United States Citizen", "A lawful permanent resident", "A protected individual", "Other"]) == "Other"
    assert answer("Are you any of the following?", CITIZEN, ["A United States Citizen", "A lawful permanent resident", "A protected individual", "Other"]) == "A United States Citizen"
    status = ["I am a U.S. citizen or permanent resident", "I currently hold an H-1B visa", "I currently hold OPT and will require H-1B sponsorship", "I currently hold STEM OPT and will require H-1B sponsorship"]
    assert answer("Are you legally authorized to work in the U.S.?", OPT, status) == status[2]


def test_a_status_is_never_the_answer_to_a_yes_no_question():
    label = "Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status)?"
    verdict = decide(field(label, [], "text"), OPT)
    assert verdict.value == "No"
    assert not holds("F-1 OPT", verdict)
    assert holds("No", verdict)


def test_wording_may_differ_but_not_polarity():
    verdict = decide(field("Are you a U.S. citizen?"), OPT)
    assert holds("NO", verdict) and holds("No, I am not a U.S. citizen", verdict)
    assert not holds("Yes", verdict)
    authorized = decide(field("Are you legally authorized to work in the United States?", ["I am authorized for any employer", "I require sponsorship"]), OPT)
    assert holds("I am authorized for any employer", authorized)


def test_false_status_claims():
    assert false_claim("(a) U.S. citizen or national of the United States", OPT)
    assert false_claim("NO, I am a U.S. Citizen, Permanent Resident, or otherwise permanently authorized", OPT)
    assert not false_claim("(f) Other (please explain)", OPT)
    assert not false_claim("Not a U.S. citizen or lawful permanent resident", OPT)
    assert not false_claim("No", OPT)
    assert not false_claim("U.S. citizen or permanent resident", GREEN_CARD)
    assert false_claim("U.S. citizen", GREEN_CARD)
    assert not false_claim("U.S. citizen", CITIZEN)
    assert not false_claim("U.S. citizen", {})


def test_one_standing_at_a_time():
    assert standing(OPT).settled is True and "citizen" in standing(OPT).denied
    assert standing(GREEN_CARD).settled is False and "citizen" in standing(GREEN_CARD).denied
    assert "national" in standing(CITIZEN).held
    assert not standing({"current_immigration_status_US": "Previously on OPT"}).names


def resolver(service, facts):
    return Resolver(Profile(facts=facts), service.db, service.config)


def test_resolver_answers_before_any_saved_answer_or_repair(service):
    from jobpilot.answers import normalize

    label = "Are you legally authorized to work in the U.S. as a U.S. citizen or Fully Naturalized U.S. Citizen?"
    r = resolver(service, OPT)
    r.job = {"id": "job-1"}
    service.db.set_setting("repair_overrides:job-1", {normalize(label): {"value": "Yes, I am", "run_id": "r"}})
    got = r.local(field(label))
    assert got.value == "No"
    assert got.disposition == "answer" and "fact:us_citizen" in got.evidence_ids
    assert "Work status rule" in got.reason


def test_the_status_rule_leaves_yes_no_boxes_alone(service):
    r = resolver(service, OPT)
    box = field("Do you currently require sponsorship for employment visa status? If so, please explain.", [], "textarea")
    got = r._local({**box, "own_label": box["label"]})
    assert not (got and got.value == "F-1 OPT")
    asked = field("What is your current immigration status?", [], "text")
    assert r._local({**asked, "own_label": asked["label"]}).value == "F-1 OPT"


@pytest.mark.parametrize(
    "label",
    [
        "Will you now or in the future require sponsorship for an employment visa for any of the posted locations "
        "(for example, if you are on a temporary visa)?",
        "Will you now or in the future require sponsorship for employment (including but not limited to F1, OPT/CPT (with EAD) visas)?",
        "Will you now or in the future require immigration sponsorship from the company to work in the United States? "
        "This includes, but is not limited to, H-1B, F-1 OPT/CPT, STEM OPT extensions, TN, E-3 and O-1.",
        "Will you now or in the future require sponsorship to work in the US? (This includes work authorization that expires in the next 2-3 years.)",
        "Will you require visa sponsorship at some time in the future to continue to work in the U.S. (e.g. your current work "
        "authorization will expire on some future date and you would need us to obtain a new form of work authorization)?",
        "Will you need us to sponsor you for work authorization, or for a renewal of any current work authorization that may expire in the future?",
        "Will you now or in the future require sponsorship or work authorization in the country you are applying for? "
        "*Please select yes if you currently have work authorization outside of permanent residence/citizenship",
        'Will you now or in the future require immigration related sponsorship? *If you possess U.S. Citizenship, a green card, '
        'or any type of visa that definitely will NOT require future sponsorship, please answer "no". If you possess a type of '
        'work authorization which MAY later require sponsorship, please select "yes".',
        "Do you now or in the future require any type of immigration filing or visa sponsorship to maintain work authorization, "
        "including sponsorship by us for any renewals/extensions or permanent residency?",
        "Will you now or in the future require us to file, petition for, sponsor, support, provide documentation for, or "
        "otherwise participate in an immigration or work authorization matter in order for you to begin or continue employment?",
        "Will you now, or in the future, require sponsorship to work in the United States (for example if you currently have a F, J, H or L visa status)?",
    ],
)
def test_a_definition_reaching_time_limited_status_is_answered_for_it(label):
    assert answer(label, OPT) == "Yes"
    assert answer(label, CITIZEN) == "No"


@pytest.mark.parametrize(
    "label",
    [
        "Will you now or in the future require any visa sponsorships or transfers for employment in the US?",
        "Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status)?",
        "Will you now or in the future require visa transfer or sponsorship? (if yes, please share more details on the kind of visa you have, when it expires)",
        "Will you at any time need us to sponsor you for a visa? Answer no below if you can renew employment authorization without our sponsorship.",
        "Will you now or in the future require sponsorship by the company to attain or maintain your employment eligibility?",
        "Will you now or in the future require visa sponsorship to maintain work authorization?",
        "Will you now or in the future require visa sponsorship for employment here (e.g., H1B, O1, TN, J1)? NOTE: If you are "
        "currently sponsored by another employer for your work authorization, you may require sponsorship when changing employers.",
        "Will you now or in the future require sponsorship? For the purposes of this question, sponsorship means an H-1B visa, an O-1 visa, or TN status.",
    ],
)
def test_the_plain_question_still_follows_the_saved_fact(label):
    assert answer(label, OPT) == "No"


def test_authorization_that_excludes_practical_training_is_no_for_opt():
    label = "Are you authorized to work in the United States? This does not include practical training."
    assert answer(label, OPT) == "No"
    assert answer(label, CITIZEN) == "Yes"


def test_now_and_in_the_future_for_any_employer_is_not_time_limited_status():
    label = "Are you legally authorized to work in the US now and in the future for any employer without visa sponsorship?"
    assert answer(label, OPT) == "No"
    assert answer(label, CITIZEN) == "Yes"


def test_not_limited_to_is_no_negation_but_excluding_still_is():
    assert answer("Will you require sponsorship (excluding OPT)?", OPT) == "No"
    assert answer("Will you require sponsorship (including but not limited to OPT)?", OPT) == "Yes"
