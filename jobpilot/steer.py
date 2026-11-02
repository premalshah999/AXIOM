"""Take over: the owner finishes an application by hand in the app's own Chrome, then hands it back.

Owner 2026-11-01: "add an option to manually steer on failed/repairing/needs answer questions. Just an option, does not have
to take away the autonomous nature." Nothing here runs unless the owner presses Take over on a job.

How it holds the page without a second writer on a worker's tab:

1. Take over pauses the job with the existing owner pause (job_control), so no start, retry, recovery, repair or Telegram
   path touches it, and marks it `steering`. Every automatic path already refuses a paused job.
2. One real dry run (never submits) rebuilds the draft in the worker Chrome, to the point where the application stopped.
   Its packet carries `steer`: the engine keeps that tab open whatever the result (browser.releases_retained_tab), the
   result becomes a plain "paused" run with no questions, no repair and no Telegram message (complete()), and the
   inactive-tab cleanup leaves the tab alone (operations/close_tabs).
3. The tab is brought to the front over Chrome's HTTP endpoint, which attaches nothing to it: a second automation client
   on a worker page once handled a dialog the worker was handling and killed three runs.
4. The owner works in the tab. No run is attached to it. Then, one of:
   - Done, continue: the job is released and a normal submit run resumes from that tab (the engine's draft recovery
     re-reads the page and carries on). A page that already shows a confirmation is refused: it would be sent twice.
   - I submitted it myself: the owner's word is recorded as the application's receipt (service.reconcile), so every
     duplicate guard holds and a retry is refused.
   - Give it back: released the same way; the engine decides what to do, and asks again if it must.
"""

import re

import httpx
from sqlalchemy import select

from .db import Job, Run, Setting, now

KEY = "steer:"
# Run states Take over may start from: the application stopped on a question, a failure, or is repairing itself.
TAKEABLE = {"waiting_answer", "needs_review", "waiting_browser", "timed_out", "failed", "cancelled", "waiting_email"}
# Nothing was sent, or nothing is known to have been sent. A sent or uncertain application is never taken over:
# "I submitted it myself" and the evidence forms exist for those.
COMMITTED_STATES = {"confirmed", "already_applied", "submitting", "submission_unknown"}
ACTIVE_STATES = {"queued", "running", "submitting"}
# A page whose address or title says the application went through.
CONFIRMATION = re.compile(
    r"thank[\s-]*you|\bconfirm(?:ation|ed)?\b|\bsubmitted\b|\bsuccess(?:ful)?\b|application[\s-]*(?:received|complete)|"
    r"\bapplied\b|\breceived\b",
    re.I,
)


def record(db, job_id):
    return db.get_setting(KEY + job_id, {}) or {}


def steered_jobs(db):
    """{job_id: steer record} for every job the owner holds."""
    with db.session() as s:
        rows = s.scalars(select(Setting).where(Setting.key.startswith(KEY, autoescape=True)))
        return {r.key.removeprefix(KEY): r.value for r in rows if (r.value or {}).get("phase") in {"opening", "yours"}}


def is_steer_run(db, run_id):
    with db.session() as s:
        run = s.get(Run, run_id)
        return bool(run and (run.packet or {}).get("steer"))


def holding(session, job_id):
    """True while the owner holds this job (opening the page, or working in it)."""
    row = session.get(Setting, KEY + job_id)
    return bool(row and (row.value or {}).get("phase") in {"opening", "yours"})


def opening(session, job_id):
    """True only while the one dry run that rebuilds the draft may be queued."""
    row = session.get(Setting, KEY + job_id)
    return bool(row and (row.value or {}).get("phase") == "opening")


def held_targets(db):
    """Chrome targets of jobs the owner holds: the tab cleanup must never close them."""
    held = set()
    for job_id in steered_jobs(db):
        target = (db.get_setting("desktop_job:" + job_id, {}) or {}).get("target_id")
        if target:
            held.add(target)
    return held


def _last_runs(session, job_id):
    return list(session.scalars(select(Run).where(Run.job_id == job_id).order_by(Run.created_at.desc())))


def _worker_chrome(config):
    url = (getattr(config, "browser_cdp_url", "") or "").rstrip("/")
    return url


def _targets(config):
    """The worker Chrome's page tabs as its HTTP endpoint lists them: id, url and title, with no client attached."""
    endpoint = _worker_chrome(config)
    if not endpoint:
        return None
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(endpoint + "/json/list", headers={"Host": "localhost"})
            response.raise_for_status()
            return {t["id"]: t for t in response.json() if t.get("type") == "page"}
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def surface(config, target_id):
    """Bring the tab to the front. True when Chrome did it. Nothing attaches to the page."""
    endpoint = _worker_chrome(config)
    if not endpoint or not target_id:
        return False
    try:
        with httpx.Client(timeout=3, trust_env=False) as client:
            return client.get(endpoint + "/json/activate/" + target_id, headers={"Host": "localhost"}).is_success
    except httpx.HTTPError:
        return False


def status(service, job_id):
    """What the Take over panel shows for one job."""
    held = record(service.db, job_id)
    phase = held.get("phase", "")
    out = {"job_id": job_id, "phase": phase, "run_id": held.get("run_id", ""), "since": held.get("at", "")}
    if phase == "yours":
        target = (service.db.get_setting("desktop_job:" + job_id, {}) or {}).get("target_id")
        tabs = _targets(service.config)
        tab = (tabs or {}).get(target or "")
        out.update(tab_open=bool(tab), title=(tab or {}).get("title", ""), address=(tab or {}).get("url", ""),
                   browser=bool(_worker_chrome(service.config)))
    return out


async def start(service, job_id):
    """Pause the job and rebuild its draft in the worker Chrome, to be handed to the owner when it is ready."""
    from .admission import QueueBlocked

    if not _worker_chrome(service.config):
        raise ValueError("Take over opens the application in the app's own Chrome, and none is configured.")
    with service.db.exclusive() as s:
        job = s.get(Job, job_id)
        if not job or job.demo:
            raise ValueError("Job not found")
        if holding(s, job_id):
            raise ValueError("You already have this application")
        if job.status in {"archived", "closed"}:
            raise ValueError("This job was removed or closed")
        runs = _last_runs(s, job_id)
        if any(r.state in ACTIVE_STATES for r in runs):
            raise ValueError("The application is running right now. Take over once it has stopped.")
        if any(r.state in COMMITTED_STATES for r in runs):
            raise ValueError("This application was sent or may have been. Confirm it from the job instead of taking it over.")
        last = runs[0] if runs else None
        if not last or last.state not in TAKEABLE | {"paused"}:
            raise ValueError("Take over works on an application that stopped, failed or is asking for you.")
        previous = job.status
        approved = any((r.packet or {}).get("match_approved") for r in runs)
        resume_id = last.resume_id or job.resume_id
        control = s.get(Setting, "job_control:" + job_id)
        paused = {"state": "paused", "at": now(), "previous_status": previous, "steering": "opening"}
        if control:
            control.value = paused
        else:
            s.add(Setting(key="job_control:" + job_id, value=paused))
        recovery = s.get(Setting, "draft_recovery:" + job_id)
        if recovery:
            recovery.value = {**recovery.value, "scheduled": False, "status": "owner_steering"}
        row = s.get(Setting, KEY + job_id)
        value = {"phase": "opening", "at": now(), "previous_status": previous, "approved": approved}
        if row:
            row.value = value
        else:
            s.add(Setting(key=KEY + job_id, value=value))
        job.status = "paused"
    try:
        run = await service.queue(job_id, "dry_run", resume_id, retry=True)
    except QueueBlocked as exc:
        release(service, job_id)
        raise ValueError(str(exc)) from None
    except BaseException:
        release(service, job_id)
        raise
    service.db.event(run["id"], "steer", "Opening the application for the owner to finish by hand", {"job_id": job_id})
    with service.db.exclusive() as s:
        row = s.get(Setting, KEY + job_id)
        if row:
            row.value = {**row.value, "run_id": run["id"]}
    return {"job_id": job_id, "run_id": run["id"], "phase": "opening",
            "message": "Opening the application in Chrome. It is yours as soon as the page is ready."}


def complete(service, run_id, result, elapsed):
    """The rebuilding run ended: the application is the owner's. No question, repair or message is made from it."""
    from .application_slots import release_slot

    target = ""
    with service.db.exclusive() as s:
        run = s.get(Run, run_id)
        if not run or run.state == "cancelled":
            return
        held = s.get(Setting, KEY + run.job_id)
        reason = "You took this application over and are finishing it by hand."
        run.state, run.reason, run.finished_at, run.elapsed = "paused", reason, now(), elapsed
        run.packet = {**run.packet, "steer_result": result.get("state", ""),
                      "steer_stopped_on": str(result.get("reason") or "")[:600]}
        release_slot(s, run)
        job = s.get(Job, run.job_id)
        if held and (held.value or {}).get("phase") == "opening":
            held.value = {**held.value, "phase": "yours", "ready_at": now()}
            control = s.get(Setting, "job_control:" + run.job_id)
            if control and (control.value or {}).get("steering"):
                control.value = {**control.value, "steering": "yours"}
            if job:
                job.status = "paused"
            desktop = s.get(Setting, "desktop_job:" + run.job_id)
            target = ((desktop.value if desktop else {}) or {}).get("target_id") or ""
    service.db.event(run_id, "steer_ready", "The application is the owner's", {"stopped_on": str(result.get("reason") or "")[:300]})
    surface(service.config, target)


def hands_back(session, job_id):
    """True once, for the submit run that follows Done, continue: it keeps what the owner entered on the page (forms.adopt_owner_values)."""
    row = session.get(Setting, KEY + job_id)
    if row and (row.value or {}).get("phase") == "released" and (row.value or {}).get("hands"):
        row.value = {**row.value, "hands": False}
        return True
    return False


def release(service, job_id, *, hands=False):
    """Lift the hold: the job is no longer paused. `hands`: the next run keeps the owner's entries on the page."""
    with service.db.exclusive() as s:
        job = s.get(Job, job_id)
        held = s.get(Setting, KEY + job_id)
        value = dict((held.value if held else {}) or {})
        if held:
            held.value = {**value, "phase": "released", "released_at": now(), "hands": hands}
        control = s.get(Setting, "job_control:" + job_id)
        if control and (control.value or {}).get("steering"):
            control.value = {"state": "active", "at": now(), "previous_status": value.get("previous_status", "")}
        runs = _last_runs(s, job_id)
        committed = any(r.state in COMMITTED_STATES for r in runs)
        if job and job.status == "paused" and not committed:
            if value.get("approved"):
                job.status = "ready"
            else:
                previous = value.get("previous_status") or "needs_review"
                job.status = "needs_review" if previous in {"waiting_answer", "paused"} else previous
        resume_id = (runs[0].resume_id if runs else None) or (job.resume_id if job else None)
    return resume_id


async def finish(service, job_id, how):
    """Done, continue (how="done") or Give it back (how="back"): a normal run resumes from the tab the owner left open."""
    from .admission import QueueBlocked

    held = record(service.db, job_id)
    if held.get("phase") == "opening":
        raise ValueError("The application is still opening. Give it a moment.")
    if held.get("phase") != "yours":
        raise ValueError("You don't have this application.")
    if how == "done":
        target = (service.db.get_setting("desktop_job:" + job_id, {}) or {}).get("target_id")
        tab = (_targets(service.config) or {}).get(target or "")
        if tab and CONFIRMATION.search(" ".join([tab.get("title", ""), tab.get("url", "")])):
            raise ValueError(
                "That page looks like a confirmation. If you sent the application, choose \"I submitted it myself\": "
                "continuing could send it twice."
            )
    resume_id = release(service, job_id, hands=how == "done")
    try:
        run = await service.queue(job_id, "submit", resume_id, retry=True, owner_pick=True)
    except QueueBlocked as exc:
        if exc.outcome != "deferred":
            raise
        from .owner_apply import start as wait_for_worker

        return await wait_for_worker(service, job_id)
    service.db.event(run["id"], "steer_back", "The owner finished by hand and the application resumes" if how == "done"
                     else "The owner gave the application back", {"job_id": job_id, "how": how})
    return {"job_id": job_id, "run_id": run["id"], "state": "queued",
            "message": "Carrying on from your page." if how == "done" else "Back on its own."}


def submitted(service, job_id):
    """I submitted it myself: the owner's word, recorded as the application's receipt. Duplicate guards hold from then on."""
    held = record(service.db, job_id)
    if held.get("phase") not in {"opening", "yours"}:
        raise ValueError("You don't have this application.")
    with service.db.session() as s:
        runs = _last_runs(s, job_id)
        run_id = runs[0].id if runs else ""
        state = runs[0].state if runs else ""
    if not run_id or state != "paused":
        raise ValueError("The application is still opening. Give it a moment.")
    service.reconcile(
        run_id, True,
        "Submitted by the applicant in the browser, after taking the application over (Take over).",
        submitted_by_owner=True,
    )
    with service.db.exclusive() as s:
        row = s.get(Setting, KEY + job_id)
        if row:
            row.value = {**row.value, "phase": "released", "released_at": now(), "outcome": "submitted_by_owner"}
        control = s.get(Setting, "job_control:" + job_id)
        if control and (control.value or {}).get("steering"):
            control.value = {"state": "active", "at": now(), "previous_status": (row.value if row else {}).get("previous_status", "")}
    return {"job_id": job_id, "run_id": run_id, "state": "confirmed", "message": "Recorded as sent. It will not be sent again."}
