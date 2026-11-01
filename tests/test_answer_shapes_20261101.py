"""False answers read back from confirmed applications (2026-11-01 audit of every saved answers.json): a stored day answering
"Today's Date", the applicant's degree answering a school, a major and a parent's schooling, "No" and the applicant's own name
in a referrer's box, "Yes" in a "where would you relocate" box. The applicant is synthetic."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jobpilot.answers import Resolver
from jobpilot.answers.text import answer_key
from jobpilot.schemas import Profile


def field(label, kind="text", options=None, **extra):
    return {"id": "q", "label": label, "type": kind, "required": True, "group": "", "section": "", "value": "",
            "valid": True, "options": options or [], **extra}


def profile(**extra):
    return Profile(
        name="Alex Example",
        education=[
            {"school": "Example University", "degree": "Master of Science", "field_of_study": "Data Science",
             "start": "2024-08", "end": "2026-05"},
            {"school": "Sample College", "degree": "BE", "field_of_study": "Computer Engineering",
             "start": "2020-06", "end": "2024-05"},
        ],
        facts={"referred_by_employee": False, "knows_employee_at_company": False, "willing_to_relocate": True,
               "relocation_locations": "Anywhere", "has_relatives_at_employer": False},
        **extra,
    )


def local(service, prof, f):
    return Resolver(prof, service.db, service.config).local(f)


@pytest.mark.parametrize("label,part", [("Today's Date (Day)", "day"), ("Today's Date Month", "month"), ("Date Signed Year", "year")])
def test_a_stored_day_never_answers_todays_date(service, label, part):
    kind = "select" if part != "year" else "text"
    options = [str(d) for d in range(1, 32)] if part == "day" else (
        ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        if part == "month" else [])
    f = field(label, kind, options)
    stale = {"day": "22", "month": "March", "year": "2019"}[part]
    prof = profile(approved_answers={answer_key({**f, "employer": ""}): stale})
    answer = local(service, prof, f)
    today = datetime.now(ZoneInfo(service.config.timezone)).date()
    assert answer is not None and answer.disposition == "answer"
    assert answer.value != stale and "policy:today" in answer.evidence_ids
    assert {"day": str(today.day), "year": str(today.year)}.get(part, today.strftime("%B")) in {answer.value, answer.value.lstrip("0")}


def test_the_institution_of_the_highest_education_is_the_school_not_the_degree(service):
    f = field("The name of the institution where you completed your highest level of education?")
    answer = local(service, profile(), f)
    assert answer.disposition == "answer" and answer.value == "Example University"


def test_the_area_of_study_of_the_highest_education_is_the_major(service):
    f = field("In which area of study was the major coursework of your highest level of education?")
    assert local(service, profile(), f).value == "Data Science"


def test_a_city_box_inside_a_highest_education_group_is_not_the_degree(service):
    f = field("City", group="Highest Level of Education", section="Education (1)")
    answer = Resolver(profile(), service.db, service.config).local(f)
    assert answer is None or answer.value != "Master of Science"


def test_an_aside_about_education_level_does_not_make_a_school_box_a_degree_box(service):
    f = field("School (If you select 'GED' or 'High School' in Education Level, please choose 'Others' here)")
    answer = local(service, profile(), f)
    assert answer is not None and answer.value != "Master of Science"
    assert answer.value == "Example University"


def test_a_parents_education_is_not_the_applicants_degree(service):
    f = field("Highest Education Level of Caregiver/Parent", "combobox", ["Masters or Equivalent", "Prefer not to say"])
    answer = local(service, profile(), f)
    assert answer.disposition == "answer" and answer.value == "Prefer not to say"
    f = field("Highest Education Level of Caregiver/Parent", "combobox", ["Masters or Equivalent"])
    assert local(service, profile(), f).disposition != "answer"


def test_a_level_menu_takes_graduate_school_not_other(service):
    f = field("What is your highest level of education (grade completed)?", "dropdown",
              ["High School/GED", "College/University", "Graduate School", "Other"])
    assert local(service, profile(), f).value == "Graduate School"


@pytest.mark.parametrize("label", ["If employee referral, provide name", "Referred by name",
                                   "1. Were you referred by an employee if yes, please enter their name."])
def test_a_referrers_name_box_is_not_answered_no(service, label):
    answer = local(service, profile(), field(label))
    assert answer is None or answer.value not in {"No", "Yes", "true", "false"}


def test_where_would_you_relocate_is_a_place_not_yes(service):
    answer = local(service, profile(), field("If so, where would you be open to relocating"))
    assert answer is not None and answer.value == "Anywhere"


def test_another_persons_name_is_not_the_applicants(service):
    optional = {**field("What's their name or email address?"), "required": False}
    assert local(service, profile(), optional).disposition == "skip"
    answer = local(service, profile(), field("If yes to the above question, what is the current employee's name"))
    assert answer.value == "N/A"
    assert local(service, profile(), field("Referral email", "email")).disposition != "answer"


def test_the_applicants_own_name_box_is_still_answered(service):
    assert local(service, profile(), field("Full name")).value == "Alex Example"
