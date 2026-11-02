"""One employer or one job cannot keep burning runs (2026-11-01: 47 runs at one employer in 40 minutes, none confirmed)."""

from datetime import UTC, datetime

import pytest

from jobpilot.admission import QueueBlocked, attempted_job_ids
from jobpilot.application_budget import JOB_TRY_CEILING, job_ceiling_block
from jobpilot.db import Job, Run
from jobpilot.employer_limit import APPLICATION_LIMIT, application_cap
from jobpilot.loop_guard import CONFIRMED_EMPLOYER_JOBS, NEW_EMPLOYER_JOBS, SAME_STOP_JOBS, held_employers, release, stop_of, verdict

NOTICE = ("From 2026-07-01 to 2026-12-31, you can submit up to 2 job applications (including referrals) within the "
          "“Graduate - 2027 Start” recruitment project. You've reached the application limit.")
AT = datetime(2026, 11, 1, 14, tzinfo=UTC)


def test_the_full_notice_gives_its_limit_and_its_period():
    assert application_cap(NOTICE, AT) == {
        "limit": 2, "days": None, "until": "2027-01-01T12:00:00+00:00", "project": "Graduate - 2027 Start"}


@pytest.mark.parametrize("text", [
    "You've reached the application limit", "You have hit your job application limit.", "Maximum number of applications reached",
    "You've reached the maximum of 3 active applications at ExampleSoft.", "You cannot submit any more applications until Mar 3, 2027.",
    "You have exceeded the allowed number of applications", "You have already applied to the maximum number of jobs.",
])
def test_refusals_are_read_with_or_without_a_number_or_date(text):
    assert APPLICATION_LIMIT.search(text) and application_cap(text, AT) is not None


@pytest.mark.parametrize("text", [
    "We have reached the maximum number of applications for this role.", "This position has reached the maximum number of applications.",
    "You can submit up to 2 job applications within this project.", "You've reached the file upload limit",
])
def test_a_posting_or_a_stated_rule_is_not_a_refusal(text):
    assert not APPLICATION_LIMIT.search(text)


def rows(*items):
    return [{"job_id": job, "state": state, "reason": reason, "blocker": None} for job, state, reason in items]


def test_the_guard_counts_jobs_not_one_jobs_retries():
    assert verdict(rows(*[("one", "waiting_browser", "Fault A")] * 30)) is None
    assert verdict(rows(*[(f"j{i}", "waiting_browser", f"Fault {'x' * i}") for i in range(NEW_EMPLOYER_JOBS)]))


def test_any_success_clears_the_count_and_an_employer_that_confirms_gets_more_room():
    failed = [(f"j{i}", "waiting_browser", f"Fault {'x' * i}") for i in range(NEW_EMPLOYER_JOBS)]
    assert verdict(rows(*failed, ("ok", "confirmed", ""))) is None
    assert verdict(rows(*failed), confirmed_recently=True) is None
    many = [(f"j{i}", "waiting_browser", f"Fault {'x' * i}") for i in range(CONFIRMED_EMPLOYER_JOBS)]
    assert verdict(rows(*many), confirmed_recently=True)
    assert verdict(rows(*[(f"j{i}", "waiting_browser", "Same fault") for i in range(SAME_STOP_JOBS)]), confirmed_recently=True)


def test_the_apps_own_trouble_and_the_owners_questions_say_nothing_about_the_employer():
    own = [(f"j{i}", "waiting_browser", "The selected model provider is unavailable; draft retained") for i in range(20)]
    asks = [(f"a{i}", "waiting_answer", "Missing information") for i in range(20)]
    assert verdict(rows(*own, *asks)) is None


def add(service, company, n, ats="custom", title="Engineer"):
    with service.db.exclusive() as s:
        for i in range(n):
            s.add(Job(id=f"{company}{i}", identity=f"{company}{i}", url=f"https://careers.{company}.example.test/jobs/{i}", ats=ats,
                      company=company, title=title, status="ready"))


def start(service, job_id):
    with service.db.exclusive() as s:
        run = Run(job_id=job_id, mode="submit", state="running", started_at=datetime.now(UTC).isoformat(), packet={})
        s.add(run)
        s.flush()
        return run.id


def test_one_refusal_holds_every_job_and_the_refused_one_waits_with_them(service):
    add(service, "Limitco", 3, title="New Grad 2027: Engineer")  # The notice names the "Graduate - 2027 Start" project.
    run_id = start(service, "Limitco0")
    service.complete(run_id, {"state": "closed", "reason": "The employer's application limit: " + NOTICE,
                              "blocker_type": "employer_limit", "employer_cap": application_cap(NOTICE)}, 1)
    with service.db.session() as s:
        run, refused = s.get(Run, run_id), s.get(Job, "Limitco0")
        assert run.state == "cancelled" and "until January 1, 2027" in run.reason and refused.status == "ready"
        assert not attempted_job_ids(s, ["Limitco0"])  # it starts again when the period ends
        blocked = stop_of(s, s.get(Job, "Limitco2"))
        assert blocked.code == "employer_cap" and blocked.not_before.startswith("2027-01-01")
        assert [h["kind"] for h in held_employers(s)] == ["limit"]


def test_a_refusal_only_described_in_the_reason_holds_too(service):
    add(service, "Limitco", 2)
    run_id = start(service, "Limitco0")
    service.complete(run_id, {"state": "waiting_browser", "reason": "The page shows a limit: 'You've reached the application limit'."}, 1)
    with service.db.session() as s:
        assert stop_of(s, s.get(Job, "Limitco1")).code == "employer_cap"


def test_the_guard_stops_an_unknown_failure_at_its_limit_as_one_item_and_try_again_lifts_it(service):
    add(service, "Brokenco", NEW_EMPLOYER_JOBS + 2)
    for i in range(NEW_EMPLOYER_JOBS):
        with service.db.session() as s:
            assert stop_of(s, s.get(Job, f"Brokenco{i}")) is None
        service.complete(start(service, f"Brokenco{i}"), {"state": "waiting_browser", "reason": f"Fault QZ-{i}: declined"}, 1)
    with service.db.session() as s:
        blocked = stop_of(s, s.get(Job, f"Brokenco{NEW_EMPLOYER_JOBS}"))
        assert blocked.code == "loop_guard" and "Brokenco" in str(blocked)
        assert len(held_employers(s)) == 1
    with service.db.exclusive() as s:
        assert release(s, held_employers(s)[0]["key"])
    with service.db.session() as s:
        assert stop_of(s, s.get(Job, f"Brokenco{NEW_EMPLOYER_JOBS}")) is None


def test_thirty_successes_are_never_counted(service):
    add(service, "Goodco", 30)
    for i in range(30):
        service.complete(start(service, f"Goodco{i}"), {"state": "already_applied", "reason": "The employer says you already applied"}, 1)
    with service.db.session() as s:
        assert stop_of(s, s.get(Job, "Goodco0")) is None and not held_employers(s)


def test_one_job_stops_at_its_ceiling_however_its_stops_are_worded(service):
    add(service, "Stuckco", 1)
    with service.db.exclusive() as s:
        for i in range(JOB_TRY_CEILING):
            s.add(Run(job_id="Stuckco0", mode="submit", state="waiting_browser", reason=f"The page stayed {'x' * i}",
                      started_at=datetime.now(UTC).isoformat(), finished_at=datetime.now(UTC).isoformat(), packet={}))
    with service.db.session() as s:
        blocked = job_ceiling_block(s, "Stuckco0", aliases=["Stuckco0"])
    assert isinstance(blocked, QueueBlocked) and blocked.code == "job_ceiling"
