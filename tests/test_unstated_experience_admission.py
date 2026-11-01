"""A plain-titled role that states no experience is a candidate for a profile that accepts roles asking up to N years.

Early-career-only refused "Software Engineer", "Data Engineer" and "AI Engineer" for saying nothing about years: 62 of the
1,816 US postings in a 30-day sample of 270 public boards were refused as experience_unspecified while 45 matched
(2026-11-01). The ranking reads the posting against the accepted years; the screen only keeps out postings that name a
high minimum, a clearance or citizenship requirement, or were never read."""

import pytest

from jobpilot.jobboard import BoardSettings, Posting, role_matches, screen, screen_fingerprint
from jobpilot.schemas import Profile

BODY = "You will design and build services for our product with a small team. " * 15  # 1,000+ chars, no years stated


def post(title="Software Engineer", description=BODY):
    return Posting("https://example.test/jobs/1", title, "ExampleSoft", "New York, NY", description, "US", "2026-10-30", "")


def profile(years=3):
    return Profile(target_roles=["Software Engineer", "AI Engineer"], skills=["Python"], accept_experience_years=years)


CFG = BoardSettings(early_career_only=True)


def test_a_plain_title_with_no_stated_experience_is_admitted_when_the_profile_accepts_years():
    match, why = screen(post(), profile(3), CFG)
    assert match[0] == "Experience unspecified" and why is None


def test_a_profile_that_accepts_no_years_keeps_the_old_refusal():
    assert screen(post(), profile(0), CFG) == (None, "experience_unspecified")


@pytest.mark.parametrize("text", [
    "Required: Five (5) years of relevant IT experience.",
    "Required: 6-10+ years in data engineering.",
    "You bring 4 years building distributed systems.",
    "An active security clearance is required.",
    "This role is open to U.S. citizens only; US citizenship required.",
])
def test_a_high_minimum_a_clearance_or_citizenship_requirement_stays_out(text):
    assert screen(post(description=text + "\n" + BODY), profile(3), CFG)[0] is None


def test_a_preferred_high_minimum_does_not_keep_it_out():
    assert screen(post(description="Ideally 6+ years with Kubernetes is a plus.\n" + BODY), profile(3), CFG)[0]


def test_a_posting_that_was_never_read_says_nothing():
    assert screen(post(description="Software Engineer"), profile(3), CFG) == (None, "experience_unspecified")
    assert screen(post(title="Cleared Software Engineer"), profile(3), CFG)[0] is None


@pytest.mark.parametrize("title", ["Software Engineering Co-op", "AI Trainer (Contractor), Voice Evaluation", "AI Data Annotator"])
def test_internships_and_data_work_are_not_roles_of_an_ml_profile(title):
    assert not role_matches(title, "", profile(3))


def test_the_screen_is_read_again_when_the_accepted_years_change():
    assert screen_fingerprint(profile(3), CFG) != screen_fingerprint(profile(0), CFG)
