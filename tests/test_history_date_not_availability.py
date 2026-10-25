"""A history row's date part is that record's date, never the applicant's availability. An Oracle education row with
no heading took "October 2026" for Start Date Month/Year from availability; the site refused the End Date before it
and the run stalled (BNY 2026-10-25)."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.schemas import Answer, Profile

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


@pytest.mark.parametrize("label", ["Start Date Month", "Start Date Year", "End Date Month", "From Date Year"])
def test_availability_never_answers_a_row_date_part(service, label):
    resolver = Resolver(Profile(), service.db, service.config)
    question = {"id": "q", "label": label, "type": "combobox", "required": True,
                "options": MONTHS if label.endswith("Month") else ["2025", "2026"]}
    value = "October" if label.endswith("Month") else "2026"
    answer = Answer(field_id="q", disposition="answer", value=value, evidence_ids=["fact:availability"])
    result = resolver._validate_answer(question, answer, {"fact:availability": "Immediately"}, {}, {})
    assert result.disposition != "answer" or result.value != value


def test_a_start_date_question_still_takes_availability(service):
    resolver = Resolver(Profile(), service.db, service.config)
    question = {"id": "q", "label": "Available start date", "type": "text", "required": True, "options": []}
    answer = Answer(field_id="q", disposition="answer", value="Immediately", evidence_ids=["fact:availability"])
    result = resolver._validate_answer(question, answer, {"fact:availability": "Immediately"}, {}, {})
    assert result.disposition == "answer" and result.value == "Immediately"
