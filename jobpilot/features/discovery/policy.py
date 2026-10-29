"""Discovery policy: freshness with provenance, hard gates, requisition dedup.

Nothing here executes work. These are pure decisions over listings.
"""

import re
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from .._store import digest, norm, now
from ..knowledge.employers import tenant_alias
from .tables import DcRequisition

POLICY_VERSION = "discovery-v2-posting-paths"


class DiscoveryPolicy(BaseModel):
    max_age_days: int = Field(default=30, ge=1, le=30)
    early_career_only: bool = False
    us_only: bool = True
    minimum_fit: int = Field(default=70, ge=0, le=100)
    classify_batch: int = Field(default=24, ge=1, le=24)
    owners: int = Field(default=7, ge=1, le=7)
    tailor_lookahead: int = Field(default=2, ge=1, le=6)  # ready resumes kept ahead per owner
    classify_lookahead: int = Field(default=4, ge=1, le=20)  # eligible jobs kept ahead per owner (and per free one)
    classify_yield: float = Field(default=0.35, gt=0, le=1)  # share of ranked postings that end up eligible
    classify_batches_per_tick: int = Field(default=3, ge=1, le=6)  # model batches planned at once
    source_backoff_minutes: int = Field(default=15, ge=1)
    source_backoff_max_hours: int = Field(default=24, ge=1)
    classify_retry_minutes: int = Field(default=20, ge=1)


# ---------- posting time ----------
def posting_time(posted: str | None, basis: str, first_seen: str | None):
    """(instant, basis) where basis is verified | inferred | first_seen | unknown.

    A feed's own publish timestamp is verified; a relative phrase ("3 days ago")
    is inferred; with neither, only first-seen is known, which never proves
    freshness on its own.
    """
    if posted:
        try:
            at = datetime.fromisoformat(posted.replace("Z", "+00:00"))
            at = at if at.tzinfo else at.replace(tzinfo=UTC)
            kind = (
                "inferred"
                if re.search(r"relative|ago|search|inferred|estimated", basis or "", re.I)
                else "verified"
            )
            return at.astimezone(UTC), kind
        except ValueError:
            pass
    if first_seen:
        return datetime.fromisoformat(first_seen.replace("Z", "+00:00")), "first_seen"
    return None, "unknown"


def fresh(posted, basis, first_seen, policy: DiscoveryPolicy, at=None):
    at = at or datetime.now(UTC)
    instant, kind = posting_time(posted, basis, first_seen)
    if kind not in {"verified", "inferred"} or instant is None:
        return False, kind
    return at - timedelta(days=policy.max_age_days) <= instant <= at + timedelta(hours=14), kind


# ---------- hard gates (no model) ----------
CLOSED = re.compile(
    r"no longer (?:accepting|available|open)|position has been filled|this (?:job|posting|requisition) "
    r"(?:is )?(?:closed|expired)|applications? (?:are )?closed|job not found|page (?:you requested )?"
    r"(?:could not be|was not) found",
    re.I,
)
GENERIC_TITLE = re.compile(
    r"^\s*(?:careers?|jobs|open (?:positions|roles)|current openings|join (?:us|our team)|"
    r"work with us|search jobs|job search|all jobs|opportunities)\s*$",
    re.I,
)
JOB_ID_IN_URL = re.compile(
    r"/jobs?/\d+|/job/[^?#]+_[A-Za-z0-9-]+(?:[/?#]|$)|"
    r"applytojob\.com/apply/[A-Za-z0-9]{8,}(?:[/?#]|$)|apply\.workable\.com/[^/?#]+/j/[A-Za-z0-9]{8,}(?:[/?#]|$)|"
    r"jobs\.lever\.co/[^/]+/[0-9a-f-]{20,}|jobs\.ashbyhq\.com/[^/]+/[0-9a-f-]{20,}|"
    r"gh_jid=\d+|[?&](?:jobid|job_id|id|req|requisition)=|/\d{5,}|/R\d{4,}|/[0-9a-f]{8}-[0-9a-f]{4}-",
    re.I,
)
SENIOR = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|distinguished|lead|leader|director|head of|manager|vp|avp|vice president|"
    r"architect|SMTS|LMTS|PMTS|III|IV)\b",
    re.I,
)
YEARS = re.compile(
    r"(?P<low>\d{1,2})\s*(?:\+|(?:-|to)\s*\d{1,2})?\s*(?:years?|yrs?)\b[^.\n]{0,60}experience", re.I
)


# A description shorter than this was never fetched (a feed's label, a page title, 6-27 characters on short-link URLs).
UNFETCHED_CHARS = 50


def unfetched(description) -> bool:
    return len((description or "").strip()) < UNFETCHED_CHARS


def gate(job: dict, policy: DiscoveryPolicy) -> str | None:
    """Reason a listing can never be a suitable application, or None.

    A posting whose text was never fetched says nothing about its page: "no job id in the URL and under 400 characters"
    refused short-link postings for good because the description fetch (ranking's) never ran for a refused job
    (2026-10-29). Ranking fetches the text, defers the posting while it cannot, and the shape check runs on what it got.
    """
    title, url, description = job.get("title", ""), job.get("url", ""), job.get("description", "") or ""
    if GENERIC_TITLE.match(title or "") or (
        not JOB_ID_IN_URL.search(url) and not unfetched(description) and len(description) < 400
    ):
        return "generic_careers_page"
    if CLOSED.search(description[:3000]) or CLOSED.search(title):
        return "closed"
    if policy.us_only:
        from ...jobboard import GERMAN_ROLE, us_location

        if not us_location(job.get("location", ""), job.get("country", "")) or GERMAN_ROLE.search(title or ""):
            return "non_us_or_unknown"
    if policy.early_career_only:
        if SENIOR.search(title):
            return "seniority"
        required = []
        for m in YEARS.finditer(description):
            context = description[max(0, m.start() - 70) : m.end() + 40]
            if not re.search(r"prefer|nice.to.have|bonus|ideally|plus", context, re.I):
                required.append(int(m["low"]))
        if required and min(required) > 3:
            return "experience_required"
    return None


# ---------- requisition identity and dedup ----------
REQ_IN_TEXT = re.compile(
    r"\b(?:req(?:uisition)?(?:\s*(?:id|#|number|no\.?))?|job\s*id)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{3,})", re.I
)


def requisition_key(url: str, title: str, location: str, description: str = "", company: str = "") -> str:
    """Same requisition seen through search results, boards and direct links → same key."""
    tenant = tenant_alias(url) or ("name:" + norm(company))
    try:
        from ...ats import canonicalize, detect

        # A recognized ATS URL has a native job identity (board + job ID, tenant + requisition).
        if detect(url).id not in {"custom", "generic", "unknown"}:
            return "req:" + canonicalize(url)[1][:32]
    except Exception:
        pass
    m = REQ_IN_TEXT.search(description[:4000] or "")
    if m:
        return "req:" + digest([tenant, m.group(1).upper()])[:24]
    return "req:" + digest([tenant, norm(title), norm(location)])[:24]


def register(
    s, job: dict, source_id: str = "", posted: str | None = None, basis: str = ""
) -> tuple[str, bool]:
    """Upsert a requisition; returns (canonical job id, duplicate?)."""
    key = requisition_key(
        job["url"],
        job.get("title", ""),
        job.get("location", ""),
        job.get("description", ""),
        job.get("company", ""),
    )
    row = s.get(DcRequisition, key)
    evidence = {"url": job["url"], "source_id": source_id, "seen_at": now(), "posted": posted, "basis": basis}
    if row is None:
        _, kind = posting_time(posted, basis, None)
        s.add(
            DcRequisition(
                id=key,
                job_id=job["id"],
                title=job.get("title", ""),
                location=job.get("location", ""),
                posted_at=posted,
                posted_basis=kind if kind != "unknown" else "first_seen",
                sources=[evidence],
            )
        )
        s.flush()
        return job["id"], False
    from ...db import Job

    if row.job_id and row.job_id != job["id"] and s.get(Job, row.job_id) is None:
        # The earlier job row was removed (e.g. an operational reset). Duplicate
        # submission protection lives in the submission guard, not here.
        row.job_id = job["id"]
    urls = {x.get("url") for x in row.sources or []}
    if job["url"] not in urls:
        row.sources = (row.sources or [])[-19:] + [evidence]
    row.last_seen = now()
    # Upgrade provenance: a verified date beats an inferred one or first-seen.
    _, kind = posting_time(posted, basis, None)
    rank = {"verified": 3, "inferred": 2, "first_seen": 1, "unknown": 0}
    if posted and rank.get(kind, 0) > rank.get(row.posted_basis, 0):
        row.posted_at, row.posted_basis = posted, kind
    return row.job_id or job["id"], (row.job_id or job["id"]) != job["id"]


def guard_keys(s) -> set:
    """Every submission guard key, read once for a whole scan. One lookup per identity of 6,445 jobs, twice per
    autopilot tick, kept the shared event loop overloaded and new starts held to one at a time (2026-10-21)."""
    from sqlalchemy import select

    from ...db import Setting

    return set(s.scalars(select(Setting.key).where(Setting.key.like("submission_guard:%"))))


def protected(s, url: str, guards: set | None = None) -> bool:
    """Codex's submission guard: an earlier confirmed/uncertain job survives resets."""
    from ...db import Setting
    from ...operations.reset import guard_key

    try:
        from ...ats import identity_variants

        identities = identity_variants(url)
    except Exception:
        return False
    if guards is not None:
        return any(guard_key(identity) in guards for identity in identities)
    return any(s.get(Setting, guard_key(identity)) is not None for identity in identities)


# ---------- dead sources ----------
# A board that answers "not found" or refuses the public API will answer the same tomorrow. 450 of 4,263 sources (174
# Greenhouse and 99 Ashby 404s, 77 career pages 404, 36 Workday 422s) failed on every scan, most with 34+ failures in
# a row, yet each was retried every 24 hours for good (2026-10-29).
PERMANENT_STATUS = frozenset({400, 401, 403, 404, 410, 422})
DEAD_SOURCE_BASE_HOURS, DEAD_SOURCE_MAX_DAYS = 24, 14


def permanent_failure(exc) -> bool:
    """Whether the error says the board itself is gone or closed to us (not a timeout, 429 or server error)."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in PERMANENT_STATUS:
        return True
    text = str(exc)
    return "could not be resolved" in text or "CERTIFICATE_VERIFY_FAILED" in text


def dead_source_wait(failures: int) -> timedelta:
    """Wait before the next try after `failures` consecutive permanent errors: 1 day, 2, 4, 8, then 14 days."""
    hours = DEAD_SOURCE_BASE_HOURS * 2 ** max(0, min(failures, 10) - 1)
    return timedelta(hours=min(hours, 24 * DEAD_SOURCE_MAX_DAYS))
