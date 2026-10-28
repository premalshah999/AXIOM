"""A current contract's end date, for an applicant available immediately, is the date they are free to start.
OmegaHires' required "Current contract end date" (JazzHR datepicker) kept an approved "N/A" that no date box accepts,
and every fill raised; the owner's own answer was today's date (2026-10-27). The applicant is synthetic."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jobpilot.answers import Resolver
from jobpilot.answers.text import answer_key
from jobpilot.schemas import Profile

WORK = [{"company": "Acme Labs", "title": "Engineer", "start": "2025-12", "current": True}]


def field(label, **extra):
    return {"id": "q", "label": label, "type": "text", "widget": "date-text", "date_format": "mdy", "required": True,
            "value": "", "valid": True, "group": "", "section": "", "options": [], **extra}


@pytest.mark.parametrize("label", ["Current contract end date", "Contract End Date",
                                   "When does your current contract end?"])
def test_contract_end_date_is_the_availability_date(service, label):
    f = field(label)
    profile = Profile(availability="Immediately", work=WORK,
                      approved_answers={answer_key({**f, "employer": ""}): "N/A"})
    answer = Resolver(profile, service.db, service.config).local(f)
    today = datetime.now(ZoneInfo(service.config.timezone)).date().strftime("%m/%d/%Y")
    assert answer.disposition == "answer" and answer.value == today
    assert answer.evidence_ids == ["fact:availability"]


def test_without_immediate_availability_the_rule_does_not_guess(service):
    answer = Resolver(Profile(availability="Two weeks", work=WORK), service.db, service.config).local(
        field("Current contract end date"))
    today = datetime.now(ZoneInfo(service.config.timezone)).date().strftime("%m/%d/%Y")
    assert answer is None or answer.value != today


def test_a_history_rows_contract_end_date_is_not_availability(service):
    answer = Resolver(Profile(availability="Immediately", work=WORK), service.db, service.config).local(
        field("Contract end date", group="Work Experience 1", section="Work Experience"))
    assert answer is None or answer.evidence_ids != ["fact:availability"]
