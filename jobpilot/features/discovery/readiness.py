"""Due work, ready applications, deferrals, source health and honest status.

These return decisions only. Codex's DBOS workers execute them: discovery
fetches, classification, tailoring and — for applications — `service.queue`,
which keeps configured owner admission, final-submit reservation, budgets and the
submission guard authoritative.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from .._store import now
from .classify import eligible as classified_eligible, outcomes as classified_outcomes
from .graduation import graduation_gate
from .policy import DiscoveryPolicy, fresh, gate, guard_keys, protected
from .tables import DcDeferral, DcSourceHealth

COMMITTED = {"confirmed", "submission_unknown", "already_applied"}
ACTIVE = {"queued", "running", "submitting"}


class Capacity(BaseModel):
    owners_total: int = 7
    owners_busy: int = 0


class DueWork(BaseModel):
    sources: list[str] = Field(default_factory=list)
    classify: list[list[str]] = Field(default_factory=list)
    tailor: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ReadyApplication(BaseModel):
    job_id: str
    artifact_id: str
    resume_id: str
    score: int | None
    posted_at: str | None
    posted_basis: str


def capacity(db, owners_total=7) -> Capacity:
    from ...db import Run

    with db.session() as s:
        busy = s.scalar(select(func.count()).select_from(Run).where(Run.state.in_(["running", "submitting"])))
    return Capacity(owners_total=owners_total, owners_busy=busy or 0)


def _candidates(db, policy: DiscoveryPolicy, at, manual_only=False) -> tuple[list[dict], dict]:
    """Never-attempted, non-archived jobs that are fresh (listed) or manually imported. `manual_only` reads only
    the applicant's pasted links: the master-resume path wanted those few and read all 7,000 postings each tick."""
    from ...db import Job, JobListing, Run, Setting

    stats = {"stale_or_undated": 0, "protected": 0, "controlled": 0, "attempted": 0, "out_of_scope": 0}
    # Classification and tailoring spend only on the owner's ATS scope (ats_focus.exclude):
    # 1000 unclassified postings were mostly sites the autopilot will never apply to.
    excluded_ats = set((db.get_setting("ats_focus", {}) or {}).get("exclude", []))
    board = db.get_setting("job_board_settings", {}) or {}
    seen_cutoff = at - timedelta(hours=max(6, board.get("interval_hours", 2) * 2 + 1))
    with db.session() as s:
        excluded_ats.update(
            row.key.removeprefix("ats_outage:")
            for row in s.scalars(select(Setting).where(Setting.key.like("ats_outage:%")))
            if row.value.get("until", "") > at.isoformat()
        )
        attempted = set(s.scalars(select(Run.job_id).distinct()))
        # Only the paused/deleted controls and manual origins matter, and the database picks them out of the 17,700
        # small rows instead of this process loading all of them on every tick (readiness was 31% of the loop
        # thread's samples with the description loads below, py-spy 2026-10-29).
        controls = {
            key.removeprefix("job_control:"): state
            for key, state in s.execute(
                select(Setting.key, Setting.value["state"].as_string()).where(
                    Setting.key.like("job_control:%"), Setting.value["state"].as_string().in_(["paused", "deleted"])
                )
            )
        }
        # Only a link the applicant added (origin "manual") is a candidate without a listing. A board or feed
        # import without one never passed the board screen: 983 unscreened postings from new boards
        # (2026-10-18) would otherwise fill every classification batch with jobs that can never be dispatched.
        manual = {
            key.removeprefix("job_origin:")
            for key in s.scalars(
                select(Setting.key).where(
                    Setting.key.like("job_origin:%"), Setting.value["kind"].as_string() == "manual"
                )
            )
        }
        # Every column but the description: it is most of a 16,000-row, 100 MB read, and only the postings that
        # survive the filters below need it (fetched after them).
        columns = [c for c in Job.__table__.columns if c.name != "description"]
        query = (
            select(*columns, JobListing)
            .outerjoin(JobListing, JobListing.job_id == Job.id)
            .where(Job.demo.is_(False), Job.status.not_in(["archived", "closed"]))
        )
        rows = s.execute(query.where(Job.id.in_(manual)) if manual_only else query).all()
        guards = guard_keys(s)
        out = []
        for *values, listing in rows:
            job = SimpleNamespace(**dict(zip((c.name for c in columns), values)))
            if job.ats in excluded_ats:
                stats["out_of_scope"] += 1
                continue
            if job.id in attempted:
                stats["attempted"] += 1
                continue
            if controls.get(job.id) in {"paused", "deleted"}:
                stats["controlled"] += 1
                continue
            if protected(s, job.url, guards):
                stats["protected"] += 1
                # An earlier pipeline already applied (its submission guard survives resets). Leaving the job "ready"
                # showed 84 of 218 ready jobs on the board as applicable, and every one was refused at the queue.
                if job.status in {"new", "ready"}:
                    s.execute(
                        update(Job)
                        .where(Job.id == job.id)
                        .values(status="archived", reason="Already applied by an earlier pipeline (submission guard)")
                    )
                continue
            if listing is not None:
                ok, basis = fresh(listing.posted_at, listing.date_basis, listing.first_seen, policy, at)
                try:
                    seen = datetime.fromisoformat(listing.last_seen.replace("Z", "+00:00"))
                    seen = seen if seen.tzinfo else seen.replace(tzinfo=UTC)
                except (ValueError, TypeError, AttributeError):
                    seen = None
                if not ok or not listing.active or seen is None or seen < seen_cutoff:
                    stats["stale_or_undated"] += 1
                    continue
                posted, country = listing.posted_at, listing.country
            elif job.id in manual:
                basis, posted, country = "manual", None, ""  # a pasted link is the applicant's choice
            else:
                stats["stale_or_undated"] += 1
                continue
            out.append(
                {
                    "id": job.id,
                    "url": job.url,
                    "identity": job.identity,
                    "title": job.title,
                    "company": job.company,
                    "location": job.location,
                    "country": country,
                    "description": "",
                    "status": job.status,
                    "score": job.score,
                    "resume_id": job.resume_id,
                    "posted_at": posted,
                    "posted_basis": basis,
                }
            )
        descriptions = {}
        ids = [job["id"] for job in out]
        for start in range(0, len(ids), 500):
            descriptions.update(
                s.execute(select(Job.id, Job.description).where(Job.id.in_(ids[start : start + 500]))).all()
            )
        for job in out:
            job["description"] = descriptions.get(job["id"]) or ""
    return out, stats


def master_resume(db) -> str | None:
    """The source resume id when the application resume policy is `master` (no tailored PDF is ever built), else None.
    Readiness and status asked for tailored resumes under this policy: "Tailoring: 31 eligible jobs are waiting for a
    tailored resume" while nothing had been built since 2026-10-07 and `ready_to_apply` read 0 beside 260 ready jobs
    (2026-10-28)."""
    policy = db.get_setting("application_resume_policy", {}) or {}
    return (policy.get("resume_id") or policy.get("source_resume_id") or "") if policy.get("mode") == "master" else None


def _deferred(db, at) -> set:
    with db.session() as s:
        return set(s.scalars(select(DcDeferral.job_id).where(DcDeferral.not_before > at.isoformat())))


_GATES: dict = {}


def gated(job, policy):
    """gate(), remembered per posting and policy. Each autopilot tick (every 15 s) gated about 2,000 postings by
    scanning their full descriptions, on threads that hold the interpreter the browsers' event loop needs."""
    key = (policy.model_dump_json(), job.get("id"), job.get("title"), job.get("url"), job.get("location"),
           job.get("country"), len(job.get("description") or ""))
    if key not in _GATES:
        if len(_GATES) > 50_000:
            _GATES.clear()
        _GATES[key] = gate(job, policy)
    return _GATES[key]


def _eligible(db, jobs, profile, policy):
    from .roles import matching_role

    # Backpressure must count jobs dispatch can actually use. Old high scores
    # cannot make off-role, senior or otherwise gated jobs block new ranking.
    roles = profile.get("target_roles") or []
    jobs = [j for j in jobs if not gated(j, policy)
            and (not roles or matching_role(j["title"], roles))]
    mine = classified_eligible(db, jobs, profile, policy)
    # Jobs the application's ranker already marked ready count as eligible too,
    # unless the posting's stated graduation window excludes every recorded degree.
    for job in jobs:
        if (
            job["id"] not in mine
            and job["status"] == "ready"
            and (job["score"] or 0) >= policy.minimum_fit
            and not graduation_gate(job, profile)
        ):
            mine[job["id"]] = {"score": job["score"], "eligible": True, "resume_id": job["resume_id"]}
    return mine


def ready_manual_job_ids(db, policy=None, at=None):
    """Master-resume imports need no tailored artifact to enter dispatch.

    Use the same freshness, protection, classification and deferral checks as
    tailored applications. Queue admission still validates the selected resume.
    """
    policy, at = policy or DiscoveryPolicy(), at or datetime.now(UTC)
    jobs, _ = _candidates(db, policy, at, manual_only=True)
    deferred = _deferred(db, at)
    jobs = [j for j in jobs if j["posted_basis"] == "manual" and j["id"] not in deferred]
    good = _eligible(db, jobs, db.get_setting("profile", {}) or {}, policy)
    ready = [j for j in jobs if j["id"] in good and j["status"] == "ready" and not gated(j, policy)]
    if not ready:
        return set()
    # One query for the origins of the ready few, not a settings read per job.
    from ...db import Setting

    with db.session() as s:
        manual = {
            key.removeprefix("job_origin:")
            for key in s.scalars(
                select(Setting.key).where(
                    Setting.key.in_(["job_origin:" + j["id"] for j in ready]), Setting.value["kind"].as_string() == "manual"
                )
            )
        }
    return {j["id"] for j in ready if j["id"] in manual}


def ready_applications(
    db, policy: DiscoveryPolicy | None = None, limit: int = 6, at=None, data_dir=None, candidates=None
) -> list[ReadyApplication]:
    """Eligible, guarded, undeferred jobs with a `ready` tailored resume, best first. `candidates`: the
    `_candidates` jobs a caller already read at `at` (status() reads them once, not twice)."""
    from ..resumes import current_artifact

    policy, at = policy or DiscoveryPolicy(), at or datetime.now(UTC)
    profile = db.get_setting("profile", {}) or {}
    jobs = candidates if candidates is not None else _candidates(db, policy, at)[0]
    deferred = _deferred(db, at)
    good = _eligible(db, [j for j in jobs if j["id"] not in deferred], profile, policy)
    out = []
    master = master_resume(db)
    for job in sorted(jobs, key=lambda j: (-(good.get(j["id"], {}).get("score") or 0), j["posted_at"] or "")):
        if job["id"] not in good:
            continue
        if master is not None:
            # The master resume needs no artifact: a job the ranker marked ready applies with the source PDF.
            if job["status"] != "ready":
                continue
            artifact_id, resume_id = "", job["resume_id"] or master
        else:
            artifact = current_artifact(db, job["id"], data_dir)
            if not artifact:
                continue
            artifact_id, resume_id = artifact.artifact_id, artifact.source_resume_id
        out.append(
            ReadyApplication(
                job_id=job["id"],
                artifact_id=artifact_id,
                resume_id=resume_id,
                score=good[job["id"]].get("score"),
                posted_at=job["posted_at"],
                posted_basis=job["posted_basis"],
            )
        )
        if len(out) >= limit:
            break
    return out


def discover_due(
    db, policy: DiscoveryPolicy | None = None, cap: Capacity | None = None, at=None, data_dir=None
) -> DueWork:
    from ...db import Source
    from ..resumes import preparation_state

    policy, at = policy or DiscoveryPolicy(), at or datetime.now(UTC)
    cap = cap or capacity(db, policy.owners)
    profile = db.get_setting("profile", {}) or {}
    jobs, _ = _candidates(db, policy, at)
    deferred = _deferred(db, at)
    jobs = [j for j in jobs if j["id"] not in deferred]
    good = _eligible(db, jobs, profile, policy)
    # Current-identity readiness: a stale variant or an expired build lease counts as not prepared.
    work = DueWork()
    if master_resume(db) is not None:
        # The master resume is the application resume: nothing is tailored, so nothing is planned or waiting.
        artifacts, ready, building = {}, 0, 0
        work.reasons.append("Tailoring not needed: the resume policy is master")
    else:
        artifacts = {j: preparation_state(db, j, data_dir) for j in good}
        ready = sum(1 for j in good if artifacts.get(j) == "ready")
        building = sum(1 for j in good if artifacts.get(j) == "building")
        # Tailoring: keep a bounded number of ready resumes ahead of the owners.
        budget = policy.owners * policy.tailor_lookahead - ready - building
        waiting = [j for j in good if artifacts.get(j) in {"none", "stale"}]
        waiting.sort(key=lambda j: -(good[j].get("score") or 0))
        if budget > 0:
            work.tailor = waiting[:budget]
        elif waiting:
            work.reasons.append(
                f"Tailoring paused: {ready + building} resumes already ahead of {policy.owners} owners"
            )
    # Classification: only while the eligible pipeline is short. Jobs that
    # already have a run are not dispatched again by the autopilot (recovery,
    # protection and employer caps own them), so they do not fill the pipeline.
    from ...db import Run

    with db.session() as s:
        attempted = (
            set(s.scalars(select(Run.job_id).where(Run.job_id.in_(list(good))).distinct())) if good else set()
        )
    # Only jobs the autopilot can still start fill the pipeline. Internships it excludes and jobs its active batch
    # already skipped (employer cap, guards) counted as 37 waiting jobs while none could start; classification
    # paused and 3,137 fresh postings stayed unranked with five of six workers idle (2026-10-23).
    from .feeds import INTERN

    batch = db.get_setting("job_board_autopilot", {}) or {}
    decided = set(batch.get("considered_ids") or []) if batch.get("state") == "active" else set()
    titles = {j["id"]: j.get("title") or "" for j in jobs}
    interns_ok = bool(profile.get("include_internships"))
    pipeline = len([
        j for j in good
        if j not in attempted and j not in decided and (interns_ok or not INTERN.search(titles.get(j, "")))
    ])
    outcomes = classified_outcomes(db, jobs, profile)
    unclassified = [
        j["id"]
        for j in jobs
        if j["id"] not in good
        and (
            j["id"] not in outcomes
            or (
                outcomes[j["id"]]["status"] == "failed"
                and (outcomes[j["id"]]["retry_at"] or "") <= at.isoformat()
            )
        )
        # Exactly what the classification workflow ranks: unranked new postings. Jobs the older ranking path had
        # already scored (status ready, score 58-82, no classification row) were counted as unclassified, the
        # workflow skipped them, the count never fell, and discovery stayed paused for 41 hours (2026-10-25).
        and j["status"] == "new"
        and j.get("score") is None
        and not gated(j, policy)
    ]
    # Supply is sized to the slots, not a fixed trickle: every owner keeps `classify_lookahead` eligible jobs ahead,
    # and every FREE owner needs that many more at once. Only about a third of ranked postings are eligible, so the
    # shortfall is multiplied up to the postings that must be ranked. 3,225 fresh postings sat unclassified because
    # ~4 eligible jobs per owner was the whole target and 48 postings the whole plan (2026-10-28).
    free = max(0, cap.owners_total - cap.owners_busy)
    target = (policy.owners + free) * policy.classify_lookahead
    if pipeline < target:
        wanted = -(-(target - pipeline) * 100 // int(policy.classify_yield * 100))
        wanted = max(policy.classify_batch, min(wanted, policy.classify_batch * policy.classify_batches_per_tick))
        ids = unclassified[:wanted]
        work.classify = [
            ids[i : i + policy.classify_batch] for i in range(0, len(ids), policy.classify_batch)
        ]
    elif unclassified:
        work.reasons.append(f"Classification paused: {pipeline} eligible jobs already waiting")
    # Discovery: only while unclassified inventory is short; respect source backoff.
    if len(unclassified) < policy.owners * policy.classify_lookahead:
        with db.session() as s:
            cooling = set(
                s.scalars(
                    select(DcSourceHealth.source_id).where(DcSourceHealth.next_attempt_at > at.isoformat())
                )
            )
            sources = s.execute(
                select(Source.id, Source.next_run, Source.last_run).where(Source.enabled.is_(True))
            )
            due = []
            for sid, next_run, last_run in sources:
                if sid in cooling:
                    continue
                try:
                    scheduled = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
                    scheduled = scheduled if scheduled.tzinfo else scheduled.replace(tzinfo=UTC)
                except (ValueError, TypeError, AttributeError):
                    continue
                if scheduled <= at:
                    due.append((last_run or "", sid))
            # Source schedules can contain Eastern offsets while `at` is UTC.
            # Compare instants before bounding work; future rows must not keep
            # crowding never-scanned or older sources out of the planner.
            work.sources = [sid for _, sid in sorted(due)[:60]]
        if cooling:
            work.reasons.append(f"{len(cooling)} sources cooling down after errors")
    else:
        work.reasons.append("Discovery paused: enough unclassified inventory")
    if cap.owners_busy >= cap.owners_total:
        work.reasons.append("All application owners are busy")
    return work


def defer(db, job_id: str, reason: str, seconds: int):
    """A temporary cooldown delays a job; it is never discarded."""
    until = (datetime.now(UTC) + timedelta(seconds=max(1, seconds))).isoformat()
    with db.exclusive() as s:
        row = s.get(DcDeferral, job_id)
        if row:
            row.reason, row.not_before = reason[:200], until
        else:
            s.add(DcDeferral(job_id=job_id, reason=reason[:200], not_before=until))
    return until


def record_source(db, source_id: str, ok: bool, error: str = "", policy: DiscoveryPolicy | None = None):
    policy = policy or DiscoveryPolicy()
    at = datetime.now(UTC)
    with db.exclusive() as s:
        row = s.get(DcSourceHealth, source_id)
        if row is None:
            row = DcSourceHealth(source_id=source_id, failures=0)
            s.add(row)
        if ok:
            row.failures, row.next_attempt_at, row.last_error, row.last_ok = 0, None, "", now()
        else:
            row.failures = (row.failures or 0) + 1
            wait = min(
                policy.source_backoff_minutes * 2 ** (row.failures - 1), policy.source_backoff_max_hours * 60
            )
            row.next_attempt_at = (at + timedelta(minutes=wait)).isoformat()
            row.last_error = error[:250]
        return {"failures": row.failures, "next_attempt_at": row.next_attempt_at}


def status(db, policy: DiscoveryPolicy | None = None, cap: Capacity | None = None, at=None) -> dict:
    """Pipeline counts and the current bottleneck, stated plainly."""
    from ..resumes.tables import RsArtifact

    policy, at = policy or DiscoveryPolicy(), at or datetime.now(UTC)
    cap = cap or capacity(db, policy.owners)
    profile = db.get_setting("profile", {}) or {}
    jobs, excluded = _candidates(db, policy, at)
    outcomes = classified_outcomes(db, jobs, profile)
    good = _eligible(db, jobs, profile, policy)
    # Not named `gated`: that shadowed the gated() helper, so the Batch page's Pipeline block answered 500 whenever
    # a fresh listing had no classification gate (2026-10-22).
    gates = {}
    for j in jobs:
        reason = (outcomes.get(j["id"], {}).get("result") or {}).get("gate") or gated(j, policy)
        if reason:
            gates[reason] = gates.get(reason, 0) + 1
    with db.session() as s:
        tailoring = dict(s.execute(select(RsArtifact.status, func.count()).group_by(RsArtifact.status)).all())
    ready = len(ready_applications(db, policy, limit=10_000, at=at, candidates=jobs))
    master = master_resume(db) is not None
    with db.session() as s:
        from ...db import Job, Run

        # Every ready job nothing has attempted, listed or not (imports and listings a sweep has not re-seen).
        ready_jobs = s.scalar(
            select(func.count()).select_from(Job).where(
                Job.status == "ready", Job.demo.is_(False), Job.id.not_in(select(Run.job_id))
            )
        )
    basis = {}
    for j in jobs:
        basis[j["posted_basis"]] = basis.get(j["posted_basis"], 0) + 1
    failed_cls = sum(1 for o in outcomes.values() if o["status"] == "failed")
    unclassified = sum(1 for j in jobs if j["id"] not in outcomes and j["id"] not in good)
    free = max(0, cap.owners_total - cap.owners_busy)
    if not jobs:
        bottleneck = "No fresh, unattempted listings. Discovery continues on schedule."
    elif ready and free:
        bottleneck = (
            f"{ready} applications are ready and {free} owners are free; the executor picks them up next."
        )
    elif ready:
        bottleneck = "All application owners are busy. Ready applications wait for a free owner."
    elif good and master:
        bottleneck = (
            f"Ranking: {len(good)} classified-eligible jobs are not marked ready "
            f"(resume policy is master, so no tailored resume is needed)."
        )
    elif good:
        bottleneck = (
            f"Tailoring: {len(good)} eligible jobs are waiting for a tailored resume "
            f"({tailoring.get('building', 0)} building, {tailoring.get('failed', 0)} need repair)."
        )
    elif unclassified or failed_cls:
        bottleneck = f"Classification: {unclassified} not yet ranked, {failed_cls} waiting to retry."
    else:
        bottleneck = "No eligible jobs in current inventory. Discovery continues on schedule."
    return {
        "fresh_jobs": len(jobs),
        "posting_basis": basis,
        "excluded": excluded,
        "gated": gates,
        "classification": {
            "eligible": len(good),
            "ineligible": sum(1 for o in outcomes.values() if o["status"] in {"ok", "rejected"})
            - len([j for j in good if j in outcomes]),
            "failed": failed_cls,
            "unclassified": unclassified,
        },
        "tailoring": tailoring,
        "resume_policy": "master" if master else "tailored",
        "ready_to_apply": ready,
        "ready_jobs": ready_jobs,
        "owners": {"total": cap.owners_total, "busy": cap.owners_busy},
        "bottleneck": bottleneck,
        "policy": policy.model_dump(),
    }
