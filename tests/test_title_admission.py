"""Most postings the fit score skipped in a role the applicant named were good matches. A profile with admit_by_title
applies to them by title, unless the title is outside the profile's field or past early career, or the ranking found a
requirement the applicant cannot meet (clearance, citizenship, another language). One click applies any job.

The roles are the profile's own: a broad one ("Developer", "Graduate Engineer") never qualifies by title, and a profile
without roles admits nothing."""

from datetime import UTC, datetime

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.db import Job, Resume, Run, Setting
from jobpilot.discovery import TITLE_ADMITTED, readmit_by_title
from jobpilot.features.discovery.roles import admitted_by_title, specific_roles

# A software and data applicant's roles, broad ones included.
ROLES = [
    "Data Science", "Machine Learning Engineer", "AI Engineer", "Software Development Engineer", "Developer",
    "Forward Deployed Engineer", "Graduate Engineer", "New Grad Engineer", "Full Stack Engineer", "Data Engineer",
    "Platform Engineer", "Data Analyst", "Analytics Engineer", "Backend Engineer",
]


@pytest.mark.parametrize("title,admitted", [
    ("Data Scientist II, Applied ML", True),
    ("Business Data Analyst", True),
    ("Machine Learning Engineer, New Grad", True),
    ("Software Development Engineer I", True),
    ("SWE, Platform", True),
    ("Forward Deployed Engineer", True),
    ("AI Engineer - Agents", True),
    ("Formal Verification Engineer New Grad", False),  # hardware; "New Grad Engineer" is a broad role
    ("Semiconductor Packaging Research Engineer", False),
    ("Full-stack Engineer 4 (Go & Kubernetes)", False),  # level 4
    ("Embedded Software Engineer", False),
    ("Entry Level Korean Bilingual QA / Test Engineer", False),
    ("Senior Data Scientist", True),  # the deterministic gate rejects seniority separately
])
def test_titles_of_the_profiles_own_roles_qualify_by_title(title, admitted):
    assert admitted_by_title(title, roles=ROLES) is admitted


def test_a_requirement_the_applicant_cannot_meet_still_skips():
    reason = "Requires an active TS/SCI clearance and U.S. citizenship"
    assert not admitted_by_title("Software Engineer I", reason, ROLES)
    assert admitted_by_title("Software Engineer I", "Asks for 3+ years of Kubernetes; applicant has 1.5", ROLES)


def test_only_roles_named_by_a_discipline_count():
    assert specific_roles(ROLES) == [
        "Data Science", "Machine Learning Engineer", "AI Engineer", "Software Development Engineer",
        "Forward Deployed Engineer", "Full Stack Engineer", "Data Engineer", "Data Analyst", "Analytics Engineer",
        "Backend Engineer",
    ]
    assert not admitted_by_title("Software Developer", roles=["Developer", "Graduate Engineer"])
    assert not admitted_by_title("Data Engineer I")  # a profile without roles admits nothing


def test_another_field_is_not_excluded_by_the_software_words():
    mechanical = ["Mechanical Engineer"]
    assert admitted_by_title("Mechanical Engineer I", roles=mechanical)
    assert not admitted_by_title("Electrical Engineer I", roles=mechanical)  # not one of its roles
    assert admitted_by_title("Electrical Engineer I", roles=["Electrical Engineer"])
    embedded = ["Embedded Software Engineer", "Software Engineer"]
    assert admitted_by_title("Embedded Software Engineer II", roles=embedded)  # "embedded" is the profile's own word
    assert not admitted_by_title("Silicon Software Engineer", roles=embedded)  # "silicon" is not


def _profile(service, admit=True):
    service.db.set_setting(
        "profile",
        {
            "first_name": "Alex",
            "target_roles": ["Software Engineer", "Data Engineer", "Machine Learning Engineer", "AI Engineer"],
            "admit_by_title": admit,
        },
    )


def _job(s, n, title, status="skipped", reason="Resume fit 40/100 is below the unattended threshold 54"):
    job = Job(id=f"ta{n}", identity=f"ta{n}", url=f"https://acme{n}.wd5.myworkdayjobs.com/job/{n}", company=f"Acme {n}",
              title=title, ats="workday", status=status, score=40, reason=reason,
              description="Build data pipelines. 1+ years of experience preferred.", location="Remote, US")
    s.add(job)
    return job.id


def test_underscored_postings_in_core_families_return_to_the_pipeline(service):
    _profile(service)
    with service.db.exclusive() as s:
        s.add(Resume(id="master", name="Master", filename="master.pdf", sha256="m"))
        keep = _job(s, 1, "Data Engineer I")
        hardware = _job(s, 2, "Silicon Validation Engineer")
        blocked = _job(s, 3, "Software Engineer", reason="Requires U.S. citizenship and an active clearance")
        ran = _job(s, 4, "Machine Learning Engineer")
        s.add(Run(id="r4", job_id=ran, state="failed", mode="submit", packet={}))
        unlisted = _job(s, 5, "AI Engineer")
    count = readmit_by_title(service.db, [keep, hardware, blocked, ran])
    with service.db.session() as s:
        assert count == 1
        job = s.get(Job, keep)
        assert job.status == "ready" and job.resume_id == "master" and job.reason.startswith(TITLE_ADMITTED)
        assert {s.get(Job, j).status for j in (hardware, blocked, ran, unlisted)} == {"skipped"}


def test_title_admission_is_per_profile(service):
    _profile(service, admit=False)
    with service.db.exclusive() as s:
        s.add(Resume(id="master", name="Master", filename="master.pdf", sha256="m"))
        job_id = _job(s, 6, "Data Engineer I")
    assert readmit_by_title(service.db, [job_id]) == 0


async def test_one_click_apply_approves_without_a_reason_and_remembers_a_deferred_start(service, monkeypatch):
    from jobpilot import owner_apply

    with service.db.exclusive() as s:
        s.add(Resume(id="master", name="Master", filename="master.pdf", sha256="m"))
        job_id = _job(s, 7, "Data Engineer I")
    calls = []

    async def busy(job, mode, *args, **kwargs):
        calls.append(job)
        raise QueueBlocked("This worker pool is busy", outcome="deferred", code="worker_capacity",
                           not_before=datetime(2000, 1, 1, tzinfo=UTC).isoformat())

    monkeypatch.setattr(service, "queue", busy)
    result = await owner_apply.apply(service, job_id)
    assert result["deferred"] and "starts on its own" in result["message"]
    with service.db.session() as s:
        job = s.get(Job, job_id)
        assert job.status == "ready" and job.resume_id == "master" and job.reason.startswith(owner_apply.OWNER_APPROVED)
        assert s.get(Setting, owner_apply.KEY + job_id) is not None

    async def accept(job, mode, *args, **kwargs):
        calls.append(job)
        return {"id": "run-1"}

    monkeypatch.setattr(service, "queue", accept)
    assert await owner_apply.due(service, 5) == 1
    with service.db.session() as s:
        assert s.get(Setting, owner_apply.KEY + job_id) is None
    assert calls == [job_id, job_id]


async def test_the_owners_click_gives_a_spent_job_a_fresh_try(service, monkeypatch):
    # Owner 2026-10-18: "give them fresh tries ... never ask me for permission". A job that used its three starts on
    # a since-fixed bug starts again when the owner clicks Apply; an application that may be submitted never does.
    from jobpilot import owner_apply
    from jobpilot.application_budget import allowance

    with service.db.exclusive() as s:
        s.add(Resume(id="master", name="Master", filename="master.pdf", sha256="m"))
        job_id = _job(s, 8, "Data Scientist", status="waiting_answer")
        for i in range(3):
            s.add(Run(id=f"spent{i}", job_id=job_id, mode="submit", state="waiting_answer",
                      reason="Required profile information is missing", started_at=f"2026-10-1{i}T10:00:00+00:00"))
        unknown = _job(s, 9, "Data Engineer", status="submission_unknown")
    queued = []

    async def accept(job, mode, *args, **kwargs):
        queued.append(job)
        return {"id": "fresh"}

    monkeypatch.setattr(service, "queue", accept)
    assert (await owner_apply.apply(service, job_id))["state"] == "queued"
    with service.db.session() as s:
        assert allowance(s, job_id)[2] == 0
        assert s.get(Setting, "application_budget_reset:" + job_id).value["reason"] == "Owner clicked Apply"
    assert (await owner_apply.apply(service, unknown))["state"] == "submission_unknown"
    with service.db.session() as s:
        assert s.get(Setting, "application_budget_reset:" + unknown) is None
    assert queued == [job_id]
