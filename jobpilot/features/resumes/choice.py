"""Apply, or Apply with a tailored resume: the per-job choice, the approval that guards it, and what queue admission asks.

Off by default: no row means the job applies with the uploaded resume, as it always has. A tailored resume waits for the applicant's review, unless
they've turned approval off in Settings. While approval is on, nothing tailored is sent unapproved, and approval names the exact file by hash. It's the same for every mode.
It's checked twice in code, when a run is queued (pin_resume) and when the run takes the PDF (run_problem), so a later edit needs approving again even for a run already waiting.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .._store import digest, now
from . import modes, pipeline
from .rules import draft_leaks
from .tables import RsArtifact, RsTailorChoice

log = logging.getLogger(__name__)
SETTING = "tailor_settings"
LEASE_SECONDS = 420
WAITING = "tailoring_wait"
REVIEW = "tailoring_review"
FAILED = "tailoring_failed"
STALE = "tailoring_stale"
BROKEN = "tailoring_rules"
UNCONFIRMED = "tailoring_unconfirmed"
STARTED = frozenset({"queued", "running", "submitting", "waiting_browser", "confirmed", "submission_unknown", "already_applied"})
_TASKS: dict[str, asyncio.Task] = {}


def approval_required(db) -> bool:
    """On unless the applicant turned it off."""
    return (db.get_setting(SETTING, {}) or {}).get("approval", True) is not False


def _approval_required_in(session) -> bool:
    from ...db import Setting

    row = session.get(Setting, SETTING)
    return ((row.value if row else None) or {}).get("approval", True) is not False


def set_approval(db, on: bool) -> dict:
    db.set_setting(SETTING, {**(db.get_setting(SETTING, {}) or {}), "approval": bool(on)})
    return {"approval": bool(on)}


def lease() -> str:
    return (datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)).isoformat()


def get_choice(db, job_id: str) -> RsTailorChoice | None:
    with db.session() as s:
        return s.get(RsTailorChoice, job_id)


def wake(db, job_id: str) -> None:
    """A job waiting on tailoring can start now: its queue request is tried on the next tick, not after its backoff."""
    from ...db import Setting

    with db.exclusive() as s:
        row = s.get(Setting, "owner_apply:" + job_id)
        if row is not None:
            row.value = {**(row.value or {}), "not_before": now()}


def choose(db, job_id: str, mode: str | None) -> dict | None:
    """mode None or "off" is plain Apply. Choosing the same mode again keeps what's been reviewed; a different one starts over."""
    from ...db import Job

    with db.exclusive() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise ValueError("Job not found")
        row = s.get(RsTailorChoice, job_id)
        if job.status in STARTED:
            # The resume is already chosen: a run has it, or it was sent. Saying otherwise here would be a lie about what the employer got.
            raise LookupError("This application has already started, so its resume can't change now.")
        if mode in (None, "", "off"):
            if row is not None:
                s.delete(row)
            return None
        if modes.get(mode) is None:
            raise LookupError(f"{mode} isn't available yet")
        if row is None:
            row = RsTailorChoice(job_id=job_id, mode=mode, state="preparing", lease_until=lease())
            s.add(row)
        elif row.mode != mode or row.state == "failed":
            row.mode, row.state, row.artifact_id, row.error, row.lease_until = mode, "preparing", None, {}, lease()
            row.approved_artifact_id = row.approved_sha = row.approved_at = row.approved_how = None
        row.updated_at = now()
        return {"mode": row.mode, "state": row.state, "artifact_id": row.artifact_id}


async def run(db, config, job_id: str, *, model=None, compile_fn=None, fresh=False) -> dict:
    """Build the chosen mode's resume and record what the applicant does next."""
    row = get_choice(db, job_id)
    if row is None:
        return {"state": "off"}
    kwargs = {"compile_fn": compile_fn} if compile_fn else {}
    try:
        await read_posting(db, config, job_id)
        result = await pipeline.prepare_resume(db, config, job_id, mode=row.mode, model=model, fresh=fresh, **kwargs)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # An unexpected fault must not leave the job waiting for ever with nothing said.
        log.exception("tailoring failed for %s", job_id)
        _record(db, job_id, state="failed", error={"code": "tailoring_error", "retryable": True, "detail": type(exc).__name__})
        return {"state": "failed"}
    if result.status == "ready" and result.artifact:
        approved = not approval_required(db)
        _record(
            db,
            job_id,
            state="approved" if approved else "review",
            artifact_id=result.artifact.artifact_id,
            error={},
            **(
                {"approved_artifact_id": result.artifact.artifact_id, "approved_sha": result.artifact.pdf_sha, "approved_at": now(), "approved_how": "setting_off"}
                if approved
                else {"approved_artifact_id": None, "approved_sha": None, "approved_at": None, "approved_how": None}
            ),
        )
        if approved:
            wake(db, job_id)
        return {"state": "approved" if approved else "review", "artifact_id": result.artifact.artifact_id}
    if result.status == "building":
        return {"state": "preparing"}
    error = result.error.model_dump() if result.error else {"code": "tailoring_error", "retryable": True, "detail": ""}
    _record(db, job_id, state="failed", error=error)
    return {"state": "failed", "error": error}


async def read_posting(db, config, job_id: str) -> None:
    """A job added by link has no text until a run reads it, and there's nothing to tailor to without it. Read it now, through the ordinary loader."""
    from ...db import Job
    from ...discovery import enrich
    from ..discovery.policy import unfetched

    with db.session() as s:
        job = s.get(Job, job_id)
        missing = job is not None and unfetched(job.description)
    if missing:
        try:
            await enrich(db, config, job_id)
        except Exception as exc:  # A site that won't load says so below, as the missing posting text it is.
            log.info("posting read failed for %s: %s", job_id, type(exc).__name__)


def _record(db, job_id, **fields) -> None:
    with db.exclusive() as s:
        row = s.get(RsTailorChoice, job_id)
        if row is None:
            return
        for k, v in fields.items():
            setattr(row, k, v)
        row.lease_until = None
        row.updated_at = now()


def start(db, config, job_id: str, *, fresh=False) -> None:
    """Build in the background; the screen polls. One task per job."""
    existing = _TASKS.get(job_id)
    if existing is not None and not existing.done():
        return

    async def go():
        try:
            await run(db, config, job_id, fresh=fresh)
        finally:
            _TASKS.pop(job_id, None)

    _TASKS[job_id] = asyncio.create_task(go())


def abandoned(db, limit: int = 2) -> list[str]:
    """A restart mid-build leaves a lease nobody holds. The autopilot's tick starts these again, so such a job doesn't wait for ever."""
    at = datetime.now(UTC).isoformat()
    with db.exclusive() as s:
        rows = s.scalars(
            select(RsTailorChoice).where(RsTailorChoice.state == "preparing", (RsTailorChoice.lease_until.is_(None)) | (RsTailorChoice.lease_until < at)).limit(limit)
        ).all()
        ids = [r.job_id for r in rows if r.job_id not in _TASKS]
        for r in rows:
            if r.job_id in ids:
                r.lease_until = lease()
    return ids


def artifact_current(session, row: RsArtifact, job) -> bool:
    """Built from today's profile and today's posting text."""
    from ...db import Setting
    from .evidence import profile_digest

    profile = session.get(Setting, "profile")
    return row.profile_digest == profile_digest((profile.value if profile else {}) or {}) and row.description_sha == digest(job.description or "")[:20]


def send_problem(session, row: RsArtifact) -> tuple[str, str] | None:
    """Why this artifact can't go to an employer, or None. The last check before a run is bound to a PDF, whichever path asked, approval on or off."""
    validation = row.validation or {}
    tailored = modes.is_tailored_policy(row.policy_version)
    rules = validation.get("rules")
    if (tailored or rules is not None) and not (rules or {}).get("ok"):
        first = ((rules or {}).get("problems") or [{}])[0].get("message", "It hasn't passed the resume rules")
        return BROKEN, f"This tailored resume can't be sent: {first}"
    leaks = draft_leaks(row.text or "", validation.get("drafts") or [])
    if leaks:
        return UNCONFIRMED, f'"{leaks[0]}" is a draft you haven\'t confirmed, so this resume can\'t be sent'
    if _approval_required_in(session):
        choice = session.get(RsTailorChoice, row.job_id) if row.job_id else None
        if not choice or choice.approved_artifact_id != row.id or choice.approved_sha != row.pdf_sha:
            return REVIEW, "Waiting for you to review the tailored resume"
    return None


def run_problem(db, packet: dict) -> str | None:
    """Asked when a run takes its PDF. An edit or a withdrawn approval since it was queued stops it here, so it can't send what's no longer approved."""
    artifact_id = (packet or {}).get("resume_artifact_id")
    if not artifact_id:
        return None
    with db.session() as s:
        row = s.get(RsArtifact, artifact_id)
        if row is None:
            return "This tailored resume is no longer on file"
        problem = send_problem(s, row)
    return problem[1] if problem else None


def artifact_for_queue(session, job, mode: str) -> str | None:
    """What a queue request for this job sends: None for no choice, else the artifact. Raises QueueBlocked while it must wait, and says why."""
    from ...admission import QueueBlocked, temporary_time

    choice = session.get(RsTailorChoice, job.id)
    if choice is None:
        return None

    def wait(code, message):
        raise QueueBlocked(message, outcome="deferred", code=code, not_before=temporary_time())

    artifact = session.get(RsArtifact, choice.artifact_id) if choice.artifact_id else None
    usable = artifact is not None and artifact.status == "ready" and artifact_current(session, artifact, job)
    if mode != "submit":
        # Nothing is sent in a dry run. Unapproved stays out anyway, because a resumed draft keeps the PDF it started with.
        approved = usable and choice.approved_artifact_id == artifact.id and choice.approved_sha == artifact.pdf_sha
        return artifact.id if approved else None
    if choice.state == "failed":
        detail = (choice.error or {}).get("detail") or (choice.error or {}).get("code") or ""
        wait(FAILED, f"The tailored resume couldn't be made: {detail}. Try again, or switch this job to plain Apply".strip())
    if artifact is None or artifact.status != "ready":
        wait(WAITING, "Preparing the tailored resume")
    if not usable:
        wait(STALE, "Your profile or the posting changed after this resume was tailored. Tailor it again")
    problem = send_problem(session, artifact)
    if problem:
        wait(*problem)
    return artifact.id


def approve(db, config, job_id: str, artifact_id: str) -> dict:
    """The applicant's yes, for this exact file. Refuses what the rules refuse."""
    with db.exclusive() as s:
        artifact = s.get(RsArtifact, artifact_id)
        if artifact is None or artifact.job_id != job_id or artifact.status != "ready":
            raise ValueError("This tailored version isn't ready")
        if not pipeline.file_intact(config.data_dir, artifact):
            raise ValueError("The PDF changed or went missing. Tailor it again")
        validation = artifact.validation or {}
        rules = validation.get("rules") or {}
        if not rules.get("ok"):
            first = (rules.get("problems") or [{}])[0].get("message", "It hasn't passed the resume rules")
            raise ValueError(first)
        leaks = draft_leaks(artifact.text or "", validation.get("drafts") or [])
        if leaks:
            raise ValueError(f'"{leaks[0]}" is a draft you haven\'t confirmed, so it can\'t be approved')
        choice = s.get(RsTailorChoice, job_id)
        if choice is None:
            choice = RsTailorChoice(job_id=job_id, mode=validation.get("mode") or "normal")
            s.add(choice)
        choice.artifact_id = artifact.id
        choice.state, choice.error, choice.lease_until = "approved", {}, None
        choice.approved_artifact_id, choice.approved_sha = artifact.id, artifact.pdf_sha
        choice.approved_at, choice.approved_how, choice.updated_at = now(), "reviewed", now()
        result = {"artifact_id": artifact.id, "pdf_sha": artifact.pdf_sha, "approved_at": choice.approved_at}
    wake(db, job_id)
    return result


WAITING_WORDS = {
    "preparing": "Preparing the tailored resume.",
    "review": "Waiting for you to review the tailored resume.",
    "failed": "The tailored resume couldn't be made. Open the job to try again.",
}


def waiting_words(db, job_ids: list[str]) -> dict:
    """What each of these jobs is really waiting on, where that's its tailored resume. Queue's "Waiting on" column says this, and the switch being off means nothing's awaited."""
    if not job_ids:
        return {}
    with db.session() as s:
        rows = s.execute(select(RsTailorChoice.job_id, RsTailorChoice.state).where(RsTailorChoice.job_id.in_(job_ids), RsTailorChoice.state.in_(list(WAITING_WORDS)))).all()
    return {job_id: WAITING_WORDS[state] for job_id, state in rows}


def many(db, job_ids: list[str]) -> dict:
    """{job_id: {mode, state}} for a list of rows. A job that isn't here is plain Apply."""
    if not job_ids:
        return {}
    with db.session() as s:
        rows = s.scalars(select(RsTailorChoice).where(RsTailorChoice.job_id.in_(job_ids))).all()
        return {r.job_id: {"mode": r.mode, "state": r.state} for r in rows}
