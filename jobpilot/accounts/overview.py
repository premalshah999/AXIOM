"""Setup → Accounts: each employer account record in plain words, with what the owner can do about it.

Rows are built field by field from the record; sealed values (the login, a replaced login, Dayforce security
answers) never leave this module. Only the login's email is read from its seal, so the owner knows which address
to reset on the employer's site.
"""

from urllib.parse import urlsplit

from sqlalchemy import func, select

from ..db import Job, MailChallenge, Run, Setting
from ..mail import origin

PREFIX = "employer_account:"

# Recorded engine state → the owner's status. "password_unknown" is the engine's state for an account whose reset
# never arrived (2026-10-22); "credentials_rejected" means the same to the owner.
STATUS = {
    "authenticated": "active",
    "password_reset": "active",
    "verification_pending": "verification",
    "credentials_rejected": "password_unknown",
    "password_unknown": "password_unknown",
    "reset_requested": "reset_pending",
    "locked": "locked",
    "created_locally": "created",
    "signing_in": "signing_in",
    "exists": "registered",
    "identity_conflict": "identity_conflict",
}
LABEL = {
    "active": "Active",
    "verification": "Needs email verification",
    "password_unknown": "Password unknown",
    "reset_pending": "Reset pending",
    "locked": "Locked",
    "created": "Created",
    "signing_in": "Signing in",
    "registered": "Already registered",
    "identity_conflict": "Phone number on another account",
    "unknown": "Unknown",
}
# Statuses only the owner can settle, or that are worth a look.
ATTENTION = {"verification", "password_unknown", "reset_pending", "locked", "identity_conflict"}
FIX_IT = "Reset it on the employer's site, then save the new password here."
# A verification request that ended without its email; a matched or pending one is still in progress.
RESTARTABLE = {"cancelled", "expired", "failed"}


def detail(status: str, record: dict, retry: bool) -> str:
    if status == "active":
        if record.get("owner_set_at"):
            return "You saved this password. The next application signs in with it."
        if record.get("state") == "password_reset":
            return "The password was reset and accepted. Applications sign in with it."
        return "Signed in successfully. Applications sign in with this login."
    if status == "verification":
        return "The employer asked to confirm the email address and it isn't confirmed yet. " + (
            "A paused application is waiting for it: Retry verification asks for a new email."
            if retry
            else "The next application to this employer signs in and asks for a new email."
        )
    if status == "password_unknown":
        return "The employer refused the saved password and no reset reached the mailbox. " + FIX_IT
    if status == "reset_pending":
        return (
            "ApplyPilot asked the employer for a password reset email. If it doesn't arrive, reset the password "
            "yourself and save it here."
        )
    if status == "locked":
        return (
            "The employer locked this account after failed sign-ins. Unlock or "
            + FIX_IT[0].lower()
            + FIX_IT[1:]
        )
    if status == "created":
        return "Created during an application. No sign-in has succeeded yet."
    if status == "signing_in":
        return "An application started signing in here and didn't record the outcome."
    if status == "identity_conflict":
        return (
            "The employer already has an account that holds your phone number under a different email address, so "
            "this email cannot sign in or create one. No codes or resets are requested for it. Save the password of "
            "the account's email under this record, or forget the record once the other account is dealt with."
        )
    if status == "registered":
        return "The employer says this email already has an account. The next application signs in with the saved login."
    return f"Recorded state: {str(record.get('state') or 'none').replace('_', ' ')}."


def employer(tenant: str) -> str:
    """The employer a shared ATS host serves, in words: "company:acme" → "acme"."""
    kind, _, value = (tenant or "").partition(":")
    if kind == "employer":
        return (urlsplit(value).hostname or value).removeprefix("www.")
    if kind in {"company", "recruiting"}:
        return value
    if kind == "cid":
        return "client " + value
    return ""


def login_email(service, record: dict) -> str:
    try:
        return (
            str(service.mail.vault.open(record["credentials"]).get("email", ""))
            if record.get("credentials")
            else ""
        )
    except Exception:
        return ""


def _origin(url: str) -> str:
    try:
        return origin(url)
    except ValueError:
        return ""


def retained_verifications(s) -> list[tuple[str, str, str]]:
    """(job origin, run id, challenge id) for each latest-of-its-job submission_unknown run whose email verification
    request ended unanswered, newest first: the drafts Service.resume_mail_wait(restart_verification=True) restarts.
    That method still checks every guard; this only keeps the button off rows it would certainly refuse."""
    rows = s.execute(
        select(Run.id, Run.job_id, Run.packet, Run.created_at, Job.url)
        .join(Job, Job.id == Run.job_id)
        .where(Run.state == "submission_unknown")
        .order_by(Run.created_at.desc())
    ).all()
    if not rows:
        return []
    latest = dict(
        s.execute(
            select(Run.job_id, func.max(Run.created_at))
            .where(Run.job_id.in_({row.job_id for row in rows}))
            .group_by(Run.job_id)
        ).all()
    )
    found = []
    for row in rows:
        if row.created_at != latest.get(row.job_id):
            continue
        packet = row.packet or {}
        challenge = (packet.get("wait_handle") or {}).get("challenge_id", "")
        if not challenge and packet.get("reservation_run_id"):
            original = s.get(Run, packet["reservation_run_id"])
            if original and original.job_id == row.job_id:
                challenge = ((original.packet or {}).get("wait_handle") or {}).get("challenge_id", "")
        mail = s.get(MailChallenge, challenge) if challenge else None
        if mail and mail.state in RESTARTABLE:
            found.append((_origin(row.url), row.id, challenge))
    return found


def verification_draft(record: dict, drafts: list[tuple[str, str, str]]) -> tuple[str, str] | None:
    """The newest retained verification draft of this record's employer: same origin, or the employer site that
    a shared host's tenant names (iCIMS Central records carry "employer:<site>")."""
    if record.get("state") != "verification_pending":
        return None
    sites = {record.get("origin", "")}
    tenant = record.get("tenant") or ""
    if tenant.startswith("employer:"):
        sites.add(_origin(tenant.removeprefix("employer:")))
    sites.discard("")
    for site, run_id, challenge in drafts:
        if site in sites:
            return run_id, challenge
    return None


def row(service, key: str, record: dict, drafts) -> dict:
    status = STATUS.get(record.get("state") or "", "unknown")
    retry = bool(verification_draft(record, drafts))
    site = record.get("origin", "")
    return {
        "id": key,
        "origin": site,
        "host": (urlsplit(site).hostname or "").removeprefix("www.") or key.removeprefix(PREFIX)[:12],
        "tenant": record.get("tenant", ""),
        "employer": employer(record.get("tenant", "")),
        "state": record.get("state", ""),
        "status": status,
        "label": LABEL[status],
        "detail": detail(status, record, retry),
        "attention": status in ATTENTION,
        "login_email": login_email(service, record),
        "updated_at": record.get("updated_at", ""),
        "confirmed_at": record.get("confirmed_at"),
        "owner_set_at": record.get("owner_set_at"),
        "reset_requested_at": record.get("reset_requested_at") or None,
        "can_retry_verification": retry,
    }


def overview(service) -> list[dict]:
    with service.db.session() as s:
        records = [
            (r.key, dict(r.value)) for r in s.scalars(select(Setting).where(Setting.key.like(PREFIX + "%")))
        ]
        drafts = (
            retained_verifications(s)
            if any(v.get("state") == "verification_pending" for _, v in records)
            else []
        )
    rows = [row(service, key, value, drafts) for key, value in records]
    # Needs-attention first, then the most recently changed.
    rows.sort(key=lambda r: r["updated_at"] or "", reverse=True)
    rows.sort(key=lambda r: not r["attention"])
    return rows


def one(service, key: str) -> dict | None:
    record = service.db.get_setting(key) if key.startswith(PREFIX) else None
    if not record:
        return None
    with service.db.session() as s:
        drafts = retained_verifications(s) if record.get("state") == "verification_pending" else []
    return row(service, key, record, drafts)


def draft_for(service, key: str) -> tuple[str, str] | None:
    record = service.db.get_setting(key) if key.startswith(PREFIX) else None
    if not record:
        return None
    with service.db.session() as s:
        return verification_draft(record, retained_verifications(s))
