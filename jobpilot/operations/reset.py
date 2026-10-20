"""Explicit operational reset; applicant knowledge and submission guards survive."""

from sqlalchemy import delete, func, select

from ..ats import canonicalize
from ..db import (
    Event,
    Job,
    JobListing,
    MailChallenge,
    MailConsumption,
    MailOAuth,
    Outbox,
    Review,
    Run,
    Setting,
    Source,
    now,
)

PROTECTED_STATES = {"confirmed", "submission_unknown", "already_applied", "submitting"}
ACTIVE_STATES = {"queued", "running", "submitting"}
PRESERVED_KEYS = {
    "profile",
    "profile_skill_provenance",
    "education_dates_provenance",
    "confirmation_policy",
    "job_board_settings",
    "job_board_sources",
    "model_switch",
    "agent_settings",
    "telegram",
}
PRESERVED_PREFIXES = (
    "migrated:",
    "employer_account:",
    "application_credentials:",
    "submission_guard:",
    "knowledge:",
    "company_knowledge:",
    "historical_company_submissions:",
    "application_pacing:",
)


def guard_key(identity):
    return "submission_guard:" + identity


def preserved_setting(key):
    return key in PRESERVED_KEYS or key.startswith(PRESERVED_PREFIXES)


def reset_operational_state(db, *, workers_stopped=False):
    """Caller must stop the executor and archive its database/artifacts first.

    No credentials or applicant values are returned. DBOS history and artifact cleanup
    are deliberately external steps performed while the executor remains stopped.
    """
    if not workers_stopped:
        raise RuntimeError("Stop the executor and archive the installation before resetting")
    with db.exclusive() as session:
        if session.scalar(select(func.count()).select_from(Run).where(Run.state.in_(ACTIVE_STATES))):
            raise RuntimeError("Active applications must drain before an operational reset")
        guards = 0
        for run, job in session.execute(
            select(Run, Job)
            .join(Job, Job.id == Run.job_id)
            .where(Run.state.in_(PROTECTED_STATES), Job.demo.is_(False))
        ):
            key = guard_key(canonicalize(job.url)[1])
            existing = session.get(Setting, key)
            # Keep only a hash and protected state, never a packet, URL or receipt.
            value = {
                "state": "confirmed" if run.state == "confirmed" else "submission_unknown",
                "archived_at": now(),
            }
            if existing:
                if existing.value.get("state") != "confirmed":
                    existing.value = value
            else:
                session.add(Setting(key=key, value=value))
                session.flush()
                guards += 1
        counts = {}
        for model in (Event, Review, Outbox, MailConsumption, MailChallenge, MailOAuth, JobListing, Run, Job):
            counts[model.__tablename__] = session.scalar(select(func.count()).select_from(model))
            session.execute(delete(model))
        removed_settings = 0
        for item in session.scalars(select(Setting)):
            if not preserved_setting(item.key):
                session.delete(item)
                removed_settings += 1
        session.flush()
        board = session.get(Setting, "job_board_settings")
        if board:
            board.value = {**board.value, "enabled": False, "auto_apply": False}
        for source in session.scalars(select(Source)):
            source.enabled = False
            source.last_run, source.error, source.found = None, "", 0
        session.add(Setting(key="control", value={"paused": True, "auto_submit": False}))
        # A deliberate fresh start: feature settings survive, but no old batch resumes.
        session.add(
            Setting(key="operational_reset", value={"at": now(), "counts": counts, "new_guards": guards})
        )
        return {"removed": counts, "removed_settings": removed_settings, "new_guards": guards}
