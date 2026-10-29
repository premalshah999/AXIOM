"""Batched classification with a version-keyed cache and job-level outcomes.

A batch that fails as a whole is bisected so one malformed posting or model
omission cannot discard the other results; jobs that still fail get a retry
time (a deferral), never a silent drop. Hard gates run first and cost no model
calls.
"""

from datetime import UTC, datetime, timedelta
from typing import Awaitable, Callable

from sqlalchemy import select

from .._store import digest, now
from .graduation import graduation_gate
from .policy import POLICY_VERSION, DiscoveryPolicy, gate
from .tables import DcClassification

# classify_fn(job_ids) -> {job_id: {"score": int, "eligible": bool, "reason": str, "resume_id": str|None}}
ClassifyFn = Callable[[list[str]], Awaitable[dict]]
RANKING_FIELDS = (
    "skills",
    "work",
    "education",
    "target_roles",
    "accept_experience_years",
    "target_locations",
    "minimum_fit",
    "excluded_keywords",
    "facts",
    "autonomous",
)


def profile_version(profile: dict) -> str:
    return digest({k: profile.get(k) for k in RANKING_FIELDS})[:16]


def cache_key(job: dict, pversion: str) -> str:
    return digest(
        [
            job.get("identity") or job["url"],
            digest(job.get("description") or "")[:16],
            pversion,
            POLICY_VERSION,
        ]
    )[:32]


def existing_rank_adapter(db, config) -> ClassifyFn:
    """Uses the application's batch ranker (which also writes Job.status/score)."""
    import asyncio

    from ...discovery import rank_batch_in_thread

    async def run(job_ids):
        # Off the shared loop: a batch reads every resume, fetches postings and writes 24 rows synchronously.
        rows = await asyncio.to_thread(rank_batch_in_thread, db, config, job_ids)
        return {r["job_id"]: r for r in rows}

    return run


async def _bisect(fn: ClassifyFn, ids: list[str], depth=0):
    try:
        out = await fn(ids)
        missing = [i for i in ids if i not in out]
        errors = {i: "model omitted this job" for i in missing}
        return {i: out[i] for i in ids if i in out}, errors
    except Exception as exc:
        if len(ids) == 1 or depth >= 5:
            return {}, {i: f"{type(exc).__name__}: {str(exc)[:160]}" for i in ids}
        mid = len(ids) // 2
        a, ea = await _bisect(fn, ids[:mid], depth + 1)
        b, eb = await _bisect(fn, ids[mid:], depth + 1)
        return {**a, **b}, {**ea, **eb}


async def classify(db, jobs: list[dict], fn: ClassifyFn, policy: DiscoveryPolicy, profile: dict) -> dict:
    """Classify jobs (dicts with id, url, identity, title, location, description) in bounded batches."""
    pversion = profile_version(profile)
    at = datetime.now(UTC)
    report = {"cached": 0, "gated": 0, "classified": 0, "failed": 0, "deferred": 0, "model_batches": 0}
    todo = []
    with db.session() as s:
        for job in jobs:
            row = s.get(DcClassification, cache_key(job, pversion))
            if row and (row.status in {"ok", "rejected"} or (row.retry_at and row.retry_at > at.isoformat())):
                report["cached" if row.status != "failed" else "deferred"] += 1
                continue
            todo.append(job)
    gated, rankable = [], []
    for job in todo:
        # Deterministic gates first: posting shape, then the posting's stated graduation window.
        reason = gate(job, policy) or graduation_gate(job, profile, at)
        (gated if reason else rankable).append((job, reason))
    results, errors = {}, {}
    for i in range(0, len(rankable), policy.classify_batch):
        batch = [job["id"] for job, _ in rankable[i : i + policy.classify_batch]]
        report["model_batches"] += 1
        ok, bad = await _bisect(fn, batch)
        # A posting that cannot be fetched right now comes back flagged: it is retried later with its own reason,
        # not recorded as "model omitted this job" (the real blocker was hidden).
        for jid in [j for j, r in ok.items() if r.get("deferred")]:
            bad[jid] = ok.pop(jid)["deferred"]
        results.update(ok)
        errors.update(bad)
    with db.exclusive() as s:

        def upsert(job, **fields):
            key = cache_key(job, pversion)
            row = s.get(DcClassification, key)
            if row is None:
                row = DcClassification(id=key, job_id=job["id"], status=fields["status"])
                s.add(row)
            for k, v in fields.items():
                setattr(row, k, v)
            row.created_at = now()
            return row

        for job, reason in gated:
            upsert(
                job,
                status="rejected",
                result={"eligible": False, "score": 0, "reason": reason, "gate": reason},
            )
            report["gated"] += 1
        for job, _ in rankable:
            if job["id"] in results:
                r = results[job["id"]]
                upsert(
                    job,
                    status="ok",
                    error="",
                    retry_at=None,
                    result={
                        k: r.get(k) for k in ("score", "eligible", "reason", "resume_id", "uncertainties")
                    },
                )
                report["classified"] += 1
            else:
                prev = s.get(DcClassification, cache_key(job, pversion))
                attempts = (prev.attempts if prev else 0) + 1
                wait = timedelta(minutes=policy.classify_retry_minutes * min(8, 2 ** (attempts - 1)))
                upsert(
                    job,
                    status="failed",
                    error=errors.get(job["id"], "unknown")[:250],
                    retry_at=(at + wait).isoformat(),
                    attempts=attempts,
                )
                report["failed"] += 1
    return report


def _rows(db, jobs: list[dict], pversion: str) -> dict:
    """job_id -> its current-version classification row, read a thousand keys per query.

    One primary-key read per posting was ~2,700 queries per call, made by every autopilot tick and the Batch page's
    readiness block (2026-10-22)."""
    keys = {job["id"]: cache_key(job, pversion) for job in jobs}
    wanted = list(dict.fromkeys(keys.values()))
    found = {}
    with db.session() as s:
        for start in range(0, len(wanted), 1000):
            query = select(DcClassification).where(DcClassification.id.in_(wanted[start : start + 1000]))
            found.update((row.id, row) for row in s.scalars(query))
    return {job_id: found[key] for job_id, key in keys.items() if key in found}


def eligible(db, jobs: list[dict], profile: dict, policy: DiscoveryPolicy) -> dict:
    """job_id -> outcome, only for the current description/profile/policy version."""
    rows = _rows(db, jobs, profile_version(profile))
    out = {}
    for job in jobs:
        row = rows.get(job["id"])
        r = (row.result or {}) if row and row.status == "ok" else {}
        # Rankings made before the graduation check still respect it, without a re-rank.
        if (
            r.get("eligible")
            and (r.get("score") or 0) >= policy.minimum_fit
            and not graduation_gate(job, profile)
        ):
            out[job["id"]] = r
    return out


def outcomes(db, jobs: list[dict], profile: dict) -> dict:
    """job_id -> current-version classification row summary (any status)."""
    return {
        job_id: {"status": row.status, "result": row.result or {}, "retry_at": row.retry_at, "error": row.error}
        for job_id, row in _rows(db, jobs, profile_version(profile)).items()
    }
