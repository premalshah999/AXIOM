"""A brief a little over its limits is cut, not refused (2026-11-02): refused, it was asked again, refused again, and the whole
Aggressive tailoring failed with "Exceeded maximum retries (1) for output validation". Synthetic data only."""

import pytest
from pydantic import ValidationError

from jobpilot.features.resumes.aggressive.schema import Brief


def test_an_over_long_brief_is_cut_to_its_limits():
    brief = Brief.model_validate({
        "product": "Experiment tooling " * 60,
        "proof_points": ["Ship tests", "Read results", "Explain tradeoffs", "Document reviews"],
        "real_stack": [f"Tool{i}" for i in range(25)],
        "seniority": "Mid-level, " * 50,
        "hard_keywords": [f"Term{i}" for i in range(14)],
    })
    assert len(brief.product) <= 800 and len(brief.seniority) <= 400
    assert not brief.product.endswith(" ") and brief.product.split()[-1] in {"Experiment", "tooling"}
    assert brief.proof_points == ["Ship tests", "Read results", "Explain tradeoffs"]
    assert brief.real_stack == [f"Tool{i}" for i in range(20)] and brief.hard_keywords == [f"Term{i}" for i in range(10)]


def test_too_few_proof_points_are_still_refused():
    with pytest.raises(ValidationError):
        Brief.model_validate({"product": "Reporting tools", "proof_points": ["One", "Two"], "seniority": "Junior"})
