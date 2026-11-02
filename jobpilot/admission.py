"""Shared admission decisions for unattended jobs, manual batches and recovery."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

from sqlalchemy import or_, select

from .ats import detect
from .db import Event, Job, Run, Setting


class QueueBlocked(ValueError):
    """A queue guard with an explicit permanent/protected/temporary outcome."""

    def __init__(self, message, *, outcome="excluded", code="queue_guard", not_before=None, detail=None):
        super().__init__(message)
        self.outcome, self.code, self.not_before = outcome, code, not_before
        self.detail = detail  # What the caller that records this block needs (never serialized with as_dict).

    def as_dict(self):
        return {"outcome": self.outcome, "code": self.code, "not_before": self.not_before}


def require_intake(session, job_id):
    """Every wake path observes the same temporary deployment boundary."""
    row = session.get(Setting, "execution_v2_deployment_hold")
    stage = (row.value if row else {}).get("stage")
    if stage == "canary":
        cohort = session.get(Setting, "execution_v2_canary")
        if job_id in {j["id"] for j in (cohort.value if cohort else {}).get("jobs", [])}:
            return
    if stage in {"draining", "canary"}:
        raise QueueBlocked(
            "Application intake is temporarily held for deployment or a bounded trial",
            outcome="deferred",
            code="deployment_hold",
            not_before=temporary_time(),
        )


# Link shorteners and listing aggregators serve many employers from one host.
SHARED_LINK_HOSTS = {
    "go.vansh.nyc", "bit.ly", "lnkd.in", "tinyurl.com", "t.co", "ow.ly", "buff.ly", "rebrand.ly", "cutt.ly",
    "short.gy", "simplify.jobs", "jobright.ai",
}
# Career portals that serve many employers from one host with no employer in the address (Paylocity's job pages are
# /Recruiting/Jobs/Details/<id>). The company names the employer; these are not redirect hosts, so not SHORTENER_HOSTS.
SHARED_PORTAL_HOSTS = {"recruiting.paylocity.com"}


def requisition_twins(session, job_id, urls):
    """(id, url) of every other job that is the same requisition as one of `urls` under another link, on any platform:
    the employer's page around a Greenhouse or Ashby form beside the board URL, an embed, an /apply tail, a Workday or
    iCIMS portal variant (ats.requisition_keys). Roblox, Abnormal, Rubrik and IXL applied twice that way (2026-10)."""
    from .ats import requisition_keys

    wanted = {key: needle for url in urls for key, needle in requisition_keys(url)}
    if not wanted:
        return []
    # The id is searched in the stored links first (one pass over the url column); keys decide.
    rows = session.execute(
        select(Job.id, Job.url).where(
            Job.id != job_id, Job.demo.is_(False), or_(*[Job.url.icontains(n, autoescape=True) for n in set(wanted.values())])
        )
    )
    return [(row.id, row.url) for row in rows if wanted.keys() & {key for key, _ in requisition_keys(row.url)}]


def employer_partition(job):
    get = job.get if isinstance(job, dict) else lambda key, default=None: getattr(job, key, default)
    parsed = urlsplit(get("url", ""))
    host = (parsed.hostname or "").lower()
    if host in SHARED_LINK_HOSTS:
        # Every go.vansh.nyc job was one "employer": DV Trading, OneImaging, IXL, ALTEN and Perpay waited for each
        # other as employer_busy, one at a time across all workers (2026-10-29). The employer is the company.
        company = re.sub(r"\s+", " ", str(get("company", "") or "")).strip().casefold()
        return host + "/" + (company or parsed.path.strip("/").lower())
    if host in SHARED_PORTAL_HOSTS:
        company = re.sub(r"\s+", " ", str(get("company", "") or "")).strip().casefold()
        return host + "/" + company if company else host
    parts = parsed.path.strip("/").split("/")
    ats = get("ats", "custom")
    if ats in {"greenhouse", "lever", "ashby", "smartrecruiters", "rippling"} and parts[0] and detect(get("url", "")).id == ats:
        # Only the platform's own address names its employer in the path. A run that finds Greenhouse behind a career site
        # saves "greenhouse" on the job (persist_observed_ats), and its partition must not move from the site's host to
        # "careers.example.com/jobs" while the application runs: a second job there would then start beside it.
        return host + "/" + parts[0].lower()
    if ats == "workday" and host.endswith(".myworkdaysite.com") and "recruiting" in parts:
        index = parts.index("recruiting")
        return host + "/" + "/".join(parts[index : index + 3]).lower()
    if host == "jobs.dayforcehcm.com":
        from .features.knowledge.employers import dayforce_tenant

        if tenant := dayforce_tenant([p for p in parts if p]):
            return host + "/" + tenant  # one host serves every Dayforce employer
    return host


@dataclass(frozen=True)
class Capacity:
    running: int
    queued_partitions: frozenset[str]
    running_partitions: frozenset[str]
    limit: int

    @property
    def occupied(self):
        return self.running + len(self.queued_partitions - self.running_partitions)

    @property
    def available(self):
        return max(0, self.limit - self.occupied)

    @property
    def employers(self):
        return self.running_partitions | self.queued_partitions


def active_runs(session, states=("queued", "running", "submitting"), ats=None):
    """(run, job) pairs for runs in `states`, with only what capacity and pacing decisions read.

    Whole Run and Job rows carried ~320 KB packets and full descriptions; every snapshot, autopilot tick and queue
    click decoded them for each queued and running application, under the app lock (2026-10-22). The packet keeps the
    three keys these checks read (worker pool and slot, verification-only), absent when the run has none.
    """
    query = (
        select(
            Run.id,
            Run.job_id,
            Run.state,
            Run.mode,
            Run.created_at,
            Run.started_at,
            Run.packet["worker_pool"].as_string(),
            Run.packet["worker_slot"].as_integer(),
            Run.packet["verification_only"].as_boolean(),
            Job.url,
            Job.ats,
            Job.company,
            Job.demo,
        )
        .join(Job, Job.id == Run.job_id)
        .where(Run.state.in_(list(states)))
    )
    if ats is not None:
        query = query.where(Job.ats == ats)
    rows = []
    for (
        rid,
        job_id,
        state,
        mode,
        created,
        started,
        pool,
        slot,
        verification,
        url,
        ats_,
        company,
        demo,
    ) in session.execute(query):
        packet = {
            key: value
            for key, value in (
                ("worker_pool", pool),
                ("worker_slot", slot),
                ("verification_only", verification),
            )
            if value is not None
        }
        run = SimpleNamespace(
            id=rid,
            job_id=job_id,
            state=state,
            mode=mode,
            created_at=created,
            started_at=started,
            packet=packet,
        )
        rows.append((run, SimpleNamespace(id=job_id, url=url, ats=ats_, company=company, demo=demo)))
    return rows


def capacity(session, workers=8, rows=None):
    rows = active_runs(session) if rows is None else rows
    running = [(r, j) for r, j in rows if r.state in {"running", "submitting"}]
    return Capacity(
        running=len(running),
        running_partitions=frozenset(employer_partition(j) for _, j in running),
        queued_partitions=frozenset(employer_partition(j) for r, j in rows if r.state == "queued"),
        limit=min(32, workers),
    )


def temporary_time(seconds=60):
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def outcome_for(exc):
    if isinstance(exc, QueueBlocked):
        return exc.as_dict()
    # Legacy callers must not turn an unknown transport/runtime guard into permanent loss.
    text = str(exc).lower()
    if any(term in text for term in ("cooldown", "paused", "six hours", "daily", "budget", "active run")):
        return {"outcome": "deferred", "code": "temporary_guard", "not_before": temporary_time()}
    if isinstance(exc, RuntimeError):
        return {"outcome": "deferred", "code": "delivery_failure", "not_before": temporary_time()}
    return {"outcome": "excluded", "code": "queue_guard", "not_before": None}


# Off unless the installation turns it on (setting unknown_resubmit_policy): a resubmission can duplicate an
# application the site did accept.
UNKNOWN_RESUBMIT = {"enabled": False, "max_resubmits": 2, "receipt_wait_minutes": 30}


# How long a search of the mailbox for an uncertain application's acknowledgement stays good for a resubmission.
RECEIPT_CHECK_VALID = timedelta(hours=1)


def newest_unknown(runs):
    """The uncertain run a resubmission and its mailbox search are about: the one that finished last."""
    return max(
        (r for r in runs if r.state == "submission_unknown"), key=lambda r: r.finished_at or r.created_at, default=None
    )


def unknown_resubmit_allowed(session, runs, at=None, *, checked=True):
    """When the installation enables it: retry uncertain submissions until confirmed.

    Bounded so a site that silently accepts cannot collect a pile of duplicates:
    no confirmed/already-applied run, no matched receipt email, the newest
    uncertain attempt is old enough for its receipt to have arrived, at most
    `max_resubmits` retries after the first uncertain attempt, and (`checked`) the mailbox was searched for this
    application's acknowledgement after that wait (autopilot.receipt_first writes `unknown_receipt_check:<job>`).
    Asana, Assured Guaranty and Perseus were resubmitted on the matcher's silence while the employer's
    acknowledgement sat in the mailbox (2026-10-12); every caller passes through here, so none can skip the search.
    `checked=False` asks whether the search is the only thing missing.
    """
    saved = session.get(Setting, "unknown_resubmit_policy")
    policy = {**UNKNOWN_RESUBMIT, **(saved.value if saved else {})}
    unknown = [r for r in runs if r.state == "submission_unknown"]
    if not policy.get("enabled") or not unknown:
        return False
    if any(r.state in {"confirmed", "already_applied", "submitting"} for r in runs):
        return False
    if any(
        (r.receipt or {}).get("email_confirmed") or (r.receipt or {}).get("website_confirmed") for r in runs
    ):
        return False
    if any((r.receipt or {}).get("other_posting") for r in runs):
        return False  # An application reached another of the employer's postings: a second send is never the answer.
    if any((r.receipt or {}).get("type") == "possible_send" for r in runs):
        return False  # Sent with nothing armed, and no one has read the page after it (browser.settle_possible_send): only a reading clears it.
    if len(unknown) > int(policy.get("max_resubmits", 2)):
        return False
    # A post-submit screenshot that shows the employer's confirmation means the
    # application went through; a receipt or portal check confirms it, never a resubmit.
    # Of every listing among these runs: admission passes the runs of a posting's twins too.
    for job_id in {r.job_id for r in unknown if hasattr(r, "job_id")}:
        triage = session.get(Setting, "unknown_triage:" + job_id)
        if triage and triage.value.get("verdict") == "submitted":
            return False
    last = newest_unknown(runs)
    try:
        finished = datetime.fromisoformat(last.finished_at or last.created_at)
    except (TypeError, ValueError):
        return False
    now = at or datetime.now(UTC)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=UTC)
    wait = timedelta(minutes=int(policy.get("receipt_wait_minutes", 30)))
    if now - finished < wait:
        return False
    if not checked:
        return True
    search = session.get(Setting, "unknown_receipt_check:" + str(getattr(last, "job_id", "")))
    try:
        searched = datetime.fromisoformat(search.value["at"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    if searched.tzinfo is None:
        searched = searched.replace(tzinfo=UTC)
    return (
        search.value.get("run_id") == getattr(last, "id", None)
        and finished + wait <= searched
        and now - searched <= RECEIPT_CHECK_VALID
    )


def receipt_page_seen(session, runs, job_url):
    """An earlier run of this job stood on the employer's own receipt address for this posting (browser.received_application_page), whatever
    state it ended in. Humana (2026-11-02) sits in "waiting_browser": its run reopened that page, walked on to Workday's sign-in and stopped
    there, so its saved route no longer shows it and a retry would start from the posting. The record of the visit outlives the route."""
    from .browser import received_application_page

    ids = [r.id for r in runs]
    if not ids:
        return False
    return any(
        received_application_page(url, job_url)
        for url in session.scalars(
            select(Event.data["url"].as_string()).where(
                Event.run_id.in_(ids), Event.kind.in_(("browser", "step")), Event.data["url"].as_string().ilike("%thank%you%")
            )
        )
    )


# A cancelled run that sent nothing to the employer is not an attempt. Only the app's own cancellations qualify; a
# verdict on the job (the owner removing it, the SSN / government-ID rule, a closed posting, any reason not listed here)
# keeps it out of the autopilot for good. 43 Ready jobs sat idle for a week, every worker free (2026-10-31): 23 Workable
# runs cancelled while still queued to unblock a deploy (no reason, never started) and 20 drafts swept by the
# 2026-10-10 maintenance, which the owner then resumed.
APP_CANCELS = (
    "Owner cancelled saved application during maintenance",  # the 2026-10-10 sweep of saved drafts
    "Replaced by a newer attempt",  # operations.superseded: the newer run is the attempt
    "This application waits: ",  # EMPLOYER_WAIT: its employer's own limit or a loop stopped it; nothing was sent
)
EMPLOYER_WAIT = "This application waits: "
HOLD_WAIT = "; it waits for your approval"  # service.execution: queued before its employer was reserved


def attempted_job_ids(session, only=None):
    """Ids of the jobs (limited to `only` when given) that already have an attempt: any run except a cancellation by the
    app before anything was sent. A cancelled run still counts when it has a `submitting` event or a receipt (a commit
    may have reached the employer: a second application is worse than a missed one), when the owner removed the job, and
    when its reason is not one the app writes for its own cancellations."""
    scope = () if only is None else (Run.job_id.in_(list(only)),)
    attempted = set(session.scalars(select(Run.job_id).where(Run.state != "cancelled", *scope).distinct()))
    undecided = {}
    for run_id, job_id, reason, started, receipt in session.execute(
        select(Run.id, Run.job_id, Run.reason, Run.started_at, Run.receipt).where(Run.state == "cancelled", *scope)
    ):
        if job_id in attempted:
            continue
        reason = (reason or "").strip()
        own = reason.startswith(APP_CANCELS) or (not started and (not reason or reason.endswith(HOLD_WAIT)))
        if receipt or not own:
            attempted.add(job_id)
        else:
            undecided[run_id] = job_id
    # Only what is still undecided is looked up, by key and in chunks: a scan of every event or job control per tick is
    # what the readiness reads were profiled for (py-spy, 2026-10-29).
    pending = {run_id: job_id for run_id, job_id in undecided.items() if job_id not in attempted}
    ids = list(pending)
    for start in range(0, len(ids), 400):
        run_ids = ids[start : start + 400]
        sent = set(session.scalars(select(Event.run_id).where(Event.kind == "submitting", Event.run_id.in_(run_ids))))
        removed = {
            key.removeprefix("job_control:")
            for key, state in session.execute(
                select(Setting.key, Setting.value["state"].as_string()).where(
                    Setting.key.in_(["job_control:" + pending[run_id] for run_id in run_ids])
                )
            )
            if state == "deleted"
        }
        attempted.update(pending[run_id] for run_id in run_ids if run_id in sent or pending[run_id] in removed)
    return attempted
