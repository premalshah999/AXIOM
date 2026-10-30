"""Citizenship and clearance requirements are public hiring restrictions, checked against explicit applicant facts.

Two link-only jobs said "U.S. citizenship is required" in text the engine never read; the applicant is on F-1 OPT and the
engine spent days on password resets for them (2026-10-30). Wordings below are the employers' own."""

import pytest

from jobpilot.eligibility import check_eligibility
from jobpilot.schemas import Profile

NOT_CITIZEN = {"us_citizen": False, "current_immigration_status_US": "F-1 OPT", "has_security_clearance": False}


def check(text, facts=None, **kwargs):
    return check_eligibility(text, Profile.model_validate({"facts": NOT_CITIZEN if facts is None else facts}), **kwargs)


@pytest.mark.parametrize(
    "text,subject",
    [
        ("U.S. citizenship is required.", "citizenship"),
        ("U.S. Citizenship required", "citizenship"),
        ("Candidates must be a U.S. citizen.", "citizenship"),
        ("Only U.S. citizens will be considered.", "citizenship"),
        ("This position requires United States citizenship.", "citizenship"),
        ("Must be able to obtain and maintain a Secret level DoD security clearance", "security_clearance"),
        ("Must obtain and maintain a U.S. Government Security Clearance", "security_clearance"),
        ("Active TS/SCI clearance required.", "security_clearance"),
    ],
)
def test_the_employers_wording_conflicts_with_a_non_citizen(text, subject):
    found = check(f"About the job. {text} Apply now.")
    assert [f.subject for f in found.conflicts] == [subject]
    assert text.rstrip(".") in found.conflicts[0].quote
    assert found.conflicts[0].fact_ids


def test_a_citizen_meets_the_requirement_and_unknown_facts_ask():
    assert not check("U.S. citizenship is required.", {"us_citizen": True}).conflicts
    asked = check("U.S. citizenship is required.", {})
    assert not asked.conflicts and [f.subject for f in asked.uncertainties] == ["citizenship"]


@pytest.mark.parametrize(
    "text",
    [
        "U.S. citizenship is not required.",
        "No U.S. citizenship required.",
        "We do not require U.S. citizenship.",
        "Equal opportunity employer: we hire regardless of citizenship.",
        "A security clearance is a plus.",
        "Security clearance preferred but not required.",
        "U.S. citizen or authorized to work in the U.S.",
    ],
)
def test_wording_that_does_not_close_the_job_is_no_conflict(text):
    assert not check(text).conflicts


def test_citizen_or_permanent_resident_conflicts_with_a_nonimmigrant_status():
    assert [f.subject for f in check("Must be a U.S. citizen or permanent resident.").conflicts] == ["citizenship"]
    assert not check("Must be a U.S. citizen or permanent resident.", {"permanent_resident": True}).conflicts


def test_a_clearance_the_applicant_must_already_hold_is_checked_against_the_clearance_fact():
    facts = {"has_security_clearance": False}
    assert [f.subject for f in check("Must hold an active Secret security clearance.", facts).conflicts] == [
        "security_clearance"
    ]
    assert not check("Must hold an active Secret security clearance.", {"has_security_clearance": True}).conflicts


def test_a_negation_does_not_reach_across_a_clause():
    text = "Must obtain and maintain a U.S. Government Security Clearance, but not required on day one; U.S. Citizenship required."
    assert any(f.subject == "citizenship" and f.verdict == "unmet" for f in check(text, NOT_CITIZEN).findings)
    # Inside one clause the negation still stands.
    assert not any(f.subject == "citizenship" for f in check("U.S. citizenship is not required for this role.", NOT_CITIZEN).findings)
