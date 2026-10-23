"""Durable employer volume and Ashby pacing at canonical admission boundaries.

All helpers run inside the caller's existing exclusive transaction. They acquire
no process-local lock and never sleep while occupying an application slot.
"""

from datetime import UTC, datetime, timedelta
import re
import time
from types import SimpleNamespace

from sqlalchemy import select

from .admission import QueueBlocked
from .db import Job, Run, Setting
from .features.knowledge.employers import name_aliases, tenant_alias
from .features.knowledge.tables import KnAlias

ASHBY_KEY = "application_pacing:ashby"
ASHBY_GAP = 150
# Platforms that take one application at a time with a gap between starts (seconds). Workable answers HTTP 412
# "Something went wrong. We are working on this, please try again later." once a candidate has applied a few times:
# 3 accepted on 10-07, then 412s on 10-12/14; 4 accepted in one minute on 10-23 after nine quiet days, then every
# apply refused for hours, five minutes apart too (no CAPTCHA involved). At most four starts a day.
START_GAPS = {"ashby": ASHBY_GAP, "workable": 6 * 3600}


def gap_key(ats):
    return "application_pacing:" + ats  # "application_pacing:ashby" is ASHBY_KEY
HISTORICAL_PREFIX = "historical_company_submissions:"
COMPANY_LIMIT = 3
LIMIT_KEY = "application_pacing:company_limit"
# Platforms whose verification codes come from one sender shared by every employer. Oracle Recruiting lost 21
# jobs to "email sender busy" on 2026-10-17 when 47 runs reached the code step within one hour; each waited for
# the per-sender mail lock and gave up after the browser had started. A small concurrency keeps the lock short,
# and other platforms keep dispatching while extra Oracle jobs wait. Codes are attributed per tenant since 2026-10-18,
# so only one tenant's runs serialize. SmartRecruiters: DataDome walled every tenant after about ten oneclick-ui
# applications in ten minutes (2026-10-18, see bot_wall). SuccessFactors: runs on one career host share the browser
# session, so a second run's sign-in replaced the first's (AMETEK and Corning on career4, 2026-10-18), and every tenant
# sends codes from one address. iCIMS: one sender for every employer; a second run started while the sender looked
# free and then waited out the lease at the code step (six "sender busy" stops, 2026-10-18 19:30-20:30).
# Override with `application_pacing:ats_limits`.
ATS_LIMITS = {"oracle": 4, "smartrecruiters": 2, "successfactors": 1, "icims": 1, "adp": 1}
ATS_LIMITS_KEY = "application_pacing:ats_limits"


def ats_limit(session, ats):
    saved = session.get(Setting, ATS_LIMITS_KEY)
    limits = {**ATS_LIMITS, **((saved.value or {}) if saved else {})}
    value = limits.get(ats)
    return None if value in (None, 0, "") else int(value)


def _ats_active(session, ats, run_id, states):
    count = 0
    for other, employer in session.execute(
        select(Run, Job).join(Job, Job.id == Run.job_id).where(Run.state.in_(states), Job.ats == ats)
    ):
        if other.id != run_id and not _exempt(employer, other):
            count += 1
    return count


def _get(row, name, default=None):
    return row.get(name, default) if isinstance(row, dict) else getattr(row, name, default)


def _exempt(job, run):
    return _get(job, "demo", False) or bool((_get(run, "packet", {}) or {}).get("verification_only"))


def _stamp(at):
    if at is None:
        return time.time()
    return at.timestamp() if isinstance(at, datetime) else float(at)


def may_start(session, job, run, at=None):
    """Whether a run may own a page now: Ashby's start gap and per-platform concurrency limits."""
    if _exempt(job, run):
        return True
    limit = ats_limit(session, _get(job, "ats"))
    if limit is not None and _ats_active(session, _get(job, "ats"), _get(run, "id"), ["running", "submitting"]) >= limit:
        return False
    ats = _get(job, "ats")
    if ats not in START_GAPS:
        return True
    for other, employer in session.execute(
        select(Run, Job)
        .join(Job, Job.id == Run.job_id)
        .where(Run.state.in_(["running", "submitting"]), Job.ats == ats)
    ):
        if other.id != _get(run, "id") and not _exempt(employer, other):
            return False
    saved = session.get(Setting, gap_key(ats))
    # A durable continuation is the same attempt, not another browser start.
    if saved and saved.value.get("run_id") == _get(run, "id"):
        return True
    return not saved or _stamp(at) >= float(saved.value.get("started_at", 0)) + START_GAPS[ats]


def record_start(session, job, run, at=None):
    """Persist successful queued -> running admission in the same transaction."""
    ats = _get(job, "ats")
    if _exempt(job, run) or ats not in START_GAPS:
        return
    saved = session.get(Setting, gap_key(ats))
    if saved and saved.value.get("run_id") == _get(run, "id"):
        return
    if not may_start(session, job, run, at):
        raise QueueBlocked(f"{ats.title()} application pacing is active", outcome="deferred", code="ats_pacing")
    value = {"run_id": _get(run, "id"), "started_at": _stamp(at)}
    if saved:
        saved.value = value
    else:
        session.add(Setting(key=gap_key(ats), value=value))
    session.flush()


def require_ats_focus(session, job, run=None):
    """Saved platform scope applies at every submission entry, including manual batches."""
    if _exempt(job, run):
        return
    focus = session.get(Setting, "ats_focus")
    if focus and _get(job, "ats") in set(focus.value.get("exclude") or []):
        raise QueueBlocked("This ATS is excluded by the saved platform focus", code="ats_focus")


def require_ats_admission(session, job, run_id=None, owner_pick=False):
    """Defer additional Ashby/Workable jobs before they occupy DBOS application tasks. The owner's own pick skips the focus."""
    current = session.get(Run, run_id) if run_id else None
    if not owner_pick:
        require_ats_focus(session, job, current)
    if _exempt(job, current):
        return
    limit = ats_limit(session, _get(job, "ats"))
    if limit is not None and _ats_active(session, _get(job, "ats"), run_id, ["queued", "running", "submitting"]) >= limit:
        raise QueueBlocked(
            f"{limit} {_get(job, 'ats')} application(s) already queued or active; this one waits its turn",
            outcome="deferred",
            code="ats_pacing",
            not_before=datetime.fromtimestamp(_stamp(None) + 60, UTC).isoformat(),
        )
    ats = _get(job, "ats")
    if ats not in START_GAPS:
        return
    name = ats.title()
    at = _stamp(None)
    for other, employer in session.execute(
        select(Run, Job)
        .join(Job, Job.id == Run.job_id)
        .where(Run.state.in_(["queued", "running", "submitting"]), Job.ats == ats)
    ):
        if other.id != run_id and not _exempt(employer, other):
            raise QueueBlocked(
                f"Another {name} application is queued or active",
                outcome="deferred",
                code="ats_pacing",
                not_before=datetime.fromtimestamp(at + 60, UTC).isoformat(),
            )
    saved = session.get(Setting, gap_key(ats))
    if saved and saved.value.get("run_id") != run_id:
        available = float(saved.value.get("started_at", 0)) + START_GAPS[ats]
        if at < available:
            raise QueueBlocked(
                f"{name} application start interval has not elapsed",
                outcome="deferred",
                code="ats_pacing",
                not_before=datetime.fromtimestamp(available, UTC).isoformat(),
            )


EMPLOYER_CAP_PREFIX = "employer_cap:"
# The employer's own application limit, read from its refusal (Ashby, 2026-10-18/19):
#   Benchling: "You've reached the maximum of 3 active applications at Benchling."
#   Aurora Innovation: "candidates are limited to 3 applications every 30 days."
#   OpenAI: "Candidates may not apply more than 5 times in any 180 day span."
APPLICATION_CAP = (
    re.compile(r"maximum of (?P<n>\d+) active applications", re.I),
    re.compile(r"limited to (?P<n>\d+) applications? (?:every|per|in(?: any)?(?: a)?) (?P<days>\d+)[ -]days?", re.I),
    re.compile(r"(?:may not|cannot|can.t) apply (?:to more than|more than) (?P<n>\d+) (?:times?|roles?|positions?|jobs?)?"
               r"\s*(?:in|within|per) (?:any|a|each) (?P<days>\d+)[ -]day", re.I),
)


def application_cap(text):
    """{"limit", "days"} when an employer's refusal states its application limit, else None."""
    for pattern in APPLICATION_CAP:
        if match := pattern.search(text or ""):
            days = match.groupdict().get("days")
            return {"limit": int(match["n"]), "days": int(days) if days else None}
    return None


def employer_key(alias_rows, name, url=""):
    names = name_aliases(name or "")
    if not names:
        return None
    tenant = alias_rows.get(tenant_alias(url)) if url else None
    if tenant:
        return tenant[0]
    for name in names:
        found = alias_rows.get(name)
        if found and found[1] == "name":
            return found[0]
    return "name:" + names[-1]


def record_employer_cap(db, job, cap):
    """Remember an employer's stated limit so its other jobs wait instead of being refused one by one."""
    with db.exclusive() as s:
        alias_rows = {a.alias: (a.employer_id, a.kind) for a in s.scalars(select(KnAlias))}
        target = employer_key(alias_rows, _get(job, "company", ""), _get(job, "url", ""))
        if not target or not cap or not cap.get("limit"):
            return None
        value = {**cap, "company": _get(job, "company", ""), "at": datetime.now(UTC).isoformat()}
        row = s.get(Setting, EMPLOYER_CAP_PREFIX + target)
        if row:
            row.value = value
        else:
            s.add(Setting(key=EMPLOYER_CAP_PREFIX + target, value=value))
        return value


def cap_reason(company, cap):
    window = f" every {cap['days']} days" if cap.get("days") else " at a time"
    return f"{company or 'This employer'} accepts at most {cap['limit']} applications{window}; this one was not submitted"


def require_employer_cap(session, target, occupied):
    row = session.get(Setting, EMPLOYER_CAP_PREFIX + target) if target else None
    cap = (row.value or {}) if row else {}
    if not cap.get("limit") or len(occupied) < int(cap["limit"]):
        return
    until = None
    if cap.get("days"):
        until = (datetime.fromisoformat(cap["at"]) + timedelta(days=int(cap["days"]))).isoformat()
    raise QueueBlocked(
        cap_reason(cap.get("company"), cap).replace("this one was not submitted", "its other jobs wait"),
        outcome="deferred" if until else "excluded",
        code="employer_cap",
        not_before=until,
    )


def require_company_limit(session, job, run_id=None):
    """Cap unique confirmed postings plus pending submit admissions at three.

    Count current posting once, even when it appears in imported history. The
    current job is excluded for rechecking an existing reservation. Existing
    queue duplicate guards still own whether that same posting may be retried.
    """
    if _get(job, "demo", False):
        return
    current_run = session.get(Run, run_id) if run_id else None
    if current_run and _exempt(job, current_run):
        return
    alias_rows = {a.alias: (a.employer_id, a.kind) for a in session.scalars(select(KnAlias))}

    def identity(name, url=""):
        return employer_key(alias_rows, name, url)

    target = identity(_get(job, "company", ""), _get(job, "url", ""))
    if target is None:
        return
    confirmed, reserved = set(), set()
    current_identity = _get(job, "identity")
    # Columns only: full job descriptions and run packets (hundreds of MB) were
    # loaded here at every queue and final-submit reservation, under the app lock.
    jobs = session.execute(
        select(Job.id, Job.company, Job.url, Job.status, Job.identity).where(Job.demo.is_(False))
    ).all()
    employers = {candidate.id: identity(candidate.company, candidate.url) for candidate in jobs}
    by_id = {candidate.id: candidate for candidate in jobs}
    for candidate in jobs:
        if employers[candidate.id] == target and candidate.status in {"confirmed", "already_applied"}:
            confirmed.add(candidate.identity)
    target_ids = [jid for jid, employer in employers.items() if employer == target]
    runs = session.execute(
        select(Run.id, Run.job_id, Run.state, Run.mode, Run.packet["verification_only"].as_boolean())
        .where(
            Run.job_id.in_(target_ids),
            Run.state.in_({"confirmed", "already_applied", "queued", "running", "submitting"}),
        )
    ).all()
    for run_row in runs:
        run = SimpleNamespace(
            id=run_row[0],
            job_id=run_row[1],
            state=run_row[2],
            mode=run_row[3],
            packet={"verification_only": bool(run_row[4])},
        )
        candidate = by_id.get(run.job_id)
        if not candidate or employers[candidate.id] != target or _exempt(candidate, run):
            continue
        if run.state in {"confirmed", "already_applied"}:
            confirmed.add(candidate.identity)
        elif run.mode == "submit" and run.state in {"queued", "running", "submitting"}:
            if run.id != run_id:
                reserved.add(candidate.identity)
    for item in session.scalars(select(Setting).where(Setting.key.like(HISTORICAL_PREFIX + "%"))):
        data = item.value
        if identity(data.get("company", ""), data.get("url", "")) == target:
            confirmed.update(i for i in data.get("identities", []) if isinstance(i, str))
    occupied = (confirmed | reserved) - {current_identity}
    require_employer_cap(session, target, occupied)
    # Owner setting (2026-10-14): 1 = only employers with no earlier application.
    saved = session.get(Setting, LIMIT_KEY)
    limit = int((saved.value or {}).get("limit", COMPANY_LIMIT)) if saved else COMPANY_LIMIT
    if len(occupied) >= limit:
        permanent = len(confirmed - {current_identity}) >= limit
        raise QueueBlocked(
            f"Employer already has {len(occupied)} confirmed or reserved application(s); limit {limit}",
            outcome="excluded" if permanent else "deferred",
            code="company_limit",
            not_before=None if permanent else datetime.fromtimestamp(time.time() + 60, UTC).isoformat(),
        )
