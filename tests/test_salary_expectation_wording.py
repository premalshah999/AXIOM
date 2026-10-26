""""Current Salary Expectation" asks the applicant's expectation, not pay already earned. Prosidian's
"14 - What is Your Current Salary Expectation Per Year" reached the owner as a current-salary question although the
profile holds the expectation (2026-10-25)."""

import pytest

from jobpilot.answers import NOT_DESIRED, Resolver
from jobpilot.schemas import Profile


@pytest.mark.parametrize("label,earned", [
    ("14 - What is Your Current Salary Expectation Per Year (or Effective $/Hr Rate Using 1920 Hrs.)", False),
    ("What is your current desired salary?", False),
    ("What is your current salary?", True),
    ("Previous salary (required)", True),
    ("Most recent base pay", True),
])
def test_expectation_wording_is_not_pay_already_earned(label, earned):
    assert bool(NOT_DESIRED.search(label)) is earned


def test_a_current_salary_expectation_is_answered_from_the_saved_expectation(service):
    resolver = Resolver(Profile(first_name="Alex", salary_expectation="90000"), service.db, service.config,
                        job={"ats": "smartrecruiters", "url": "https://example.test/j"})
    answer = resolver.local({"id": "s", "type": "text", "required": True, "options": [], "maxlength": 200,
                             "label": "14 - What is Your Current Salary Expectation Per Year (or Effective $/Hr Rate "
                                      "Using 1920 Hrs.)"})
    assert answer is not None and "90000" in str(answer.value).replace(",", "")
