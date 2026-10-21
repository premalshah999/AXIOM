"""The dashboard's one-poll state: the snapshot builder and the cache that answers repeated polls.

The dashboard polls /api/snapshot every 6 s. Its ~0.2 s of work took ~10 s while ten browser workers shared this
process (2026-10-15), so polls are answered from the last result while a background thread refreshes it. Any write
through the API marks it stale: the next read rebuilds it.
"""

import json
import threading
import time
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from ..ats import catalog
from ..db import Budget, Job, Resume, Review, Run, Setting, Source, record
from ..schemas import Profile


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


def build_snapshot(service, include_sources: bool):
    db, config = service.db, service.config
    day = datetime.now(ZoneInfo(config.timezone)).date().isoformat()
    profile = Profile.model_validate(db.get_setting("profile", {}))
    with db.session() as s:
        jobs = [
            dict(x._mapping)
            for x in s.execute(
                select(*(column for column in Job.__table__.columns if column.name != "description"))
                .where(Job.status != "archived")
                .order_by(Job.created_at.desc())
                .limit(1000)
            )
        ]
        runs = [
            dict(x._mapping)
            for x in s.execute(
                select(
                    *(column for column in Run.__table__.columns if column.name != "packet"),
                    Run.packet["worker_slot"].as_integer().label("worker_slot"),
                    Run.packet["application_pipeline"].as_string().label("application_pipeline"),
                    Run.packet["controller_policy"].as_string().label("controller_policy"),
                    Run.packet["worker_pool"].as_string().label("worker_pool"),
                    Run.packet["ats_specialist"].as_string().label("ats_specialist"),
                )
                .join(Job, Run.job_id == Job.id)
                .where(Job.status != "archived")
                .order_by(Run.created_at.desc())
                .limit(1000)
            )
        ]
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
        reviews = [
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
        latest = {}
        for run in runs:
            latest.setdefault(run["job_id"], run["id"])
        reviews = [q for q in reviews if q["run_id"] in latest.values()]
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
    real_ids = {j["id"] for j in jobs if not j["demo"]}
    real = [r for r in runs if r["job_id"] in real_ids]
    today = [
        r
        for r in real
        if r["finished_at"]
        and datetime.fromisoformat(r["finished_at"]).astimezone(ZoneInfo(config.timezone)).date().isoformat()
        == day
    ]
    confirmed = [
        r
        for r in today
        if r["state"] == "confirmed" and r["receipt"].get("evidence_reconciliation") != "pending"
    ]
    with db.session() as s:
        interventions = s.scalar(select(func.count()).select_from(Review).where(Review.created_at >= day))
    return {
        "jobs": jobs,
        "job_controls": job_controls,
        "runs": runs,
        "resumes": resumes,
        "reviews": reviews,
        "sources": sources,
        # Server-owned answer stores are never shown here and PUT /api/profile restores them;
        # leaving them out keeps the 6-second poll light (up to ~250 KB, 2026-10-15).
        "profile": profile.model_dump(exclude={"reviewed_answers", "approved_answers", "forgotten_answers"}),
        "control": db.get_setting("control"),
        "application_resume_policy": db.get_setting("application_resume_policy", {"mode": "tailored"}),
        "telegram_health": db.get_setting("telegram_health", {}),
        "telegram_workers": __import__("jobpilot.telegram_workers", fromlist=["worker_status"]).worker_status(
            service
        ),
        # The batch's run_ids (~150 KB at 3,000 runs) are server bookkeeping the dashboard never reads.
        "autopilot": {k: v for k, v in db.get_setting("job_board_autopilot", {}).items() if k != "run_ids"},
        "autopilot_runtime": __import__("jobpilot.autopilot", fromlist=["batch_snapshot"]).batch_snapshot(
            service
        ),
        "discovery_settings": db.get_setting("job_board_settings", {}),
        "scheduler_health": db.get_setting("scheduler_health", {}),
        "scheduler_heartbeat": db.get_setting("scheduler_heartbeat", {}),
        "engine_version": __import__("jobpilot.runtime", fromlist=["engine_version"]).engine_version(),
        "ats": catalog(),
        "stats": {
            "confirmed_today": len(confirmed),
            "confirmed_total": confirmed_total,
            "submissions_reserved": submissions,
            "spend_today": spend,
            "states": dict(Counter(r["state"] for r in real)),
            "average_seconds": round(sum(r["elapsed"] for r in confirmed) / len(confirmed))
            if confirmed
            else 0,
            # What the handoff asked to measure: model calls, interventions and receipts.
            "model_calls_per_run": round(sum(r["model_calls"] for r in today) / len(today), 1)
            if today
            else 0,
            "interventions_today": (interventions or 0)
            + sum(r["state"] in {"waiting_browser", "waiting_answer"} for r in today),
            "website_receipts_today": sum(bool(r["receipt"].get("website_confirmed")) for r in confirmed),
            "email_receipts_today": sum(trusted_email(r["receipt"]) for r in confirmed),
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
