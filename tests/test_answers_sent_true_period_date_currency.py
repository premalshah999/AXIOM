"""Answers read back from confirmed applications that were false (2026-11-01). A finished degree sent as starting today:
iCIMS's "Education Start Date Month" took availability because its row is named only in the section. $105,000 an hour: an
hourly pay box took the annual figure. A salary sent in Australian dollars: a currency menu took the amount, found nothing
and recovery picked the first row. The applicant is synthetic."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.control_agent import answer_is_amount_not_choice
from jobpilot.schemas import Answer, Profile

MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def profile(**extra):
    return Profile(
        availability="Immediately",
        education=[
            {"school": "Example University", "degree": "Master of Science", "field_of_study": "Data Science",
             "start": "2024-08", "end": "2026-05"},
            {"school": "Sample College", "degree": "BE", "field_of_study": "Computer Engineering",
             "start": "2020-06", "end": "2024-05"},
        ],
        **extra,
    )


def education_date(part, row, options=None):
    return {"id": "q", "label": f"Education Start Date {part}", "type": "select" if options else "text", "required": True,
            "value": "", "valid": True, "group": "Start Date", "section": f"Education ({row}) Start Date",
            "options": options or []}


@pytest.mark.parametrize(
    "part,row,options,expected",
    [("Month", 1, MONTHS, "Aug"), ("Year", 1, None, "2024"), ("Month", 2, MONTHS, "Jun"), ("Year", 2, None, "2020")],
)
def test_an_education_start_date_is_the_rows_own_date(service, part, row, options, expected):
    answer = Resolver(profile(), service.db, service.config).local(education_date(part, row, options))
    assert answer.disposition == "answer" and str(answer.value) == expected


def test_an_education_start_day_is_the_first_of_the_month(service):
    days = [str(d) for d in range(1, 32)]
    answer = Resolver(profile(), service.db, service.config).local(education_date("Day", 1, days))
    assert str(answer.value) == "1"


def test_availability_never_answers_an_education_start_date(service):
    resolver = Resolver(Profile(), service.db, service.config)
    question = {"id": "q", "label": "Education Start Date Year", "type": "text", "required": True, "options": []}
    answer = Answer(field_id="q", disposition="answer", value="2026", evidence_ids=["fact:availability"])
    result = resolver._validate_answer(question, answer, {"fact:availability": "Immediately"}, {}, {})
    assert result.disposition != "answer" or result.value != "2026"
    bare = {"id": "q", "label": "Education Start Date Year", "type": "text", "required": True, "options": [], "group": "",
            "section": "", "value": "", "valid": True}
    local = Resolver(profile(), service.db, service.config).local(bare)
    assert local is None or "fact:availability" not in local.evidence_ids


def test_when_can_you_start_still_takes_today(service):
    f = {"id": "q", "label": "Earliest start date", "type": "date", "required": True, "group": "", "section": "",
         "value": "", "valid": True, "options": []}
    answer = Resolver(profile(), service.db, service.config).local(f)
    assert answer.disposition == "answer" and answer.evidence_ids == ["fact:availability"]


def pay(label, kind="text"):
    return {"id": "q", "label": label, "type": kind, "required": True, "group": "", "section": "", "value": "",
            "valid": True, "options": []}


@pytest.mark.parametrize(
    "label,expected",
    [
        ("What are your hourly salary expectations in USD?", "50"),
        ("Salary expectation per hour", "50"),
        ("What are your monthly salary expectations in USD?", "8750"),
        ("What is your desired salary or hourly wage?", "105000"),
        ("What is your expected hourly or base salary?", "105000"),
        ("Desired Salary/Hourly Rate", "105000"),
        ("What is your desired salary? (enter an annual or hourly rate amount)", "105000"),
        ("What is your desired annual salary?", "105000"),
        ("What is your desired salary?", "105000"),
        ("What is your salary expectation in THB monthly?", "105000"),
    ],
)
def test_a_pay_box_takes_the_amount_in_its_own_period(service, label, expected):
    answer = Resolver(Profile(salary_expectation="105000"), service.db, service.config).local(pay(label))
    assert answer.disposition == "answer" and str(answer.value).replace(",", "") == expected


def test_an_hourly_box_from_a_saved_range_is_its_midpoint_in_hours(service):
    profile_ = Profile(autonomous=True, answer_rules={"salary_stance": "$90,000 - $120,000"})
    answer = Resolver(profile_, service.db, service.config).local(pay("What is your desired hourly pay?"))
    assert answer.disposition == "answer" and str(answer.value) == "50"


CURRENCY = "Salary Expectation - Currency * Select an option"


def test_a_currency_menu_with_unloaded_rows_takes_the_dollar_for_a_us_posting(service):
    resolver = Resolver(Profile(salary_expectation="105000"), service.db, service.config, job={"location": "Austin, TX"})
    answer = resolver.local(pay(CURRENCY, "combobox"))
    assert answer.disposition == "answer" and answer.value == "US Dollar"


def test_a_currency_menu_takes_no_amount_for_a_posting_elsewhere(service):
    resolver = Resolver(Profile(salary_expectation="105000"), service.db, service.config, job={"location": "Sydney, Australia"})
    answer = resolver.local(pay(CURRENCY, "combobox"))
    assert answer is None or "105" not in str(answer.value)


def test_recovery_cannot_trade_an_amount_for_a_name():
    assert answer_is_amount_not_choice("105000", "Australian Dollar")
    assert answer_is_amount_not_choice("$105,000", "Australian Dollar")
    assert not answer_is_amount_not_choice("105000", "$100,000 - $124,999")
    assert not answer_is_amount_not_choice("US Dollar", "USD - United States Dollar")


WORKED = [{"company": "ExampleSoft", "title": "Engineer", "start": "2024-01", "end": "", "current": True}]


@pytest.mark.parametrize(
    "label",
    ["Who is your current or previous employer?", "What company are you currently employed at or have worked at most recently?"],
)
def test_a_question_for_the_employers_name_gets_a_name_not_no(service, label):
    profile_ = Profile(work=WORKED, facts={"previously_employed_here": False})
    answer = Resolver(profile_, service.db, service.config).local(pay(label))
    assert answer.disposition == "answer" and answer.value == "ExampleSoft"


def test_the_reason_for_leaving_an_employer_is_not_the_employers_name(service):
    profile_ = Profile(work=WORKED, facts={"previously_employed_here": False})
    label = "What is the reason for leaving your current/previous employer?"
    answer = Resolver(profile_, service.db, service.config).local(pay(label))
    assert answer is None or answer.value != "ExampleSoft"


def test_a_who_question_takes_no_saved_yes_or_no(service):
    profile_ = Profile(facts={"referred_by_employee": False})
    answer = Resolver(profile_, service.db, service.config).local(pay("Who were you referred by?"))
    assert answer is None or str(answer.value).strip().lower() not in {"no", "false"}


def test_the_former_employee_question_still_takes_the_saved_no(service):
    profile_ = Profile(facts={"previously_employed_here": False})
    f = {**pay("Are you a current or former employee of Example?"), "type": "select", "options": ["Yes", "No"]}
    answer = Resolver(profile_, service.db, service.config).local(f)
    assert answer.disposition == "answer" and answer.value == "No"
