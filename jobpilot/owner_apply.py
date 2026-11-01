"""One click applies (owner, 2026-10-18: "I do not want to explain why a job is a match, a click should do the work";
"queuing is super slow and bugging").

The owner's Apply approves the job (no reason to type; the job's resume, else the first uploaded one) and queues it.
When admission defers the start (all workers busy, a platform's pacing, a shared sender in use, an employer's
cooldown), that is not an error: the request is remembered, and the autopilot starts it as soon as admission allows.
"""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .admission import QueueBlocked
from .db import Job, Resume, Setting, now
from .service.queueing import off_loop

KEY = "owner_apply:"
# A job the owner applied to or pasted. Its retries keep the owner's say over the autopilot's fit filters.
PICK = "owner_pick:"
OWNER_APPROVED = "Owner approved: "
PREPARING = "Preparing a tailored resume first. The application starts on its own when it's ready."
# One request applies to at most this many jobs (Jobs › Discovered "Apply to selected", 2026-10-22).
MANY = 50


def picked(session, job_id):
    return session.get(Setting, PICK + job_id) is not None


def _later(not_before):
    return not_before or (datetime.now(UTC) + timedelta(seconds=60)).isoformat()


# Refusals that end when a slot, a worker or a mail sender frees: tried again on the next autopilot tick (a few
# seconds), not after a fixed wait. The 60-second not_before admission gives them made a freed slot idle until the
# request's turn came round; with 140 requests waiting, 4 of 9 workers sat idle (2026-10-29).
FREES_UP = frozenset({"worker_capacity", "employer_busy", "sender_busy", "stopped"})
# Admissions one pass tries: each takes the app lock, and a full pool refuses all the rest alike.
TRIES = 12


def _retry_at(exc):
    return now() if exc.code in FREES_UP else _later(exc.not_before)


async def apply(service, job_id):
    """Approve and start one job, or remember it until admission allows."""
    answered = await off_loop(_approve, service, job_id)
    return answered or await start(service, job_id)


async def apply_many(service, job_ids):
    """The owner's Apply for several jobs (owner, 2026-10-22: "for unfit applications there should be an option of
    ticking it or select all, to apply to a few at once rather than opening every single one").

    Each job goes through exactly the single-click path, one after another, so every duplicate-submission and
    admission guard decides it as it would a click. The answer reports each job: queued, waiting (with the reason),
    already running or applied, or refused (with the reason).
    """
    results = []
    for job_id in list(dict.fromkeys(job_ids))[:MANY]:
        try:
            result = await apply(service, job_id)
            # queued | waiting | preparing; a job that was already queued, running or applied needed no start.
            started = result["state"] != "queued" or result.get("run_id")
            outcome = "already" if result["state"] in ALREADY or not started else result["state"]
        except QueueBlocked as exc:
            result = {"job_id": job_id, "state": "refused", "code": exc.code, "message": str(exc)[:300]}
            outcome = "refused"
        except ValueError as exc:
            result, outcome = {"job_id": job_id, "state": "refused", "message": str(exc)[:300]}, "refused"
        results.append({**result, "outcome": outcome})
    names = await off_loop(_names, service.db, [result["job_id"] for result in results])
    results = [{**names.get(result["job_id"], {}), **result} for result in results]
    summary = {}
    for result in results:
        summary[result["outcome"]] = summary.get(result["outcome"], 0) + 1
    return {"results": results, "summary": summary}


def _names(db, job_ids):
    """Company and title per job, so the summary names each job without another request."""
    with db.session() as s:
        rows = s.execute(select(Job.id, Job.company, Job.title).where(Job.id.in_(job_ids))).all()
    return {row.id: {"company": row.company, "title": row.title} for row in rows}


# _approve's answers for a job that needs no start.
ALREADY = {"running", "submitting", "confirmed", "submission_unknown", "already_applied"}


def _approve(service, job_id):
    """The click's approval, in one short transaction; an answer when the job needs no start."""
    with service.db.exclusive() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise ValueError("Job not found")
        return _approve_row(s, job)


def _approve_row(s, job):
    job_id = job.id
    if job.status in {"queued", "running", "submitting"}:
        return {"job_id": job_id, "state": job.status, "message": "This application is already running."}
    if job.status in {"confirmed", "submission_unknown", "already_applied"}:
        return {"job_id": job_id, "state": job.status,
                "message": f'This job is already applied: "{" ".join((job.title or "").split())[:120] or "untitled role"}" at {job.company or "this employer"}.'}
    if job.status == "archived":
        raise ValueError("This job was removed; restore it before applying")
    if job.status != "ready":
        # The newest: the file the person gave AXIOM last, never an older or mistaken upload (a first run, 2026-11-01).
        job.resume_id = job.resume_id or s.scalar(
            select(Resume.id).where(Resume.demo.is_(False)).order_by(Resume.created_at.desc()).limit(1)
        )
        if not job.resume_id:
            raise ValueError("Upload a resume before applying")
        job.status = "ready"
        if not (job.reason or "").startswith(OWNER_APPROVED):
            job.reason = OWNER_APPROVED + (job.reason or "")
    from .application_budget import MAX_STARTS, allowance, job_ceiling_block, repeat_stop_block

    if allowance(s, job_id)[2] >= MAX_STARTS or repeat_stop_block(s, job_id) or job_ceiling_block(s, job_id):
        # The owner's click is the approval a fresh try needs (owner, 2026-10-18: "give them fresh tries").
        # Jobs that may already be submitted returned above, so this never repeats a submission.
        key = "application_budget_reset:" + job_id
        value = {"at": now(), "reason": "Owner clicked Apply"}
        if row := s.get(Setting, key):
            row.value = value
        else:
            s.add(Setting(key=key, value=value))
    return None


async def start(service, job_id, mode="submit", **queue_options):
    """Queue now; a deferred admission is remembered instead of reported as a failure."""
    try:
        run = await service.queue(job_id, mode, owner_pick=mode == "submit", **queue_options)
    except QueueBlocked as exc:
        if exc.code == "tailoring_pending":
            return {"job_id": job_id, "state": "preparing", "preparing": True, "message": PREPARING}
        if exc.outcome != "deferred" or mode != "submit":
            raise
        await off_loop(
            service.db.set_setting,
            KEY + job_id,
            {"at": now(), "code": exc.code, "reason": str(exc)[:300], "not_before": _retry_at(exc)},
        )
        return {
            "job_id": job_id,
            "state": "waiting",
            "deferred": True,
            "code": exc.code,
            "message": f"{str(exc).rstrip('.')}." if exc.code == "stopped"
            else f"{str(exc).rstrip('.')}. It starts on its own as soon as it can.",
        }
    await off_loop(_forget, service.db, job_id)
    return {"job_id": job_id, "run_id": run["id"], "state": "queued", "message": "Application queued."}


# What holds a job that a click on Queue doesn't lift, and what to do about it. The rest (a role outside the targets, a fit under the
# minimum, an internship) a person may overrule by asking for that job themselves. Every one of these used to answer "Added to the
# queue." and then vanish when its turn came, or, over the employer limit, apply with no approval (a board add, 2026-11-01).
REFUSED_HOLDS = {
    "employer_limit": "Approve it in Needs you to apply past your limit.",
    "employer_cap": "",
    "protected": "",
    "excluded": "",
    "gate": "",
    "twin": "",
    "aged": "",
}


def refusal(session, job, hold=None):
    """Why a job can't join the line, raised in plain words before anything is written. `hold`: its entry in the dispatcher's reading,
    when the caller has one. Over the limit is read from the database when it hasn't: a lone click can afford the truth."""
    from .application_pacing import require_company_limit
    from .reserved import require_approval

    if job.demo:
        return
    # The approval stays each job's own: a reserved employer never joins the line by a click on Queue or Select all.
    require_approval(session, job)
    if re.match(r"(?:an )?explicit hiring restriction", job.reason or "", re.I):
        raise QueueBlocked("The posting's own requirements rule you out, so this job can't be queued.", code="excluded")
    if hold and hold.get("code") in REFUSED_HOLDS:
        raise QueueBlocked(" ".join(filter(None, (hold.get("reason"), REFUSED_HOLDS[hold["code"]]))), code=hold["code"])
    if hold is None:
        try:
            require_company_limit(session, job)
        except QueueBlocked as exc:
            if exc.code == "company_limit" and exc.detail:
                from .startable import limit_sentence

                said = limit_sentence(job.company, exc.detail["count"], exc.detail["limit"])
                raise QueueBlocked(f"{said} {REFUSED_HOLDS['employer_limit']}", code="employer_limit") from exc
            raise


def enqueue(service, job_id, front=False, hold=None):
    """Add a job to the queue, or move it to the front, without starting anything: the dispatcher starts it in its turn, so a
    click here can never run past a Stop or a pause. `front`: ahead of everything else waiting (the latest click goes first).
    What will never start is refused here, in words, not accepted and dropped later (`refusal`); a job already asked for has had
    that say. The job's other guards decide when its turn comes, as they do any Apply. Runs on a worker thread."""
    with service.db.exclusive() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise ValueError("This job is no longer in AXIOM.")
        return _enqueue_row(s, job, front, hold)


def _enqueue_row(s, job, front, hold):
    known = s.get(Setting, KEY + job.id)
    if job.status in {*ALREADY, "queued"}:
        return _approve_row(s, job)
    if known is None:
        refusal(s, job, hold)
    # Asking for a job you took out of the line puts it back: the pause would otherwise refuse it when its turn came, and the request
    # would be dropped without a word.
    control = s.get(Setting, "job_control:" + job.id)
    if control is not None and (control.value or {}).get("state") == "paused":
        if job.status == "paused":
            job.status = (control.value or {}).get("previous_status") or "ready"
        s.delete(control)
    if answered := _approve_row(s, job):
        return answered
    value = dict(known.value or {}) if known else {"at": now()}
    value.update(code=value.get("code") or "queue", reason=value.get("reason") or "", not_before=now())
    if front:
        value["front"] = now()
    if known:
        known.value = value
    else:
        s.add(Setting(key=KEY + job.id, value=value))
    return {
        "job_id": job.id,
        "state": "waiting",
        "deferred": True,
        "code": "queue",
        "message": "Moved to the front of the queue." if front else "Added to the queue.",
    }


# Jobs one transaction decides. Each takes the application lock for its length: one job's commit each cost hundreds of writes in a row,
# and the whole list in one would hold every worker's admission behind a Select all.
MANY_CHUNK = 25


def enqueue_many(service, job_ids, front=False):
    """Ticked jobs, or a whole Select all: each decided as its own click, with one reading of what holds what. Returns what happened to
    each: `added`, `already` (running, queued or applied, so nothing was done), and `refused` with the sentence. An employer limit
    counts what this same request has just put in line, and what was waiting: ten ticked jobs at an employer with room for two are two
    jobs, or a select-all walks past the approval the limit asks for."""
    from sqlalchemy.orm import defer

    from .startable import reading

    wanted = list(dict.fromkeys(job_ids))
    holds = reading(service).get(service).get("jobs", {})
    room = _Room(service, wanted)
    added, already, refused = [], [], []
    for start in range(0, len(wanted), MANY_CHUNK):
        chunk = wanted[start : start + MANY_CHUNK]
        with service.db.exclusive() as s:
            jobs = {job.id: job for job in s.scalars(select(Job).options(defer(Job.description)).where(Job.id.in_(chunk)))}
            for job_id in chunk:
                job = jobs.get(job_id)
                try:
                    if job is None:
                        raise ValueError("This job is no longer in AXIOM.")
                    room.check(job_id)
                    result = _enqueue_row(s, job, front, holds.get(job_id, {}))
                except ValueError as exc:  # QueueBlocked is one
                    refused.append({"job_id": job_id, "company": job.company if job else "", "title": job.title if job else "", "reason": str(exc)[:300]})
                    continue
                if result["state"] == "waiting":
                    added.append(job_id)
                    room.took(job_id)
                else:
                    already.append(job_id)
    return {"added": added, "already": already, "refused": refused}


class _Room:
    """How many more each employer in a request may take before the user's limit, counting what is already waiting in line. Only the
    employers with more than one job in play need the count: for the others the dispatcher's own reading has said."""

    def __init__(self, service, wanted):
        from .admission import employer_partition
        from .application_pacing import company_count, employer_keys
        from .features.knowledge.tables import KnAlias

        self.left = {}
        with service.db.session() as s:
            self.waiting = {key.removeprefix(KEY) for key in s.scalars(select(Setting.key).where(Setting.key.startswith(KEY, autoescape=True)))}
            rows = {row.id: row for row in s.execute(select(Job.id, Job.company, Job.url, Job.identity, Job.demo).where(Job.id.in_({*wanted, *self.waiting})))}
            identity = employer_keys({a.alias: (a.employer_id, a.kind) for a in s.scalars(select(KnAlias))})
            self.of = {job_id: identity(row.company, row.url) or "site:" + employer_partition(row) for job_id, row in rows.items() if not row.demo}
            ahead, asked = {}, {}
            for job_id in self.waiting & self.of.keys():
                ahead[self.of[job_id]] = ahead.get(self.of[job_id], 0) + 1
            for job_id in wanted:
                if job_id in self.of and job_id not in self.waiting:
                    asked.setdefault(self.of[job_id], []).append(job_id)
            for employer, jobs in asked.items():
                if len(jobs) + ahead.get(employer, 0) > 1 and (count := company_count(s, rows[jobs[0]])):
                    have = count[0] + ahead.get(employer, 0)
                    self.left[employer] = {"have": have, "limit": count[1]}
            self.names = {job_id: row.company for job_id, row in rows.items()}

    def check(self, job_id):
        state = self.left.get(self.of.get(job_id))
        if state and job_id not in self.waiting and state["have"] >= state["limit"]:
            from .startable import limit_sentence

            said = limit_sentence(self.names.get(job_id), state["have"], state["limit"])
            raise QueueBlocked(f"{said} {REFUSED_HOLDS['employer_limit']}", code="employer_limit")

    def took(self, job_id):
        state = self.left.get(self.of.get(job_id))
        if state and job_id not in self.waiting:
            state["have"] += 1


def back_in_line(service, job_id):
    """Undo Remove from the queue: the pause is lifted and the job is Ready again, for the dispatcher to reach in its turn. Nothing
    starts here. A job that has attempts goes through the owner's Resume instead (job_control.manage)."""
    with service.db.exclusive() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise ValueError("Job not found")
        control = s.get(Setting, "job_control:" + job_id)
        if control is not None:
            if job.status == "paused":
                job.status = (control.value or {}).get("previous_status") or "ready"
            s.delete(control)
        status = job.status
    return {"job_id": job_id, "state": status, "message": "Back in the queue."}


def _forget(db, job_id):
    with db.exclusive() as s:
        row = s.get(Setting, KEY + job_id)
        if row is not None:
            s.delete(row)


def request_order(waiting, attempted):
    """The order waiting requests start in: first attempts before retries; within each, what the user moved to the front (the
    latest click first), then the least recently tried, so a few jobs that stay blocked (one busy employer) never keep the
    ones behind them from being tried. Queue shows this same order (queue_order.py)."""
    def key(item):
        job_id, value = item
        front = value.get("front", "")
        return (not front and job_id in attempted, not front, _descending(front), value.get("tried", ""), value.get("at", ""))

    return sorted(waiting, key=key)


def _descending(text):
    return tuple(-ord(c) for c in text)


async def due(service, limit, retries=None):
    """Start remembered owner requests whose wait is over: jobs never attempted first, then retries of earlier
    attempts, oldest first within each. `retries` caps how many retries start now. Returns how many were queued."""
    if limit <= 0:
        return 0
    from .db import Run

    with service.db.session() as s:
        waiting = [(row.key.removeprefix(KEY), dict(row.value or {})) for row in s.scalars(
            select(Setting).where(Setting.key.startswith(KEY, autoescape=True))
        )]
        attempted = set(s.scalars(
            select(Run.job_id).where(Run.job_id.in_([job_id for job_id, _ in waiting])).distinct()
        )) if waiting else set()
    # 247 resumed repeat-failures queued ahead of fresh postings filled every worker on 2026-10-24; their retries
    # succeed about one time in eight, first attempts far more often.
    rows = request_order(waiting, attempted)
    started = retried = tries = 0
    for job_id, value in rows:
        if started >= limit or tries >= max(TRIES, 3 * limit):
            break
        # What the user moved to the front is theirs to start first, an earlier attempt or not.
        if job_id in attempted and not value.get("front") and retries is not None and retried >= retries:
            break  # the rest are retries too
        if (value.get("not_before") or "") > now():
            continue
        tries += 1
        try:
            await service.queue(job_id, "submit", owner_pick=True)
        except QueueBlocked as exc:
            if exc.outcome == "deferred":
                await off_loop(service.db.set_setting, KEY + job_id, {
                    **value, "code": exc.code, "reason": str(exc)[:300], "not_before": _retry_at(exc), "tried": now()})
                if exc.code == "worker_capacity":
                    break  # The pool is full: every other request would be refused the same way.
                continue
            _forget(service.db, job_id)  # A permanent refusal (closed, protected, excluded) ends the request.
            continue
        except ValueError:
            _forget(service.db, job_id)
            continue
        _forget(service.db, job_id)
        started += 1
        retried += job_id in attempted
    return started


def restore(service, job_id):
    """Bring a removed job back to Ready. Refused where an application may have reached the employer, since that record is what
    stops a second one. The job's page offered this ("restore it before applying") with no way to do it."""
    from sqlalchemy import select

    from .db import Event, Job, Run, Setting

    with service.db.exclusive() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise ValueError("Job not found")
        if job.status != "archived":
            return {"job_id": job_id, "state": job.status, "message": "This job was not removed."}
        runs = list(s.execute(select(Run.id, Run.state).where(Run.job_id == job_id)))
        sent = any(state in {"confirmed", "already_applied", "submission_unknown", "submitting"} for _, state in runs) or bool(
            runs and s.scalar(select(Event.id).where(Event.run_id.in_([r for r, _ in runs]), Event.kind == "submitting").limit(1))
        )
        if sent:
            raise ValueError("An application for this job may already have reached the employer, so it stays removed")
        # The owner rule: an application asking for a government or bank identifier is removed for good. Restoring it
        # would send the next run to the same question (completion.py and sensitive_ids.py leave this text in the reason).
        if s.scalar(select(Run.id).where(Run.job_id == job_id, Run.reason.contains("removed under the owner rule")).limit(1)):
            raise ValueError("This application asks for a government or bank identifier, so the job stays removed")
        job.status = "ready"
        for key in ("archived_application:", "job_control:"):
            saved = s.get(Setting, key + job_id)
            # A pause the owner set is a different decision from removing the job: it stays.
            if saved and not (key == "job_control:" and (saved.value or {}).get("state") == "paused"):
                s.delete(saved)
    return {"job_id": job_id, "state": "ready", "message": "Restored. It can be applied to again."}

