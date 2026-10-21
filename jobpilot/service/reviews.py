"""Questions a run is waiting on: classification, answering, and resuming a parked run."""

from sqlalchemy import and_, or_, select

from ..db import Job, MailChallenge, Review, Run, Setting, now
from ..knowledge import learned_answer
from ..admission import QueueBlocked, temporary_time, require_intake
from ..runtime import engine_version


class ReviewsMixin:
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
            if source.state not in {"waiting_browser", "waiting_email", "submission_unknown"} or (
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
            from ..application_budget import require_allowance

            require_allowance(s, source.job_id)
            if source.state == "submission_unknown":
                # One-use continuation: it inherits the reservation and can only verify the saved draft.
                job = s.get(Job, source.job_id)
                if job.status in {"confirmed", "archived"} or (
                    challenge.state != "matched" and not restart_verification
                ):
                    return
                from ..pipeline_comparison import pool_for, pool_capacity

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
                from ..outbox import enqueue

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
            from ..outbox import enqueue

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
                    from ..outbox import enqueue

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
