"""Old jobs never applied to and old logs go; whatever duplicate protection and open work read stays (owner 2026-10-31)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from jobpilot.db import Event, Job, JobListing, Review, Run, Setting
from jobpilot.housekeeping import sweep

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def ago(days):
    return (NOW - timedelta(days=days)).isoformat()


def job(service, key, status="new", days=30, **extra):
    with service.db.session() as s:
        row = Job(identity=key, url=f"https://jobs.example.test/{key}", status=status, created_at=ago(days), **extra)
        s.add(row)
        s.flush()
        return row.id


def run(service, job_id, state, days=30, files=(), events=()):
    with service.db.session() as s:
        row = Run(job_id=job_id, state=state, created_at=ago(days), packet={})
        s.add(row)
        s.flush()
        run_id = row.id
        for kind in events:
            s.add(Event(run_id=run_id, kind=kind, message="", data={}))
    folder = service.config.data_dir / "runs" / run_id
    for name in files:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 100)
    return run_id


def clean(service, **extra):
    return sweep(service.db, service.config.data_dir, apply=True, at=NOW, **extra)


def has_job(service, job_id):
    with service.db.session() as s:
        return s.get(Job, job_id) is not None


def test_an_old_job_never_applied_to_goes_with_everything_tied_to_it(service):
    untried = job(service, "a")
    skipped = job(service, "b", status="skipped")
    failed = job(service, "c", status="closed")
    run_id = run(service, failed, "closed", files=["steps/1.json", "final.png"], events=["agent_step"])
    with service.db.session() as s:
        s.add(Review(run_id=run_id, question="Q", options=[], key="k", reason="", context={}))
        s.add(Setting(key=f"job_origin:{failed}", value={"source": "x"}))
        s.add(Setting(key=f"failure_report:{run_id}", value={"sent": True}))
    report = clean(service)
    assert not has_job(service, untried) and not has_job(service, skipped) and not has_job(service, failed)
    assert report["jobs_removed"] == 3 and report["runs_removed"] == 1 and report["bytes_freed"] == 200
    with service.db.session() as s:
        assert s.scalar(select(Run).where(Run.id == run_id)) is None
        assert not list(s.scalars(select(Event).where(Event.run_id == run_id)))
        assert not list(s.scalars(select(Review).where(Review.run_id == run_id)))
        assert s.get(Setting, f"job_origin:{failed}") is None and s.get(Setting, f"failure_report:{run_id}") is None
    assert not (service.config.data_dir / "runs" / run_id).exists()


def test_a_dry_run_counts_the_same_and_changes_nothing(service):
    gone = job(service, "a", status="closed")
    run_id = run(service, gone, "closed", files=["steps/1.json"], events=["agent_step"])
    report = sweep(service.db, service.config.data_dir, apply=False, at=NOW)
    assert report["jobs_removed"] == 1 and report["runs_removed"] == 1 and report["bytes_freed"] == 100
    assert has_job(service, gone) and (service.config.data_dir / "runs" / run_id / "steps" / "1.json").exists()


def test_every_trace_of_a_possible_submission_stays(service):
    confirmed = job(service, "a", status="closed")
    run(service, confirmed, "confirmed")
    uncertain = job(service, "b", status="archived")
    run(service, uncertain, "submission_unknown")
    # A later failure does not hide a commit that may have reached the employer.
    attempted = job(service, "c", status="closed")
    run(service, attempted, "failed", events=["submitting"])
    duplicate = job(service, "d", status="closed")
    run(service, duplicate, "already_applied")
    assert clean(service)["jobs_removed"] == 0
    for kept in (confirmed, uncertain, attempted, duplicate):
        assert has_job(service, kept)


def test_open_work_stays(service):
    waiting = job(service, "a", status="waiting_answer")
    run(service, waiting, "waiting_answer", days=1)
    browser = job(service, "b", status="waiting_browser")
    run(service, browser, "waiting_browser", files=["steps/1.json"])
    paused = job(service, "c")
    requested = job(service, "d")
    recovering = job(service, "e", status="closed")
    run(service, recovering, "closed", files=["steps/1.json"])
    with service.db.session() as s:
        s.add(Setting(key=f"job_control:{paused}", value={"state": "paused"}))
        s.add(Setting(key=f"owner_apply:{requested}", value={"at": ago(1)}))
        s.add(Setting(key=f"draft_recovery:{recovering}", value={"scheduled": True}))
    clean(service)
    for kept in (waiting, browser, paused, requested, recovering):
        assert has_job(service, kept)
    assert list((service.config.data_dir / "runs").rglob("1.json"))


def test_a_question_left_unanswered_past_the_jobs_age_goes_with_the_job(service):
    old = job(service, "a", status="waiting_answer")
    run(service, old, "waiting_answer", days=20)
    review = job(service, "b", status="needs_review")
    run(service, review, "needs_review", days=20)
    sent = job(service, "c", status="waiting_answer")
    run(service, sent, "submission_unknown", days=25)
    run(service, sent, "waiting_answer", days=20)
    clean(service)
    assert not has_job(service, old) and not has_job(service, review)
    assert has_job(service, sent)  # Something may have reached the employer.


def test_a_superseded_old_stop_does_not_keep_a_closed_job(service):
    closed = job(service, "a", status="closed")
    run(service, closed, "waiting_browser", days=30)
    run(service, closed, "closed", days=29)
    clean(service)
    assert not has_job(service, closed)


def test_recent_jobs_a_waiting_turn_and_a_listed_job_stay(service):
    recent = job(service, "a", days=3)
    waiting_turn = job(service, "b", status="ready", days=10)
    old_turn = job(service, "c", status="ready", days=20)
    listed = job(service, "d", status="skipped", days=30)
    unlisted = job(service, "e", status="skipped", days=30)
    def listing(job_id, active, seen):
        return JobListing(job_id=job_id, source_id="s", posted_at=ago(30), date_basis="posted", location="", career_level="",
                          match_reason="", active=active, first_seen=ago(30), last_seen=seen)

    with service.db.session() as s:
        s.add(listing(listed, True, ago(1)))
        s.add(listing(unlisted, True, ago(20)))
    clean(service)
    assert has_job(service, recent) and has_job(service, waiting_turn) and has_job(service, listed)
    assert not has_job(service, old_turn) and not has_job(service, unlisted)


def test_old_logs_go_but_a_sent_applications_record_stays(service):
    sent_job = job(service, "a", status="confirmed", days=40)
    sent = run(service, sent_job, "confirmed", days=20, files=["final.png", "answers.json", "steps/1.html", "help.jpg", "upload/r.pdf"],
               events=["submitting", "finished", "agent_step", "field_timing"])
    failed = run(service, sent_job, "waiting_browser", days=21, files=["steps/2.html", "final.png"], events=["agent_step"])
    new_job = job(service, "b", status="confirmed", days=3)
    fresh = run(service, new_job, "confirmed", days=3, files=["steps/3.html"], events=["agent_step"])
    clean(service)
    root = service.config.data_dir / "runs"
    assert sorted(p.name for p in (root / sent).iterdir()) == ["answers.json", "final.png"]
    assert not (root / failed).exists()
    assert (root / fresh / "steps" / "3.html").exists()
    with service.db.session() as s:
        kinds = lambda r: sorted(s.scalars(select(Event.kind).where(Event.run_id == r)))
        assert kinds(sent) == ["finished", "submitting"]
        assert kinds(failed) == []
        assert kinds(fresh) == ["agent_step"]
        assert s.get(Run, failed) is not None  # Its row stays: the job's history, and the failed run's reason.


def test_an_open_job_keeps_its_latest_runs_files_and_older_logs_go(service):
    # Recovery reads the latest run. Owner 2026-10-31: no logs older than a week, and 554 older runs had kept theirs.
    open_job = job(service, "a", status="waiting_browser", days=30)
    older = run(service, open_job, "failed", days=20, files=["steps/1.html"], events=["agent_step"])
    latest = run(service, open_job, "waiting_browser", days=10, files=["steps/2.html"], events=["agent_step"])
    clean(service)
    root = service.config.data_dir / "runs"
    assert not (root / older).exists()
    assert (root / latest / "steps" / "2.html").exists()
    with service.db.session() as s:
        assert not s.scalars(select(Event).where(Event.run_id == older)).all()
        assert s.scalars(select(Event).where(Event.run_id == latest)).all()


def test_only_old_run_folders_with_no_run_are_orphans(service):
    root = service.config.data_dir / "runs"
    old, new, other = root / ("a" * 32), root / ("b" * 32), root / "keep-me"
    for folder in (old, new, other):
        folder.mkdir(parents=True)
        (folder / "f").write_bytes(b"x")
    import os

    stamp = (NOW - timedelta(days=30)).timestamp()
    os.utime(old, (stamp, stamp))
    os.utime(other, (stamp, stamp))
    os.utime(new, (NOW.timestamp(), NOW.timestamp()))
    assert clean(service)["orphans_removed"] == 1
    assert not old.exists() and new.exists() and other.exists()


def test_zero_keeps_everything(service):
    gone = job(service, "a", status="closed")
    run_id = run(service, gone, "closed", files=["steps/1.json"])
    clean(service, job_days=0, log_days=0)
    assert has_job(service, gone) and (service.config.data_dir / "runs" / run_id / "steps" / "1.json").exists()


def test_a_stop_ends_the_round(service):
    gone = job(service, "a")
    report = sweep(service.db, service.config.data_dir, apply=True, at=NOW, stop=lambda: True)
    assert report["stopped_early"] and has_job(service, gone)
