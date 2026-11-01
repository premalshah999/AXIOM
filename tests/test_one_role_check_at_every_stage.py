"""Discovery, ranking, the autopilot's screen and the queue ask the same role question (2026-11-01).

Discovery admitted "Cloud Engineer" as a specialisation of a named role and the ranker scored it Ready; the autopilot's and
the queue's own check refused it as outside the target roles, so 7 Ready jobs (6 never tried) sat unstarted."""

from jobpilot.features.discovery.roles import matching_role, role_fits
from jobpilot.schemas import Profile
from jobpilot.service.queueing import fit_blocker

ROLES = ["Platform Engineer", "Software Engineer", "Data Analyst"]


def test_a_specialisation_is_a_fit_but_the_named_forms_alone_are_not():
    assert not matching_role("Cloud Engineer", ROLES)
    assert role_fits("Cloud Engineer", ROLES)
    assert role_fits("Junior Front-End Engineer", ROLES)
    assert not role_fits("Mechanical Engineer", ROLES)
    assert not role_fits("General AI Data Annotator", ["AI Engineer"])


def test_the_queue_admits_what_discovery_admitted():
    profile = Profile(target_roles=ROLES)
    assert fit_blocker(profile, {}, "Site Reliability Engineer") is None
    assert fit_blocker(profile, {}, "Mechanical Engineer") == ("Outside the profile's target roles", "target_role")
