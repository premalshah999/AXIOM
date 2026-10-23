"""Repair agent: when an application fails, read its evidence, diagnose it with the navigator model (DeepSeek),
apply a guarded fix as data and retry, so failures do not wait for a person to read the logs.

It runs only after a repairable failure and at most MAX_DAILY times per job per day. Its fixes are data, never
code: an answer override for one field of this job, an instruction for the browser agent's next attempt, a
question for the owner (Telegram, with the field's options), or the outcome of an uncertain submission read from
the final page. Every proposed value is checked before use: offered options or values from the applicant's own
saved answers and profile only; passwords, identity numbers, legal disclosures and demographics are never set.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from .db import Event, Job, Review, Run, now

MAX_DAILY = 2
REPAIRABLE = {"waiting_browser", "needs_review", "submission_unknown", "failed", "timed_out"}
# Failures a page-level fix cannot change: they stay with their own flows (owner, CAPTCHA, mail, caps).
NOT_REPAIRABLE = re.compile(
    r"captcha|security check|\bsms\b|employer already has|model budget|archiv|social security|identification number|"
    r"already applied|posting is closed|no longer available|verification email|verification code|identity conflict|"
    r"person not found|provider is unavailable|paused|cancel|"
    # The app's own waits: the recovery scheduler retries these once the sender, browser or worker is back.
    # Honeywell (2026-10-18): this agent read a sender wait as a missing email address and asked the owner.
    r"email sender busy|open the applypilot chrome|browser tab closed unexpectedly|worker interrupted|"
    r"tab initialization failed|chrome debugging endpoint|worker host was overloaded",
    re.I,
)
# The applicant's email address and phone number are always saved (masked as [email] and 9s in the evidence).
CONTACT_QUESTION = re.compile(
    r"\b(?:what|which)\b[^?]{0,80}\b(?:e-?mail(?: address)?|phone(?: number)?|mobile(?: number)?)\b", re.I
)
# Account creation, sign-in and the account password are the app's (owner, 2026-10-12: "any account creation OK"):
# the agent asked which password to use for Dayforce and whether to create a TikTok account (2026-10-23).
ACCOUNT_QUESTION = re.compile(
    r"\b(?:create|creating|register|registering|sign(?:ing)? up)\b[^?]{0,60}\baccount\b|\bpassword\b|"
    r"\bexisting account\b|\bsign(?:ing)? in\b[^?]{0,40}\baccount\b",
    re.I,
)
# Owner 2026-10-23: "a lot of error and debugging texts from telegram". Ten of the twenty questions this agent sent in
# three days asked for an employer-account password (8 for one SuccessFactors tenant, each worded differently, so
# none was ever recognised as already asked); Telegram itself refuses to keep a password. Others described the app
# ("The dropdown options couldn't be read, so I can't map your saved answers to exact choices"). An owner question is
# one plain question about the applicant. Credentials in the question or its options belong to the account flow.
CREDENTIAL_QUESTION = re.compile(
    r"password|passcode|credential|sign[ -]?in details|login details|user ?name for|security answer", re.I
)
INTERNAL_QUESTION = re.compile(
    r"dropdown|combobox|option text|couldn.?t be read|could not be read|selector|the (?:agent|browser|app|engine)\b|"
    r"\bfield (?:label|id)\b|validation|stalled|\bretry\b|\bthe run\b|saved answers? to exact",
    re.I,
)
# The evidence's masks for the applicant's email and numbers; never an answer. mask() shows a phone number or long id
# with every digit as 9 and the format kept, so a run of seven or more nines is the mask too: the agent saved
# "9999999999" as a Greenhouse Phone override (Stripe, 2026-10-18).
MASKED_VALUE = re.compile(
    r"\[(?:email|number|phone|private|redacted)\]|(?<!\d)\+?(?:9[ ()+.-]*){7,}(?!\d)", re.I
)
SENSITIVE = re.compile(
    r"password|passcode|\bssn\b|social security|passport|driver.?s? licen[cs]e|national id|tax id|\bbank\b|"
    r"routing|account number|credit card|one.?time|verification code|security answer|date of birth|birth ?date",
    re.I,
)
CONFIRMATION = re.compile(
    r"thank you for (?:your )?(?:application|applying)|application (?:has been |was )?(?:successfully )?"
    r"(?:submitted|received)|we(?:'ve| have)? received your application|your application is complete",
    re.I,
)
NOISE = {"model_call", "phase_timing", "mail_poll", "widget_snapshot", "queued", "field_timing", "jev_choices"}

INSTRUCTIONS = """You repair failed job applications for an automated applicant. You receive the failed run's evidence:
the page's fields (label, type, required, whether filled, error, offered options, the answer the app used), the
visible error messages, the last browser actions, the run's outcome, the final page outline and the applicant's
saved profile values. Find the concrete cause and the smallest fix.

- field_fixes: the field label exactly as shown and the exact value to type, or an offered option verbatim.
  Values must come from the applicant's saved answers or profile values given here; reformatting them is fine
  (3.9 from 3.9/4.0, a phone number written in the format the page shows). Never invent personal facts, legal
  disclosures, demographics, passwords or identity numbers.
- owner_question: only when a personal fact is genuinely missing; ask it as the form asks it, in one plain
  sentence, and give the field's offered options. The applicant's email address and phone number are always saved
  (shown masked as [email] and 9s); never ask for them. Never ask about accounts: the app creates employer accounts
  and manages their passwords itself. Never describe the app, the browser or the page's controls to the applicant.
- navigator_instruction: one or two sentences for the browser agent's next attempt (what to fill or click,
  what to stop repeating).
- submission: for an uncertain submission, "confirmed" only when the final page outline shows an explicit
  acknowledgement (quote it in evidence_quote); "not_submitted" only when it still shows the unsent form;
  otherwise "unknown".
- retry: true when your fix makes another attempt likely to succeed. A CAPTCHA, a closed posting, an account
  conflict or a site outage is not fixable here: name it in category and set retry false."""


class FieldFix(BaseModel):
    label: str = Field(max_length=200)
    value: str = Field(max_length=500)
    reason: str = Field(default="", max_length=300)


class OwnerQuestion(BaseModel):
    question: str = Field(max_length=500)
    options: list[str] = Field(default_factory=list, max_length=80)


class RepairPlan(BaseModel):
    diagnosis: str = Field(max_length=1500)
    category: Literal[
        "field_value", "missing_fact", "control", "navigation", "site", "account", "captcha", "closed",
        "submission", "unknown",
    ] = "unknown"
    submission: Literal["confirmed", "not_submitted", "unknown"] = "unknown"
    evidence_quote: str = Field(default="", max_length=400)
    field_fixes: list[FieldFix] = Field(default_factory=list, max_length=8)
    navigator_instruction: str = Field(default="", max_length=800)
    owner_question: OwnerQuestion | None = None
    retry: bool = False
    confidence: float = Field(default=0.5, ge=0, le=1)


def already_asked(db, job_id, question):
    """True when this job already has an open or answered owner question that says the same thing in other words."""
    from difflib import SequenceMatcher

    from .answers import normalize

    want = normalize(question)
    with db.session() as s:
        asked = list(
            s.scalars(
                select(Review.question)
                .join(Run, Review.run_id == Run.id)
                .where(Run.job_id == job_id, Review.key.like("repair:%"))
            )
        )
    for other in asked:
        theirs = normalize(other)
        mine, words = set(want.split()), set(theirs.split())
        overlap = len(mine & words) / max(1, len(mine | words))
        if overlap >= 0.6 or SequenceMatcher(None, want, theirs).ratio() >= 0.75:
            return True
    return False


def mask(text):
    """Emails hidden; digits of phone-like numbers shown as 9s so their format stays visible."""
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", str(text))
    return re.sub(r"\+?\d[\d ()+.-]{8,}\d", lambda m: re.sub(r"\d", "9", m[0]), text)


def label_key(label):
    from .answers import normalize

    return normalize(re.sub(r"[\s.*:]+$", "", str(label or "")))


def alnum(text):
    return re.sub(r"[^a-z0-9]", "", str(text).casefold())


def profile_values(profile):
    """Non-sensitive saved values the agent may propose (exact text, used for provenance checks)."""
    a = profile.address
    values = [a.city, a.state, a.postal_code, a.country, a.county, profile.first_name, profile.last_name]
    for e in profile.education:
        values += [e.school, e.degree, e.field_of_study, e.gpa, e.start, e.end]
    for w in profile.work:
        values += [w.company, w.title, w.location]
    for key, value in (profile.facts or {}).items():
        if isinstance(value, (str, int, float)) and not SENSITIVE.search(str(key)):
            values.append(str(value))
    return [str(v) for v in values if str(v or "").strip()]


def evidence(db, data_dir, run_id, profile):
    with db.session() as s:
        run = s.get(Run, run_id)
        job = s.get(Job, run.job_id)
        events = list(s.scalars(select(Event).where(Event.run_id == run_id).order_by(Event.id.desc()).limit(90)))
        outcome = {"state": run.state, "reason": mask(run.reason or "")[:600]}
        ats, company, title, job_id = job.ats, job.company, job.title, job.id
    timeline = []
    for e in reversed(events):
        if e.kind in NOISE:
            continue
        d = e.data or {}
        item = {"kind": e.kind, "message": mask(e.message)[:200]}
        if e.kind == "agent_step":
            item.update(action=d.get("action"), control=str((d.get("control") or {}).get("label", ""))[:80])
        elif e.kind == "verification":
            item["problems"] = [mask(p)[:200] for p in d.get("problems", [])][:8]
        elif e.kind == "stall_breaker":
            item["finding"] = mask(d.get("finding", ""))[:500]
        elif e.kind in {"field_error", "answer_validation"}:
            item.update({k: mask(d.get(k, ""))[:120] for k in ("field", "cause", "interaction", "proposed") if d.get(k)})
        elif e.kind in {"finished", "finish_refused", "account_unchanged", "submission_http_error"}:
            item["data"] = mask(json.dumps(d, default=str))[:600]
        timeline.append(item)
    folder = Path(data_dir) / "runs" / run_id
    answers = {}
    try:
        for entry in json.loads((folder / "answers.json").read_text()).values():
            label = entry.get("label", "")
            if label and not SENSITIVE.search(label):
                answers[label_key(label)] = mask(entry.get("answer", ""))[:200]
    except (OSError, ValueError):
        pass
    page = {}
    steps = sorted((folder / "steps").glob("*.json")) if (folder / "steps").exists() else []
    steps = [path for path in steps if not path.name.endswith(".form.json")]  # Frame records, not step summaries.
    if steps:
        try:
            last = json.loads(steps[-1].read_text())
            page = {
                "url_path": re.sub(r"\?.*", "", str(last.get("url", "")))[:200],
                "headings": [mask(h)[:160] for h in last.get("headings", [])][:8],
                "errors": [mask(x)[:400] for x in last.get("errors", [])][:6],
                "fields": [
                    {
                        "label": f.get("label", "")[:160],
                        "type": f.get("type"),
                        "required": f.get("required"),
                        "filled": f.get("filled"),
                        "error": mask(f.get("error", ""))[:200],
                        "options": [str(o)[:80] for o in (f.get("options") or [])][:30],
                        "answer_used": answers.get(label_key(f.get("label", "")), ""),
                    }
                    for f in last.get("fields", [])
                    if not SENSITIVE.search(f.get("label", ""))
                ][:60],
                "controls": [
                    str(c.get("label", "") if isinstance(c, dict) else c)[:60] for c in last.get("controls", [])
                ][:30],
                # Failed requests the page made before this step (a 4xx validation can show only as a toast).
                "network_failures": [
                    {k: e.get(k) for k in ("method", "path", "status", "message", "failed") if e.get(k) is not None}
                    for e in last.get("network", [])
                    if (e.get("status") or 0) >= 400 or e.get("failed")
                ][-10:],
            }
        except (OSError, ValueError):
            page = {}
    outline = ""
    try:
        outline = mask((folder / "final_outline.txt").read_text())[:6000]
    except OSError:
        pass
    return {
        "job": {"id": job_id, "ats": ats, "company": company, "title": title},
        "outcome": outcome,
        "timeline": timeline[-45:],
        "page": page,
        "final_page_outline": outline,
        "profile_values": [mask(v)[:120] for v in profile_values(profile)][:80],
    }


def check_fixes(plan, ev, profile):
    """Fixes that are safe to apply, and the rest with reasons. Never trusts a value it cannot trace."""
    from .answer_rules import similarity
    from .answers import DEMOGRAPHIC, critical_question

    fields = {label_key(f["label"]): f for f in ev.get("page", {}).get("fields", [])}
    known = profile_values(profile)
    accepted, rejected = [], []
    for fix in plan.field_fixes:
        key, value = label_key(fix.label), fix.value.strip()
        field = fields.get(key)
        if not value or SENSITIVE.search(fix.label):
            rejected.append((fix.label, "protected field"))
            continue
        if MASKED_VALUE.search(value):
            # The evidence masks the applicant's email and numbers, so "[email]" traced to the masked answer_used and was
            # saved as the answer; every later run typed "[email]" into the Email box (iPipeline, 2026-10-19).
            rejected.append((fix.label, "a masked placeholder is not an answer"))
            continue
        if critical_question(fix.label) or DEMOGRAPHIC.search(fix.label):
            rejected.append((fix.label, "legal or demographic answers come from the owner"))
            continue
        if field is None:
            rejected.append((fix.label, "no such field on the page"))
            continue
        used = field.get("answer_used", "")
        options = field.get("options") or []
        traced = (
            any(alnum(value) == alnum(v) or alnum(value) in alnum(v) for v in known if len(alnum(value)) >= 2)
            or (used and alnum(value) and alnum(value) in alnum(used))
            or (used and similarity(value, used) >= 0.6)
        )
        if options and value not in options:
            rejected.append((fix.label, "not an offered option"))
        elif not traced:
            rejected.append((fix.label, "value not found in the applicant's saved answers or profile"))
        else:
            accepted.append(fix)
    return accepted, rejected


def attempts_today(db, job_id):
    day = datetime.now(UTC).date().isoformat()
    record = db.get_setting("repair_attempts:" + job_id, {}) or {}
    return (record.get("count", 0) if record.get("day") == day else 0), day


def eligible(run_state, reason):
    return run_state in REPAIRABLE and not NOT_REPAIRABLE.search(reason or "")


async def repair_run(service, run_id):
    """Diagnose only while the application still has time and a retry available."""
    import asyncio
    from .admission import QueueBlocked
    from .application_budget import require_allowance

    with service.db.session() as s:
        run = s.get(Run, run_id)
        if not run:
            return {"skipped": "missing run"}
        try:
            _, remaining = require_allowance(s, run.job_id)
        except QueueBlocked as exc:
            return {"skipped": exc.code}
    try:
        # Uncapped production has no lifetime clock; a repair still gets a bounded window.
        async with asyncio.timeout(remaining if remaining is not None else 600):
            return await _repair_run(service, run_id)
    except TimeoutError:
        return {"skipped": "application_deadline"}


async def _repair_run(service, run_id):
    """Diagnose one failed run and apply a guarded repair. Returns a summary dict (also recorded as an event)."""
    from .models import effective_thinking, structured
    from .schemas import Profile

    db, config = service.db, service.config
    if not getattr(config, "repair_agent_enabled", True):
        return {"skipped": "disabled"}
    with db.session() as s:
        run = s.get(Run, run_id)
        if not run:
            return {"skipped": "missing run"}
        state, reason, job_id = run.state, run.reason or "", run.job_id
        newer = s.scalar(select(Run.id).where(Run.job_id == job_id, Run.created_at > run.created_at).limit(1))
        # Questions already sent to the owner are the repair; never ask twice or race their answer.
        asking = s.scalar(select(Review.id).where(Review.run_id == run_id, Review.answer.is_(None)).limit(1))
    if newer or asking or not eligible(state, reason):
        return {"skipped": "superseded" if newer else "waiting on the owner" if asking else "not repairable"}
    if state == "submission_unknown" and not (Path(config.data_dir) / "runs" / run_id / "final_outline.txt").exists():
        return {"skipped": "no final page to read"}
    count, day = attempts_today(db, job_id)
    if count >= MAX_DAILY:
        return {"skipped": "daily repair budget used"}
    db.set_setting("repair_attempts:" + job_id, {"day": day, "count": count + 1})
    profile = Profile.model_validate(db.get_setting("profile", {}))
    ev = evidence(db, config.data_dir, run_id, profile)
    try:
        plan = await structured(
            config,
            db,
            RepairPlan,
            INSTRUCTIONS,
            json.dumps(ev, default=str),
            run_id,
            3000,
            thinking=effective_thinking(db, config, "enabled"),
            timeout=90,
            retries=1,
        )
    except Exception as exc:  # A failed diagnosis leaves the run exactly as it was.
        db.event(run_id, "repair_agent", "Repair agent could not diagnose this failure", {"error": type(exc).__name__})
        return {"skipped": "model unavailable"}
    accepted, rejected = check_fixes(plan, ev, profile)
    applied = {"fixes": [f.model_dump() for f in accepted], "rejected": rejected, "instruction": "", "question": None}
    if accepted:
        overrides = dict(db.get_setting("repair_overrides:" + job_id, {}) or {})
        for fix in accepted:
            overrides[label_key(fix.label)] = {"label": fix.label, "value": fix.value, "run_id": run_id, "at": now()}
        db.set_setting("repair_overrides:" + job_id, overrides)
    if plan.navigator_instruction.strip():
        applied["instruction"] = plan.navigator_instruction.strip()[:800]
        db.set_setting("repair_directive:" + job_id, {"text": applied["instruction"], "run_id": run_id, "at": now()})
    outline = ev.get("final_page_outline", "")
    outcome = "unchanged"
    if state == "submission_unknown":
        quote = plan.evidence_quote.strip()
        if (
            plan.submission == "confirmed"
            and quote
            and alnum(quote) in alnum(outline)
            and CONFIRMATION.search(quote)
            and CONFIRMATION.search(outline)
        ):
            service.reconcile(run_id, True, "Repair agent read the final page: " + quote[:300])
            outcome = "confirmed"
        elif plan.submission == "not_submitted" and not CONFIRMATION.search(outline) and re.search(
            r"\bsubmit\b", outline, re.I
        ):
            service.reconcile(run_id, False, "Repair agent read the final page: the unsent form is still shown")
            outcome = "not_submitted"
    question = plan.owner_question
    if question and CONTACT_QUESTION.search(question.question):
        applied["rejected"] = [*applied["rejected"], (question.question[:200], "the applicant's contact details are saved")]
        question = None
    if question and ACCOUNT_QUESTION.search(question.question):
        applied["rejected"] = [
            *applied["rejected"],
            (question.question[:200], "accounts and their passwords are created and managed by the app"),
        ]
        question = None
    if question and CREDENTIAL_QUESTION.search(question.question + " " + " ".join(question.options)):
        applied["rejected"] = [*applied["rejected"], (question.question[:200], "credentials belong to the account flow")]
        question = None
    if question and INTERNAL_QUESTION.search(question.question):
        applied["rejected"] = [*applied["rejected"], (question.question[:200], "describes the app, not the applicant")]
        question = None
    if question and already_asked(db, job_id, question.question):
        applied["rejected"] = [*applied["rejected"], (question.question[:200], "the owner was already asked this")]
        question = None
    if question and question.question.strip() and outcome == "unchanged":
        key = "repair:" + hashlib.sha256(question.question.strip().casefold().encode()).hexdigest()[:24]
        with db.exclusive() as s:
            run = s.get(Run, run_id)
            exists = s.scalar(select(Review.id).where(Review.run_id == run_id, Review.key == key).limit(1))
            if run and not exists:
                s.add(
                    Review(
                        run_id=run_id,
                        question=question.question.strip()[:8000],
                        options=[o for o in question.options if o.strip()][:80],
                        key=key,
                        reason="Repair agent: " + plan.diagnosis[:300],
                        context={"source": "repair"},
                    )
                )
                run.state = "needs_review"
                s.get(Job, job_id).status = "needs_review"
                from .outbox import enqueue

                enqueue(s, "notify", "repair:" + run_id, run_id=run_id)
        applied["question"] = question.model_dump()
        outcome = "asked_owner"
    retried = None
    if outcome in {"unchanged", "not_submitted"} and plan.retry and (accepted or applied["instruction"]):
        recovery = db.get_setting("draft_recovery:" + job_id, {}) or {}
        with db.session() as s:
            reasons = [
                r.reason or ""
                for r in s.scalars(select(Run).where(Run.job_id == job_id).order_by(Run.created_at.desc()).limit(2))
            ]
        if recovery.get("scheduled") and recovery.get("source_run_id") == run_id:
            # Charles Schwab (iCIMS, 2026-10-17): this agent and the recovery scheduler both retried one
            # deterministic failure, five runs in five minutes. The scheduled retry already reads this fix.
            retried, outcome = "left to the scheduled recovery", "fix_applied"
        elif len(reasons) == 2 and reasons[0] and reasons[0] == reasons[1]:
            retried, outcome = "identical failure repeated; not retried", "stopped"
        else:
            from .job_control import manage

            try:
                result = await manage(service, job_id, "resume", actor="repair")
                retried = result.get("run_id")
                outcome = "retried"
            except ValueError as exc:
                retried = "blocked: " + str(exc)[:160]
    summary = {
        "category": plan.category,
        "diagnosis": plan.diagnosis[:1500],
        "confidence": plan.confidence,
        "outcome": outcome,
        "retry_run_id": retried,
        **applied,
    }
    db.event(run_id, "repair_agent", "Repair agent: " + plan.diagnosis[:180], summary)
    return summary
