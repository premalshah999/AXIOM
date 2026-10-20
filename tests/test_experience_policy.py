"""Owner decision 2026-10-18: in-field postings that ask for up to N years of experience count as met when ranking.

213 of 276 skipped Greenhouse/Ashby jobs in one week were experience skips, many asking 2-3 years from an applicant
with ~1.5 years and an MS. The rule is a per-profile setting (Setup), never hard-coded to one applicant; 0 keeps the
strict judgement, and changing it re-ranks (the classification cache is keyed by the profile version).
"""

import asyncio

from jobpilot import discovery
from jobpilot.db import Resume
from jobpilot.features.discovery.classify import profile_version
from jobpilot.schemas import JobInput, Profile


def test_policy_text_only_when_the_profile_opts_in():
    assert discovery.experience_policy(Profile()) == ""
    text = discovery.experience_policy(Profile(accept_experience_years=3))
    assert "up to 3 years" in text and "'3+ years'" in text and "minimum above 3 years" in text
    assert "different field" in text
    strict = discovery.fact_policy(Profile())
    assert strict == discovery.CLASSIFICATION_FACT_POLICY
    relaxed = discovery.fact_policy(Profile(accept_experience_years=3))
    assert "except as the EXPERIENCE YEARS setting below allows" in relaxed and relaxed.endswith(text)


def test_changing_the_setting_invalidates_cached_rankings():
    assert profile_version({"accept_experience_years": 0}) != profile_version({"accept_experience_years": 3})


def test_both_ranking_prompts_carry_the_applicants_choice(service, monkeypatch):
    with service.db.exclusive() as s:
        s.add(Resume(id="r1", name="Main", filename="main.pdf", sha256="x" * 64, text="Python, SQL"))
    service.db.set_setting("profile", Profile(accept_experience_years=3).model_dump())
    rows = [
        discovery.add_job(
            service.db,
            JobInput(url=f"https://job-boards.greenhouse.io/acme/jobs/{n}", title="Software Engineer",
                     company="Acme", description="Build services in Python. 2+ years of experience required. " * 8),
            origin="manual",
        )[0]
        for n in (101, 102)
    ]
    prompts = []

    async def structured(config, db, output_type, instructions, prompt, *args, **kwargs):
        prompts.append(instructions)
        if output_type is discovery.RankedBatch:
            return output_type(jobs=[{"job_id": rows[1]["id"], "score": 80, "eligible": True, "resume_id": "r1",
                                      "reason": "fit"}])
        return output_type(score=80, eligible=True, resume_id="r1", reason="fit")

    monkeypatch.setattr(discovery, "structured", structured)
    asyncio.run(asyncio.to_thread(discovery.rank_in_thread, service.db, service.config, rows[0]["id"]))
    asyncio.run(discovery.rank_batch(service.db, service.config, [rows[1]["id"]]))
    assert len(prompts) == 2 and all("up to 3 years" in p for p in prompts)
