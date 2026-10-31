"""Queueing a run: the admission guards run in order, then the run, its outbox row and its event are written."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextvars
from datetime import UTC, datetime, timedelta
import functools
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import defer

from ..db import Event, Job, Resume, Run, Setting, now, record
from ..schemas import Profile
from .. import shortlinks
from ..admission import QueueBlocked, employer_partition, temporary_time, require_intake
from ..runtime import engine_version, profile_version
from .states import ACTIVE, RETRYABLE

# Admission is synchronous database work: dozens of guard queries inside the app lock (a PostgreSQL advisory lock).
# Run on the event loop, every Apply click and autopilot start stalled the browsers that share the loop, and waiting
# for the lock held by a worker thread stalled them for as long as that thread kept it (2026-10-22). Its own two
# threads, so a slow mailbox or provider call queued on the default executor never delays a click. The lock, not the
# loop, keeps two admissions of one job from both writing a run.
ADMISSION = ThreadPoolExecutor(max_workers=2, thread_name_prefix="admission")


class ShortlinkPending(Exception):
    """The job's link is a short link nobody has asked yet; _queue resolves it outside the admission transaction."""

    def __init__(self, url):
        super().__init__(url)
        self.url = url


async def off_loop(fn, *args, **kwargs):
    """`fn(*args, **kwargs)` on an admission thread, with the caller's context variables."""
    call = functools.partial(contextvars.copy_context().run, fn, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(ADMISSION, call)


def fit_blocker(profile, board, title):
    """(message, code) of the fit filter that refuses a title for the autopilot, or None. The owner's own pick skips
    these; a run that reads a title the job did not have at queue time applies them again (posting_read.py)."""
    from ..features.discovery.feeds import INTERN
    from ..features.discovery.policy import SENIOR
    from ..features.discovery.roles import matching_role

    if profile.target_roles and not matching_role(title, profile.target_roles):
        return "Outside the profile's target roles", "target_role"
    if INTERN.search(title) and not profile.include_internships:
        return "Internships are excluded from application discovery", "internship"
    if board.get("early_career_only", False) and SENIOR.search(title):
        return "Outside the saved career-level policy", "seniority"
    return None


class QueueingMixin:
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
        busy = [
            other
            for other, other_job in session.execute(
                select(Run.id, Job).join(Job, Run.job_id == Job.id).where(Run.state.in_(ACTIVE), Job.id != job.id)
            )
            if employer_partition(other_job) == employer_partition(job)
        ]
        if busy:
            # One application per employer at a time: seven Amazon retries signed in to one account together and locked
            # it out (2026-10-27). The next starts when this employer's run ends.
            raise QueueBlocked(
                "Another application at this employer is running; this one starts when it ends",
                outcome="deferred",
                code="employer_busy",
                not_before=temporary_time(),
            )
        key = "site_circuit:" + hashlib.sha256(employer_partition(job).encode()).hexdigest()
        circuit = session.get(Setting, key)
        # A circuit holds only the build that tripped it: a deploy may have fixed the shared blocker (ADP's account
        # step held five jobs for five hours after its fix, 2026-10-27; owner: nothing in Repairing is held).
        if (
            circuit
            and circuit.value.get("engine") == engine_version()
            and datetime.fromisoformat(circuit.value["until"]) > datetime.now(UTC)
        ):
            raise QueueBlocked(
                circuit.value.get("reason")
                or "This employer site repeated the same temporary failure. Other employers can continue.",
                outcome="deferred",
                code="site_circuit",
                not_before=circuit.value["until"],
            )

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
        owner_pick=False,
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
                owner_pick,
            )
        except QueueBlocked as exc:
            if exc.code == "tailoring_pending":
                await off_loop(
                    self._request_preparation, job_id, mode, resume_id, retry, batch_id, controller_policy
                )
            elif exc.code == "eligibility_conflict":
                await off_loop(self._close_ineligible, job_id, str(exc))
            elif exc.code == "duplicate_posting":
                await off_loop(self._record_duplicate, job_id, exc.detail or {})
            raise

    def _record_duplicate(self, job_id, detail):
        """A short link that resolves to a posting already applied to is recorded the way an employer's "already applied"
        is, without a browser: an already_applied run saying why, and the job leaves the ready list. The run is exempt
        from the employer cap (application_pacing): the application it duplicates already holds the slot."""
        with self.db.exclusive() as session:
            job = session.get(Job, job_id)
            if not job or job.demo or job.status in {"confirmed", "already_applied"}:
                return
            reason = "The short link leads to a posting this account already applied to; no second application was made"
            run = Run(
                job_id=job_id,
                state="already_applied",
                mode="submit",
                reason=reason,
                created_at=now(),
                finished_at=now(),
                receipt={"type": "duplicate_posting", **detail},
            )
            session.add(run)
            session.flush()
            session.add(Event(run_id=run.id, kind="duplicate_posting", message=reason, data=detail))
            job.status, job.reason = "already_applied", reason

    def _close_ineligible(self, job_id, reason):
        """An employer restriction the applicant cannot meet closes the job with the employer's own words, so it leaves
        the ready list instead of being refused on every autopilot tick."""
        with self.db.exclusive() as session:
            job = session.get(Job, job_id)
            if job and not job.demo and job.status in {"new", "ready", "skipped", "needs_review", "waiting_answer"}:
                job.status, job.reason = "closed", reason[:500]

    def _request_preparation(self, job_id, mode, resume_id, retry, batch_id, controller_policy):
        from ..outbox import enqueue

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
        owner_pick=False,
    ):
        """Admit and write the run off the event loop (_admit), then hand it to the workers."""
        args = (
            job_id,
            mode,
            resume_id,
            retry,
            batch_id,
            recovery_source_run,
            controller_policy,
            resume_artifact_id,
            owner_pick,
        )
        try:
            result, run_id = await off_loop(self._admit, *args)
        except ShortlinkPending as pending:
            # A short link is asked once, here, outside the database lock and off the event loop; its answer (or a
            # failure, which never blocks) is saved, and the admission then reads it.
            await asyncio.to_thread(shortlinks.resolve, pending.url, self.db)
            result, run_id = await off_loop(self._admit, *args, resolved=True)
        if self.dispatch:
            try:
                await self.dispatch(run_id)
            except Exception:
                # The run is already committed. Retry its delivery, never create another run.
                from ..outbox import enqueue

                with self.db.exclusive() as s:
                    enqueue(s, "dispatch", run_id, {"run_id": run_id}, run_id=run_id)
                self.kick_outbox()
        return result

    def _admit(
        self,
        job_id,
        mode="dry_run",
        resume_id=None,
        retry=False,
        batch_id=None,
        recovery_source_run=None,
        controller_policy=None,
        resume_artifact_id=None,
        owner_pick=False,
        resolved=False,
    ):
        """`owner_pick`: the owner applied or pasted this job themselves. Owner, 2026-10-22: "if I say apply, then it
        should apply no questions asked." Fit and policy filters (target roles, internships, career level, the
        per-employer cap, platform focus) are the autopilot's, not the owner's; duplicate-submission guards, pauses,
        budgets, pacing and the employer's own eligibility restrictions (citizenship, clearance, PhD, sponsorship)
        still apply."""
        from ..controller_policy import validate_policy
        from ..tiers import job_tier

        if controller_policy is not None:
            validate_policy(controller_policy)
        from ..provider_health import dispatch_gate

        gate = dispatch_gate(self.db, self.config)
        if not gate.limit:
            raise QueueBlocked(
                gate.reason,
                code=gate.code,
                outcome="deferred",
                not_before=(datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            )
        profile = Profile.model_validate(self.db.get_setting("profile", {}))
        with self.db.exclusive() as s:
            job = s.get(Job, job_id)
            if not job:
                raise QueueBlocked("Job not found", code="not_found")
            from ..job_control import require_running

            require_running(s, job_id)
            from ..reserved import require_approval

            # FAANG, MANGO and the Big Four wait for the owner's approval of this job, whichever path asks: the autopilot,
            # recovery, the repair agent, a remembered Apply (owner 2026-10-27: "these scrapers should not apply before
            # my permission"). Before the owner's pick is recorded, so a refused click leaves nothing behind.
            require_approval(s, job)
            from ..owner_apply import PICK, picked

            if owner_pick and not job.demo and not picked(s, job_id):
                s.add(Setting(key=PICK + job_id, value={"at": now()}))  # Kept only if this queue succeeds.
            # Automatic retries of the owner's pick (stalls, repair, tiers) keep the owner's say.
            owner_pick = owner_pick or picked(s, job_id)
            if mode == "submit" and not job.demo and not owner_pick:
                board = self.db.get_setting("job_board_settings", {}) or {}
                if blocker := fit_blocker(profile, board, job.title):
                    raise QueueBlocked(blocker[0], code=blocker[1])
            from ..pipeline_comparison import routed_pool
            from ..specialist import pool_boost
            from ..tiers import job_over_budget

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
            from ..ats import canonicalize, detect, icims_requisition_alias, identity_variants, url_identity

            identity = canonicalize(job.url)[1]
            from ..operations.reset import guard_key

            # A short link is the posting it redirects to (Shield AI's feed link ran after its Lever twin was
            # confirmed, 2026-10-29). The answer is saved before this runs; a link never asked raises ShortlinkPending,
            # and one whose lookup failed is treated as before.
            final = None
            if not job.demo and shortlinks.is_shortlink(job.url):
                known, final = shortlinks.cached(s, job.url)
                if not known and not resolved:
                    raise ShortlinkPending(job.url)
            own_urls = [job.url, *([final] if final else [])]
            if not job.demo and any(s.get(Setting, guard_key(i)) for u in own_urls for i in identity_variants(u)):
                raise QueueBlocked(
                    "An archived application protects this job from duplicate submission.",
                    outcome="protected",
                    code="archived_submission",
                )
            # Columns only: whole rows (descriptions included) for every job on the platform were read per attempt.
            aliases = [
                candidate.id
                for candidate in s.execute(select(Job.id, Job.url, Job.ats, Job.company).where(Job.ats == job.ats))
                if url_identity(candidate.url) == identity or icims_requisition_alias(job, candidate)
            ]
            # The same posting under its other link: the target of this short link, or short links that lead here.
            linked = []
            if final and url_identity(final) != identity:
                final_ats, final_identity = detect(final).id, url_identity(final)
                linked = [
                    candidate.id
                    for candidate in s.execute(
                        select(Job.id, Job.url, Job.ats, Job.company).where(Job.ats == final_ats)
                    )
                    if url_identity(candidate.url) == final_identity
                ]
            elif not shortlinks.is_shortlink(job.url) and not job.demo:
                if urls := shortlinks.twins(s, job.url):
                    linked = list(s.scalars(select(Job.id).where(Job.url.in_(urls), Job.id != job.id)))
            aliases = [*aliases, *(i for i in linked if i not in aliases)]
            # A packet loads when asked for: admission holds the app lock, and most attempts read none of these.
            previous = list(s.scalars(select(Run).options(defer(Run.packet)).where(Run.job_id.in_(aliases))))
            if any(x.state in ACTIVE for x in previous):
                raise QueueBlocked(
                    "This job already has an active run", outcome="protected", code="active_run"
                )
            # Applied through the other link: recorded as already applied (queue()), never a second application.
            if duplicate := next(
                (x for x in previous if x.job_id in linked and x.state in {"confirmed", "already_applied"}), None
            ):
                raise QueueBlocked(
                    "The same posting was already applied to through another link.",
                    outcome="protected",
                    code="duplicate_posting",
                    detail={"duplicate_of": duplicate.job_id, "resolved_url": final or job.url},
                )
            from ..admission import unknown_resubmit_allowed

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
                from ..application_budget import require_allowance

                waited = s.get(Setting, "draft_recovery:" + job_id) if recovery_source_run else None
                email_continuation = bool(
                    waited
                    and waited.value.get("cause") == "email_matched"
                    and waited.value.get("source_run_id") == recovery_source_run
                )
                # A matched email continues the attempt that waited for it (service.reviews); it is not a retry.
                require_allowance(s, job_id, aliases=aliases, new_attempt=not email_continuation)
                from ..application_pacing import require_company_limit, require_ats_admission

                if not owner_pick:
                    require_company_limit(s, job)
                require_ats_admission(s, job, owner_pick=owner_pick)
                if not self.mail.shared_sender_free(s, job):
                    raise QueueBlocked(
                        "Another application is verifying with this platform's email sender; this one waits its turn",
                        outcome="deferred",
                        code="sender_busy",
                        not_before=(datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
                    )
            from ..eligibility import require_eligibility

            # The owner's pick included: it skips the autopilot's fit filters, not an employer's own restriction
            # (owner rule: a job that requires U.S. citizenship is closed for an applicant who is not a citizen).
            require_eligibility(s, job, profile, self.config.data_dir)
            self.check_cooldown(s, job)
            recovery_row = None
            if recovery_source_run:
                recovery_row = s.get(Setting, "draft_recovery:" + job_id)
                source = s.get(Run, recovery_source_run)
                control = s.get(Setting, "control").value
                from ..pipeline_comparison import SPECIALIST, assign_pool
                from ..recovery import specialist_attempts

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
                        not in {
                            "email_matched",
                            "sender_busy",
                            "browser_unavailable",
                            "provider_unavailable",
                            "worker_interrupted",
                            "host_overloaded",
                        }
                        and exhausted
                    )
                    or not source
                    or source.job_id != job_id
                    or source.state not in {"waiting_browser", "waiting_email", "timed_out"}
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
            from ..execution_services import master_policy

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
                from ..agent_settings import for_role, missing_key_message

                for role in ("answers", "navigator"):
                    role_config = for_role(self.db, self.config, role)
                    if not role_config.llm_api_key:
                        raise ValueError(missing_key_message(role_config.llm_provider))
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
            from ..email_alias import applying_email, employer_alias_key

            # A platform that bound the profile address to an unusable identity applies with a plus-address (iCIMS);
            # so does one employer, on any platform, whose identity for it was abandoned (Adapter.fresh_identity).
            employer_alias = s.get(Setting, employer_alias_key(job.url) or "-")
            packet_profile["email"] = applying_email(
                packet_profile.get("email", ""), job.ats, profile.ats_email_aliases, employer_alias.value if employer_alias else None
            )
            if job.demo:
                packet_profile = {
                    "name": "Alex Example",
                    "email": "alex@example.test",
                    "phone": "2025550100",
                    "location": "New York",
                    "facts": {"first_name": "Alex", "last_name": "Example"},
                }
            from ..pipeline_comparison import continued_run

            continuation = continued_run(previous, source if recovery_source_run else None)
            path = self.config.data_dir / "resumes" / chosen.filename
            from ..execution_services import pin_resume

            if (
                not job.demo
                and not master
                and mode == "submit"
                and not (continuation and continuation.packet.get("resume_artifact_id"))
            ):
                from ..execution_services import enabled
                from ..features.resumes.tables import RsArtifact
                from ..features.resumes.pipeline import CURRENT_POLICIES, file_intact
                from ..features.resumes.evidence import profile_digest as resume_profile_digest
                from ..features._store import digest as feature_digest

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
            from ..pipeline_comparison import assign, assign_pool, normalize_ats, pool_capacity
            from ..run_profile import keep as keep_profile

            pipeline_source = continuation or (
                s.get(Run, recovery_source_run) if recovery_source_run else None
            )
            assigned_pool = worker_pool = assign_pool(s, job, pipeline_source)
            if worker_pool == "custom" and not self.config.topology.custom:
                raise QueueBlocked(
                    "Applications on custom career sites are turned off (Setup › Connections › Workers); this one "
                    "waits until the custom-site worker is on",
                    outcome="deferred",
                    code="custom_off",
                    not_before=temporary_time(),
                )
            if (
                worker_pool == "specialist"
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
                    **keep_profile(s, packet_profile),
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
            from ..pipeline_comparison import AGENTIC, REASONING

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
                    + (
                        recovery_row.value.get("cause") not in {"email_matched", "worker_interrupted"}
                        and worker_pool != "specialist"
                    ),
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
            from ..specialist import routing_note

            routing = routing_note(
                assigned_pool, worker_pool, pipeline_source.packet if pipeline_source else None
            )
        self.db.event(
            run.id, "queued", "Application queued", {"mode": mode, "resume": chosen.name, **routing}
        )
        return result, run.id
