"""Degree catalogs that state a status ("Bachelors in Progress" / "Bachelors Completed"). Lennar's Workday got
"Bachelors in Progress" for a BE finished in June 2024, because it is listed first (2026-10-18). The recorded end
month decides: past means completed, otherwise in progress."""

import pytest

from jobpilot.answers import Resolver, degree_status_options
from jobpilot.forms import SCHOOL_LABEL, SCHOOL_WRITE_SECONDS, FIELD_WRITE_SECONDS
from jobpilot.schemas import Profile

PROFILE = {
    "education": [
        {"school": "University of Michigan, Ann Arbor", "degree": "Master of Science", "field_of_study": "Data Science",
         "start": "2024-08", "end": "2099-05"},
        {"school": "Nanyang Technological University", "degree": "BE",
         "field_of_study": "Information & Communication Technology", "start": "2020-06", "end": "2024-06"},
    ]
}
LENNAR = ["GED", "High School", "Associates in Progress", "Associates Completed", "Bachelors in Progress",
          "Bachelors Completed", "Masters in Progress", "Masters Completed", "Doctorate in Progress", "Doctorate Completed"]


def degree(row):
    return {"id": f"d{row}", "label": "Degree", "type": "dropdown", "group": f"Education {row}", "section": ""}


@pytest.mark.parametrize("row,expected", [(1, "Masters in Progress"), (2, "Bachelors Completed")])
def test_the_recorded_end_month_chooses_completed_or_in_progress(service, row, expected):
    r = Resolver(Profile.model_validate(PROFILE), service.db, service.config)
    assert r.policy_choice(degree(row), LENNAR) == expected


def test_catalogs_without_a_status_are_unchanged(service):
    from datetime import date

    options = ["Bachelor's Degree", "Master's Degree"]
    assert degree_status_options(options, "2024-06", date(2026, 10, 18)) == options
    assert degree_status_options(["Bachelor's (Not completed)", "Bachelor's Degree"], "2024-06", date(2026, 10, 18)) == [
        "Bachelor's Degree"
    ]
    assert degree_status_options(LENNAR, "", date(2026, 10, 18)) == LENNAR


def test_school_searches_get_time_for_their_fallbacks():
    assert SCHOOL_LABEL.search("School or University") and not SCHOOL_LABEL.search("Field of Study")
    assert SCHOOL_WRITE_SECONDS > FIELD_WRITE_SECONDS
