"""ALTEN's Greenhouse "If yes, please enter visa type" (2026-10-29) offers H1B, TN, F1, E2, L1, L2, OTHER, NA; the
applicant's status is F-1 OPT, and "F-1 OPT" matched none of them, so the combobox never committed."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile
from jobpilot.widgets import best_option, visa_class, visa_option

OFFERED = ["H1B", "TN", "F1", "E2", "L1", "L2", "OTHER", "NA"]


@pytest.mark.parametrize("status", ["F-1 OPT", "F1", "F-1", "f1 opt", "F 1"])
def test_a_status_of_the_offered_class_is_that_option(status):
    assert best_option(OFFERED, status) == "F1"
    assert best_option([{"text": o} for o in OFFERED], status) == {"text": "F1"}


@pytest.mark.parametrize(
    "options",
    [["F-1", "J-1", "H-1B"], ["Student Visa (F-1)", "Work Visa (H-1B)"], ["F1 OPT", "H1B"], ["F-1 (OPT)", "F-1 (CPT)"]],
)
def test_other_spellings_of_the_same_class(options):
    got = best_option(options, "F-1 OPT")
    assert got is not None and visa_class(got) == "F1" and "CPT" not in got


def test_an_option_claiming_a_qualifier_the_status_lacks_is_never_chosen():
    assert best_option(["F-1 CPT", "H-1B"], "F-1 OPT") is None
    assert visa_option("F-1", ["F-1 STEM OPT", "H-1B"]) is None
    # The plain class and the OPT one are both offered: the status that says OPT takes the OPT row.
    assert visa_option("F-1 OPT", ["F-1", "F-1 OPT", "F-1 CPT"]) == "F-1 OPT"
    # Two equally specific rows are ambiguous, not a guess.
    assert visa_option("F-1 OPT", ["F-1 OPT (3 years)", "F-1 OPT (12 months)"]) is None


def test_a_different_class_is_not_matched():
    assert best_option(["H1B", "L1", "TN"], "F-1 OPT") is None
    assert visa_class("OTHER") is None and visa_class("Option 2") is None


def test_the_local_rule_picks_the_offered_class_for_an_opt_status(service):
    resolver = Resolver(Profile(facts={"current_immigration_status_US": "F-1 OPT"}), service.db, service.config)
    field = {
        "id": "q", "label": "If yes, please enter visa type", "type": "combobox", "options": OFFERED,
        "required": True, "value": "", "valid": True, "section": "", "group": "", "name": "", "automation_id": "", "widget": "",
    }
    answer = resolver.local(field)
    assert answer.value == "F1" and answer.evidence_ids == ["fact:current_immigration_status_US"]
