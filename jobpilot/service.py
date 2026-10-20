import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json
import re
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select

from .db import Budget, Event, Job, MailChallenge, Resume, Review, Run, Setting, now, record
from .knowledge import learned_answer
from .schemas import Profile
from .admission import QueueBlocked, employer_partition, outcome_for, temporary_time, require_intake
from .runtime import engine_version, profile_version
from .run_trace import phase_timing

ACTIVE = {"queued", "running", "submitting"}
RETRYABLE = {"needs_review", "timed_out", "failed", "waiting_browser"}
TERMINAL = {
    "paused",
    "confirmed",
    "dry_run_passed",
    "needs_review",
    "timed_out",
    "submission_unknown",
    "failed",
    "cancelled",
    "already_applied",
    "closed",
    "skipped",
    "waiting_answer",
    "waiting_browser",
}


class Service:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.dispatch = None
        self.notification_tasks = set()
        from .inbox import Inbox
        from .mail import MailService

        self.mail = MailService(db, config)
        self.inbox = Inbox(self)
        self.migrate_reviewed_answers()
        self.migrate_legacy()
        self.relabel_custom_ats()
        from .features._store import create_tables

        create_tables(db.engine)

    @staticmethod
    def cooldown_key(job):
        employer = employer_partition(job)
        return "submission_cooldown:" + hashlib.sha256(employer.encode()).hexdigest()

    def check_cooldown(self, session, job):
        outage = session.get(Setting, "ats_outage:" + job.ats)
        if outage and datetime.fromisoformat(outage.value["until"]) > datetime.now(UTC):
            raise QueueBlocked(
                outage.value.get("message")
                or "The ATS is in planned maintenance; the saved draft will resume afterwards.",
                outcome="deferred",
                code=outage.value.get("code") or "ats_maintenance",
                not_before=outage.value["until"],
            )
        item = session.get(Setting, self.cooldown_key(job))
        if item and datetime.fromisoformat(item.value["until"]) > datetime.now(UTC):
            raise QueueBlocked(
                "This employer rejected an application as possible spam. Applications there are paused for six hours; other employers can continue.",
                outcome="deferred",
                code="employer_cooldown",
                not_before=item.value["until"],
            )
        key = "site_circuit:" + hashlib.sha256(employer_partition(job).encode()).hexdigest()
        circuit = session.get(Setting, key)
        if circuit and datetime.fromisoformat(circuit.value["until"]) > datetime.now(UTC):
            raise QueueBlocked(
                "This employer site repeated the same temporary failure. Other employers can continue.",
                outcome="deferred",
                code="site_circuit",
                not_before=circuit.value["until"],
            )

    def relabel_custom_ats(self):
        """Jobs saved as a custom career site before their site was recognized (SuccessFactors
        Career Site Builder on employer domains, 2026-10-15) take the ATS their URL is known to use."""
        from .ats import detect

        with self.db.session() as s:
            for job in s.scalars(select(Job).where(Job.ats == "custom")):
                if (ats := detect(job.url).id) != "custom":
                    job.ats = ats

    def migrate_reviewed_answers(self):
        """Give existing applicant reviews provenance without changing their answers.

        Runs once per database: afterwards the reviewed answers are the store, and answers you
        corrected or forgot must not come back from old review rows."""
        with self.db.exclusive() as s:
            item = s.get(Setting, "profile")
            if not item or s.get(Setting, "migrated:reviewed_answers"):
                return
            value = dict(item.value)
            learned = {a["id"]: a for a in value.get("reviewed_answers", [])}
            forgotten = set(value.get("forgotten_answers", []))
            for review, job in s.execute(
                select(Review, Job)
                .join(Run, Review.run_id == Run.id)
                .join(Job, Run.job_id == Job.id)
                .where(Review.answer.is_not(None))
            ):
                # Only migrate actual answers which are still in the approved answer store.
                if (
                    review.id not in learned
                    and review.id not in forgotten
                    and review.key not in {"manual", "session"}
                    and value.get("approved_answers", {}).get(review.key) == review.answer
                ):
                    learned[review.id] = learned_answer(
                        review.id, review.question, review.answer, review.options, job.company or job.url
                    )
            value["reviewed_answers"] = list(learned.values())
            item.value = value
            s.add(Setting(key="migrated:reviewed_answers", value={"at": now()}))

    def audit_legacy_confirmation_evidence(self):
        """Keep duplicate guards while reconciling acknowledgements from the old detector."""
        with self.db.exclusive() as session:
            if session.get(Setting, "migrated:confirmation_evidence_v2"):
                return
            pending = 0
            for row in session.scalars(
                select(Run)
                .join(Job, Job.id == Run.job_id)
                .where(Run.state == "confirmed", Job.demo.is_(False))
            ):
                receipt = dict(row.receipt)
                email = receipt.get("email", {})
                trusted_email = (
                    isinstance(email, dict) and email.get("message_id") and email.get("sender_trusted", True)
                )
                current_dom = (
                    receipt.get("evidence_version") == 2
                    and receipt.get("evidence_selector")
                    and receipt.get("status_region")
                )
                if trusted_email or current_dom or receipt.get("type") == "user_reconciled":
                    receipt["evidence_reconciliation"] = "verified"
                else:
                    receipt["evidence_reconciliation"] = "pending"
                    pending += 1
                row.receipt = receipt
            session.add(
                Setting(key="migrated:confirmation_evidence_v2", value={"at": now(), "pending": pending})
            )

    def migrate_legacy(self):
        """One-way, idempotent import of data written by the earlier adapter branch.

        Its `knowledge` table becomes reviewed answers with provenance; its `accounts` table
        becomes encrypted employer account records, but only where the password it recorded is
        provably the configured one (fingerprint match). Legacy tables are left untouched."""
        from sqlalchemy import inspect, text

        from .knowledge import save

        tables = set(inspect(self.db.engine).get_table_names())
        if "knowledge" in tables and not self.db.get_setting("migrated:knowledge"):
            with self.db.engine.connect() as c:
                rows = list(c.execute(text("SELECT * FROM knowledge")).mappings())
            for row in rows:
                if not row.get("question") or not row.get("answer"):
                    continue
                scope = row.get("scope") or "global"
                employer = "" if scope == "global" else scope.removeprefix("employer:")
                options = row.get("options") or []
                if isinstance(options, str):
                    options = json.loads(options or "[]")
                try:
                    save(
                        self.db,
                        row["question"],
                        row["answer"],
                        options,
                        employer,
                        "employer" if employer else None,
                        review_id="legacy-" + str(row["id"])[:24],
                    )
                except (ValueError, KeyError, TypeError):
                    continue
            self.db.set_setting("migrated:knowledge", {"rows": len(rows), "at": now()})
        if "accounts" in tables and not self.db.get_setting("migrated:accounts"):
            import hashlib as _hashlib
            import hmac

            from .accounts import AccountStore

            password = self.config.application_password
            fingerprint = (
                hmac.new(self.config.app_token.encode(), password.encode(), _hashlib.sha256).hexdigest()[:16]
                if password and self.config.app_token
                else ""
            )
            with self.db.engine.connect() as c:
                rows = list(c.execute(text("SELECT * FROM accounts")).mappings())
            moved = 0
            for row in rows:
                host = (row.get("realm") or "").split(":")[1] if ":" in (row.get("realm") or "") else ""
                if (
                    not host
                    or not row.get("email")
                    or not fingerprint
                    or row.get("password_hash") != fingerprint
                ):
                    continue
                store = AccountStore(self, row["email"])
                url = "https://" + host
                if store.get(url):
                    continue  # Never overwrite a record main already keeps for this employer.
                state = {
                    "active": "authenticated",
                    "verified": "authenticated",
                    "reset": "password_reset",
                }.get(row.get("state"), "created_locally")
                store.save(url, {"email": row["email"].lower(), "password": password}, state, migrated=True)
                moved += 1
            self.db.set_setting("migrated:accounts", {"rows": len(rows), "moved": moved, "at": now()})

    async def queue(
        self,
        job_id,
        mode="dry_run",
        resume_id=None,
        retry=False,
        batch_id=None,
        recovery_source_run=None,
        controller_policy=None,
        resume_artifact_id=None,
    ):
        try:
            return await self._queue(
                job_id,
                mode,
                resume_id,
                retry,
                batch_id,
                recovery_source_run,
                controller_policy,
                resume_artifact_id,
            )
        except QueueBlocked as exc:
            if exc.code == "tailoring_pending":
                from .outbox import enqueue

                with self.db.exclusive() as session:
                    job = session.get(Job, job_id)
                    key = job_id + ":" + profile_version(self.db.get_setting("profile", {}))
                    key += ":" + hashlib.sha256((job.description or "").encode()).hexdigest()[:16]
                    enqueue(session, "tailor", key, {"job_id": job_id})
                    request = session.get(Setting, "preparation_request:" + job_id)
                    value = {
                        "state": "pending",
                        "request_id": key,
                        "job_id": job_id,
                        "at": now(),
                        "mode": mode,
                        "resume_id": resume_id,
                        "retry": retry,
                        "batch_id": batch_id,
                        "controller_policy": controller_policy,
                    }
                    if request:
                        request.value = value
                    else:
                        session.add(Setting(key="preparation_request:" + job_id, value=value))
            raise

    async def _queue(
        self,
        job_id,
        mode="dry_run",
        resume_id=None,
        retry=False,
        batch_id=None,
        recovery_source_run=None,
        controller_policy=None,
        resume_artifact_id=None,
    ):
        from .controller_policy import validate_policy
        from .tiers import job_tier

        if controller_policy is not None:
            validate_policy(controller_policy)
        from .provider_health import dispatch_limit

        if not dispatch_limit(self.db, self.config):
            raise QueueBlocked(
                "The selected model is cooling down after failed requests; saved work is retained",
                code="provider_unavailable",
                outcome="deferred",
                not_before=(datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            )
        profile = Profile.model_validate(self.db.get_setting("profile", {}))
        with self.db.exclusive() as s:
            job = s.get(Job, job_id)
            if not job:
                raise QueueBlocked("Job not found", code="not_found")
            from .job_control import require_running

            require_running(s, job_id)
            if mode == "submit" and not job.demo:
                from .features.discovery.roles import matching_role
                from .features.discovery.policy import SENIOR
                from .features.discovery.feeds import INTERN

                if profile.target_roles and not matching_role(job.title, profile.target_roles):
                    raise QueueBlocked("Outside the profile's target roles", code="target_role")
                if INTERN.search(job.title):
                    raise QueueBlocked("Internships are excluded from application discovery", code="internship")
                board = self.db.get_setting("job_board_settings", {}) or {}
                if board.get("early_career_only", True) and SENIOR.search(job.title):
                    raise QueueBlocked("Outside the saved career-level policy", code="seniority")
            from .pipeline_comparison import routed_pool
            from .specialist import pool_boost
            from .tiers import job_over_budget

            # A job already routed to a specialist has the larger token cap, whichever run it continues from.
            routed = routed_pool(s, job, recovery_source_run)
            if mode == "submit" and job_over_budget(s, self.db, job_id, pool_boost(self.config, routed)):
                raise QueueBlocked(
                    "This job already used its model budget for today; it will not be retried until tomorrow",
                    code="job_budget",
                )
            if job.status == "archived":
                raise QueueBlocked(
                    "This application is archived and is outside the active pipeline", code="archived"
                )
            from .ats import canonicalize, icims_requisition_alias

            identity = canonicalize(job.url)[1]
            from .operations.reset import guard_key

            if not job.demo and s.get(Setting, guard_key(identity)):
                raise QueueBlocked(
                    "An archived application protects this job from duplicate submission.",
                    outcome="protected",
                    code="archived_submission",
                )
            aliases = [
                candidate.id
                for candidate in s.scalars(select(Job).where(Job.ats == job.ats))
                if canonicalize(candidate.url)[1] == identity or icims_requisition_alias(job, candidate)
            ]
            previous = list(s.scalars(select(Run).where(Run.job_id.in_(aliases))))
            clearance = s.get(Setting, "failed_application_clearance_gate")
            if not job.demo and clearance and clearance.value.get("enabled"):
                allowed = set(clearance.value.get("job_ids", []))
                if not allowed.intersection(aliases):
                    raise QueueBlocked(
                        "New applications are held until the existing failed applications are cleared",
                        outcome="deferred",
                        code="clearance_gate",
                        not_before=temporary_time(),
                    )
            if any(x.state in ACTIVE for x in previous):
                raise QueueBlocked(
                    "This job already has an active run", outcome="protected", code="active_run"
                )
            from .admission import unknown_resubmit_allowed

            if any(x.state in {"confirmed", "already_applied"} for x in previous) or (
                any(x.state == "submission_unknown" for x in previous)
                and not unknown_resubmit_allowed(s, previous)
            ):
                raise QueueBlocked(
                    "This job is submitted or has an uncertain submission. Reconcile it first.",
                    outcome="protected",
                    code="submitted_or_uncertain",
                )
            require_intake(s, job_id)
            if mode == "submit":
                from .application_budget import require_allowance

                require_allowance(s, job_id, aliases=aliases)
                from .application_pacing import require_company_limit, require_ats_admission

                require_company_limit(s, job)
                require_ats_admission(s, job)
                if not self.mail.shared_sender_free(s, job):
                    raise QueueBlocked(
                        "Another application is verifying with this platform's email sender; this one waits its turn",
                        outcome="deferred",
                        code="sender_busy",
                        not_before=(datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
                    )
            from .eligibility import require_eligibility

            require_eligibility(s, job, profile, self.config.data_dir)
            self.check_cooldown(s, job)
            recovery_row = None
            if recovery_source_run:
                recovery_row = s.get(Setting, "draft_recovery:" + job_id)
                source = s.get(Run, recovery_source_run)
                control = s.get(Setting, "control").value
                from .pipeline_comparison import SPECIALIST, assign_pool
                from .recovery import specialist_attempts

                specialist = source and assign_pool(s, job, source) == SPECIALIST
                exhausted = (
                    specialist_attempts(s, job_id) >= self.config.specialist_attempts
                    if specialist
                    else recovery_row is not None
                    and recovery_row.value.get("attempts", 0)
                    >= recovery_row.value.get("attempt_limit", self.config.recovery_max_attempts)
                )
                if (
                    not retry
                    or mode != "submit"
                    or control.get("paused")
                    or not profile.autonomous
                    or not recovery_row
                    or not recovery_row.value.get("scheduled")
                    or recovery_row.value.get("source_run_id") != recovery_source_run
                    or recovery_row.value.get("not_before", "") > now()
                    or (
                        # Lease waits sent nothing, so a carried-over repair count must not strand them.
                        recovery_row.value.get("cause")
                        not in {"email_matched", "sender_busy", "browser_unavailable", "provider_unavailable", "worker_interrupted"}
                        and exhausted
                    )
                    or not source
                    or source.job_id != job_id
                    or source.state not in {"waiting_browser", "timed_out"}
                    or not source.packet.get("match_approved")
                    or any(r.id != source.id and r.created_at >= source.created_at for r in previous)
                ):
                    if control.get("paused"):
                        raise QueueBlocked(
                            "Draft recovery is paused",
                            outcome="deferred",
                            code="paused",
                            not_before=temporary_time(),
                        )
                    raise QueueBlocked(
                        "Draft recovery is stale, exhausted or unauthorized", code="stale_recovery"
                    )
            chosen = s.get(
                Resume,
                resume_id or (source.resume_id if recovery_source_run else None) or job.resume_id or "",
            )
            from .execution_services import master_policy

            master = master_policy(self.db) if not job.demo else None
            if master:
                chosen = s.get(Resume, master.get("resume_id", ""))
                resume_artifact_id = None
            if not chosen:
                raise ValueError("Choose an uploaded resume or classify the job first")
            if chosen.demo != job.demo:
                raise ValueError("Demo resumes can only be used on local demo jobs")
            if not job.demo and (not profile.name or not profile.email):
                raise ValueError("Complete your profile name and email before applying")
            if not job.demo:
                from .agent_settings import for_role

                if any(
                    not for_role(self.db, self.config, role).llm_api_key for role in ("answers", "navigator")
                ):
                    raise ValueError(
                        "Configure the selected answer and navigation provider keys before live browser runs"
                    )
            if mode == "submit" and not job.demo:
                control = s.get(Setting, "control").value
                if not control.get("auto_submit"):
                    raise QueueBlocked(
                        "Enable automatic submission in Settings first",
                        outcome="deferred",
                        code="submission_disabled",
                        not_before=temporary_time(),
                    )
                # An automatic retry after answered questions keeps the original approval.
                if job.status != "ready" and not (retry and job.status in RETRYABLE):
                    raise ValueError("Approve or classify this job before submission")
            packet_profile = profile.model_dump()
            from .email_alias import ats_address

            # A platform that bound the profile address to an unusable identity applies with a plus-address (iCIMS).
            packet_profile["email"] = ats_address(packet_profile.get("email", ""), job.ats, profile.ats_email_aliases)
            if job.demo:
                packet_profile = {
                    "name": "Alex Example",
                    "email": "alex@example.test",
                    "phone": "2025550100",
                    "location": "New York",
                    "facts": {"first_name": "Alex", "last_name": "Example"},
                }
            from .pipeline_comparison import continued_run

            continuation = continued_run(previous, source if recovery_source_run else None)
            path = self.config.data_dir / "resumes" / chosen.filename
            from .execution_services import pin_resume

            if (
                not job.demo
                and not master
                and mode == "submit"
                and not (continuation and continuation.packet.get("resume_artifact_id"))
            ):
                from .execution_services import enabled
                from .features.resumes.tables import RsArtifact
                from .features.resumes.pipeline import CURRENT_POLICIES, file_intact
                from .features.resumes.evidence import profile_digest as resume_profile_digest
                from .features._store import digest as feature_digest

                if enabled(self.db) and not resume_artifact_id:
                    artifacts = s.scalars(
                        select(RsArtifact)
                        .where(
                            RsArtifact.job_id == job.id,
                            RsArtifact.status == "ready",
                            RsArtifact.source_resume_id == chosen.id,
                            RsArtifact.source_resume_sha == chosen.sha256,
                            RsArtifact.profile_digest == resume_profile_digest(packet_profile),
                            RsArtifact.description_sha == feature_digest(job.description or "")[:20],
                            RsArtifact.policy_version.in_(CURRENT_POLICIES),
                        )
                        .order_by(RsArtifact.finished_at.desc())
                    )
                    artifact = next((a for a in artifacts if file_intact(self.config.data_dir, a)), None)
                    if not artifact:
                        raise QueueBlocked(
                            "Preparing a tailored resume; the application will resume when ready",
                            outcome="deferred",
                            code="tailoring_pending",
                            not_before=temporary_time(),
                        )
                    resume_artifact_id = artifact.id

            if master:
                artifact_packet = {"resume_policy": "master"}
            else:
                artifact_packet, packet_profile = pin_resume(
                    s,
                    self.config,
                    job,
                    chosen,
                    packet_profile,
                    artifact_id=resume_artifact_id,
                    continuation=continuation,
                )
            from .pipeline_comparison import assign, assign_pool, normalize_ats, pool_capacity

            pipeline_source = continuation or (
                s.get(Run, recovery_source_run) if recovery_source_run else None
            )
            worker_pool = assign_pool(s, job, pipeline_source)
            if (
                worker_pool != "normal"
                and pool_capacity(s, worker_pool, self.config.topology).available <= 0
                and pool_capacity(s, "normal", self.config.topology).available > 0
            ):
                # The specialist pool has 2 slots and its escalation flag is carried forward on
                # every retry; a full specialist pool must not idle 18 normal workers (2026-10-14).
                worker_pool = "normal"
            if pool_capacity(s, worker_pool, self.config.topology).available <= 0:
                raise QueueBlocked(
                    "This worker pool is busy; the saved job will resume when capacity opens",
                    outcome="deferred",
                    code="worker_capacity",
                    not_before=temporary_time(),
                )
            run = Run(
                job_id=job.id,
                mode=mode,
                resume_id=chosen.id,
                packet={
                    "profile": packet_profile,
                    "resume_path": str(path),
                    "resume_sha": chosen.sha256,
                    "resume_text": "" if job.demo else (chosen.text or "")[:16000],
                    "match_approved": job.status == "ready"
                    or bool(retry and any(x.packet.get("match_approved") for x in previous)),
                    "engine_version": engine_version(),
                    "model_version": self.config.llm_provider + ":" + self.config.llm_model,
                    "runner_tier": job_tier(self.db, job.id),
                    "profile_version": profile_version(packet_profile),
                    **(
                        {
                            "source_run_id": continuation.id,
                            "previous_engine_version": continuation.packet.get("engine_version"),
                            "browser_checkpoint": continuation.packet.get("browser_checkpoint", {}),
                            "worker_slot": continuation.packet.get("worker_slot"),
                            "controller_policy": continuation.packet.get("controller_policy", "hybrid"),
                        }
                        if continuation
                        else {}
                    ),
                    **(
                        {
                            "source_run_id": source.id,
                            "recovery_continuation_cause": recovery_row.value.get("cause"),
                            "browser_checkpoint": source.packet.get("browser_checkpoint")
                            or self.db.get_setting("desktop_job:" + job.id, {}),
                            **(
                                {
                                    "email_wait_resume": {
                                        "source_run_id": source.id,
                                        **source.packet.get("wait_handle", {}),
                                    }
                                }
                                if recovery_row.value.get("cause") == "email_matched"
                                else {}
                            ),
                        }
                        if recovery_source_run
                        else {}
                    ),
                    "controller_policy": controller_policy
                    or (continuation.packet.get("controller_policy", "hybrid") if continuation else "hybrid"),
                    **artifact_packet,
                    "application_pipeline": assign(s, job, pipeline_source),
                    "worker_pool": worker_pool,
                    "ats_specialist": normalize_ats(job.ats),
                    **(
                        {"specialist_escalation": pipeline_source.packet["specialist_escalation"]}
                        if pipeline_source and pipeline_source.packet.get("specialist_escalation")
                        else {}
                    ),
                },
            )
            from .pipeline_comparison import AGENTIC, REASONING

            pipeline = run.packet["application_pipeline"]
            if pipeline in {AGENTIC, REASONING}:
                run.packet = {
                    **run.packet,
                    "controller_policy": "continuous_agent" if pipeline == AGENTIC else "reasoning_first",
                }
            elif run.packet.get("controller_policy") in {"continuous_agent", "reasoning_first"}:
                # The cheap engine never runs the model-led agent, whether inherited from the run it
                # continues or requested by a recovery: that agent was 90% of model spend.
                run.packet = {**run.packet, "controller_policy": "hybrid"}
            s.add(run)
            s.flush()
            if recovery_row:
                recovery_row.value = {
                    **recovery_row.value,
                    "scheduled": False,
                    "status": "queued",
                    "attempts": recovery_row.value.get("attempts", 0)
                    + (recovery_row.value.get("cause") not in {"email_matched", "worker_interrupted"}
                       and worker_pool != "specialist"),
                    **(
                        {"specialist_attempts": specialist_attempts(s, job_id)}
                        if worker_pool == "specialist"
                        else {}
                    ),
                    "recovery_run_id": run.id,
                    "updated_at": now(),
                }
                # Recovery belongs to the same unattended batch. Its eventual
                # receipt must count once toward the original success target.
                batch_row = s.get(Setting, "job_board_autopilot")
                if (
                    batch_row
                    and batch_row.value.get("state") == "active"
                    and recovery_source_run in batch_row.value.get("run_ids", [])
                ):
                    batch_row.value = {
                        **batch_row.value,
                        "run_ids": [*batch_row.value["run_ids"], run.id],
                        "queued": len(batch_row.value["run_ids"]) + 1,
                    }
            if batch_id:
                # Record batch ownership atomically with the run, before any external dispatch.
                batch_row = s.get(Setting, "job_board_autopilot")
                batch = dict(batch_row.value) if batch_row else {}
                if batch.get("started_at") != batch_id or batch.get("state") != "active":
                    raise ValueError("This unattended batch is no longer active")
                batch["run_ids"] = [*batch.get("run_ids", []), run.id]
                batch["queued"] = len(batch["run_ids"])
                batch_row.value = batch
            job.status = "queued"
            result = record(run)
        self.db.event(run.id, "queued", "Application queued", {"mode": mode, "resume": chosen.name})
        if self.dispatch:
            try:
                await self.dispatch(run.id)
            except Exception:
                # The run is already committed. Retry its delivery, never create another run.
                from .outbox import enqueue

                with self.db.exclusive() as s:
                    enqueue(s, "dispatch", run.id, {"run_id": run.id}, run_id=run.id)
                self.kick_outbox()
        return result

    async def queue_batch(self, job_ids, mode="submit"):
        queued, skipped = [], []
        for job_id in dict.fromkeys(job_ids):
            with self.db.session() as s:
                job = s.get(Job, job_id)
                ready = job and job.status == "ready"
            if not ready:
                skipped.append({"job_id": job_id, "reason": "Only Ready jobs can be mass applied"})
                continue
            try:
                run = await self.queue(job_id, mode)
                queued.append({"job_id": job_id, "run_id": run["id"]})
            except (ValueError, RuntimeError) as exc:
                skipped.append({"job_id": job_id, "reason": str(exc)[:300], **outcome_for(exc)})
        return {"queued": queued, "skipped": skipped}

    def reserve_submission(self, run_id):
        day = datetime.now(ZoneInfo(self.config.timezone)).date().isoformat()
        with self.db.exclusive() as s:
            run = s.get(Run, run_id)
            job = s.get(Job, run.job_id)
            from .job_control import require_running

            require_running(s, run.job_id)
            from .application_budget import require_allowance

            require_allowance(s, run.job_id, new_attempt=False)
            control = s.get(Setting, "control").value
            if control.get("paused"):
                raise ValueError("Workers were paused before submission")
            if run.state != "running" or run.mode != "submit":
                raise ValueError("Submission is not authorized for this run")
            if not job.demo:
                if not control.get("auto_submit"):
                    raise ValueError("Automatic submission was disabled")
                from .application_pacing import require_company_limit, require_ats_focus

                require_ats_focus(s, job, run)
                require_company_limit(s, job, run_id)
                budget = s.get(Budget, day)
                if not budget:
                    budget = Budget(day=day, spent=0, submissions=0)
                    s.add(budget)
                if budget.submissions >= self.config.daily_application_limit:
                    raise ValueError("Daily submission cap reached")
                budget.submissions += 1
            run.state = "submitting"

    def authorize_validation_retry(self, run_id, rejection):
        """Keep one submission reservation; retries need a recorded, explicit field rejection."""
        if rejection.get("category") != "validation" or not rejection.get("missing_fields"):
            raise ValueError("No explicit missing-field rejection permits a retry")
        with self.db.exclusive() as s:
            run = s.get(Run, run_id)
            control = s.get(Setting, "control").value
            if not run or run.state != "submitting" or run.mode != "submit":
                raise ValueError("This run has no authorized submission reservation")
            from .job_control import require_running

            require_running(s, run.job_id)
            if control.get("paused") or (not s.get(Job, run.job_id).demo and not control.get("auto_submit")):
                raise ValueError("Application submission is paused or disabled")
            key = "validation_retry:" + run_id
            from .application_budget import require_allowance

            require_allowance(s, run.job_id)
            row = s.get(Setting, key)
            count = (row.value.get("count", 0) if row else 0) + 1
            if count > 2:
                raise ValueError("Application validation repair limit reached")
            value = {"count": count, "missing_fields": rejection["missing_fields"], "at": now()}
            if row:
                row.value = value
            else:
                s.add(Setting(key=key, value=value))
        self.db.event(
            run_id,
            "validation_repair",
            "Employer rejected incomplete fields; verified corrections permit another attempt",
            value,
        )

    def complete(self, run_id, result, elapsed):
        with self.db.exclusive() as s:
            run = s.get(Run, run_id)
            if not run or run.state == "cancelled":
                return
            if run.state == "confirmed" and result.get("state") != "confirmed":
                return  # Trusted asynchronous receipt reconciliation is terminal.
            state = result.get("state", "failed")
            from .sensitive_ids import sensitive_field

            asked = [
                kind
                for q in result.get("reviews") or []
                if (kind := sensitive_field({"type": "text", "label": q.get("question", "")}))
            ]
            if (
                asked
                and state in {"needs_review", "waiting_answer"}
                and not run.packet.get("verification_only")
            ):
                # Owner rule: an application asking for an SSN / ID / bank number is removed, even when
                # the question reached the review path instead of the page hook (Maersk, 2026-10-13).
                state = "cancelled"
                result = {
                    "state": "cancelled",
                    "reason": "Application asks for "
                    + ", ".join(dict.fromkeys(asked)).replace("_", " ")
                    + "; the job was removed under the owner rule",
                    "blocker_type": "sensitive_id",
                }
                job = s.get(Job, run.job_id)
                control = s.get(Setting, "job_control:" + run.job_id)
                value = {
                    "state": "deleted",
                    "at": now(),
                    "previous_status": job.status if job else "",
                    "reason": "sensitive_id",
                }
                if control:
                    control.value = value
                else:
                    s.add(Setting(key="job_control:" + run.job_id, value=value))
                if job:
                    job.status = "archived"
            if state in {"needs_review", "waiting_answer"} and result.get("reviews"):
                from .answers import residence_outside

                profile = run.packet.get("profile") or {}
                outside = next(
                    (
                        where
                        for q in result["reviews"]
                        if (where := residence_outside(
                            q.get("question", ""), q.get("options") or [], profile.get("facts"), profile.get("location", "")
                        ))
                    ),
                    None,
                )
                if outside:
                    # The employer hires only in places it lists, and the applicant's state is not one; no answer
                    # can make this application truthful, so it is closed rather than asked.
                    state = "closed"
                    result = {
                        "state": "closed",
                        "reason": f"The employer hires only in its listed locations, and the applicant lives in {outside}",
                        "blocker_type": "ineligible",
                    }
            if state == "needs_review" and not result.get("reviews"):
                # A control/upload failure is not an unanswered personal fact.
                # Keep its owned draft available instead of skipping and closing it.
                state = "waiting_browser"
                result = {**result, "recovery_cause": result.get("recovery_cause") or "control_observation"}
            if state == "needs_review" and result.get("reviews"):
                facts = [q for q in result["reviews"] if self.review_kind(q) == "missing_fact"]
                if not facts:
                    state = "waiting_browser"
                    result = {
                        **result,
                        "reason": "The website did not accept known answers: "
                        + "; ".join(q["question"] for q in result["reviews"]),
                        "reviews": [],
                        "recovery_cause": "control_observation",
                    }
                elif len(facts) != len(result["reviews"]):
                    result = {**result, "reviews": facts}
            if state == "needs_review" and run.packet.get("profile", {}).get("autonomous"):
                answerable = any(q.get("key") not in {"manual", "session"} for q in result.get("reviews", []))
                # Telegram is one delivery channel. Missing facts remain saved
                # and answerable in the web app when that channel is disabled.
                state = "waiting_answer" if answerable else "waiting_browser"
                questions = [q["question"] for q in result.get("reviews", [])]
                if questions:
                    result["reason"] = (
                        result.get("reason", "Missing information") + ": " + "; ".join(questions)
                    )
            if state not in TERMINAL:
                state = "failed"
            evidence = result.get("receipt", {})
            website_proof = (
                evidence.get("type") == "explicit_confirmation_page"
                and evidence.get("evidence_version") == 2
                and evidence.get("evidence_selector")
                and evidence.get("status_region")
                and evidence.get("confirmation")
                and evidence.get("job_id") == run.job_id
                and evidence.get("run_id") == run.id
            )
            email = evidence.get("email", {})
            email_proof = (
                isinstance(email, dict)
                and email.get("authenticated")
                and email.get("sender_trusted")
                and email.get("job_id") == run.job_id
            )
            if state == "confirmed" and not (
                website_proof or email_proof or evidence.get("type") == "user_reconciled"
            ):
                # "Confirmed" needs a website confirmation, an employer email, or the applicant's word.
                state = "submission_unknown" if run.state == "submitting" else "waiting_browser"
                result = {**result, "reason": "No confirmation evidence was captured"}
            rejected = evidence.get("type") in {"explicit_rejection_page", "explicit_validation_rejection"}
            if (
                run.state == "submitting"
                and state not in {"confirmed", "submission_unknown"}
                and not rejected
            ):
                state = "submission_unknown"
            run.state, run.reason = state, result.get("reason", "")[:4000]
            run.finished_at, run.elapsed = now(), elapsed
            run.packet = {
                **run.packet,
                "blocker_type": result.get("blocker_type"),
                "recovery_cause": result.get("recovery_cause"),
                "waiting_kind": result.get("waiting_kind"),
                "wait_handle": result.get("wait_handle", {}),
                "diagnostic_capture": result.get("diagnostic_capture"),
                **(
                    {"browser_checkpoint": result["browser_checkpoint"]}
                    if result.get("browser_checkpoint")
                    else {}
                ),
            }
            run.receipt = result.get("receipt", {})
            receipt = dict(result.get("receipt", {}))
            if receipt:
                # Separate evidence: what the employer's website showed vs. what its email said.
                receipt["website_confirmed"] = bool(
                    state == "confirmed" and receipt.get("type") == "explicit_confirmation_page"
                )
                receipt["email_confirmed"] = bool(receipt.get("email"))
                run.receipt = receipt
            if rejected and re.search(r"spam|too many requests|rate limit", str(run.receipt), re.I):
                key = self.cooldown_key(s.get(Job, run.job_id))
                cooldown = {"until": (datetime.now(UTC) + timedelta(hours=6)).isoformat(), "run_id": run_id}
                item = s.get(Setting, key)
                if item:
                    item.value = cooldown
                else:
                    s.add(Setting(key=key, value=cooldown))
            other_confirmed = s.scalar(
                select(Run.id).where(Run.job_id == run.job_id, Run.id != run.id, Run.state == "confirmed")
            )
            s.get(Job, run.job_id).status = (
                "archived"
                if (
                    (s.get(Setting, "job_control:" + run.job_id) or Setting(key="", value={})).value or {}
                ).get("state")
                == "deleted"
                else "confirmed"
                if other_confirmed
                else ("ready" if state == "dry_run_passed" and run.packet.get("match_approved") else state)
            )
            from .recovery import schedule_recovery

            from .application_slots import release_slot

            release_slot(s, run)
            schedule_recovery(s, self.config, run, result)
            # Questions already asked live during the run (Telegram) are persisted; never duplicate.
            known = {
                r.key
                for r in s.scalars(select(Review).where(Review.run_id == run_id, Review.answer.is_(None)))
                if r.key not in {"manual", "session"}
            }  # An answered question that is still missing is asked again, so the run can resume.
            for q in result.get("reviews", []) if state in {"needs_review", "waiting_answer"} else []:
                if self.review_kind(q) != "missing_fact":
                    continue
                if q.get("key", "manual") in known:
                    continue
                s.add(
                    Review(
                        run_id=run_id,
                        question=q["question"][:8000],
                        options=q.get("options", []),
                        key=q.get("key", "manual"),
                        reason=q.get("reason", ""),
                    )
                )
            from .outbox import enqueue

            if self.config.failure_reports_enabled and state not in {
                "confirmed",
                "dry_run_passed",
                "already_applied",
                "closed",
                "cancelled",
                "paused",
            }:
                enqueue(s, "report", run_id, {"diagnostics": result.get("diagnostics", {})}, run_id=run_id)
            if getattr(self.config, "repair_agent_enabled", True) and state in {
                "waiting_browser",
                "needs_review",
                "submission_unknown",
                "failed",
                "timed_out",
            }:
                # The repair agent reads this run's evidence and applies a guarded fix (repair_agent.py).
                enqueue(s, "repair", run_id, run_id=run_id)
            if self.dispatch and self.config.enable_workers:
                enqueue(s, "refill", run_id, run_id=run_id)
            from .telegram_workers import any_configured

            if any_configured(self.config):
                key = "telegram_run_pending:" + run_id
                row = s.get(Setting, key)
                if row:
                    row.value = {"pending": True}
                else:
                    s.add(Setting(key=key, value={"pending": True}))
                enqueue(s, "notify", run_id, run_id=run_id)
            if result.get("waiting_kind") == "email":
                handle = result.get("wait_handle", {})
                challenge = s.get(MailChallenge, handle.get("challenge_id", ""))
                if challenge and challenge.run_id == run_id and challenge.state == "matched":
                    enqueue(s, "mail_resume", challenge.id, {"run_id": run_id, **handle}, run_id=run_id)
        self.db.event(
            run_id,
            "finished",
            result.get("reason", state),
            # Control failures carry their exception and code locations; three NCMEC failures had none.
            {"state": state, "elapsed": round(elapsed, 2)}
            | ({"diagnostics": result["diagnostics"]} if isinstance(result.get("diagnostics"), dict) else {}),
        )
        self._trace_phase_timing(run_id)

    def _trace_phase_timing(self, run_id):
        """Diagnostics only: tracing must never fail or alter a run."""
        try:
            with self.db.session() as s:
                rows = s.execute(
                    select(Event.kind, Event.created_at, Event.data)
                    .where(Event.run_id == run_id)
                    .order_by(Event.id)
                ).all()
            timing = phase_timing(rows)
            if timing:
                self.db.event(run_id, "phase_timing", "Run phase timings", timing)
        except Exception:
            return

    def _trace_failure(self, run_id, diagnostics):
        """Record exception class and frame locations only; messages may carry secrets."""
        try:
            with self.db.session() as s:
                last_kind = s.execute(
                    select(Event.kind).where(Event.run_id == run_id).order_by(Event.id.desc()).limit(1)
                ).scalar()
            self.db.event(
                run_id,
                "failure_trace",
                "Worker error trace",
                {
                    "exc_class": diagnostics.get("exception", ""),
                    "locations": list(diagnostics.get("locations", []))[:5],
                    "last_event_kind": last_kind or "",
                },
            )
        except Exception:
            return

    def preflight_questions(self):
        """Only unresolved facts, deduplicated across drafts; no inferred questionnaire."""
        with self.db.session() as s:
            rows = s.execute(
                select(Review, Run, Job)
                .join(Run, Review.run_id == Run.id)
                .join(Job, Run.job_id == Job.id)
                .where(
                    Review.answer.is_(None),
                    Run.state.in_(["needs_review", "waiting_answer"]),
                    Job.status != "archived",
                )
                .order_by(Review.created_at)
            ).all()
        questions = {}
        for review, run, job in rows:
            if self.review_kind({"key": review.key, "reason": review.reason}) != "missing_fact":
                continue
            item = questions.setdefault(
                review.key,
                {
                    "id": review.id,
                    "question": review.question,
                    "options": review.options,
                    "reason": review.reason,
                    "applications": [],
                    "sensitive": bool(
                        re.search(
                            r"social security|\bssn\b|date of birth|birth date|passport",
                            review.question,
                            re.I,
                        )
                    ),
                },
            )
            item["applications"].append({"run_id": run.id, "company": job.company})
        return list(questions.values())

    @staticmethod
    def review_kind(question):
        kind = question.get("blocker_type")
        if kind:
            return kind
        reason = question.get("reason", "")
        if reason.startswith("Could not commit the answer:"):
            return "control_failure"
        if reason.startswith(("Model omitted", "Unverifiable answer", "Model output", "Model unavailable")):
            return "model_output"
        return "missing_fact" if question.get("key") not in {"manual", "session"} else "account"

    def kick_outbox(self):
        if getattr(self, "outbox_running", False):
            return
        from .outbox import process_one

        task = asyncio.create_task(process_one(self))
        self.notification_tasks.add(task)
        task.add_done_callback(self.notification_tasks.discard)

    async def resume_mail_wait(self, run_id, challenge_id, *, restart_verification=False):
        """A matched challenge wakes its approved draft once, without exposing the payload."""
        with self.db.exclusive() as s:
            source, challenge = s.get(Run, run_id), s.get(MailChallenge, challenge_id)
            if not source or not challenge:
                return
            if restart_verification:
                target = s.get(Setting, "desktop_job:" + source.job_id)
                request_owner = s.get(Run, challenge.run_id)
                if (
                    source.state != "submission_unknown"
                    or not request_owner
                    or request_owner.job_id != source.job_id
                    or challenge.run_id not in {source.id, source.packet.get("reservation_run_id", source.id)}
                    or request_owner.packet.get("wait_handle", {}).get("challenge_id") != challenge.id
                    or challenge.state not in {"cancelled", "expired", "failed"}
                    or not target
                    or not (target.value.get("target_id") or target.value.get("resume_route"))
                ):
                    raise ValueError("A retained, uncertain verification draft is required")
            elif challenge.state not in {"matched", "claimed"}:
                return
            if source.state not in {"waiting_browser", "submission_unknown"} or (
                not restart_verification
                and source.packet.get("wait_handle", {}).get("challenge_id") != challenge_id
            ):
                return
            newer = s.scalar(
                select(Run.id).where(Run.job_id == source.job_id, Run.created_at > source.created_at)
            )
            if newer:
                return
            control = s.get(Setting, "control").value
            if control.get("paused") or not control.get("auto_submit"):
                raise QueueBlocked(
                    "Email-matched draft is paused",
                    outcome="deferred",
                    code="paused",
                    not_before=temporary_time(),
                )
            if (
                source.mode != "submit"
                or not source.packet.get("match_approved")
                or not source.packet.get("profile", {}).get("autonomous")
            ):
                return
            require_intake(s, source.job_id)
            from .application_budget import require_allowance

            require_allowance(s, source.job_id)
            if source.state == "submission_unknown":
                # One-use continuation: it inherits the reservation and can only verify the saved draft.
                job = s.get(Job, source.job_id)
                if job.status in {"confirmed", "archived"} or (
                    challenge.state != "matched" and not restart_verification
                ):
                    return
                from .pipeline_comparison import pool_for, pool_capacity

                if pool_capacity(s, pool_for(source), self.config.topology).available <= 0:
                    raise QueueBlocked(
                        "Verification owner pool is busy",
                        outcome="deferred",
                        code="worker_capacity",
                        not_before=temporary_time(),
                    )
                child = Run(
                    job_id=source.job_id,
                    mode=source.mode,
                    resume_id=source.resume_id,
                    packet={
                        **source.packet,
                        "source_run_id": source.id,
                        "verification_only": True,
                        "reservation_run_id": source.packet.get("reservation_run_id", source.id),
                        "verification_repair": restart_verification,
                        "email_wait_resume": {}
                        if restart_verification
                        else {"source_run_id": source.id, **source.packet["wait_handle"]},
                        "engine_version": engine_version(),
                    },
                )
                s.add(child)
                s.flush()
                job.status = "queued"
                from .outbox import enqueue

                enqueue(s, "dispatch", child.id, {"run_id": child.id}, run_id=child.id)
                return {"run_id": child.id, "state": "queued", "verification_only": True}
            key = "draft_recovery:" + source.job_id
            row = s.get(Setting, key)
            value = {
                **(row.value if row else {}),
                "source_run_id": source.id,
                "cause": "email_matched",
                "attempts": (row.value.get("attempts", 0) if row else 0),
                "scheduled": True,
                "not_before": now(),
                "status": "scheduled",
                "updated_at": now(),
            }
            if row:
                row.value = value
            else:
                s.add(Setting(key=key, value=value))
            job_id = source.job_id
        await self.queue(job_id, "submit", retry=True, recovery_source_run=run_id)
        self.db.event(
            run_id,
            "EmailMatched",
            "Matched email released its draft for resumption",
            {"challenge_id": challenge_id},
        )

    def answer_review(self, review_id, answer):
        if not answer.strip() or len(answer) > 8000:
            raise ValueError("Provide an answer of 1–8000 characters")
        with self.db.exclusive() as s:
            review = s.get(Review, review_id)
            if not review:
                raise ValueError("Review not found")
            if review.answer is not None:
                raise ValueError("This review has already been resolved")
            if review.options and answer not in review.options:
                raise ValueError("Choose an offered option")
            review.answer = answer
            from .outbox import enqueue

            # The originating draft needs the same durable wakeup as its
            # siblings. Queueing once in the interactive request is insufficient
            # when a deploy, cooldown or capacity gate temporarily blocks it.
            if review.key not in {"manual", "session"}:
                enqueue(s, "fact_resume", review.id, {"review_id": review.id}, run_id=review.run_id)
            if review.key not in {"manual", "session"}:
                item = s.get(Setting, "profile")
                value = dict(item.value if item else {})
                value["approved_answers"] = {**value.get("approved_answers", {}), review.key: answer}
                run = s.get(Run, review.run_id)
                job = s.get(Job, run.job_id) if run else None
                if job:
                    value["reviewed_answers"] = [
                        *value.get("reviewed_answers", []),
                        learned_answer(
                            review.id, review.question, answer, review.options, job.company or job.url
                        ),
                    ]
                if item:
                    item.value = value
                else:
                    s.add(Setting(key="profile", value=value))
                if job:
                    scope = learned_answer(
                        review.id, review.question, answer, review.options, job.company or job.url
                    )["scope"]
                    from .outbox import enqueue

                    for other, owner, employer in s.execute(
                        select(Review, Run, Job)
                        .join(Run, Review.run_id == Run.id)
                        .join(Job, Run.job_id == Job.id)
                        .where(
                            Review.key == review.key,
                            Review.answer.is_(None),
                            Review.id != review.id,
                            Run.state.in_(["waiting_answer", "needs_review"]),
                            Job.status != "archived",
                        )
                    ):
                        if (not other.options or answer in other.options) and (
                            scope == "personal" or employer.company.casefold() == job.company.casefold()
                        ):
                            other.answer = answer
                            enqueue(s, "fact_resume", other.id, {"review_id": other.id}, run_id=owner.id)
        return {"resolved": True, "message": "Answer saved to your knowledge base."}

    async def resolve_review(self, review_id, answer):
        return await self.resume_review(review_id, self.answer_review(review_id, answer))

    async def resume_review(self, review_id, result=None):
        durable_delivery = result is None
        result = result or {"resolved": True, "message": "Saved answer reused."}
        with self.db.exclusive() as s:
            review = s.get(Review, review_id)
            if not review or review.answer is None:
                return result
            run = s.get(Run, review.run_id)
            remaining = s.scalar(
                select(Review.id).where(Review.run_id == review.run_id, Review.answer.is_(None)).limit(1)
            )
            control = s.get(Setting, "control").value
            if control.get("paused") and durable_delivery:
                raise QueueBlocked(
                    "Answered draft is paused", outcome="deferred", code="paused", not_before=temporary_time()
                )
            if not run or remaining or control.get("paused") or review.key in {"manual", "session"}:
                return result
            job = s.get(Job, run.job_id)
            newer = s.scalar(
                select(Run.id)
                .where(
                    Run.job_id == run.job_id,
                    or_(
                        Run.created_at > run.created_at,
                        and_(Run.created_at == run.created_at, Run.id > run.id),
                    ),
                )
                .limit(1)
            )
            if (
                newer
                or job.status in {"archived", "confirmed", "submission_unknown"}
                or run.state not in {"needs_review", "waiting_answer"}
            ):
                return result
            if run.mode == "submit":
                if not control.get("auto_submit") and durable_delivery:
                    raise QueueBlocked(
                        "Automatic applications are disabled",
                        outcome="deferred",
                        code="auto_submit_disabled",
                        not_before=temporary_time(),
                    )
                if not control.get("auto_submit") or not run.packet.get("match_approved"):
                    return result
                job.status = "ready"
            job_id, mode, resume_id = run.job_id, run.mode, run.resume_id
            batch = s.get(Setting, "job_board_autopilot")
            batch_id = (
                batch.value.get("started_at")
                if batch and batch.value.get("state") == "active" and run.id in batch.value.get("run_ids", [])
                else None
            )
        try:
            resumed = await self.queue(job_id, mode, resume_id, retry=True, batch_id=batch_id)
        except QueueBlocked as exc:
            if durable_delivery and exc.outcome == "deferred":
                raise  # Keep the correlated outbox event until its temporary gate clears.
            result["message"] += (
                " Application resumption is temporarily deferred."
                if exc.outcome == "deferred"
                else " Application remains protected by its queue guard."
            )
        except ValueError as exc:
            if durable_delivery:
                raise
            result["message"] += " " + str(exc)
        else:
            result.update(run_id=resumed["id"], message="Answer saved. Application resumed automatically.")
        return result

    def reconcile(self, run_id, submitted, evidence):
        if not evidence.strip():
            raise ValueError("Provide confirmation evidence or an explanation of non-submission")
        with self.db.exclusive() as s:
            run = s.get(Run, run_id)
            # A run that started a commit and was later parked can still be confirmed by the
            # owner (Dayforce showed its confirmation after the watch window, 2026-10-16).
            committed = bool(
                run
                and run.state in {"needs_review", "waiting_browser"}
                and s.scalar(
                    select(Event.id).where(Event.run_id == run_id, Event.kind == "submitting").limit(1)
                )
            )
            if not run or (run.state not in {"submission_unknown", "already_applied"} and not committed):
                raise ValueError("Only uncertain submissions or already-applied notices can be reconciled")
            run.state = "confirmed" if submitted else "needs_review"
            run.receipt = {"type": "user_reconciled", "evidence": evidence[:4000], "at": now()}
            run.reason = "Submission reconciled by the applicant"
            s.get(Job, run.job_id).status = run.state
        self.db.event(run_id, "reconciled", "Applicant reconciled submission", {"submitted": submitted})

    async def execute(self, run_id):
        import asyncio

        from .pipeline_comparison import POOLS, pool_for, pool_slot_ids

        if not hasattr(self, "browser_pools"):
            self.browser_pools = {
                pool: asyncio.Semaphore(len(pool_slot_ids(pool, self.config.topology))) for pool in POOLS
            }
        with self.db.session() as s:
            run = s.get(Run, run_id)
            if not run or run.state != "queued":
                return
            pool = pool_for(run)
        async with self.browser_pools[pool]:
            await self._execute_owned(run_id)

    async def _execute_owned(self, run_id):
        import time

        from .browser import BrowserUnavailable
        from .pipeline_comparison import engine_for, slots

        # The database claim also caps separate CLI processes and duplicate deliveries.
        # Never hold the transaction while waiting for a free browser slot.
        budget_checked = False
        first_attempt = False
        while True:
            claimed = False
            with self.db.exclusive() as s:
                row = s.get(Run, run_id)
                if not row or row.state != "queued":
                    return
                if s.get(Job, row.job_id).status == "archived":
                    row.state, row.finished_at = "cancelled", now()
                    return
                from .job_control import state as job_state

                if job_state(self.db, row.job_id).get("state") == "paused":
                    row.state, row.finished_at = "paused", now()
                    return
                if s.get(Setting, "control").value.get("paused"):
                    return
                from .application_budget import require_allowance

                try:
                    if not budget_checked:
                        from .application_budget import max_seconds

                        cap = max_seconds(s)
                        deadline, remaining = require_allowance(s, row.job_id, exclude_run=row.id)
                        first_attempt = remaining is not None and remaining == cap
                        budget_checked = True
                    elif first_attempt:
                        # Initial queue time is free. Avoid re-reading all alias
                        # history every second while waiting behind an employer.
                        deadline = datetime.now(UTC) + timedelta(seconds=cap)
                    elif deadline is not None and deadline <= datetime.now(UTC):
                        raise QueueBlocked("Application reached its eight-minute total deadline", code="application_deadline")
                except QueueBlocked as exc:
                    row.state, row.finished_at, row.reason = (
                        "submission_unknown" if row.packet.get("verification_only") else "timed_out", now(), str(exc)
                    )
                    current_job = s.get(Job, row.job_id)
                    current_job.status = "submission_unknown" if row.packet.get("verification_only") else "timed_out"
                    recovery = s.get(Setting, "draft_recovery:" + row.job_id)
                    if recovery:
                        recovery.value = {**recovery.value, "scheduled": False, "status": exc.code}
                    return
                occupied = len(
                    list(s.scalars(select(Run.id).where(Run.state.in_(["running", "submitting"]))))
                )
                partition = employer_partition(s.get(Job, row.job_id))
                same_employer = any(
                    employer_partition(j) == partition
                    for j in s.scalars(
                        select(Job)
                        .join(Run, Run.job_id == Job.id)
                        .where(Run.state.in_(["running", "submitting"]))
                    )
                )
                from .application_pacing import may_start, record_start

                current_job = s.get(Job, row.job_id)
                recovery_row = s.get(Setting, "draft_recovery:" + row.job_id)
                if (
                    occupied < self.config.workers
                    and not same_employer
                    and slots(s, row, self.config.topology)
                    and may_start(s, current_job, row)
                    and self.mail.recovery_sender_ready(
                        s, current_job,
                        recovery_row.value if recovery_row else None,
                    )
                    and self.mail.shared_sender_free(s, current_job)
                ):
                    from .application_slots import claim_slot

                    record_start(s, current_job, row)
                    claim_slot(s, row, self.config.topology)
                    row.state, row.started_at = (
                        ("submitting" if row.packet.get("verification_only") else "running"),
                        now(),
                    )
                    # Uncapped production (owner 2026-10-18) carries no lifetime deadline: the run's own
                    # runner budget below ends it, and mail/owner waits may extend it as before.
                    packet = {k: v for k, v in row.packet.items() if k != "application_deadline"}
                    row.packet = packet | ({"application_deadline": deadline.isoformat()} if deadline else {})
                    s.get(Job, row.job_id).status = "running"
                    run, job = record(row), record(s.get(Job, row.job_id))
                    claimed = True
            if claimed:
                break
            await asyncio.sleep(1)
        started = time.monotonic()
        try:
            with self.db.session() as s:
                self.check_cooldown(s, s.get(Job, run["job_id"]))
            from .specialist import pool_boost
            from .tiers import clamp, time_budget

            # The outer deadline follows the run's own (tier, ATS and worker pool) budget plus cleanup;
            # a fixed base cut tier-2/3 runs off at ~945 s.
            outer = time_budget(
                max(self.config.application_timeout, self.config.multipage_timeout),
                clamp((run.get("packet") or {}).get("runner_tier", 1)),
                job.get("ats", ""),
                boost=pool_boost(self.config, (run.get("packet") or {}).get("worker_pool")),
            )
            run_budget = max(outer, self.config.application_timeout, self.config.multipage_timeout) + 45
            lifetime = (run.get("packet") or {}).get("application_deadline")
            async with asyncio.timeout(
                min(run_budget, max(0, (datetime.fromisoformat(lifetime) - datetime.now(UTC)).total_seconds()))
                if lifetime
                else run_budget
            ):
                from .job_control import watch

                engine = engine_for(self, run)
                execution = asyncio.create_task(engine.run(job, run))
                monitor = asyncio.create_task(watch(self, engine, execution, run_id))
                try:
                    result = await execution
                except asyncio.CancelledError:
                    if engine.result and engine.result.get("state") in {"paused", "cancelled"}:
                        result = engine.result
                    else:
                        raise
                finally:
                    monitor.cancel()
                    await asyncio.gather(monitor, return_exceptions=True)
        except TimeoutError:
            with self.db.session() as s:
                current = s.get(Run, run_id)
                committed = current and current.state == "submitting"
            result = {
                "state": "submission_unknown" if committed else "timed_out",
                "reason": "Application reached its eight-minute total deadline; saved draft and submission protection retained",
                "recovery_cause": "operation_timeout" if not committed else "",
            }
        except BrowserUnavailable as exc:
            # Infrastructure, not the application: wait for the browser and retry
            # without spending a repair attempt.
            result = {
                "state": "timed_out",
                "reason": str(exc)[:400],
                "blocker_type": "site_transient",
                "recovery_cause": "browser_unavailable",
            }
        except Exception as exc:
            from .agent_settings import AgentDisabled

            # Keep provider payloads and secrets out of the event stream.
            known = isinstance(exc, (ValueError, RuntimeError)) and "api_key" not in str(exc).lower()
            result = {
                "state": "waiting_browser" if isinstance(exc, AgentDisabled) else "failed",
                "reason": str(exc)[:400] if known else f"Worker error: {type(exc).__name__}",
            }
            if isinstance(exc, AgentDisabled):
                result.update(blocker_type="agent_disabled", recovery_cause="agent_disabled", retryable=False)
            import traceback
            from pathlib import Path

            frames = [f for f in traceback.extract_tb(exc.__traceback__) if "/jobpilot/" in f.filename]
            result["diagnostics"] = {
                "exception": type(exc).__name__,
                "locations": [f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in frames[-5:]],
            }
            self._trace_failure(run_id, result["diagnostics"])
        self.complete(run_id, result, time.monotonic() - started)

        self.kick_outbox()
