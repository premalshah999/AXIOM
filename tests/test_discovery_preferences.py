"""Internships and the early-career filter are the applicant's settings, not fixed rules."""

from datetime import UTC, datetime

from jobpilot.features.discovery.feeds import Posting, keep
from jobpilot.jobboard import BoardSettings, role_matches, screen
from jobpilot.features.discovery.policy import DiscoveryPolicy
from jobpilot.schemas import Profile

NOW = datetime.now(UTC)
ROLES = ["Software Development Engineer"]


def posting(title):
    return Posting(
        source="synthetic",
        company="Acme",
        title=title,
        url="https://example.test/jobs/1",
        locations=["Austin, TX"],
        posted_at=NOW.isoformat(),
        basis="verified",
    )


def test_new_installs_filter_nothing_by_career_level():
    assert BoardSettings().early_career_only is False
    assert DiscoveryPolicy().early_career_only is False
    assert Profile().include_internships is False


def test_internships_follow_the_profile():
    assert not role_matches("Software Engineer Intern", "", Profile(target_roles=ROLES))
    assert role_matches("Software Engineer Intern", "", Profile(target_roles=ROLES, include_internships=True))


def test_feed_keep_follows_the_settings():
    intern, senior = posting("Software Engineer Intern"), posting("Senior Software Engineer")
    assert keep(intern, NOW, 7, True) == "internship"
    assert keep(intern, NOW, 7, True, internships=True) is None
    assert keep(senior, NOW, 7, True) is None
    assert keep(senior, NOW, 7, True, early_career_only=True) == "senior"


def test_screen_excludes_seniors_only_for_early_career_boards():
    post = type("P", (), {})()
    post.title, post.description, post.level = "Senior Software Engineer", "Build things.", ""
    post.location, post.country = "Austin, TX", "US"
    profile = Profile(target_roles=ROLES)
    assert screen(post, profile, BoardSettings(early_career_only=True)) == (None, "role_or_seniority")
    assert screen(post, profile, BoardSettings())[1] is None
