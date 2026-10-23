"""The dashboard's one-poll state: the snapshot builder and the cache that answers repeated polls.

The dashboard polls /api/snapshot every 6 s. Its ~0.2 s of work took ~10 s while ten browser workers shared this
process (2026-10-15), so polls are answered from the last result while a background thread refreshes it. Any write
through the API marks it stale: the next read rebuilds it.
"""

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from ..ats import catalog
from ..db import Budget, Job, Resume, Review, Run, Setting, Source, latest_runs, record
from ..schemas import Profile
from .pipeline import counts as pipeline_counts, live_rows


def trusted_email(receipt):
    """A trusted acknowledgement email (receipts from before sender trust count as trusted)."""
    email = receipt.get("email") or {}
    return bool(receipt.get("email_confirmed") or (email and email.get("sender_trusted", True)))


class SnapshotCache:
    def __init__(self, service):
        self.service = service
        self.body = None
        self.at = 0.0
        self.stale = True
        self.flight = threading.Lock()

    def mark_stale(self):
        self.stale = True

    def build(self) -> bytes:
        """Build once at a time; a request that waited for another build reuses its result."""
        with self.flight:
            age = time.monotonic() - self.at
            if self.body is not None and not self.stale and age < self.service.config.snapshot_cache_seconds:
                return self.body
            self.stale = False  # a write during this build marks it stale again
            body = json.dumps(
                jsonable_encoder(build_snapshot(self.service, False)),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode()
            self.body, self.at = body, time.monotonic()
            return body

    def refresh(self):
        def run():
            try:
                self.build()
            except Exception:
                self.stale = True  # the next poll builds it in the request and reports the error

        if not self.flight.locked():
            threading.Thread(target=run, name="snapshot-refresh", daemon=True).start()


# Batch bookkeeping the dashboard never reads: thousands of ids (run_ids alone was ~150 KB at 3,000 runs).
BATCH_BOOKKEEPING = ("run_ids", "considered_ids", "deferred", "skipped", "persisted")


def finished_today(session, tz):
    """Latest-day runs of real, active jobs, finished today in the installation's time zone, without packets."""
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    # A string bound one day early narrows the scan; the exact local day is decided below.
    rows = session.execute(
        select(Run.id, Run.job_id, Run.state, Run.finished_at, Run.elapsed, Run.model_calls, Run.receipt)
        .join(Job, Run.job_id == Job.id)
        .where(
            Job.demo.is_(False),
            Job.status != "archived",
            Run.finished_at.is_not(None),
            Run.finished_at >= (start - timedelta(days=1)).astimezone(UTC).date().isoformat(),
        )
    ).all()
    day = start.date()
    return [r for r in rows if datetime.fromisoformat(r.finished_at).astimezone(tz).date() == day]


def build_snapshot(service, include_sources: bool):
    """The one poll. Counts are exact SQL aggregates (pipeline.counts); `jobs` and `runs` are only the live rows
    the Batch page shows (pipeline.live_rows), never a slice of history: lists page through GET /api/jobs."""
    db, config = service.db, service.config
    tz = ZoneInfo(config.timezone)
    day = datetime.now(tz).date().isoformat()
    profile = Profile.model_validate(db.get_setting("profile", {}))
    with db.session() as s:
        live = live_rows(s)
        jobs = list({row["job"]["id"]: row["job"] for row in live}.values())
        runs = [row["run"] for row in live if row["run"]]
        counts = pipeline_counts(s)
        job_controls = {
            row.key.removeprefix("job_control:"): row.value
            for row in s.scalars(select(Setting).where(Setting.key.like("job_control:%")))
        }
        resumes = [
            dict(x._mapping)
            for x in s.execute(
                select(
                    *(
                        column
                        for column in Resume.__table__.columns
                        if column.name not in {"text", "filename"}
                    )
                ).where(Resume.demo.is_(False))
            )
        ]
        asked = [
            record(x)
            for x in s.scalars(
                select(Review)
                .join(Run, Review.run_id == Run.id)
                .join(Job, Run.job_id == Job.id)
                .where(
                    Review.answer.is_(None),
                    Job.status != "archived",
                    Run.state.in_(["waiting_answer", "needs_review", "running"]),
                )
                .order_by(Review.created_at)
                .limit(100)
            )
        ]
        # Only questions of each job's newest run are open; an older attempt's question was superseded.
        current = set()
        if asked:
            question_runs = {q["run_id"] for q in asked}
            newest = latest_runs(Run.job_id.in_(select(Run.job_id).where(Run.id.in_(question_runs))))
            current = set(s.scalars(select(newest.c.id).where(newest.c.id.in_(question_runs))))
        reviews = [q for q in asked if q["run_id"] in current]
        today = finished_today(s, tz)
        confirmed_total = s.scalar(
            select(func.count(func.distinct(Run.job_id)))
            .join(Job, Run.job_id == Job.id)
            .where(
                Run.state == "confirmed",
                Job.demo.is_(False),
                Run.receipt["evidence_reconciliation"].as_string().is_distinct_from("pending"),
            )
        )
        budget = s.get(Budget, day)
        spend, submissions = (budget.spent, budget.submissions) if budget else (0, 0)
        sources = [record(x) for x in s.scalars(select(Source))] if include_sources else []
    confirmed = [
        r
        for r in today
        if r.state == "confirmed" and (r.receipt or {}).get("evidence_reconciliation") != "pending"
    ]
    with db.session() as s:
        interventions = s.scalar(select(func.count()).select_from(Review).where(Review.created_at >= day))
    from ..autopilot import batch_snapshot

    runtime = batch_snapshot(service)
    # Jobs › Discovered's count, the same number its page shows (fresh listings not applied to yet).
    counts = {**counts, "discovered": runtime["inventory"]["discovered"]}
    return {
        "jobs": jobs,
        "job_controls": job_controls,
        "runs": runs,
        "resumes": resumes,
        "reviews": reviews,
        "sources": sources,
        # Exact counts over every job (pipeline.counts): groups for the Jobs tab and Pipeline chips, latest-run
        # states for the Batch page, the number of jobs.
        "counts": counts,
        # Server-owned answer stores are never shown here and PUT /api/profile restores them;
        # leaving them out keeps the 6-second poll light (up to ~250 KB, 2026-10-15).
        "profile": profile.model_dump(exclude={"reviewed_answers", "approved_answers", "forgotten_answers"}),
        "control": db.get_setting("control"),
        "application_resume_policy": db.get_setting("application_resume_policy", {"mode": "tailored"}),
        "telegram_health": db.get_setting("telegram_health", {}),
        "telegram_workers": __import__("jobpilot.telegram_workers", fromlist=["worker_status"]).worker_status(
            service
        ),
        "autopilot": {
            k: v for k, v in db.get_setting("job_board_autopilot", {}).items() if k not in BATCH_BOOKKEEPING
        },
        "autopilot_runtime": runtime,
        "discovery_settings": db.get_setting("job_board_settings", {}),
        "scheduler_health": db.get_setting("scheduler_health", {}),
        "scheduler_heartbeat": db.get_setting("scheduler_heartbeat", {}),
        "engine_version": __import__("jobpilot.runtime", fromlist=["engine_version"]).engine_version(),
        "ats": catalog(),
        "stats": {
            # Jobs confirmed today, over every run (it was counted from the newest 1,000 runs only).
            "confirmed_today": len({r.job_id for r in confirmed}),
            "confirmed_total": confirmed_total,
            "submissions_reserved": submissions,
            "spend_today": spend,
            # Each job's latest run, by state, over every job.
            "states": counts["states"],
            "average_seconds": round(sum(r.elapsed for r in confirmed) / len(confirmed)) if confirmed else 0,
            # What the handoff asked to measure: model calls, interventions and receipts.
            "model_calls_per_run": round(sum(r.model_calls for r in today) / len(today), 1) if today else 0,
            "interventions_today": (interventions or 0)
            + sum(r.state in {"waiting_browser", "waiting_answer"} for r in today),
            "website_receipts_today": sum(
                bool((r.receipt or {}).get("website_confirmed")) for r in confirmed
            ),
            "email_receipts_today": sum(trusted_email(r.receipt or {}) for r in confirmed),
        },
        "config": {
            "workers": config.workers,
            "timeout": config.application_timeout,
            "daily_limit": config.daily_application_limit,
            "budget": config.daily_budget_usd,
            "timezone": config.timezone,
            "model": config.llm_model,
            "model_provider": config.llm_provider,
            "demo": config.enable_demo,
            "workers_enabled": config.enable_workers,
            "mimo": bool(config.llm_api_key),
            "telegram": bool(config.telegram_bot_token and config.telegram_user_id),
            "capsolver": False,  # Retained response key for older dashboards; runtime uses 2Captcha.
            "engine": config.engine,
            "multipage_timeout": config.multipage_timeout,
            "account": bool(config.application_password),
            "account_email": config.account_email or profile.email,
            "imap": bool(config.gmail_address and config.gmail_app_password),
            "twocaptcha": bool(config.twocaptcha_api_key),
            "telegram_wait": config.telegram_wait_seconds,
        },
    }
