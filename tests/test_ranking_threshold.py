"""Ranking readiness uses the lower of the profile's minimum_fit and the board's minimum_auto_fit.

The board was 45 and the profile 54 on 2026-10-28, so every 45-53 posting was ranked 'skipped' although the
autopilot would have applied to it. No stored setting is changed."""

import asyncio

from jobpilot import discovery
from jobpilot.db import Job, Resume
from jobpilot.features.discovery import classify as classify_jobs, policy_for
from jobpilot.schemas import JobInput, Profile


def setup(service, profile_fit, board_fit, score):
    with service.db.exclusive() as s:
        if not s.get(Resume, "r1"):
            s.add(Resume(id="r1", name="Main", filename="main.pdf", sha256="x" * 64, text="Python, SQL"))
    service.db.set_setting("profile", Profile(minimum_fit=profile_fit, autonomous=True).model_dump())
    if board_fit is not None:
        service.db.set_setting("job_board_settings", {"minimum_auto_fit": board_fit, "max_age_hours": 720})
    row, _ = discovery.add_job(
        service.db,
        JobInput(url="https://job-boards.greenhouse.io/acme/jobs/4242", title="Software Engineer I", company="Acme",
                 location="Austin, TX", description="Build services in Python. US location. " * 20),
        origin="manual",
    )

    async def structured(config, db, output_type, instructions, prompt, *args, **kwargs):
        one = {"score": score, "eligible": True, "resume_id": "r1", "reason": "fit"}
        if output_type is discovery.RankedBatch:
            return output_type(jobs=[{"job_id": row["id"], **one}])
        return output_type(**one)

    return row["id"], structured


def status(service, job_id):
    with service.db.session() as s:
        return s.get(Job, job_id).status


def test_threshold_is_the_lower_of_the_two(service):
    db = service.db
    db.set_setting("job_board_settings", {"minimum_auto_fit": 45})
    assert discovery.ranking_threshold(db, Profile(minimum_fit=54)) == 45
    db.set_setting("job_board_settings", {"minimum_auto_fit": 80})
    assert discovery.ranking_threshold(db, Profile(minimum_fit=54)) == 54
    for board in ({}, {"minimum_auto_fit": None}, {"minimum_auto_fit": "x"}, {"minimum_auto_fit": 101}):
        db.set_setting("job_board_settings", board)
        assert discovery.ranking_threshold(db, Profile(minimum_fit=54)) == 54


def test_rank_marks_a_score_between_the_two_ready(service, monkeypatch):
    job_id, structured = setup(service, 54, 45, 50)
    monkeypatch.setattr(discovery, "structured", structured)
    asyncio.run(discovery.rank(service.db, service.config, job_id))
    assert status(service, job_id) == "ready"
    assert service.db.get_setting("profile", {})["minimum_fit"] == 54
    assert service.db.get_setting("job_board_settings", {})["minimum_auto_fit"] == 45


def test_rank_still_skips_below_both(service, monkeypatch):
    job_id, structured = setup(service, 54, 45, 40)
    monkeypatch.setattr(discovery, "structured", structured)
    asyncio.run(discovery.rank(service.db, service.config, job_id))
    assert status(service, job_id) == "skipped"


def test_rank_batch_uses_the_same_threshold(service, monkeypatch):
    job_id, structured = setup(service, 54, 45, 50)
    monkeypatch.setattr(discovery, "structured", structured)
    asyncio.run(discovery.rank_batch(service.db, service.config, [job_id]))
    assert status(service, job_id) == "ready"


def test_a_lower_profile_keeps_deciding_when_the_board_is_higher(service, monkeypatch):
    job_id, structured = setup(service, 40, 70, 50)
    monkeypatch.setattr(discovery, "structured", structured)
    asyncio.run(discovery.rank(service.db, service.config, job_id))
    assert status(service, job_id) == "ready"


def test_readmit_returns_only_eligible_skipped_postings(service, monkeypatch):
    job_id, structured = setup(service, 54, None, 50)  # no board floor: the profile decides, so 50 is skipped
    monkeypatch.setattr(discovery, "structured", structured)
    asyncio.run(discovery.rank_batch(service.db, service.config, [job_id]))
    assert status(service, job_id) == "skipped"
    assert discovery.readmit_by_threshold(service.db) == 0  # nothing differs yet
    service.db.set_setting("job_board_settings", {"minimum_auto_fit": 45, "max_age_hours": 720})
    # No classification row: eligibility is unknown, the posting stays skipped.
    assert discovery.readmit_by_threshold(service.db) == 0

    async def fn(ids):
        return {i: {"score": 50, "eligible": True, "reason": "fit", "resume_id": "r1", "uncertainties": []}
                for i in ids}

    profile = service.db.get_setting("profile", {})
    with service.db.session() as s:
        job = s.get(Job, job_id)
        data = {k: getattr(job, k) for k in ("id", "identity", "url", "title", "location", "description", "company")}
    asyncio.run(classify_jobs(service.db, [data], fn, policy_for(service.db), profile))
    assert discovery.readmit_by_threshold(service.db) == 1
    assert status(service, job_id) == "ready"
    assert discovery.readmit_by_threshold(service.db) == 0
