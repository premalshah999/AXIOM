"""Recording a run's outcome: `complete`, validation retries, traces and owner reconciliation."""

from datetime import UTC, datetime, timedelta
import re

from sqlalchemy import select

from ..db import Event, Job, MailChallenge, Review, Run, Setting, now
from ..run_trace import phase_timing
from .states import TERMINAL

# Parked states an application can be in when the owner finishes it by hand.
OWNER_FINISHABLE = {"waiting_browser", "waiting_email", "waiting_answer", "needs_review", "timed_out", "paused"}


class CompletionMixin:
    def authorize_validation_retry(self, run_id, rejection):
        """Keep one submission reservation; retries need a recorded, explicit field rejection."""
        if rejection.get("category") != "validation" or not rejection.get("missing_fields"):
            raise ValueError("No explicit missing-field rejection permits a retry")
        with self.db.exclusive() as s:
            run = s.get(Run, run_id)
            control = s.get(Setting, "control").value
            if not run or run.state not in {"submitting", "running"} or run.mode != "submit":
                raise ValueError("This run has no authorized submission reservation")
            if run.state == "running":
                # The refused commit gave its reservation back (release_submission); the repaired resubmit takes it
                # again. Westinghouse (2026-10-27) ended "no authorized submission reservation" after a field refusal.
                from zoneinfo import ZoneInfo

                from ..db import Budget

                run.state = "submitting"
                day = datetime.now(ZoneInfo(self.config.timezone)).date().isoformat()
                if budget := s.get(Budget, day):
                    budget.submissions += 1
            from ..job_control import require_running

            require_running(s, run.job_id)
            if control.get("paused") or (not s.get(Job, run.job_id).demo and not control.get("auto_submit")):
                raise ValueError("Application submission is paused or disabled")
            key = "validation_retry:" + run_id
            from ..application_budget import require_allowance

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
            from ..sensitive_ids import sensitive_field

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
                from ..answers import protected_status_missing, residence_outside

                profile = run.packet.get("profile") or {}
                outside = next(
                    (
                        where
                        for q in result["reviews"]
                        if (
                            where := residence_outside(
                                q.get("question", ""),
                                q.get("options") or [],
                                profile.get("facts"),
                                profile.get("location", ""),
                            )
                        )
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
                elif any(
                    protected_status_missing(q.get("question", ""), q.get("options") or [], profile.get("facts"))
                    for q in result["reviews"]
                ):
                    state = "closed"
                    result = {
                        "state": "closed",
                        "reason": "The role is open only to U.S. citizens, permanent residents, refugees or asylees",
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
            from .. import host_health

            if overloaded := host_health.host_overloaded_result(
                result, run_state=run.state, started_at=run.started_at
            ):
                # The clock ran out while the host's event loop was running late (host_health): not the form's fault
                # and nothing was sent, so this start is free and the retry waits for the host to recover.
                result = overloaded
            host_health.note_browser(
                s, state=state, reason=result.get("reason", ""), started_at=run.started_at
            )
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
            api_proof = (
                evidence.get("type") == "application_created"
                and int(evidence.get("status") or 0) in {200, 201}
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
                website_proof or api_proof or email_proof or evidence.get("type") == "user_reconciled"
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
            if state == "waiting_browser" and result.get("waiting_kind") == "email":
                # A run parked on a durable email wait is waiting, not broken: the matched email resumes it in a
                # continuation run. It used to share "waiting_browser" with "a control needs repair" and was
                # counted, reported and messaged as a failure (266 runs, 143 jobs; docs/forensics, F9).
                state = "waiting_email"
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
            from ..recovery import schedule_recovery

            from ..application_slots import release_slot

            release_slot(s, run)
            schedule_recovery(s, self.config, run, result)
            from ..recovery import open_blocked_site_circuit

            open_blocked_site_circuit(s, run, state)
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
                from ..owner_questions import context_from

                s.add(
                    Review(
                        run_id=run_id,
                        question=q["question"][:8000],
                        options=q.get("options", []),
                        key=q.get("key", "manual"),
                        reason=q.get("reason", ""),
                        context=context_from(q),
                    )
                )
            from ..outbox import enqueue

            if self.config.failure_reports_enabled and state not in {
                "confirmed",
                "dry_run_passed",
                "already_applied",
                "closed",
                "cancelled",
                "paused",
                "waiting_email",  # parked, not failed
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
            from ..telegram_workers import any_configured

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

    def reconcile(self, run_id, submitted, evidence, *, submitted_by_owner=False, final_page=False):
        if not evidence.strip():
            raise ValueError("Provide confirmation evidence or an explanation of non-submission")
        with self.db.exclusive() as s:
            run = s.get(Run, run_id)
            # A run that started a commit and was later parked can still be confirmed by the
            # owner (Dayforce showed its confirmation after the watch window, 2026-10-16).
            committed = bool(
                run
                and run.state in {"needs_review", "waiting_browser", "waiting_email"}
                and s.scalar(
                    select(Event.id).where(Event.run_id == run_id, Event.kind == "submitting").limit(1)
                )
            )
            if submitted_by_owner:
                # The owner finished a parked application in the browser (Schwab 2026-10-21: the run parked on its
                # profile page; the owner submitted and saw "Your application was submitted successfully"). That is the
                # owner's statement with its evidence, never inferred from the run; a run still active cannot take it.
                if not run or run.state not in OWNER_FINISHABLE or not submitted:
                    raise ValueError("Only a parked application the owner submitted can be recorded this way")
                run.receipt = {"type": "owner_submitted", "evidence": evidence[:4000], "at": now()}
                run.reason = "Submitted by the applicant in the browser"
            elif final_page:
                # The run's own final page acknowledges the application although the run parked (Sopra Steria,
                # SmartRecruiters 2026-10-26: "Bewerbung eingereicht!" after "Absenden" was clicked as a step).
                if not run or run.state not in OWNER_FINISHABLE | {"submission_unknown"} or not submitted:
                    raise ValueError("Only a parked run whose final page shows the acknowledgement can be recorded")
                run.receipt = {"type": "final_page_confirmation", "evidence": evidence[:4000], "at": now()}
                run.reason = "The employer's final page acknowledged the application"
            elif not run or (run.state not in {"submission_unknown", "already_applied"} and not committed):
                raise ValueError("Only uncertain submissions or already-applied notices can be reconciled")
            else:
                run.receipt = {"type": "user_reconciled", "evidence": evidence[:4000], "at": now()}
                run.reason = "Submission reconciled by the applicant"
            run.state = "confirmed" if submitted else "needs_review"
            s.get(Job, run.job_id).status = run.state
        self.db.event(run_id, "reconciled", "Applicant reconciled submission", {"submitted": submitted})
