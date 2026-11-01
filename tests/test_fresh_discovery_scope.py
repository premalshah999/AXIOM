"""Discovery assumed one applicant: early-career, anywhere in the country. A new user may be neither."""

import pytest

from jobpilot import autopilot, startable
from jobpilot.db import Job, JobListing
from jobpilot.jobboard import BoardSettings
from jobpilot.schemas import Profile


def ready_job(service, key="a", title="Software Engineer", location="Denver, CO", score=90):
    with service.db.exclusive() as s:
        job = Job(identity="fresh-" + key, url=f"https://{key}.example.test/job/12345", ats="greenhouse", title=title,
                  company="ExampleSoft " + key, location=location, status="ready", score=score,
                  description="Build and ship software. " * 40)
        s.add(job)
        s.flush()
        s.add(JobListing(job_id=job.id, source_id="src", posted_at="2026-10-30T00:00:00+00:00", date_basis="posted",
                         location=location, country="US", career_level="", match_reason="", active=True))
        return job.id


def post(title="Staff Nurse", description="", location="Denver, CO"):
    from jobpilot.jobboard import Posting

    return Posting("https://x.example.test/job/1", title, "Example Health", location, description, "US")


def test_a_posting_asking_for_many_years_reaches_an_experienced_applicant():
    from jobpilot.jobboard import screen

    profile = Profile(target_roles=["Staff Nurse"])
    asks = "Requires 8+ years of experience in patient care. " * 3
    found, why = screen(post(description=asks), profile, BoardSettings(early_career_only=False))
    assert why is None and found[0] == "Experienced"
    profile = Profile(target_roles=["Registered Nurse"])
    assert screen(post("Registered Nurse", asks), profile, BoardSettings(early_career_only=True)) == (None, "experience")


def test_accepted_years_above_three_are_honoured_for_early_career_profiles():
    from jobpilot.jobboard import screen

    profile = Profile(target_roles=["Registered Nurse"], accept_experience_years=5)
    asks = "Requires 5+ years of experience in patient care. " * 3
    assert screen(post("Registered Nurse", asks), profile, BoardSettings(early_career_only=True))[1] is None


@pytest.mark.parametrize(
    "targets, facts, location, outside",
    [
        (["Denver, CO"], {}, "New York, NY", True),
        (["Denver, CO"], {}, "Denver, CO", False),
        (["Denver, CO"], {}, "Denver, CO, USA", False),
        (["Denver, CO"], {}, "New York, NY, United States", True),
        (["Denver, CO"], {}, "Boulder, CO", False),  # Same state: a metro area crosses city names.
        (["Denver, CO"], {}, "Denver, Colorado", False),
        (["Colorado"], {}, "Austin, TX", True),
        (["Colorado"], {}, "Aurora, Colorado", False),
        (["Denver, CO"], {}, "Remote - United States", False),
        (["Denver, CO"], {}, "New York, NY; Denver, CO", False),
        (["Denver, CO"], {}, "New York, NY; Austin, TX", True),
        (["Denver, CO"], {}, "2 Locations", False),  # Unreadable: the ranker decides.
        (["Denver, CO"], {}, "", False),
        (["Denver, CO"], {"willing_to_relocate": True}, "New York, NY", False),
        (["Denver, CO"], {"willing_to_relocate": False}, "New York, NY", True),
        (["Anywhere in the USA"], {}, "New York, NY", False),
        (["United States"], {}, "New York, NY", False),
        (["Remote"], {}, "New York, NY", False),
        ([], {}, "New York, NY", False),
        (["Denver, CO", "Austin, TX"], {}, "Austin, TX", False),
    ],
)
def test_target_locations_hold_a_posting_that_names_only_other_places(targets, facts, location, outside):
    from jobpilot.jobboard import outside_target_locations

    assert outside_target_locations(Profile(target_locations=targets, facts=facts), location) is outside


def test_the_autopilot_refuses_a_job_outside_the_target_locations_with_its_reason(service):
    job_id = ready_job(service, "e", location="New York, NY")
    with pytest.raises(ValueError, match="Outside your target locations"):
        autopilot.screen_job(
            service.db, BoardSettings(auto_apply=True),
            Profile(target_roles=["Software Engineer"], target_locations=["Denver, CO"]), {"id": job_id}, frozenset(),
            board_ids={job_id}, integrated=True, use_master=True, data_dir=service.config.data_dir,
        )


def test_a_location_hold_is_named_as_such_on_the_startable_line():
    code, sentence = startable.classify("Outside your target locations: New York, NY")
    assert code == "location" and "New York, NY" in sentence and code in startable.HOLDS


def test_someone_outside_the_search_area_is_told_instead_of_waiting_for_ever():
    from jobpilot.schemas import Address

    elsewhere = Profile(address=Address(country="Canada"))
    assert "United States only" in startable.outside_search_area(elsewhere)
    assert startable.outside_search_area(Profile(address=Address(country="United States"))) is None
    assert startable.outside_search_area(Profile(address=Address(country="USA"))) is None
    assert startable.outside_search_area(Profile()) is None




def test_changing_target_locations_brings_refused_jobs_back_once(service):
    def revision(places, facts=None):
        service.db.set_setting("profile", {"target_locations": places, "facts": facts or {}})
        return autopilot.admission_revision(service.db, BoardSettings())

    unrestricted = revision([])
    assert revision(["Anywhere in the USA"]) == unrestricted  # An unrestricted profile's revision never moves.
    denver = revision(["Denver, CO"])
    assert denver != unrestricted and revision(["Austin, TX"]) != denver
    assert revision(["Denver, CO"], {"willing_to_relocate": True}) != denver
