"""Jobs › Pipeline: exact counts and one page of rows, computed in SQL over each job's latest run.

The dashboard used to receive the newest 1,000 jobs and the newest 1,000 runs and count them in the browser. With 7,000+
jobs and thousands of runs (2026-10-22) every count was wrong: "in progress", "needs you", the Jobs tab, the Batch
page's queued and repairing figures. Older jobs whose newest run was recent were missing from the list altogether.
Counts here are SQL aggregates over every job, and the list sends only the rows of the page being looked at.

Pipeline is what has started or will start: the queue (including the owner's Apply clicks still waiting for a worker,
pacing or a shared sender), running, needs you, repairing, done, stopped. Jobs found but never applied to belong to
Jobs › Discovered (owner, 2026-10-23: "so confusing for the job pipeline part, like the pipeline and the discovery").

Jobs at a reserved employer (FAANG, MANGO, the Big Four: reserved.py) that the owner has not approved read as their own
group, "reserved", whatever their latest run says, until an application starts or is submitted (owner, 2026-10-27:
"make a different section for FAANG, MANGO, Big four consulting"). Like "new", they are outside the pipeline: nothing
starts them until the owner approves them in Jobs › Reserved.

How a row reads mirrors `frontend/src/model.ts` (`displayState`, `STATES[...].group`); a test keeps the two in step.
"""

from sqlalchemy import and_, case, false as sa_false, func, or_, select
from sqlalchemy.orm import aliased

from ..application_budget import NOTHING_SENT
from ..vocabulary import WORDS
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
# "done" is the Applications ledger and nothing else (`confirmed_jobs`): the Jobs chip and the ledger tab are one number.
# "existing" is the employer saying you had already applied: nothing was sent, so it is not in the ledger (2026-11-01: the
# Done chip said 3,302 beside a ledger of 3,155; 146 of the difference were these, one was a dry run).
RUN_GROUPS = {
    "active": ("queued", "running", "submitting", "waiting_email"),
    "needs": ("waiting_answer", "needs_review"),
    "repair": ("waiting_browser", "timed_out", "failed"),
    "unconfirmed": ("submission_unknown",),
    "done": ("confirmed",),
    "existing": ("already_applied",),
    "stopped": ("cancelled", "skipped", "closed", "dry_run_passed"),
}
# The group of a job that has no run yet (model.ts "job:<status>"). A status not listed reads as "new".
JOB_GROUPS = {"new": ("new", "ready", "review", "skipped"), "paused": ("paused",)}
GROUPS = ("active", "needs", "repair", "unconfirmed", "paused", "done", "existing", "stopped", "new", "reserved")
# Groups outside the pipeline: never started, or held for the owner's approval.
OUTSIDE = ("new", "reserved")
# List order for "All": what needs attention first (model.ts PRIORITY).
PRIORITY = {group: index for index, group in enumerate(GROUPS[:5] + ("new", "done", "existing", "stopped", "reserved"))}
# A stopped run whose own reason says what it needs reads by that, not by its state: "Repairing" is for stops the engine
# can still fix. 2026-10-29: 28 of 32 Repairing jobs waited on the employer's mail or the owner's own browser (a refused
# password, Amazon's Persona identity check), and waits that sent nothing (sender busy, model or browser down) retry
# themselves (recovery.WAIT_BACKOFF).
# "%persona%" alone also matched the repair agent's own sentence "not a missing personal fact", which sent Neon's
# resume-upload site error to "Needs you" (2026-10-29): only the identity-check product's name, in its usual phrases.
OWNER_ONLY = (
    "%persona identity%", "%persona verif%", "%persona check%", "%persona page%", "%persona step%", "%persona's%",
    "%identity verification%", "%in your own browser%", "%idverify%",
)
STOPPED = ("waiting_browser", "timed_out", "failed")
# Latest runs the Batch page shows live: owners at work, waits and questions.
LIVE = ("queued", "running", "submitting", "waiting_email", "waiting_answer", "needs_review")


def settled_confirmation():
    """The one test of a confirmed run: its evidence is not still being reconciled (a legacy marker). Every number that
    says "confirmed" (the ledger, the Jobs chip, Overview, the batch goal) counts runs that pass it."""
    return and_(Run.state == "confirmed", Run.receipt["evidence_reconciliation"].as_string().is_distinct_from("pending"))


def confirmed_jobs():
    """CONFIRMED: the ids of the real jobs with at least one settled confirmed run, a later failed attempt or not. This is
    the Applications ledger, the Jobs "Confirmed" chip (group "done") and Overview's total: one definition, one query."""
    return select(Run.job_id).join(Job, Job.id == Run.job_id).where(Job.demo.is_(False), settled_confirmation()).distinct()


def held_jobs():
    """Job ids the owner paused (job_control:<id> {"state": "paused"})."""
    return select(func.substr(Setting.key, len(CONTROL) + 1)).where(
        Setting.key.startswith(CONTROL, autoescape=True), Setting.value["state"].as_string() == "paused"
    )


def requested_jobs():
    """Job ids with a waiting owner Apply (owner_apply:<id>)."""
    return select(func.substr(Setting.key, len(REQUEST) + 1)).where(Setting.key.startswith(REQUEST, autoescape=True))


def classified(*where, reserved=(), gave_up=()):
    """Every non-demo, non-archived job with its latest run and display group, as a subquery. `reserved`: the ids of
    jobs held for the owner's approval (reserved.held); `gave_up`: stopped jobs no retry is coming for (outlook.given_up)."""
    latest = latest_runs()
    held = Job.id.in_(held_jobs())
    state = latest.c.state
    # The latest run's reason, joined by primary key (latest_runs carries no text columns). As a correlated subquery
    # it ran once per pattern per row: 235 subplans, and 4-11 s for one Pipeline page (2026-10-29).
    last = aliased(Run, name="last_run")
    why = func.lower(last.reason)
    owner_only = and_(state.in_(STOPPED), or_(*[why.like(pattern) for pattern in OWNER_ONLY]))
    # A bot-check pause (bot_wall) is not an automatic retry.
    retrying = and_(
        state.in_(("waiting_browser", "timed_out")),
        or_(*[why.like(prefix.lower() + "%") for prefix in NOTHING_SENT if not prefix.startswith("A platform bot")]),
    )
    paused_by_owner = and_(held, or_(state.is_(None), state.not_in(COMMITTED)))
    # Held until approved, unless an application is already under way or submitted: that one still reads as itself.
    reserving = (
        and_(Job.id.in_(list(reserved)), ~paused_by_owner, or_(state.is_(None), state.not_in(STARTED)))
        if reserved
        else None
    )
    waiting = and_(
        Job.id.in_(requested_jobs()), ~paused_by_owner, or_(state.is_(None), state.not_in(STARTED))
    )
    if reserving is not None:
        waiting = and_(waiting, ~reserving)
    # A job an earlier run confirmed is done, whatever a later attempt says: Dayforce's confirmed application read as
    # "Repairing" after a follow-up run failed (2026-10-25).
    succeeded = Job.id.in_(confirmed_jobs())
    applied_before = Job.id.in_(select(Run.job_id).where(Run.state == "already_applied"))
    # A confirmed run whose evidence is still being reconciled is not in the ledger: it reads as unconfirmed, not done.
    unsettled = and_(state == "confirmed", func.coalesce(last.receipt["evidence_reconciliation"].as_string(), "") == "pending")
    group = case(
        (succeeded, "done"),
        (applied_before, "existing"),
        (paused_by_owner, "paused"),
        *([(reserving, "reserved")] if reserving is not None else []),
        (waiting, "active"),
        (state == "paused", "paused"),
        (unsettled, "unconfirmed"),
        (owner_only, "needs"),
        (retrying, "active"),
        *[(state.in_(states), name) for name, states in RUN_GROUPS.items()],
        (state.is_not(None), "stopped"),
        *[(Job.status.in_(statuses), name) for name, statuses in JOB_GROUPS.items()],
        else_="new",
    )
    updated = func.coalesce(latest.c.finished_at, latest.c.started_at, latest.c.created_at, Job.created_at)
    inner = (
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
        .outerjoin(last, last.id == latest.c.id)
        # The ledger keeps an archived job's confirmation, so the Confirmed chip does too.
        .where(Job.demo.is_(False), or_(Job.status != "archived", succeeded), *where)
        .subquery("grouped")
    )
    # The five words (vocabulary.py) over the group: the only place a stopped job becomes "Applying" or "Couldn't apply".
    given = inner.c.job_id.in_(list(gave_up)) if gave_up else sa_false()
    word = case(
        (inner.c.grp == "done", "applied"),
        (and_(inner.c.grp == "repair", given), "failed"),
        (inner.c.grp.in_(("active", "repair")), "applying"),
        (inner.c.grp.in_(("needs", "unconfirmed")), "needs"),
        # Found, not started: the To apply list (Discovered), not one of the five words.
        (inner.c.grp == "new", "to_apply"),
        else_="not",
    )
    return select(inner, word.label("word")).subquery("classified")


def rows_of(session, *where):
    """`classified` with the reserved employers and the jobs the app has given up on filled in."""
    from ..outlook import given_up
    from ..reserved import held as awaiting

    return classified(*where, reserved=awaiting(session), gave_up=given_up(session))


def in_pipeline(rows):
    """Pipeline scope: a job with a run, one the owner paused, a waiting Apply, or (groups "new" and "reserved") one
    not started."""
    return or_(rows.c.run_id.is_not(None), rows.c.held, rows.c.waiting, rows.c.grp.in_(OUTSIDE))


def counts(session):
    """Exact counts for the Jobs tab, the Pipeline chips and the Batch page, over every job."""
    rows = rows_of(session)
    columns = (rows.c.grp, rows.c.word, rows.c.state, rows.c.held, rows.c.waiting, rows.c.run_id.is_not(None))
    grouped = session.execute(
        select(*columns, func.count()).where(in_pipeline(rows)).group_by(*columns)
    ).all()
    groups = dict.fromkeys(GROUPS, 0)
    words = dict.fromkeys(WORDS, 0)
    states = {}
    for group, word, state, held, waiting, has_run, n in grouped:
        groups[group] += n
        if word in words:
            words[word] += n
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
    pipeline = sum(n for group, n in groups.items() if group not in OUTSIDE)
    # CONFIRMED, read straight from its definition: the ledger's count, and the Jobs "Confirmed" chip (groups["done"]).
    confirmed = session.scalar(select(func.count()).select_from(confirmed_jobs().subquery()))
    return {
        "groups": {**groups, "all": pipeline},
        "states": states,
        "pipeline": pipeline,
        "jobs": jobs or 0,
        "confirmed": confirmed or 0,
        # The five words (vocabulary.py): Applied equals `confirmed`, and every number opens that many rows (GET /api/jobs?group=<word>).
        "words": words,
    }


def funnel(session):
    """All time, for Overview's funnel and success by platform. Discovered is every real job found; eligible is one that passed the
    fit check (neither unranked nor below fit) or was started anyway; applied is one where an application really started, by
    platform too. Dry runs prove nothing was sent, so they don't count. Archived jobs were found and tried like any other, so
    they stay in: the confirmed count on the same panel includes them. Confirmed itself is GET /api/successful-applications."""
    started = select(Run.job_id).where(Run.mode == "submit", Run.started_at.is_not(None))
    applied = Job.id.in_(started).label("applied")
    rows = session.execute(
        select(Job.ats, Job.status, applied, func.count()).where(Job.demo.is_(False)).group_by(Job.ats, Job.status, "applied")
    ).all()
    out = {"discovered": 0, "eligible": 0, "applied": 0, "platforms": {}}
    for ats, status, was_applied, n in rows:
        out["discovered"] += n
        if was_applied or status not in ("new", "skipped", "archived"):
            out["eligible"] += n
        if was_applied:
            out["applied"] += n
            out["platforms"][ats] = out["platforms"].get(ats, 0) + n
    return out


def _search(q):
    text = (q or "").strip().lower()
    if not text:
        return ()
    haystack = func.lower(Job.company + " " + Job.title + " " + Job.location + " " + Job.ats)
    return (haystack.contains(text, autoescape=True),)


def page(session, group="all", q="", page=1, size=25):
    """One page of Pipeline rows: "all" is every started or paused job, attention first; a group lists only its own."""
    found = _search(q)
    if found:
        # The search is read first, as a list of ids: written into the one big query, the planner takes the filter for one row
        # and joins every job to the whole run history one at a time (a search page was 6 s, an unsearched one 1.5 s; 2026-11-01).
        ids = session.scalars(select(Job.id).where(*found)).all()
        found = (Job.id.in_(ids),) if ids else (sa_false(),)
    rows = rows_of(session, *found)
    # A word (applied, applying, needs, failed, not) lists exactly what its count says; the older group names still open.
    by = rows.c.word if group in WORDS else rows.c.grp
    scope = [in_pipeline(rows), rows.c.grp.not_in(OUTSIDE) if group == "all" else by == group]
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
    # The chips' counts from the same read as the rows: the cached snapshot lagged up to 30 s, so a chip said
    # "Unconfirmed 4" over a list of three (owner 2026-10-28: "the data on the frontend is not properly synced").
    return {
        "rows": rows_for(session, pairs),
        "total": total,
        "page": page,
        "size": size,
        "pages": pages,
        "counts": counts(session),
    }


JOB_COLUMNS = tuple(column for column in Job.__table__.columns if column.name != "description")
# No packet keys: PostgreSQL parses a run's whole ~320 KB json packet for each key read. Worker slots come with the
# snapshot's application_workers.
RUN_COLUMNS = tuple(column for column in Run.__table__.columns if column.name != "packet")


def words_of(session, job_ids):
    """{job id: word} for these jobs, by the same classification the counts use."""
    from ..outlook import given_up
    from ..reserved import held as awaiting

    ids = list(job_ids)
    rows = classified(Job.id.in_(ids), reserved=awaiting(session), gave_up=given_up(session, only=ids))
    return {row.job_id: row.word for row in session.execute(select(rows.c.job_id, rows.c.word))}


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
    # A reserved employer's job, approved or not (reserved.py): its section, so the row can offer the approval.
    from ..reserved import approvals, matcher

    rules = matcher(session)
    allowed = approvals(session, job_ids)
    reserved = {
        job_id: {**found.as_dict(), "approved": job_id in allowed}
        for job_id, job in jobs.items()
        if not job["demo"] and (found := rules(job["company"], job["url"]))
    }
    # CONFIRMED jobs read as confirmed whatever their latest attempt says (the Jobs "Confirmed" chip lists them).
    confirmed = set(session.scalars(confirmed_jobs().where(Run.job_id.in_(job_ids))))
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
    words = words_of(session, job_ids)
    from ..vocabulary import describe

    def sentence(job_id, run):
        state = (run or {}).get("state", "")
        control = (controls.get(job_id) or {}).get("state")
        wait = job_id in requests and (not run or run["state"] not in STARTED)
        return describe(
            words.get(job_id, "not"),
            "pending:waiting" if wait else state,
            (run or {}).get("reason", ""),
            request_reason=(requests.get(job_id) or {}).get("reason", ""),
            reserved=bool((reserved.get(job_id) or {}) and not reserved[job_id]["approved"] and (not run or run["state"] not in STARTED)),
            paused=control == "paused" and (not run or run["state"] not in COMMITTED),
        )

    return [
        {
            "job": jobs[job_id],
            "word": words.get(job_id, "not"),
            "sentence": sentence(job_id, runs.get(run_id) if run_id else None),
            "run": runs.get(run_id) if run_id else None,
            "attempts": tries.get(job_id, 0),
            "control": controls.get(job_id) or None,
            "request": requests.get(job_id),
            "reviews": questions.get(run_id, []) if run_id else [],
            "reserved": reserved.get(job_id),
            "confirmed": job_id in confirmed,
        }
        for job_id, run_id in picked
        if job_id in jobs
    ]


def awaiting_approval(session, *where):
    """(job id, latest run id, reserved.Match) of every job in the "reserved" group, newest first: held for the owner's
    approval and not already applying or submitted."""
    from ..reserved import held

    found = held(session)
    if not found:
        return []
    rows = classified(*where, reserved=found)
    return [
        (row.job_id, row.run_id, found[row.job_id])
        for row in session.execute(
            select(rows.c.job_id, rows.c.run_id)
            .where(rows.c.grp == "reserved")
            .order_by(bytewise(rows.c.updated).desc(), bytewise(rows.c.job_id))
        )
    ]


def reserved_page(session, section="", q="", page=1, size=25):
    """Jobs › Reserved: the jobs held for the owner's approval, one section (FAANG, MANGO, Big Four...) at a time, newest
    first, with every section's count (whatever the search: "Approve all" covers them all). The first section with held
    jobs when none is chosen."""
    from ..reserved import active as reserved_active, rules as saved_rules

    every = awaiting_approval(session)
    shown = awaiting_approval(session, *_search(q)) if _search(q) else every
    held = {}
    for _, _, found in every:
        held[found.section] = held.get(found.section, 0) + 1
    rules = saved_rules(session)
    sections = [
        {
            "id": item.id,
            "label": item.label,
            "employers": list(dict.fromkeys(employer.name for employer in item.employers)),
            "held": held.get(item.id, 0),
        }
        for item in reserved_active(rules)
    ]
    ids = [item["id"] for item in sections]
    chosen = section if section in ids else next((item["id"] for item in sections if item["held"]), ids[0] if ids else "")
    items = [(job_id, run_id) for job_id, run_id, found in shown if found.section == chosen]
    size = max(1, min(100, int(size)))
    pages = max(1, -(-len(items) // size))
    page = min(max(1, int(page)), pages)
    return {
        "enabled": bool(sections),
        "sections": sections,
        "section": chosen,
        "rows": rows_for(session, items[(page - 1) * size : page * size]),
        "total": len(items),
        "page": page,
        "pages": pages,
        "size": size,
    }


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
