"""Jobs › Pipeline: exact counts and one page of rows, computed in SQL over each job's latest run.

The dashboard used to receive the newest 1,000 jobs and the newest 1,000 runs and count them in the browser. With 7,000+
jobs and thousands of runs (2026-10-22) every count was wrong: "in progress", "needs you", the Jobs tab, the Batch
page's queued and repairing figures. Older jobs whose newest run was recent were missing from the list altogether.
Counts here are SQL aggregates over every job, and the list sends only the rows of the page being looked at.

Pipeline is what has started or will start: the queue (including the owner's Apply clicks still waiting for a worker,
pacing or a shared sender), running, needs you, repairing, done, stopped. Jobs found but never applied to belong to
Jobs › Discovered (owner, 2026-10-23: "so confusing for the job pipeline part, like the pipeline and the discovery").

How a row reads mirrors `frontend/src/model.ts` (`displayState`, `STATES[...].group`); a test keeps the two in step.
"""

from sqlalchemy import and_, case, func, or_, select

from ..db import Job, Review, Run, Setting, bytewise, latest_runs

CONTROL = "job_control:"
# owner_apply.KEY: an Apply the admission deferred; it starts on its own (owner_apply.due).
REQUEST = "owner_apply:"
# A submission that began or has evidence: a pause never hides it (model.ts COMMITTED).
COMMITTED = ("confirmed", "submission_unknown", "already_applied", "submitting")
# A waiting Apply reads as "Waiting to start" unless its job already has one of these runs (model.ts STARTED).
STARTED = ("queued", "running", *COMMITTED)
WAITING = "pending:waiting"
# The group of a run's state (model.ts STATES). A state not listed reads as "stopped".
RUN_GROUPS = {
    "active": ("queued", "running", "submitting", "waiting_email"),
    "needs": ("waiting_answer", "needs_review"),
    "repair": ("waiting_browser", "timed_out", "failed"),
    "unconfirmed": ("submission_unknown",),
    "done": ("confirmed", "already_applied", "dry_run_passed"),
    "stopped": ("cancelled", "skipped", "closed"),
}
# The group of a job that has no run yet (model.ts "job:<status>"). A status not listed reads as "new".
JOB_GROUPS = {"new": ("new", "ready", "review", "skipped"), "paused": ("paused",)}
GROUPS = ("active", "needs", "repair", "unconfirmed", "paused", "done", "stopped", "new")
# List order for "All": what needs attention first (model.ts PRIORITY).
PRIORITY = {group: index for index, group in enumerate(GROUPS[:5] + ("new", "done", "stopped"))}
# Latest runs the Batch page shows live: owners at work, waits and questions.
LIVE = ("queued", "running", "submitting", "waiting_email", "waiting_answer", "needs_review")


def held_jobs():
    """Job ids the owner paused (job_control:<id> {"state": "paused"})."""
    return select(func.substr(Setting.key, len(CONTROL) + 1)).where(
        Setting.key.like(CONTROL + "%"), Setting.value["state"].as_string() == "paused"
    )


def requested_jobs():
    """Job ids with a waiting owner Apply (owner_apply:<id>)."""
    return select(func.substr(Setting.key, len(REQUEST) + 1)).where(Setting.key.like(REQUEST + "%"))


def classified(*where):
    """Every non-demo, non-archived job with its latest run and display group, as a subquery."""
    latest = latest_runs()
    held = Job.id.in_(held_jobs())
    state = latest.c.state
    paused_by_owner = and_(held, or_(state.is_(None), state.not_in(COMMITTED)))
    waiting = and_(
        Job.id.in_(requested_jobs()), ~paused_by_owner, or_(state.is_(None), state.not_in(STARTED))
    )
    # A job an earlier run confirmed is done, whatever a later attempt says: Dayforce's confirmed application read as
    # "Repairing" after a follow-up run failed (2026-10-25).
    succeeded = Job.id.in_(select(Run.job_id).where(Run.state.in_(("confirmed", "already_applied"))))
    group = case(
        (succeeded, "done"),
        (paused_by_owner, "paused"),
        (waiting, "active"),
        (state == "paused", "paused"),
        *[(state.in_(states), name) for name, states in RUN_GROUPS.items()],
        (state.is_not(None), "stopped"),
        *[(Job.status.in_(statuses), name) for name, statuses in JOB_GROUPS.items()],
        else_="new",
    )
    updated = func.coalesce(latest.c.finished_at, latest.c.started_at, latest.c.created_at, Job.created_at)
    return (
        select(
            Job.id.label("job_id"),
            latest.c.id.label("run_id"),
            state.label("state"),
            held.label("held"),
            waiting.label("waiting"),
            group.label("grp"),
            updated.label("updated"),
        )
        .select_from(Job)
        .outerjoin(latest, latest.c.job_id == Job.id)
        .where(Job.demo.is_(False), Job.status != "archived", *where)
        .subquery("classified")
    )


def in_pipeline(rows):
    """Pipeline scope: a job with a run, one the owner paused, a waiting Apply, or (group "new") one not started."""
    return or_(rows.c.run_id.is_not(None), rows.c.held, rows.c.waiting, rows.c.grp == "new")


def counts(session):
    """Exact counts for the Jobs tab, the Pipeline chips and the Batch page, over every job."""
    rows = classified()
    columns = (rows.c.grp, rows.c.state, rows.c.held, rows.c.waiting, rows.c.run_id.is_not(None))
    grouped = session.execute(
        select(*columns, func.count()).where(in_pipeline(rows)).group_by(*columns)
    ).all()
    groups = dict.fromkeys(GROUPS, 0)
    states = {}
    for group, state, held, waiting, has_run, n in grouped:
        groups[group] += n
        # A run's own state, unless the owner's pause or a waiting Apply overrides how it reads (model.ts
        # displayState).
        shown = (
            WAITING
            if waiting
            else state
            if has_run and not (held and group == "paused" and state != "paused")
            else None
        )
        if shown:
            states[shown] = states.get(shown, 0) + n
    jobs = session.scalar(
        select(func.count()).select_from(Job).where(Job.demo.is_(False), Job.status != "archived")
    )
    pipeline = sum(n for group, n in groups.items() if group != "new")
    return {"groups": {**groups, "all": pipeline}, "states": states, "pipeline": pipeline, "jobs": jobs or 0}


def _search(q):
    text = (q or "").strip().lower()
    if not text:
        return ()
    haystack = func.lower(Job.company + " " + Job.title + " " + Job.location + " " + Job.ats)
    return (haystack.contains(text, autoescape=True),)


def page(session, group="all", q="", page=1, size=25):
    """One page of Pipeline rows: "all" is every started or paused job, attention first; a group lists only its own."""
    rows = classified(*_search(q))
    scope = [in_pipeline(rows), rows.c.grp != "new" if group == "all" else rows.c.grp == group]
    size = max(1, min(100, int(size)))
    priority = case(*[(rows.c.grp == name, rank) for name, rank in PRIORITY.items()], else_=len(PRIORITY))

    def fetch(number):
        # The total rides along with the page (count over the filtered rows): one pass over the history, not two.
        return session.execute(
            select(rows.c.job_id, rows.c.run_id, func.count().over().label("total"))
            .where(*scope)
            .order_by(priority, bytewise(rows.c.updated).desc(), bytewise(rows.c.job_id))
            .offset((number - 1) * size)
            .limit(size)
        ).all()

    page = max(1, int(page))
    picked = fetch(page)
    if picked:
        total = picked[0].total
    else:
        # Past the end (the list shrank since the reader paged): the last page instead.
        total = session.scalar(select(func.count()).select_from(rows).where(*scope)) or 0
        page = max(1, -(-total // size))
        picked = fetch(page) if total else []
    pages = max(1, -(-total // size))
    pairs = [(row.job_id, row.run_id) for row in picked]
    return {"rows": rows_for(session, pairs), "total": total, "page": page, "size": size, "pages": pages}


JOB_COLUMNS = tuple(column for column in Job.__table__.columns if column.name != "description")
# No packet keys: PostgreSQL parses a run's whole ~320 KB json packet for each key read. Worker slots come with the
# snapshot's application_workers.
RUN_COLUMNS = tuple(column for column in Run.__table__.columns if column.name != "packet")


def rows_for(session, picked):
    """Dashboard rows for (job_id, latest run_id) pairs, in order: the job, its latest run, tries, control, questions."""
    job_ids = [job_id for job_id, _ in picked]
    run_ids = [run_id for _, run_id in picked if run_id]
    if not job_ids:
        return []
    jobs = {
        row.id: dict(row._mapping) for row in session.execute(select(*JOB_COLUMNS).where(Job.id.in_(job_ids)))
    }
    runs = (
        {
            row.id: dict(row._mapping)
            for row in session.execute(select(*RUN_COLUMNS).where(Run.id.in_(run_ids)))
        }
        if run_ids
        else {}
    )
    tries = dict(
        session.execute(
            select(Run.job_id, func.count()).where(Run.job_id.in_(job_ids)).group_by(Run.job_id)
        ).all()
    )
    controls = {
        row.key.removeprefix(CONTROL): {
            key: row.value[key] for key in ("state", "at", "previous_status") if key in (row.value or {})
        }
        for row in session.scalars(select(Setting).where(Setting.key.in_([CONTROL + j for j in job_ids])))
    }
    # A waiting Apply: why it waits and when it is tried again (owner_apply.start).
    requests = {
        row.key.removeprefix(REQUEST): {
            key: row.value[key] for key in ("at", "code", "reason", "not_before") if key in (row.value or {})
        }
        for row in session.scalars(select(Setting).where(Setting.key.in_([REQUEST + j for j in job_ids])))
    }
    questions = {}
    if run_ids:
        for review in session.scalars(
            select(Review)
            .where(Review.run_id.in_(run_ids), Review.answer.is_(None))
            .order_by(Review.created_at)
        ):
            questions.setdefault(review.run_id, []).append(
                {
                    k: getattr(review, k)
                    for k in ("id", "run_id", "question", "options", "key", "reason", "created_at")
                }
            )
    return [
        {
            "job": jobs[job_id],
            "run": runs.get(run_id) if run_id else None,
            "attempts": tries.get(job_id, 0),
            "control": controls.get(job_id) or None,
            "request": requests.get(job_id),
            "reviews": questions.get(run_id, []) if run_id else [],
        }
        for job_id, run_id in picked
        if job_id in jobs
    ]


def _newest_of(session, candidates):
    """The (job_id, run_id) pairs among candidate runs that are their job's newest run, on a real, active job.

    Ranks only the runs of the candidates' jobs, never the whole history.
    """
    jobs = select(Run.job_id).where(Run.id.in_(candidates))
    newest = latest_runs(Run.job_id.in_(jobs))
    return session.execute(
        select(newest.c.job_id, newest.c.id, newest.c.state, newest.c.finished_at)
        .join(Job, Job.id == newest.c.job_id)
        .where(newest.c.id.in_(candidates), Job.demo.is_(False), Job.status != "archived")
    ).all()


def live_rows(session, recent=20):
    """The rows the Batch page needs every poll: live and waiting applications, open questions and the latest outcomes.

    Bounded by the workers and the open questions, not by the size of the history.
    """
    live = _newest_of(session, select(Run.id).where(Run.state.in_(LIVE)))
    asked = _newest_of(session, select(Review.run_id).where(Review.answer.is_(None)))
    # The latest outcomes: recently finished runs that are still their job's newest (a few spare for retried jobs).
    ended = (
        select(Run.id)
        .where(Run.finished_at.is_not(None), Run.state.not_in(("paused", *LIVE)))
        .order_by(bytewise(Run.finished_at).desc())
        .limit(recent * 3)
    )
    finished = sorted(_newest_of(session, ended), key=lambda row: row.finished_at, reverse=True)[:recent]
    picked = dict.fromkeys((row.job_id, row.id) for row in (*live, *asked, *finished))
    return rows_for(session, list(picked))


def runs_of(session, job_id, limit=200):
    """Every attempt of one job, newest first, without packets."""
    return [
        dict(row._mapping)
        for row in session.execute(
            select(*RUN_COLUMNS)
            .where(Run.job_id == job_id)
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(limit)
        )
    ]
