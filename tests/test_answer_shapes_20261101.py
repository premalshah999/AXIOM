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


# The full old-against-new replay over every saved field (20,992 distinct) found these regressions in the shape rules above.
@pytest.mark.parametrize(
    "label,kind,options",
    [
        ("What is the highest level of education that you completed at an accredited institution of higher education?",
         "select", ["High School Diploma", "Associate's", "Bachelor's", "Master's", "Doctorate"]),
        ("What is your highest degree completed from an accredited school?", "text", []),
        ("What is the highest level of education you have obtained to date?", "combobox", ["Bachelors Degree", "Masters Degree"]),
        ("What is your highest level of education? If you are currently in school what are you graduating from?",
         "dropdown", ["Bachelors Degree", "Masters Degree"]),
        ("What degree are you working towards? (Or if not currently working towards a degree, what is your highest degree obtained?)",
         "dropdown", ["Bachelors degree", "Masters degree", "Doctoral degree"]),
    ],
)
def test_a_highest_degree_question_with_a_school_or_date_word_after_it_is_still_the_degree(service, label, kind, options):
    answer = local(service, profile(), field(label, kind, options))
    assert answer is not None and answer.disposition == "answer"
    assert "University" not in answer.value and answer.value != "Other"
    assert "Master" in answer.value


@pytest.mark.parametrize(
    "label",
    [
        "How did you hear about this position (If by employee please enter their name)",
        "Please indicate how you heard about this position. If you were referred, please write the employee's name here.",
        "If you chose 'referred by an employee', 'Social Media', 'Job Board' or 'Other', please specify here:",
    ],
)
def test_a_how_did_you_hear_box_is_not_a_referrers_name_box(service, label):
    answer = local(service, profile(), field(label))
    assert answer is None or answer.value != "N/A"


def test_a_state_box_listing_only_the_school_is_not_answered_with_the_school(service):
    """WTW (Oracle) 2026-10-25: the education State's list was the School box's, one entry, and "Maryland" inside
    "University of Maryland, College Park" matched it."""
    school = "Example University of Maryland"
    prof = Profile(name="Alex Example", education=[{"school": school, "degree": "Master of Science", "field_of_study": "Data Science",
                                                    "start": "2024-08", "end": "2026-05"}])
    f = field("State", "combobox", [school], group="Education at " + school)
    answer = local(service, prof, f)
    assert answer is None or answer.value != school
    good = local(service, prof, field("State", "combobox", ["AK", "MD", "VA"], group="Education at " + school))
    assert good is not None and good.value == "MD"


def test_a_referrers_name_box_that_quotes_the_source_question_is_still_a_name_box(service):
    """iCapital: 'If "Employee Referral" was selected for "How did you hear about this job?" please type the full name of the
    referring employee. If not, please type in N/A' asks for a name; the source answer ("LinkedIn") is not a name."""
    label = ('If "Employee Referral" was selected for "How did you hear about this job?" please type the full name of the '
             'referring employee. If not, please type in N/A')
    answer = local(service, profile(), field(label))
    assert answer is not None and answer.value == "N/A"
