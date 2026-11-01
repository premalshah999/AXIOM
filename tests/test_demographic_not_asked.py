"""A demographic answer goes only to a question that asks it. 1Password's "Other than your ethnicity, gender, and disability
status (survey below), is there anything you would like to share ...?" was answered "Male"; Central Hudson's "list ...
activities and offices held: (Exclude those which would reveal ... sexual orientation ...)" was answered with the applicant's
orientation (2026-11-01); "How do you pronounce your name?" was answered "He/him"."""

import pytest

from jobpilot.answers.demographics import DEMOGRAPHIC, demographic_topic


@pytest.mark.parametrize("label", [
    "Helping you do your best means a lot to us. Other than your ethnicity, gender, and disability status (survey below), "
    "is there anything you would like to share with us, or information to help us accommodate you?",
    "Please list professional or civic activities and offices held: (Exclude those which would reveal sex, race, religion, "
    "national origin, age, ancestry, sexual orientation, physical or mental disability or other protected status).",
    "How do you pronounce your name?",
    "Name Pronunciation | Please share how to pronounce your name.",
    "Other than time off for reasons related to your religion, a disability, or a medical condition, are there any days "
    "or times when you are unavailable to work?",
    "ExampleSoft is an equal employment opportunity employer. Decisions are based on merit, and not on race, color, sex, "
    "(including pregnancy and gender identity), national origin, or veteran status. To acknowledge, please enter your full name below.",
])
def test_a_subject_set_aside_is_not_asked(label):
    assert not DEMOGRAPHIC.search(label)
    assert demographic_topic(label) is None


@pytest.mark.parametrize("label,topic", [
    ("Gender", "gender"),
    ("Veteran Status", "veteran"),
    ("What are your pronouns?", "pronouns"),
    ("Preferred Pronouns (e.g., He/Him, She/Her)", "pronouns"),
    ("What is your Sexual Orientation?", "sexual_orientation"),
    ("Regardless of your Ethnicity, please select all race(s) with which you identify.", "race"),
    ("Are you Hispanic or Latino? (A person of Cuban or other Spanish culture or origin, regardless of race)", "hispanic"),
    ("A person of Cuban, Mexican, Puerto Rican, or other Spanish culture or origin regardless of race.", "hispanic"),
    ("We are a contractor subject to VEVRAA, and consider applicants without regard to sexual orientation. "
     "If you believe you belong to any of the categories of protected veterans, please select below.", "veteran"),
])
def test_a_question_that_asks_still_reads(label, topic):
    assert DEMOGRAPHIC.search(label)
    assert demographic_topic(label) == topic


def resolver(service, **profile):
    from jobpilot.answers import Resolver
    from jobpilot.schemas import Profile

    return Resolver(Profile(**profile), service.db, service.config, job={"ats": "custom", "url": "https://example.test/job"})


def field(label, options=(), type="select"):
    return {"id": "q", "label": label, "type": type, "required": False, "value": "", "valid": True,
            "group": "", "section": "", "options": list(options)}


def test_a_label_that_is_only_the_equal_opportunity_statement_still_reads_its_choices(service):
    statement = ("ExampleSoft is an Equal Opportunity Employer and does not discriminate against any individual on the basis "
                 "of race, religion, color, gender, national origin, age, disability, veteran status or sexual orientation.")
    answer = resolver(service).local(field(statement, ["Asian", "White", "I do not wish to self identify"]))
    assert answer is not None and answer.value == "I do not wish to self identify"


def test_the_applicants_gender_stays_out_of_a_box_that_sets_it_aside(service):
    rules = {"demographics": {"gender": {"decline_first": False, "answer": "Male", "when_missing": "closest"}}}
    r = resolver(service, answer_rules=rules)
    assert r.local(field("Gender", ["Male", "Female"])).value == "Male"
    label = "Other than your ethnicity, gender, and disability status (survey below), is there anything you would like to share?"
    answer = r.local(field(label, type="textarea"))
    assert answer is None or answer.value != "Male"
