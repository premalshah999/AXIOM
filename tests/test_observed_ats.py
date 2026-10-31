"""The platform a run observed is written to the job (2026-10-31).

adopt_handoff changed only the run's copy, so 144 of 269 jobs that were routed to Greenhouse, Workday, iCIMS and the rest
still read "custom": the custom-site worker lane, the custom site's sender rule and a list that called them career sites.
Writing the label must not move anything keyed on the address, must not skip the adoption Workday's account check needs on
the next run, and must not let a running job's employer partition drift."""

from types import SimpleNamespace

import pytest

from jobpilot.admission import employer_partition
from jobpilot.browser import BrowserEngine
from jobpilot.db import Job
from jobpilot.pipeline_comparison import custom_site

SITE = "https://careers.example.test/jobs/77?gh_jid=77"
FRAME = "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=77"


def seed(service, ats="custom", url=SITE, job_id="job1"):
    with service.db.session() as s:
        s.add(Job(id=job_id, identity=job_id, url=url, ats=ats, title="Engineer", company="Acme"))


def engine(service, job_id="job1", ats="custom", url=SITE):
    e = BrowserEngine.__new__(BrowserEngine)
    e.db, e.job, e.emitted = service.db, {"id": job_id, "url": url, "ats": ats}, []
    e.emit = lambda kind, message, data=None: e.emitted.append((kind, data))
    return e


def label(service, job_id="job1"):
    with service.db.session() as s:
        row = s.get(Job, job_id)
        return row.ats, row.url


def test_the_observed_platform_replaces_a_career_sites_label_and_leaves_the_address(service):
    seed(service)
    e = engine(service)
    e.page = SimpleNamespace(url=FRAME)
    e.adopt_handoff("greenhouse", FRAME)
    assert label(service) == ("greenhouse", SITE)
    assert e.job["ats"] == "greenhouse" and e.job["url"] == FRAME  # this run's copy, as before
    assert service.db.get_setting("observed_ats:job1", {})["was"] == "custom"
    assert ("ats_observed", {"was": "custom", "ats": "greenhouse"}) in e.emitted


def test_the_observation_is_remembered_so_the_next_run_can_start_as_the_career_site(service):
    # Trusting "workday" on a short link's row would skip the adoption: "account origin does not match this employer".
    seed(service, "custom", "https://go.example.test/Bi")
    e = engine(service, url="https://go.example.test/Bi")
    e.page = SimpleNamespace(url="https://amat.wd1.myworkdayjobs.com/External/job/AZ/Engineer_R1")
    e.adopt_handoff("workday")
    assert label(service)[0] == "workday"
    seen = service.db.get_setting("observed_ats:job1", {})
    assert seen["ats"] == "workday" and seen["was"] == "custom"


def test_a_label_that_came_from_the_address_is_never_replaced(service):
    seed(service, "eightfold", "https://acme.eightfold.ai/careers/job/1")
    e = engine(service, ats="eightfold", url="https://acme.eightfold.ai/careers/job/1")
    e.page = SimpleNamespace(url="https://acme.wd5.myworkdayjobs.com/External/job/NY/Engineer_R1")
    e.adopt_handoff("workday")  # Morgan Stanley: an Eightfold posting that applies on Workday
    assert label(service)[0] == "eightfold"
    assert service.db.get_setting("observed_ats:job1", {}) == {}


@pytest.mark.parametrize(
    "start, target, kept",
    [
        ("custom", "pinpoint", "pinpoint"),  # a name for a platform with no driver
        ("pinpoint", "greenhouse", "greenhouse"),  # then the one with a driver
        ("greenhouse", "pinpoint", "greenhouse"),  # never back down
        ("phenom", "workday", "workday"),
        ("custom", "custom", "custom"),
        ("custom", "phenom", "custom"),  # a vendor is never the platform
    ],
)
def test_a_label_only_moves_up(service, start, target, kept):
    seed(service, start)
    e = engine(service, ats=start)
    e.page = SimpleNamespace(url=SITE)
    e.persist_observed_ats(target)
    assert label(service)[0] == kept


def test_an_observed_label_may_be_corrected_by_a_later_observation(service):
    seed(service)
    e = engine(service)
    e.page = SimpleNamespace(url=SITE)
    e.persist_observed_ats("workday")
    e.persist_observed_ats("oracle")  # Phenom -> Workday first, Oracle behind a later posting
    assert label(service)[0] == "oracle"
    assert service.db.get_setting("observed_ats:job1", {})["was"] == "custom"


def test_a_demo_job_or_one_without_a_row_writes_nothing(service):
    e = engine(service, job_id="missing")
    e.persist_observed_ats("greenhouse")
    demo = engine(service)
    demo.job["demo"] = True
    seed(service)
    demo.persist_observed_ats("greenhouse")
    assert label(service)[0] == "custom"


def test_a_job_with_an_observed_platform_leaves_the_custom_lane(service):
    seed(service)
    assert custom_site({"ats": label(service)[0]})
    engine(service).persist_observed_ats("greenhouse")
    assert not custom_site({"ats": label(service)[0]})


def test_a_running_jobs_employer_partition_does_not_move_when_its_label_is_saved():
    before = {"url": SITE, "ats": "custom", "company": "Acme"}
    after = {**before, "ats": "greenhouse"}
    assert employer_partition(before) == employer_partition(after) == "careers.example.test"
    # The platform's own address still names its employer by path, as before.
    board = {"url": "https://job-boards.greenhouse.io/acme/jobs/1", "ats": "greenhouse"}
    assert employer_partition(board) == "job-boards.greenhouse.io/acme"


def test_a_run_starts_as_a_career_site_when_its_label_was_learned_from_an_observation(service):
    seed(service, "custom", "https://go.example.test/Bi")
    e = engine(service, url="https://go.example.test/Bi")
    e.page = SimpleNamespace(url="https://amat.wd1.myworkdayjobs.com/External/job/AZ/Engineer_R1")
    e.adopt_handoff("workday")
    row = {"id": "job1", "url": "https://go.example.test/Bi", "ats": label(service)[0]}
    assert row["ats"] == "workday" and e.starting_label(row) == "custom"
    # A row whose label came from its address has no such record, and a demo job is left alone.
    assert e.starting_label({"id": "job2", "url": "https://x.wd1.myworkdayjobs.com/a", "ats": "workday"}) == "workday"
    assert e.starting_label({**row, "demo": True}) == "workday"
    # A later change of the label by anyone else ends the hint.
    assert e.starting_label({**row, "ats": "icims"}) == "icims"
