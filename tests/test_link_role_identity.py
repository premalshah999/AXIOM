"""A different role at an employer that was applied to must proceed; the same role stays protected, and the refusal names
the application it matched (owner, 2026-11-01: a pasted link was called submitted because another application at the
company was). Synthetic employers and ids."""

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.ats import canonicalize, identity_variants
from jobpilot.db import Job, Run
from jobpilot.discovery import add_job
from jobpilot.schemas import JobInput

SITE = "https://careers.example.test/apply"


def identity(url, **kw):
    return canonicalize(url, **kw)[1]


def test_an_id_shaped_ref_or_source_names_the_posting_and_keeps_two_roles_apart():
    assert identity(f"{SITE}?ref=REQ4001") != identity(f"{SITE}?ref=REQ4002")
    assert identity(f"{SITE}?source=40011") != identity(f"{SITE}?source=40012")
    assert identity(f"{SITE}?ref=7c1d1a2e-1111-2222-3333-444455556666") != identity(f"{SITE}?ref=7c1d1a2e-1111-2222-3333-444455557777")


def test_a_channel_label_is_still_dropped_so_one_role_reached_two_ways_is_one_job():
    base = identity(f"{SITE}?jobId=7")
    for tail in ("&source=LinkedIn", "&ref=indeed", "&referrer=Job%20Board", "&source=CC3", "&utm_source=x", "&jr_id=ab12cd34", "&sourceid=12345"):
        assert identity(f"{SITE}?jobId=7{tail}") == base, tail


def test_identity_variants_keep_the_older_hash_of_a_link_whose_id_shaped_param_was_once_dropped():
    url = f"{SITE}?ref=REQ4001"
    assert identity(f"{SITE}") in identity_variants(url)  # rows and guards written before the param was kept


def test_a_workday_location_suffix_still_makes_one_requisition():
    """Hyphenated numbers are real requisition shapes (R-4732-2 is R-4732 for a second location, REQ-2026-184-2 likewise);
    a rule that told "JR-2024-12" from "JR-2024-13" would split those, so the suffix keeps merging."""
    base = "https://example.wd5.myworkdayjobs.com/ext/job/Austin-TX/Analyst_"
    assert identity(base + "R-4732") == identity(base + "R-4732-2")
    assert identity(base + "REQ-2026-184") == identity(base + "REQ-2026-184-2")
    assert identity(base + "R-4732-2") != identity(base + "R-4733-2")


def add(service, job_id, url, *, title, status="ready", company="Example Corp", ats="custom"):
    with service.db.exclusive() as s:
        s.add(Job(id=job_id, identity=canonicalize(url)[1], url=canonicalize(url)[0], ats=ats, status=status,
                  company=company, title=title))


def applied(service, job_id, state="confirmed"):
    with service.db.exclusive() as s:
        s.add(Run(job_id=job_id, state=state, mode="submit", packet={}))


def test_pasting_a_second_role_makes_a_new_job_not_the_applied_one(service):
    first, made = add_job(service.db, JobInput(url=f"{SITE}?ref=REQ4001"), origin="manual")
    with service.db.exclusive() as s:
        s.get(Job, first["id"]).status = "confirmed"
    second, made_second = add_job(service.db, JobInput(url=f"{SITE}?ref=REQ4002"), origin="manual")
    assert made and made_second and second["id"] != first["id"]
    # The same role again, with a tracking label, is still the first job.
    again, made_again = add_job(service.db, JobInput(url=f"{SITE}?ref=REQ4001&source=LinkedIn"), origin="manual")
    assert not made_again and again["id"] == first["id"]


@pytest.mark.parametrize(
    "applied_url, new_url",
    [
        (f"{SITE}?ref=REQ4001", f"{SITE}?ref=REQ4002"),
    ],
)
async def test_a_different_role_at_an_applied_employer_passes_the_duplicate_guards(service, applied_url, new_url):
    from jobpilot.ats import detect

    add(service, "old", applied_url, title="Data Analyst", status="confirmed", ats=detect(applied_url).id)
    applied(service, "old")
    first, _ = add_job(service.db, JobInput(url=new_url, company="Example Corp"), origin="manual")
    assert first["id"] != "old"
    try:
        await service.queue(first["id"], "submit", owner_pick=True)
    except QueueBlocked as exc:
        assert exc.code not in {"submitted_or_uncertain", "duplicate_posting", "archived_submission"}, str(exc)
    except ValueError:
        pass  # No resume in this fixture: every duplicate guard ran before it.


async def test_the_same_requisition_under_another_link_stays_blocked_and_names_the_application_it_matched(service):
    board = "https://job-boards.greenhouse.io/examplecorp/jobs/8262223"
    add(service, "old", board, title="Data Analyst, Risk", status="confirmed", ats="greenhouse")
    applied(service, "old")
    add(service, "again", "https://careers.example.test/jobs?gh_jid=8262223", title="")
    with pytest.raises(QueueBlocked) as err:
        await service.queue("again", "submit", owner_pick=True)
    assert err.value.code == "duplicate_posting"
    assert '"Data Analyst, Risk" at Example Corp' in str(err.value)


async def test_an_uncertain_earlier_submission_of_the_same_role_still_blocks_and_is_named(service):
    add(service, "old", f"{SITE}?ref=REQ4001", title="Data Analyst, Risk", status="submission_unknown")
    applied(service, "old", "submission_unknown")
    with pytest.raises(QueueBlocked) as err:
        await service.queue("old", "submit", owner_pick=True)
    assert err.value.code == "submitted_or_uncertain"
    assert "Data Analyst, Risk" in str(err.value)
