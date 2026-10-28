"""Collier Aerospace (Rippling, 2026-10-27) lists "US citizen (required)" among its qualifications. Ranking's citizenship
rule read only "U.S. citizenship required" and "must be a U.S. citizen", so the posting was applied to by a non-citizen
and its "eligible ... through US citizenship?" question went to the owner."""

import pytest

from jobpilot.discovery import ClassifiedFit, validate_classification
from jobpilot.schemas import Profile


@pytest.mark.parametrize("text", [
    "Candidates should have: US citizen (required) Bachelor's degree in Computer Science.",
    "ITAR U.S. Citizenship required.",
    "Due to the nature of work performed within this facility, U.S. citizenship is required.",
])
def test_a_citizenship_requirement_rejects_a_non_citizen(text):
    fit = ClassifiedFit(score=85, eligible=True, resume_id="r1", reason="Strong fit")
    result = validate_classification(fit, {"description": text}, Profile(facts={"us_citizen": False}))
    assert result.eligible is False


def test_citizenship_that_is_not_required_never_rejects():
    fit = ClassifiedFit(score=85, eligible=True, resume_id="r1", reason="Strong fit")
    text = "U.S. citizenship is not required for this role; we sponsor visas."
    result = validate_classification(fit, {"description": text}, Profile(facts={"us_citizen": False}))
    assert result.eligible is True
