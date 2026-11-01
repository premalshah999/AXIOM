"""Collier Aerospace (Rippling, 2026-10-27) lists "US citizen (required)" among its qualifications. Ranking's citizenship
rule read only "U.S. citizenship required" and "must be a U.S. citizen", so the posting was applied to by a non-citizen
and its "eligible ... through US citizenship?" question went to the owner."""

import pytest

from jobpilot.discovery import ClassifiedFit, QualificationFinding, validate_classification
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


POSTING = "Bachelor's degree required. Build data pipelines in Python."
FACTS = {"us_citizen": False}


def finding(verdict, quote="Bachelor's degree required", fact_ids=()):
    return QualificationFinding(posting_quote=quote, fact_ids=list(fact_ids), verdict=verdict)


def fit_with(*findings, eligible=True):
    return ClassifiedFit(score=85, eligible=eligible, resume_id="r1", reason="Strong fit", hard_requirements=list(findings))


@pytest.mark.parametrize("loose", [
    finding("met", quote="a bachelor's in a related field"),
    finding("met", fact_ids=["fact:made_up"]),
    finding("unknown", quote="degree preferred"),
])
def test_a_finding_the_posting_cannot_support_does_not_skip_a_strong_fit(loose):
    result = validate_classification(fit_with(loose), {"description": POSTING}, Profile(facts=FACTS))
    assert result.eligible is True
    assert "Qualification check" not in result.reason
    assert result.uncertainties == [f'Could not check requirement: "{loose.posting_quote}"']


def test_a_posting_with_no_saved_text_is_not_skipped_for_its_quotes():
    result = validate_classification(fit_with(finding("met")), {"description": ""}, Profile(facts=FACTS))
    assert result.eligible is True


def test_a_claimed_miss_with_a_loose_quote_still_blocks():
    result = validate_classification(
        fit_with(finding("unmet", quote="five years of something")), {"description": POSTING}, Profile(facts=FACTS)
    )
    assert result.eligible is False
    assert "Unsupported qualification evidence" in result.reason


def test_a_loose_quote_never_overrides_a_real_blocker_or_the_models_refusal():
    text = POSTING + " Ability to obtain a Secret clearance."
    blocked = validate_classification(fit_with(finding("met", quote="loose")), {"description": text}, Profile(facts=FACTS))
    assert blocked.eligible is False
    refused = validate_classification(
        fit_with(finding("met", quote="loose"), eligible=False), {"description": POSTING}, Profile(facts=FACTS)
    )
    assert refused.eligible is False
