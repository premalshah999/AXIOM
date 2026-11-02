"""Apply, or Apply with a tailored resume: the per-job choice, the approval that guards it, and what queue admission asks.

Off by default: no row means the job applies with the uploaded resume, as it always has. A tailored resume waits for the applicant's review, unless
they've turned approval off in Settings. While approval is on, nothing tailored is sent unapproved, and approval names the exact file by hash. It's the same for every mode.
It's checked twice in code, when a run is queued (pin_resume) and when the run takes the PDF (run_problem), so a later edit needs approving again even for a run already waiting.
"""

import asyncio
import logging
import re
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
ALREADY_STARTED = "This application has already started, so its resume can't change now."
ACCOUNT_WIDE = "Rewording every job is on, so this job gets its own version. To send your uploaded resume, turn rewording off in Resumes."
FOLLOWED = "tailor_followed:"  # A job's choice that the account-wide switch made, not the person: it ends when the switch does.
_TASKS: dict[str, asyncio.Task] = {}

TRY_AGAIN = "Something went wrong while tailoring. Nothing was sent. Try again."
READ_POSTING = "AXIOM couldn't read this job's posting, and there's nothing to tailor to without it. Try again, or check that the posting is still up."
NEEDS_KEY = "Tailoring needs your AI key. Add it in Settings, under Connections, then try again."
# (error code, what the person can do, which screen holds the answer). None: trying again is the answer.
FAILURES = {
    "no_source_resume": ("There's no resume to tailor yet. Add yours in Resumes, then try again.", "resumes"),
    "no_source": ("AXIOM couldn't find any work history in your resume. Add it in your Profile, then try again.", "profile"),
    "no_model": (NEEDS_KEY, "connections"),
    "missing_description": (READ_POSTING, None),
    "not_found": ("This job is no longer in AXIOM.", None),
    "timeout": ("The AI took too long to answer. Try again.", None),
    "compiler_missing": ("The part of AXIOM that makes PDFs is missing from this Mac. Install AXIOM again, then try again.", None),
    "compile_error": ("AXIOM couldn't turn this resume into a PDF. Try again, or switch this job to plain Apply.", None),
    "compile_timeout": ("Making the PDF took too long. Try again.", None),
    "output_too_large": ("The PDF came out too large to send. Try again, or switch this job to plain Apply.", None),
    "artifact_file_changed": ("The PDF changed or went missing. Try again.", None),
}
# A fault with no code of its own carries the words of whatever raised it. These are read for what they mean, and never shown.
_FAULTS = (
    (r"^no_model\b", NEEDS_KEY, "connections"),
    (r"^missing_description\b", READ_POSTING, None),
    (r"^(project_quality|quality|rules)\b", "The AI's draft didn't pass AXIOM's checks, so it wasn't kept. Each try writes a new one, so try again.", None),
    (r"No API key", NEEDS_KEY, "connections"),
    (r"status_code: 40[13]\b|AuthenticationError|PermissionDenied|\bAPI key\b", "The AI service refused your key. Check it in Settings, under Connections, then try again.", "connections"),
    (r"status_code: 402\b|status_code: 429\b|RateLimit|insufficient|quota|credit", "The AI service says this key is out of credit or over its limit. Check it in Settings, under Connections, then try again.", "connections"),
    (r"Daily model budget|BudgetExceeded", "Today's AI spending limit has been reached. Try again tomorrow, or raise it in Settings, under Limits.", "applying"),
    (r"output validation|InvalidResponse|UnexpectedModelBehavior", "The AI's answer couldn't be read. Try again.", None),
    (r"Timeout|timed out", "The AI took too long to answer. Try again.", None),
    (r"Connection|Unavailable|status_code: 5\d\d", "AXIOM couldn't reach the AI service. Check your internet connection, then try again.", None),
    (r"AgentDisabled|agent is disabled", "The AI that writes answers is turned off in Settings, under Advanced. Turn it on, then try again.", "system"),
)


def plain_failure(error: dict | None, rule_problems: list | None = None) -> dict:
    """What a failed tailoring says: the reason in words, and `fix`, the screen with the answer when it isn't simply trying again."""
    error = error or {}
    code, detail = error.get("code") or "", error.get("detail") or ""
    if code in FAILURES:
        message, fix = FAILURES[code]
        return {"message": message, "fix": fix}
    if code == "rules":
        first = (rule_problems or [{}])[0].get("message") or detail.split("; ")[0]
        return {"message": f"The new version broke a resume rule, so it wasn't kept: {first.rstrip('.')}. Try again.", "fix": None}
    if code == "ats_parse":
        return {"message": f"The new version wouldn't read cleanly in an employer's system, so it wasn't kept: {detail}. Try again.", "fix": None}
    for pattern, message, fix in _FAULTS:
        if re.search(pattern, detail):
            return {"message": message, "fix": fix}
    return {"message": detail if code == "unknown_mode" else TRY_AGAIN, "fix": None}


def approval_required(db) -> bool:
    """On unless the applicant turned it off."""
    return (db.get_setting(SETTING, {}) or {}).get("approval", True) is not False


def _approval_required_in(session) -> bool:
    from ...db import Setting

    row = session.get(Setting, SETTING)
    return ((row.value if row else None) or {}).get("approval", True) is not False


def set_approval(db, on: bool) -> dict:
    """The switch, and what it means for what's waiting: off, a version in review that passes every check is approved by the setting, so its row stops
    saying it waits for you; on, what the setting approved waits for you again. What you approved yourself stays approved, and nothing started changes."""
    from ...db import Job

    db.set_setting(SETTING, {**(db.get_setting(SETTING, {}) or {}), "approval": bool(on)})
    woken = []
    with db.exclusive() as s:
        for row in s.scalars(select(RsTailorChoice).where(RsTailorChoice.state.in_(["review", "approved"]))).all():
            job = s.get(Job, row.job_id)
            if job is None or job.status in STARTED:
                continue
            if on:
                if row.approved_how == "setting_off":
                    row.state = "review"
                    row.approved_artifact_id = row.approved_sha = row.approved_at = row.approved_how = None
                continue
            artifact = s.get(RsArtifact, row.artifact_id) if row.artifact_id else None
            if row.state == "review" and artifact is not None and artifact.status == "ready" and artifact_current(s, artifact, job) and not content_problem(s, artifact):
                row.state, row.approved_artifact_id, row.approved_sha, row.approved_at, row.approved_how = "approved", artifact.id, artifact.pdf_sha, now(), "setting_off"
                woken.append(row.job_id)
    for job_id in woken:
        wake(db, job_id)
    return {"approval": bool(on)}


def lease() -> str:
    return (datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)).isoformat()


def _account_wide_in(session) -> bool:
    from ...db import Setting

    policy = session.get(Setting, "application_resume_policy")
    return ((policy.value if policy else {}) or {}).get("mode") == "tailored"


def follow_policy(db, job_id: str, artifact) -> None:
    """The account-wide switch is every job choosing Normal, so its version goes through the same screen and the same approval. With no choice here the job
    waited on "review the tailored resume" while no screen offered one (the guard at the send asks for an approved choice, as it should)."""
    from ...db import Setting

    approved = False
    with db.exclusive() as s:
        if not _account_wide_in(s) or s.get(RsTailorChoice, job_id) is not None:
            return
        approved = not _approval_required_in(s) and bool((artifact.validation.get("rules") or {}).get("ok"))
        s.add(
            RsTailorChoice(
                job_id=job_id,
                mode=artifact.validation.get("mode") or "normal",
                state="approved" if approved else "review",
                artifact_id=artifact.artifact_id,
                approved_artifact_id=artifact.artifact_id if approved else None,
                approved_sha=artifact.pdf_sha if approved else None,
                approved_at=now() if approved else None,
                approved_how="setting_off" if approved else None,
            )
        )
        if s.get(Setting, FOLLOWED + job_id) is None:
            s.add(Setting(key=FOLLOWED + job_id, value={"at": now()}))
    if approved:
        wake(db, job_id)


def policy_off(db) -> None:
    """The account-wide switch went off: "employers get your resume as uploaded". What the switch chose ends with it, unless the application has started."""
    from ...db import Job, Setting

    with db.exclusive() as s:
        for mark in s.scalars(select(Setting).where(Setting.key.like(FOLLOWED + "%"))).all():
            job_id = mark.key[len(FOLLOWED):]
            job, row = s.get(Job, job_id), s.get(RsTailorChoice, job_id)
            if row is not None and (job is None or job.status not in STARTED):
                s.delete(row)
            s.delete(mark)


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
            raise LookupError(ALREADY_STARTED)
        if mode in (None, "", "off"):
            if row is not None:
                if _account_wide_in(s):
                    # Queue admission would still ask this job for its version, and then wait for an approval no screen offers.
                    raise LookupError(ACCOUNT_WIDE)
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
    built = row.mode
    kwargs = {"compile_fn": compile_fn} if compile_fn else {}
    try:
        await read_posting(db, config, job_id)
        result = await pipeline.prepare_resume(db, config, job_id, mode=built, model=model, fresh=fresh, **kwargs)
        if result.status == "failed" and result.cached and not fresh:
            # The person chose this mode again: a stored failure is not their answer, and it may be from an older build.
            result = await pipeline.prepare_resume(db, config, job_id, mode=built, model=model, fresh=True, **kwargs)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # An unexpected fault must not leave the job waiting for ever with nothing said.
        log.exception("tailoring failed for %s", job_id)
        _record(db, job_id, built, state="failed", error={"code": "tailoring_error", "retryable": True, "detail": type(exc).__name__})
        return {"state": "failed"}
    if result.status == "ready" and result.artifact:
        approved = not approval_required(db) and bool((result.artifact.validation.get("rules") or {}).get("ok"))
        _record(
            db,
            job_id,
            built,
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
    _record(db, job_id, built, state="failed", error=error)
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


def _record(db, job_id, mode=None, **fields) -> None:
    """The result of a build of `mode`. If the person has since chosen another mode, this one isn't their answer: it would show under the wrong name."""
    with db.exclusive() as s:
        row = s.get(RsTailorChoice, job_id)
        if row is None or (mode and row.mode != mode):
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
            mode = (get_choice(db, job_id) or RsTailorChoice()).mode
            await run(db, config, job_id, fresh=fresh)
            # Another mode chosen while this one built found this task running and asked for nothing: it's its turn now.
            latest = get_choice(db, job_id)
            if latest is not None and latest.state == "preparing" and latest.mode != mode:
                await run(db, config, job_id, fresh=True)
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


def source_for(session, job):
    """The resume this job would send: the one queue admission picks (service/queueing.py), so a version made from another is said to be out of date here, not refused there."""
    from ...db import Resume, Setting

    policy = session.get(Setting, "application_resume_policy")
    wanted = ((policy.value if policy else {}) or {}).get("resume_id")
    rid = wanted or getattr(job, "resume_id", None)
    resume = session.get(Resume, rid) if rid else None
    return resume or session.scalar(select(Resume).where(Resume.demo.is_(False)).order_by(Resume.created_at.desc()).limit(1))


def reclaim(db, job_id: str) -> bool:
    """Asked when the screen reads a job: a build nobody holds any more (the app restarted, the Mac slept). The autopilot's tick does this too, but it needn't be running for a job someone is looking at."""
    if job_id in _TASKS:
        return False
    at = datetime.now(UTC).isoformat()
    row = get_choice(db, job_id)
    if row is None or row.state != "preparing" or (row.lease_until and row.lease_until >= at):
        return False
    with db.exclusive() as s:
        row = s.get(RsTailorChoice, job_id)
        if row is None or row.state != "preparing" or (row.lease_until and row.lease_until >= at):
            return False
        row.lease_until = lease()
    return True


def artifact_current(session, row: RsArtifact, job) -> bool:
    """Built from today's resume, today's profile and today's posting text."""
    from ...db import Setting
    from .evidence import profile_digest

    profile = session.get(Setting, "profile")
    if row.profile_digest != profile_digest((profile.value if profile else {}) or {}) or row.description_sha != digest(job.description or "")[:20]:
        return False
    source = source_for(session, job)
    return source is not None and source.id == row.source_resume_id and source.sha256 == row.source_resume_sha


def content_problem(session, row: RsArtifact) -> tuple[str, str] | None:
    """Content and project revisions must be valid, even with automatic approval."""
    validation = row.validation or {}
    if validation.get("mode") == "aggressive" or (validation.get("report") or {}).get("workspace_id") or row.policy_version == "tailor-aggressive-v1":
        from .aggressive.guards import send_problem as project_problem

        if problem := project_problem(session, row):
            return problem
    tailored = modes.is_tailored_policy(row.policy_version)
    rules = validation.get("rules")
    if (tailored or rules is not None) and not (rules or {}).get("ok"):
        first = ((rules or {}).get("problems") or [{}])[0].get("message", "It hasn't passed the resume rules")
        return BROKEN, f"This tailored resume can't be sent: {first}"
    leaks = draft_leaks(row.text or "", validation.get("drafts") or [])
    if leaks:
        return UNCONFIRMED, f'"{leaks[0]}" is a draft you haven\'t confirmed, so this resume can\'t be sent'
    return None


def send_problem(session, row: RsArtifact) -> tuple[str, str] | None:
    """The same content and approval gate at queue admission and when a worker takes the PDF."""
    if problem := content_problem(session, row):
        return problem
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
        wait(FAILED, f"The tailored resume couldn't be made. {plain_failure(choice.error)['message']} Or switch this job to plain Apply")
    if artifact is None or artifact.status != "ready":
        wait(WAITING, "Preparing the tailored resume")
    if not usable:
        wait(STALE, "Your resume, your profile or the posting changed after this resume was tailored. Tailor it again")
    problem = send_problem(session, artifact)
    if problem:
        wait(*problem)
    return artifact.id


def adopt(session, job_id: str, artifact_id: str, pdf_sha: str, rules_ok: bool) -> dict | None:
    """A version saved from the full editor, on top of this job's current one, is this job's resume now: otherwise the edit never reaches the employer, whatever the screen says.
    It's a new file, so it needs its own approval. Nothing changes once the application has started."""
    from ...db import Job

    row = session.get(RsTailorChoice, job_id)
    job = session.get(Job, job_id)
    if row is None or job is None or job.status in STARTED:
        return None
    approval = _approval_required_in(session) or not rules_ok
    row.artifact_id, row.updated_at, row.state, row.error = artifact_id, now(), "review" if approval else "approved", {}
    row.approved_artifact_id = None if approval else artifact_id
    row.approved_sha = None if approval else pdf_sha
    row.approved_at = None if approval else now()
    row.approved_how = None if approval else "setting_off"
    return {"mode": row.mode, "state": row.state}


def approve(db, config, job_id: str, artifact_id: str) -> dict:
    """The applicant's yes, for this exact file. Refuses what the rules refuse."""
    from ...db import Job

    with db.exclusive() as s:
        job = s.get(Job, job_id)
        if job is not None and job.status in STARTED:
            raise ValueError(ALREADY_STARTED)
        artifact = s.get(RsArtifact, artifact_id)
        if artifact is None or artifact.job_id != job_id or artifact.status != "ready":
            raise ValueError("This tailored version isn't ready")
        if not pipeline.file_intact(config.data_dir, artifact):
            raise ValueError("The PDF changed or went missing. Tailor it again")
        if problem := content_problem(s, artifact):
            raise ValueError(problem[1])
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
