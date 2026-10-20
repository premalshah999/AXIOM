"""Small diagnostic summaries. Never feed screenshots, profile values or secrets to a model."""

import re

from pydantic import BaseModel, Field
from sqlalchemy import select

from .db import Event, Job, Run, now


class Explanation(BaseModel):
    summary: str = Field(max_length=500)
    likely_cause: str = Field(max_length=700)
    next_steps: list[str] = Field(max_length=4)


def clean(text):
    text = re.sub(r"https?://\S+", "[URL omitted]", str(text))
    text = re.sub(r"[\w.+-]+@[\w.-]+", "[email omitted]", text)
    text = re.sub(
        r"(?i)\b(?:password|api[_ -]?key|app[_ -]?token|otp|verification code)\s*[:=]\s*\S+",
        "[secret omitted]",
        text,
    )
    text = re.sub(r"\b(?:\d[ -]?){6,}\b", "[number omitted]", text)
    return text[:400]


async def create_report(service, run_id, diagnostics=None):
    db, config = service.db, service.config
    if not config.failure_reports_enabled or db.get_setting("failure_report:" + run_id):
        return
    with db.session() as s:
        run = s.get(Run, run_id)
        if not run or run.state not in {
            "failed",
            "timed_out",
            "skipped",
            "needs_review",
            "waiting_answer",
            "waiting_browser",
            "submission_unknown",
        }:
            return
        job = s.get(Job, run.job_id)
        events = list(
            s.scalars(select(Event).where(Event.run_id == run_id).order_by(Event.id.desc()).limit(20))
        )
        fields, errors, stages, diagnostics_events = [], [], [], []
        for event in reversed(events):
            data = event.data or {}
            if event.kind == "verification":
                fields += [
                    clean(q.get("question", ""))
                    for q in data.get("pending", [])
                    if not re.search(r"password|code|token|secret", q.get("question", ""), re.I)
                ]
            if event.kind == "field_error":
                errors.append(
                    {
                        "field": clean(event.message),
                        "error_type": data.get("error_type", "unknown"),
                        "interaction": data.get("interaction", "unclassified"),
                    }
                )
            if event.kind == "account_error":
                diagnostics_events.append(
                    {
                        "kind": event.kind,
                        "message": clean(event.message),
                        "error_type": data.get("error_type"),
                        "function": data.get("function"),
                        "line": data.get("line"),
                    }
                )
            if event.kind in {
                "captcha",
                "submission_http_error",
                "captcha_verified_precommit",
                "site_response",
            }:
                diagnostics_events.append({"kind": event.kind, "message": clean(event.message)})
            if event.kind in {
                "submitting",
                "validation_repair",
                "browser_retry",
                "browser_recovery",
                "captcha",
                "account",
                "step",
            }:
                stages.append(event.kind)
        receipt = run.receipt or {}
        recovery = db.get_setting("draft_recovery:" + run.job_id, {})
        fields += [clean(q) for q in receipt.get("missing_fields", [])]
        evidence = {
            "run_id": run.id,
            "ats": job.ats,
            "employer": job.company,
            "state": run.state,
            "reason": clean(run.reason),
            "blocker_type": (run.packet or {}).get("blocker_type"),
            "recovery_cause": (run.packet or {}).get("recovery_cause"),
            "elapsed_seconds": round(run.elapsed, 2),
            "model_calls": run.model_calls,
            "application_cost_usd": run.cost,
            "missing_fields": list(dict.fromkeys(fields))[:15],
            "field_errors": errors[-6:],
            "stages": stages[-10:],
            "diagnostics": diagnostics_events[-4:],
            "receipt_type": receipt.get("type"),
            "website_confirmed": bool(receipt.get("website_confirmed")),
            "email_confirmed": bool(receipt.get("email_confirmed")),
            "code_locations": (diagnostics or {}).get("locations", []),
            "exception": (diagnostics or {}).get("exception"),
            "automatic_recovery": {k: recovery.get(k) for k in ("status", "cause", "attempts", "not_before")}
            if recovery.get("source_run_id") == run_id
            else {},
        }
    explanation = Explanation(
        summary=evidence["reason"],
        likely_cause="The recorded evidence does not establish a more specific cause.",
        next_steps=["Check the listed fields and browser trace before retrying."],
    )
    if evidence["state"] == "submission_unknown":
        explanation.next_steps = ["Reconcile the employer portal and confirmation email before any retry."]
    elif evidence["recovery_cause"] == "provider_unavailable":
        explanation.likely_cause = "The selected answer provider could not complete a required request."
        explanation.next_steps = ["Check the selected model's availability before resuming the saved draft."]
    elif evidence["automatic_recovery"].get("status") == "scheduled":
        explanation.next_steps = [
            "The app will retry this saved draft automatically after its backoff; other jobs continue."
        ]
    with db.session() as s:
        updated = s.get(Run, run_id)
        report_cost = max(0, updated.cost - evidence["application_cost_usd"]) if updated else 0
    db.set_setting(
        "failure_report:" + run_id,
        {
            **explanation.model_dump(),
            "source": "recorded_evidence",
            "created_at": now(),
            "evidence": evidence,
            "report_cost_usd": report_cost,
        },
    )
