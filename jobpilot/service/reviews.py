"""Questions a run is waiting on: classification, answering, and resuming a parked run."""

import re

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from ..db import Job, MailChallenge, Review, Run, Setting, now
from ..knowledge import learned_answer
from ..admission import QueueBlocked, temporary_time, require_intake
from ..runtime import engine_version


# The repair agent's account questions ("TikTok requires an account. Create one with the saved email, or …?") answer
# that job's sign-in step; they are not a fact to reuse for every employer.
REPAIR_ACCOUNT = re.compile(r"\baccount\b|sign[ -]?in|log ?in", re.I)


class ReviewsMixin:
    @staticmethod
    def review_kind(question):
        from .. import owner_questions as oq

        reason = question.get("reason", "")
        kind = question.get("blocker_type")
        if kind in {None, "", "missing_fact"} and oq.defect(
            question.get("question", ""), question.get("options") or [], oq.context_from(question), reason
        ):
            # The engine misread the field (a bare label without its row, a list that never loaded): the owner
            # cannot answer it, so it is recovered as a control failure, never asked. The engine's own pending
            # questions arrive tagged missing_fact, so the check comes before that tag.
            return "control_failure"
        if kind:
            return kind
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

            # The arrival of the email this run waits for continues the same attempt; it is not a retry. By Light's
            # iCIMS reset email matched six seconds after its run parked, and the job's spent retry count stranded it
            # in waiting_email (2026-10-24). The application's time deadline still applies.
            require_allowance(s, source.job_id, new_attempt=False)
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

    def answer_review(self, review_id, answer, *, document=False):
        """Save the owner's answer. `document` is True only when an uploaded file answers an upload question."""
        from .. import owner_questions as oq

        if not answer.strip() or len(answer) > 8000:
            raise ValueError("Provide an answer of 1–8000 characters")
        with self.db.exclusive() as s:
            review = s.get(Review, review_id)
            if not review:
                raise ValueError("That question no longer exists.")
            if review.answer is not None:
                raise ValueError("That question is already answered.")
            context = review.context or {}
            question_kind = oq.kind(review.question, review.options, context, review.reason)
            offered = [text for _, text in oq.choices(review.options)]
            if (
                question_kind == "choice"
                and answer not in offered
                and answer not in review.options
                and not oq.free_text_allowed(review.options, context)
            ):
                raise ValueError("Choose one of the offered answers")
            if question_kind == "upload" and not document:
                # Typed text cannot go into a file field; the next attempt would stop on it again.
                raise ValueError("This question needs a file. Attach the document instead of typing an answer.")
            review.answer = answer
            from ..outbox import enqueue

            # The originating draft needs the same durable wakeup as its
            # siblings. Queueing once in the interactive request is insufficient
            # when a deploy, cooldown or capacity gate temporarily blocks it.
            if review.key not in {"manual", "session"}:
                enqueue(s, "fact_resume", review.id, {"review_id": review.id}, run_id=review.run_id)
            if review.key not in {"manual", "session"}:
                run = s.get(Run, review.run_id)
                job = s.get(Job, run.job_id) if run else None
                if review.key.startswith("repair:") and job:
                    # The repair agent's question is not a form field: no later field has its key. The navigator
                    # reads the owner's answer as this job's instruction on its next attempt.
                    directive = s.get(Setting, "application_directive:" + job.id)
                    instruction = {
                        "text": f"The applicant answered “{review.question[:600]}”: {answer[:600]}",
                        "at": now(),
                    }
                    if directive:
                        directive.value = instruction
                    else:
                        s.add(Setting(key="application_directive:" + job.id, value=instruction))
                # A file is kept in the owner's documents (owner_questions.save_document), never as a text answer;
                # an account choice answers this job's sign-in step only.
                learn = question_kind != "upload" and not (
                    review.key.startswith("repair:") and REPAIR_ACCOUNT.search(review.question)
                )
                row_scoped = bool(context.get("history_kind"))
                item = s.get(Setting, "profile")
                value = dict(item.value if item else {})
                if learn and not row_scoped:
                    # One answer per field identity. A history row's "End Date" shares its identity with every
                    # other row's, so a row's answer is kept only with its row, in the reviewed answers.
                    value["approved_answers"] = {**value.get("approved_answers", {}), review.key: answer}
                saved = (
                    learned_answer(
                        review.id,
                        oq.knowledge_question(review.question, context),
                        answer,
                        offered or review.options,
                        job.company or job.url,
                    )
                    if job
                    else None
                )
                if learn and saved:
                    value["reviewed_answers"] = [*value.get("reviewed_answers", []), saved]
                if item:
                    item.value = value
                else:
                    s.add(Setting(key="profile", value=value))
                if job and saved:
                    scope = saved["scope"]
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
                        if row_scoped and (
                            other.run_id == review.run_id
                            or (other.context or {}).get("history_identity") != context.get("history_identity")
                        ):
                            continue  # Another row's "End Date" is a different question.
                        fits = answer in other.options or oq.free_text_allowed(other.options, other.context)
                        if fits and (
                            scope == "personal" or employer.company.casefold() == job.company.casefold()
                        ):
                            other.answer = answer
                            enqueue(s, "fact_resume", other.id, {"review_id": other.id}, run_id=owner.id)
        message = (
            "Saved. I'll attach this file whenever an application asks for it."
            if question_kind == "upload"
            else "Saved. I'll reuse this answer when another application asks."
        )
        return {"resolved": True, "message": message}

    async def resolve_review(self, review_id, answer, *, document=False, settle=True):
        result = await self.resume_review(review_id, self.answer_review(review_id, answer, document=document))
        if settle and not document:
            # The same question waiting on other jobs (other forms, other keys) takes this answer too.
            with self.db.session() as s:
                review = s.get(Review, review_id)
                run = s.get(Run, review.run_id) if review else None
                job = s.get(Job, run.job_id) if run else None
                question, employer = (review.question, (job.company or "") if job else "") if review else ("", "")
            if question:
                try:
                    await self.settle_open_questions(only=(question, employer))
                except Exception:
                    pass  # The owner's answer is saved; the others are still asked as before.
        return result

    async def settle_open_questions(self, only=None, documents_only=False):
        """Answer open questions the owner already answered for another job, worded the same. No model call.

        Owner 2026-10-23: "it cannot infer details or answers from my telegram answers". Three open "racial/ethnic
        background" questions and a "Graduation Date or Anticipated Graduation Date" had the owner's own answer to
        the identical question saved, yet waited: reuse went only to reviews with the same field key. `only` limits
        this to one question's wording (question, employer); `documents_only` to upload questions whose kind of
        document the owner has sent. Otherwise every open question is checked."""
        import asyncio

        from .. import owner_questions as oq
        from ..knowledge import KnowledgeBase, kb_norm

        def candidates():
            newer = aliased(Run)
            with self.db.session() as s:
                rows = s.execute(
                    select(Review.id, Review.run_id, Review.question, Review.options, Review.context, Review.reason,
                           Review.key, Job.company)
                    .join(Run, Review.run_id == Run.id)
                    .join(Job, Run.job_id == Job.id)
                    .where(
                        Review.answer.is_(None),
                        Run.state.in_(["waiting_answer", "needs_review"]),
                        Job.status != "archived",
                        ~select(newer.id).where(newer.job_id == Run.job_id, newer.created_at > Run.created_at).exists(),
                    )
                ).all()
            target = kb_norm(oq.tidy(only[0]), only[1]) if only else None
            kb = KnowledgeBase(self.db)
            found = []
            for rid, run_id, question, options, context, reason, key, company in rows:
                if key in {"manual", "session"}:
                    continue
                if oq.kind(question, options or [], context or {}, reason or "") == "upload":
                    # A document the owner sent answers every open question asking for that kind of document.
                    path = oq.document_for(self.db, self.config.data_dir, question, company or "")
                    if path is not None and only is None:
                        found.append((rid, run_id, question, "File: " + path.name, True))
                    continue
                if documents_only or (target is not None and kb_norm(oq.tidy(question), company or "") != target):
                    continue
                value = oq.reusable_answer(kb, question, options or [], context or {}, reason or "", company or "")
                if value is not None:
                    found.append((rid, run_id, question, value, False))
            return found

        settled = 0
        for rid, run_id, question, value, document in await asyncio.to_thread(candidates):
            try:
                await self.resolve_review(rid, value, document=document, settle=False)
            except ValueError:
                continue  # Answered meanwhile.
            self.db.event(run_id, "answer_reused", "Used the answer you already gave to this question",
                          {"question": question[:200]})
            settled += 1
        return settled

    async def revalidate_open_questions(self):
        """Ask the current engine every open question again.

        Owner 2026-10-25: "only few of them are truly legit, everything else is just chaos". Questions were frozen when
        asked: ones created before a rule existed (a province in another country, pronouns on file, a site's search
        box) waited for days as if they still needed the owner. Each open question is now re-checked:
        - a residence question that shows the job is outside the applicant's locations closes the job;
        - a question the engine misread (owner_questions.defect) leaves the owner's list for browser recovery;
        - a question the profile now answers (resolver.local, no model call) is answered and the run resumes."""
        import asyncio

        from .. import owner_questions as oq
        from ..answers import Resolver, protected_status_missing, residence_outside
        from ..db import record
        from ..schemas import Profile

        def candidates():
            newer = aliased(Run)
            with self.db.session() as s:
                return s.execute(
                    select(Review.id, Review.run_id, Review.question, Review.options, Review.context, Review.reason,
                           Review.key, Run.packet, Job)
                    .join(Run, Review.run_id == Run.id)
                    .join(Job, Run.job_id == Job.id)
                    .where(
                        Review.answer.is_(None),
                        Run.state.in_(["waiting_answer", "needs_review"]),
                        Job.status != "archived",
                        ~select(newer.id).where(newer.job_id == Run.job_id, newer.created_at > Run.created_at).exists(),
                    )
                ).all()

        outcome = {"closed": 0, "recovered": 0, "answered": 0}
        moved = set()
        for rid, run_id, question, options, context, reason, key, packet, job in await asyncio.to_thread(candidates):
            if run_id in moved or key in {"manual", "session"}:
                continue
            options, context, profile = options or [], context or {}, (packet or {}).get("profile") or {}
            outside = residence_outside(question, options, profile.get("facts"), profile.get("location", ""))
            closing = (
                f"The employer hires only in its listed locations, and the applicant lives in {outside}"
                if outside
                else "The role is open only to U.S. citizens, permanent residents, refugees or asylees"
                if protected_status_missing(question, options, profile.get("facts"))
                else None
            )
            problem = None if closing else oq.defect(question, options, context, reason or "")
            if closing or problem:
                state, why = (
                    ("closed", closing)
                    if closing
                    else ("waiting_browser", f"The engine misread a field ({problem}): {oq.tidy(question)[:120]}")
                )
                with self.db.exclusive() as s:
                    run = s.get(Run, run_id)
                    if run and run.state in {"waiting_answer", "needs_review"}:
                        run.state, run.reason = state, why
                        moved.add(run_id)
                        outcome["closed" if closing else "recovered"] += 1
                self.db.event(run_id, "question_revalidated", why[:300], {"question": oq.tidy(question)[:200]})
                continue
            try:
                resolver = Resolver(
                    Profile.model_validate(profile), self.db, self.config, run_id, job.company or job.url, record(job)
                )
                field = {
                    "id": rid,
                    "label": oq.tidy(question),
                    "type": context.get("type") or ("select" if oq.choices(options) else "text"),
                    "options": options,
                    "required": True,
                    "section": context.get("section", ""),
                    "group": context.get("group", ""),
                    "value": "",
                    "valid": True,
                }
                answer = await asyncio.to_thread(resolver.local, field)
            except Exception:
                continue  # The question stays asked.
            if answer is not None and answer.disposition == "answer" and str(answer.value or "").strip():
                try:
                    await self.resolve_review(rid, str(answer.value), settle=False)
                except ValueError:
                    continue
                self.db.event(run_id, "answer_inferred", "Answered an open question from the saved profile",
                              {"question": oq.tidy(question)[:200], "evidence": list(answer.evidence_ids or [])[:4]})
                outcome["answered"] += 1
        return outcome

    async def resolve_review_file(self, review_id, filename, content):
        """Answer an upload question with the owner's file (Telegram document or photo, or the web app).

        Owner 2026-10-23: "cannot even get the intent of the field, i.e. if it is uploading something". The file is
        kept in the owner's documents; the next attempt, and later forms asking for that document, attach it."""
        from .. import owner_questions as oq

        with self.db.session() as s:
            review = s.get(Review, review_id)
            if not review:
                raise ValueError("That question no longer exists.")
            if review.answer is not None:
                raise ValueError("That question is already answered.")
            if oq.kind(review.question, review.options, review.context or {}, review.reason) != "upload":
                raise ValueError("This question needs a typed answer, not a file.")
            run = s.get(Run, review.run_id)
            job = s.get(Job, run.job_id) if run else None
            question, employer = review.question, (job.company or "") if job else ""
        entry = oq.save_document(self.db, self.config.data_dir, question, filename, content, employer)
        result = await self.resolve_review(review_id, "File: " + entry["name"], document=True)
        try:
            # Other open questions asking for this kind of document (eight transcript questions for one employer).
            await self.settle_open_questions(documents_only=True)
        except Exception:
            pass
        return result

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
        # Owner 2026-10-23: "a lot of error and debugging texts from telegram". These sentences reach the owner;
        # "Application remains protected by its queue guard" and raw queue errors did too.
        saved = result["message"].split(". ")[0] + "."
        try:
            resumed = await self.queue(job_id, mode, resume_id, retry=True, batch_id=batch_id)
        except QueueBlocked as exc:
            if durable_delivery and exc.outcome == "deferred":
                raise  # Keep the correlated outbox event until its temporary gate clears.
            result["message"] = saved + (
                " The application continues on its own as soon as it can."
                if exc.outcome == "deferred"
                else " This application is on hold, so it won't restart right now."
            )
        except ValueError:
            if durable_delivery:
                raise
            result["message"] = saved + " The application will continue on its own when it can."
        else:
            result.update(run_id=resumed["id"], message="Saved. The application is continuing.")
        return result
