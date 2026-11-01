"""What a brand-new installation does with no settings rows: every default here decided production's owner's outcome only by
being overridden by hand, so nothing else exercised it."""

import pytest

from jobpilot import autopilot, execution_services, startable
from jobpilot.db import Job, JobListing, Resume, Run
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


def add_resume(service, name="Resume", created="2026-10-30T00:00:00+00:00"):
    with service.db.exclusive() as s:
        row = Resume(name=name, filename=name + ".pdf", sha256=name * 8, roles=[], text="Alex Example, engineer. " * 5,
                     created_at=created)
        s.add(row)
        s.flush()
        return row.id


def screened(service, job_id):
    return autopilot.screen_job(
        service.db, BoardSettings(auto_apply=True), Profile(target_roles=["Software Engineer"]), {"id": job_id}, frozenset(),
        board_ids={job_id}, integrated=True, use_master=bool(execution_services.master_policy(service.db)),
        data_dir=service.config.data_dir,
    )


def test_an_empty_database_sends_the_uploaded_resume_and_never_waits_for_tailoring(service):
    assert execution_services.master_policy(service.db) is not None
    state, _, artifact = screened(service, ready_job(service))
    assert (state, artifact) == ("ok", None)


def test_the_saved_tailored_choice_is_still_honoured(service):
    service.db.set_setting("application_resume_policy", {"mode": "tailored"})
    assert execution_services.master_policy(service.db) is None
    from jobpilot.features.resumes import tables
    from jobpilot.features.resumes.master import master_doc

    assert tables and master_doc(service.db) is None  # No approved master: tailoring can only keep the source.
    state, _, artifact = screened(service, ready_job(service, "b"))
    assert (state, artifact) == ("ok", None)


def test_a_job_tailoring_can_still_improve_waits(service, monkeypatch):
    service.db.set_setting("application_resume_policy", {"mode": "tailored"})
    from jobpilot.features.resumes import pipeline

    monkeypatch.setattr(pipeline, "preparation_state", lambda *a, **k: "building")
    assert screened(service, ready_job(service, "c"))[0] == "tailoring"


def test_the_policy_names_the_default_on_every_screen(service):
    from jobpilot.features.discovery.readiness import master_resume

    assert execution_services.resume_policy(service.db)["mode"] == "master"
    assert master_resume(service.db) == ""


def test_with_no_saved_choice_the_jobs_own_pick_then_the_newest_upload_is_sent(service):
    old, new = add_resume(service, "old", "2026-10-01T00:00:00+00:00"), add_resume(service, "new", "2026-10-30T00:00:00+00:00")
    with service.db.session() as s:
        assert execution_services.default_resume(s).id == new
        assert execution_services.default_resume(s, preferred=s.get(Resume, old)).id == old


@pytest.mark.parametrize("policy", [{"mode": "master", "resume_id": "named"}])
def test_a_saved_master_policy_is_returned_as_saved(service, policy):
    service.db.set_setting("application_resume_policy", policy)
    assert execution_services.master_policy(service.db) == policy


async def test_an_apply_on_an_empty_database_queues_with_the_upload(service):
    service.dispatch = lambda *a: None
    service.config.mimo_api_key = "unit-test-only"
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    resume = add_resume(service)
    service.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.test", "target_roles": ["Software Engineer"]})
    job_id = ready_job(service, "d")
    (service.config.data_dir / "resumes").mkdir(exist_ok=True)
    (service.config.data_dir / "resumes" / "Resume.pdf").write_bytes(b"%PDF-1.4 test")
    run = await service.queue(job_id, mode="submit")
    with service.db.session() as s:
        row = s.get(Run, run["id"])
        assert row.resume_id == resume and row.packet.get("resume_policy") == "master"


def test_execution_services_are_on_without_a_row_and_a_saved_off_stays_off(service):
    assert execution_services.enabled(service.db)
    service.db.set_setting("execution_services", {"enabled": False})
    assert not execution_services.enabled(service.db)


def test_uncapped_lifetime_and_proven_run_budgets_without_any_row_or_env(service, config):
    from jobpilot.application_budget import max_seconds
    from jobpilot.config import Settings

    with service.db.session() as s:
        assert max_seconds(s) is None
    service.db.set_setting("application_budget", {"max_seconds": 480})
    with service.db.session() as s:
        assert max_seconds(s) == 480
    fields = Settings.model_fields
    assert fields["application_timeout"].default == 600
    assert fields["multipage_timeout"].default == 900
    assert fields["max_model_calls"].default >= 58  # The most any confirmed application has needed.


def test_the_guides_match_score_moves_the_autopilots_threshold(client, service):
    assert client.put("/api/profile", json={"minimum_fit": 60}).status_code == 200
    assert service.db.get_setting("job_board_settings")["minimum_auto_fit"] == 60
    from jobpilot.jobboard import settings

    assert settings(service.db).minimum_auto_fit == 60
    # Saving the profile with the same score leaves a threshold Discovery's own form set apart.
    service.db.set_setting("job_board_settings", {**service.db.get_setting("job_board_settings"), "minimum_auto_fit": 45})
    assert client.put("/api/profile", json={"minimum_fit": 60}).status_code == 200
    assert settings(service.db).minimum_auto_fit == 45
    assert client.put("/api/profile", json={"minimum_fit": 80}).status_code == 200
    assert settings(service.db).minimum_auto_fit == 80




async def test_no_feed_sweep_before_the_profile_names_a_role(service):
    from jobpilot.workers import enqueue_feed_if_due

    assert execution_services.enabled(service.db)
    assert await enqueue_feed_if_due(service) is False
    assert service.db.get_setting("discovery_feed_delivery", {}) == {}
