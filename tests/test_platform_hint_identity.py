"""One requisition is one job, with or without the feed's platform hint (docs/forensics/FAILURE_ANALYSIS.md, flow F4).

Job feeds append the platform they read a posting from ("?ats=successfactors"). ASSA ABLOY's AI Scientist existed with
and without it, so it was two jobs: 81 runs in all (about $4.50), no shared budget, history or duplicate protection.
41 job URLs carry the hint; ASSA ABLOY was the only requisition that also existed without it.
"""

import pytest

from jobpilot.ats import canonicalize, identity_variants, platform_hint
from jobpilot.db import Job, Run, Setting
from jobpilot.discovery import add_job
from jobpilot.operations.reset import guard_key
from jobpilot.schemas import JobInput

PLAIN = "https://careers.example.test/job/Austin-AI-Scientist-TX-78753/1441539933"
HINTED = PLAIN + "?ats=successfactors"


@pytest.mark.parametrize(
    "hinted",
    [
        PLAIN + "?ats=successfactors",
        PLAIN + "?ATS=SuccessFactors",
        PLAIN + "?ats=%20successfactors%20",
        PLAIN + "?ats=pinpointhq",
        PLAIN + "?utm_source=feed&ats=workday&ref=x",
        PLAIN + "/?ats=successfactors",
    ],
)
def test_the_platform_hint_is_not_part_of_the_job(hinted):
    assert canonicalize(hinted) == canonicalize(PLAIN)


def test_other_parameters_and_other_values_of_ats_stay():
    keep = canonicalize(PLAIN + "?lang=en&ats=successfactors")
    assert keep[0].endswith("?lang=en") and keep[1] != canonicalize(PLAIN)[1]
    for value in ("123", "internal", "3f2b9c1e-aaaa-bbbb-cccc-0123456789ab", ""):
        assert canonicalize(PLAIN + "?ats=" + value)[1] != canonicalize(PLAIN)[1], value
    assert not platform_hint("source", "successfactors")


def test_the_older_identity_is_still_known_for_rows_and_guards_written_before():
    legacy = canonicalize(HINTED, keep_platform_hint=True)
    assert legacy[0].endswith("?ats=successfactors") and legacy[1] != canonicalize(PLAIN)[1]
    assert identity_variants(HINTED) == (canonicalize(PLAIN)[1], legacy[1])
    assert identity_variants(PLAIN) == (canonicalize(PLAIN)[1],)  # nothing older to look for


def test_greenhouse_embeds_and_requisition_urls_keep_their_native_identity():
    gh = "https://job-boards.greenhouse.io/example/jobs/77"
    assert canonicalize(gh + "?ats=greenhouse") == canonicalize(gh)
    assert canonicalize("https://example.wd1.myworkdayjobs.com/en-US/site/job/X_R1?ats=workday")[1] == canonicalize(
        "https://example.wd1.myworkdayjobs.com/en-US/site/job/X_R1"
    )[1]


def add(db, url):
    return add_job(db, JobInput(url=url, title="AI Scientist", company="Acme"))


def test_importing_either_url_after_the_other_finds_the_same_job(service):
    first, created = add(service.db, HINTED)
    second, created_again = add(service.db, PLAIN)
    assert created and not created_again and first["id"] == second["id"]
    third, created_third = add(service.db, HINTED)
    assert not created_third and third["id"] == first["id"]
    assert not first["url"].endswith("ats=successfactors")  # stored without the hint


def test_a_job_stored_with_the_older_identity_is_found_again_and_not_duplicated(service):
    """Rows written before this change carry the hint in both url and identity."""
    legacy_url, legacy_identity = canonicalize(HINTED, keep_platform_hint=True)
    with service.db.exclusive() as s:
        s.add(Job(id="legacy", identity=legacy_identity, url=legacy_url, ats="custom", title="AI Scientist"))
    for url in (HINTED, PLAIN):
        row, created = add(service.db, url)
        assert not created and row["id"] == "legacy", url


def seed_job(service, job_id, url, *, ats="successfactors"):
    identity = canonicalize(url, keep_platform_hint=True)[1] if "ats=" in url else canonicalize(url)[1]
    with service.db.exclusive() as s:
        s.add(Job(id=job_id, identity=identity, url=url, ats=ats, title="AI Scientist", company="Acme",
                  status="ready", score=90))


async def test_a_confirmed_application_under_one_url_blocks_the_other(service):
    from jobpilot.admission import QueueBlocked

    seed_job(service, "plain", PLAIN)
    seed_job(service, "hinted", HINTED)
    with service.db.exclusive() as s:
        s.add(Run(id="done", job_id="plain", mode="submit", state="confirmed", receipt={}))
    with pytest.raises(QueueBlocked) as caught:
        await service._queue("hinted", "submit")
    assert caught.value.code == "submitted_or_uncertain"


def test_the_retry_budget_is_shared_by_both_urls(service):
    from datetime import UTC, datetime, timedelta

    from jobpilot.application_budget import allowance

    seed_job(service, "plain", PLAIN)
    seed_job(service, "hinted", HINTED)
    at = datetime(2026, 10, 20, 7, tzinfo=UTC)
    with service.db.exclusive() as s:
        for i, job_id in enumerate(["plain", "hinted", "plain"]):
            s.add(Run(id=f"r{i}", job_id=job_id, mode="submit", state="timed_out", reason="Browser did not advance",
                      started_at=(at - timedelta(seconds=100 - i)).isoformat()))
    with service.db.session() as s:
        assert allowance(s, "hinted", at=at)[2] == 3  # three starts across the two rows, not one


async def test_a_submission_guard_written_under_the_older_identity_still_protects(service):
    """3 of the 41 hinted jobs had a submission_guard (a confirmed or unknown submission that survives resets)."""
    from jobpilot.admission import QueueBlocked

    seed_job(service, "guarded", HINTED)
    legacy_identity = canonicalize(HINTED, keep_platform_hint=True)[1]
    with service.db.exclusive() as s:
        s.add(Setting(key=guard_key(legacy_identity), value={"state": "confirmed"}))
    with pytest.raises(QueueBlocked) as caught:
        await service._queue("guarded", "submit")
    assert caught.value.code == "archived_submission"


def test_the_discovery_guard_checks_both_identities(service):
    from jobpilot.features.discovery.policy import protected

    legacy_identity = canonicalize(HINTED, keep_platform_hint=True)[1]
    with service.db.exclusive() as s:
        s.add(Setting(key=guard_key(legacy_identity), value={"state": "confirmed"}))
    with service.db.session() as s:
        assert protected(s, HINTED) and not protected(s, PLAIN + "?lang=fr")


SR = "https://jobs.smartrecruiters.com/Intuitive/744000156658124"
SR_SLUG = SR + "-full-stack-engineer?oga=true"


def test_a_smartrecruiters_posting_is_one_job_with_or_without_its_title_slug():
    """Intuitive 2026-10-21: /744000156658124 and /744000156658124-full-stack-engineer?oga=true were two jobs, and the
    same application was submitted twice."""
    assert canonicalize(SR_SLUG)[1] == canonicalize(SR)[1]
    assert canonicalize("https://jobs.smartrecruiters.com/Intuitive/744000156658099-full-stack-software-engineer")[1] \
        != canonicalize(SR)[1]
    # Rows and guards written before keep matching under their URL-based identity.
    assert canonicalize(SR_SLUG, requisition_ids=False)[1] in identity_variants(SR_SLUG)
    sap = "https://careers.sap.com/job/Palo-Alto-Data-Science-Chief-Expert%2C-SCM-CA-94304/1430588133/"
    assert canonicalize(sap)[1] == canonicalize("https://careers.sap.com/job/Palo-Alto-Renamed/1430588133")[1]


async def test_a_confirmed_smartrecruiters_application_blocks_its_slug_twin(service):
    from jobpilot.admission import QueueBlocked

    # The two rows Intuitive left behind: written before requisition ids, each under its URL-based identity.
    with service.db.exclusive() as s:
        for job_id, url in (("plain-sr", SR), ("slug-sr", SR_SLUG)):
            s.add(Job(id=job_id, identity=canonicalize(url, requisition_ids=False)[1], url=url, ats="smartrecruiters",
                      title="Full-Stack Engineer", company="Intuitive", status="ready", score=90))
    with service.db.exclusive() as s:
        s.add(Run(id="sr-done", job_id="plain-sr", mode="submit", state="confirmed", receipt={}))
    with pytest.raises(QueueBlocked) as caught:
        await service._queue("slug-sr", "submit")
    assert caught.value.code == "submitted_or_uncertain"


def test_a_reposted_workday_requisition_is_one_job():
    """HPE and KLA (2026-10-22): _1214365-1 after _1214365-3, and _2641706-2 on another career site after _2641706-1,
    were each applied to again and answered "already applied"."""
    from jobpilot.ats import canonicalize, identity_variants

    first = "https://hpe.wd5.myworkdayjobs.com/en-US/jobsathpe/job/Spring-Texas/Test-Engineer_1214365-3"
    repost = "https://hpe.wd5.myworkdayjobs.com/en-US/jobsathpe/job/Spring-Texas-United-States/Test-Engineer_1214365-1"
    other_site = "https://kla.wd1.myworkdayjobs.com/en-US/ur/job/Ann-Arbor-MI/AI-ML-Software-Engineer_2641706-1"
    kla = "https://kla.wd1.myworkdayjobs.com/en-US/search/job/Ann-Arbor-MI/AI-ML-Software-Engineer_2641706-2"
    assert canonicalize(first)[1] == canonicalize(repost)[1]
    assert canonicalize(other_site)[1] == canonicalize(kla)[1]
    assert canonicalize(first)[1] != canonicalize(kla)[1]
    # Guards written under the old site-and-suffix identity still protect the job.
    assert canonicalize(first, requisition_ids=False)[1] in identity_variants(first)
    # A requisition id that only ends in digits after a dash is not a repost suffix.
    assert canonicalize("https://acme.wd1.myworkdayjobs.com/en-US/c/job/X/E_JR-2024-001")[1] != canonicalize(
        "https://acme.wd1.myworkdayjobs.com/en-US/c/job/X/E_JR-2024")[1]
