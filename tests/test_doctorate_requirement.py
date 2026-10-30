"""A posting that requires a PhD is refused for a profile without one (2026-10-29/30: two "... (PhD)" graduate
postings were applied to repeatedly for a Master's applicant), and "PhD preferred", "Bachelor's, Master's or PhD" and
"PhD is a plus" stay eligible."""

import pytest

from jobpilot.eligibility import check_eligibility
from jobpilot.features.discovery.graduation import graduation_gate
from jobpilot.schemas import Profile

MASTERS = {"education": [{"school": "Example University", "degree": "Master of Science", "end": "2025-05"}]}
TITLE = "Software Engineer Graduate (AI Compute) - 2027 Start (PhD)"
MINIMUM = (
    "About the role\nBuild compute systems.\nMinimum qualifications:\n"
    "- Completing or have recently completed a PhD in Computer Science or a related field\n"
    "- Strong programming skills\nPreferred qualifications:\n- Publications"
)


def job(title="Software Engineer", description=""):
    return {"title": title, "description": description}


def test_a_title_naming_a_phd_refuses_a_masters_applicant():
    reason = graduation_gate(job(TITLE), MASTERS)
    assert reason.startswith("doctorate_required") and "(PhD)" in reason


def test_minimum_qualifications_requiring_a_phd_refuse_without_a_phd_in_the_title():
    assert graduation_gate(job(description=MINIMUM), MASTERS).startswith("doctorate_required")


@pytest.mark.parametrize(
    "text",
    [
        "Minimum qualifications: Completing or have recently completed a PhD in Computer Science Preferred qualifications: Publications",
        "A PhD in machine learning is required for this role.",
        "Candidates must hold a Ph.D. in physics.",
        "You have a PhD in a quantitative field and two publications.",
        "Requirements\nPh.D. in Computer Science or a related field",
    ],
)
def test_requirement_wording_refuses(text):
    assert graduation_gate(job(description=text), MASTERS).startswith("doctorate_required")


@pytest.mark.parametrize(
    "title,text",
    [
        ("Software Engineer", "PhD preferred."),
        ("Software Engineer", "Bachelor's, Master's or PhD in Computer Science."),
        ("Software Engineer", "Master's degree required; a PhD is a plus."),
        ("Software Engineer", "A PhD is a plus."),
        ("Software Engineer", "Preferred qualifications:\n- PhD in a related field"),
        ("Software Engineer", "Our researchers include PhDs from leading labs."),
        ("Software Engineer", "No PhD required."),
        ("Software Engineer", "Experience with PhD-level research methods."),
        ("Research Scientist (PhD or MS)", ""),
        ("Research Engineer (Master's or PhD)", ""),
        ("Software Engineer (PhD preferred)", ""),
        ("Software Engineer", ""),
    ],
)
def test_preferences_and_alternatives_stay_eligible(title, text):
    assert graduation_gate(job(title, text), MASTERS) == ""


def test_a_profile_holding_or_pursuing_a_doctorate_is_eligible():
    pursuing = {"education": [*MASTERS["education"], {"school": "Example University", "degree": "PhD in Computer Science", "end": "2028-05"}]}
    assert graduation_gate(job(TITLE, MINIMUM), pursuing) == ""


def test_a_profile_without_recorded_education_is_undecided_here():
    assert graduation_gate(job(TITLE), {"education": []}) == ""


def test_admission_conflicts_with_a_masters_profile_and_not_with_a_phd_profile():
    profile = Profile.model_validate(MASTERS)
    check = check_eligibility(MINIMUM, profile, title=TITLE)
    assert [f.subject for f in check.conflicts] == ["degree"]
    assert "PhD" in check.conflicts[0].quote
    pursuing = Profile.model_validate(
        {"education": [*MASTERS["education"], {"school": "Example University", "degree": "Ph.D.", "end": "2028-05"}]}
    )
    assert not check_eligibility(MINIMUM, pursuing, title=TITLE).conflicts


def test_admission_asks_when_no_education_is_recorded():
    check = check_eligibility(MINIMUM, Profile.model_validate({}), title=TITLE)
    assert [f.verdict for f in check.findings if f.subject == "degree"] == ["unknown"]
