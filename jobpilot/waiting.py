"""Why nothing is starting, once, for every screen (owner, 2026-11-02: friends press Start and see idle workers with a full queue).

Overview, the sidebar, Queue and Review a batch read this one answer: what holds new applications right now, the worst true thing
first, in plain words, and what the person can do about it. It reads the same switches the dispatcher reads (autopilot.tick and
service.queueing._admit) and the Queue's own plan, so it can't promise a start the dispatcher would refuse. Nothing here starts,
changes or decides anything.

`blocking`: nothing in line can start until this changes. Not blocking: some applications run, and free workers can't take the rest.
`row` is the short form the Queue shows beside each job this holds.
"""

import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

POWER_SECONDS = 60
_power = {"at": -POWER_SECONDS, "value": {}}
SETTINGS_APPLYING = {"label": "Open Settings", "view": "settings", "tab": "applying"}
SETTINGS_KEY = {"label": "Open Settings", "view": "settings", "tab": "connections"}
REVIEW = {"label": "Review a batch", "view": "review"}
BOARD = {"label": "Open the Job board", "view": "board"}


def _plural(count, word):
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _reason(code, title, says, then="", row="", action=None, blocking=True):
    return {"code": code, "title": title, "says": says, "next": then, "row": row or says, "action": action, "blocking": blocking}


def today(config):
    return datetime.now(ZoneInfo(config.timezone)).date().isoformat()


def sent_today(session, config):
    """Applications that have gone out today, the number `reserve_submission` stops at."""
    from .db import Budget

    budget = session.get(Budget, today(config))
    return budget.submissions if budget else 0


def finished_today(batch, config):
    """A batch that reached its goal says so on that day. The next morning it is simply over: "Press Start tomorrow" read for ever."""
    if batch.get("state") != "complete":
        return False
    day = batch.get("finished_day")
    if not day and batch.get("finished_at"):
        try:
            day = datetime.fromisoformat(batch["finished_at"]).astimezone(ZoneInfo(config.timezone)).date().isoformat()
        except ValueError:
            day = ""
    return not day or day == today(config)


def _global_hold(service, control, in_line):
    """What stops every application whatever is in line, or None. Sending off holds only what is in line: it's how a new install begins, and
    with nothing queued the thing to do is to queue something (Queue only or Start ask about sending at that moment)."""
    from .agent_settings import for_role, missing_key_message
    from .provider_health import dispatch_gate

    db, config = service.db, service.config
    if control.get("paused"):
        return _reason(
            "stopped", "Stopped", "Nothing starts until you press Resume. Progress is kept.",
            "Press Resume to carry on.", row="You pressed Stop. It starts after you press Resume.",
        )
    if not service.dispatch:
        return _reason(
            "workers_off", "Applying is off", "Applying is turned off in this installation's configuration, so nothing can start.",
            "Enable workers and restart.", row="Applying is off in this installation.",
        )
    for role in ("answers", "navigator"):
        chosen = for_role(db, config, role)
        if not chosen.llm_api_key:
            return _reason(
                "no_key", "No AI key", f"AXIOM can't read job pages or write answers without an AI key ({chosen.llm_provider}).",
                "Add your key in Settings, Connections.",
                row=missing_key_message(chosen.llm_provider)[:120], action=SETTINGS_KEY,
            )
    gate = dispatch_gate(db, config)
    if not gate.limit:
        words = (gate.reason or "New applications can't start right now").rstrip(".") + "."
        if gate.code == "provider_unavailable":
            return _reason("provider", "The AI service isn't answering", words, "It tries again by itself. If your account is out of credit, add credit with your AI provider.", row="The AI service is not answering.", action=SETTINGS_KEY)
        if gate.code == "ai_budget":
            return _reason("ai_budget", "AI spending limit reached", words.rstrip(".") + ", so nothing new starts until midnight.", "To carry on today, raise AI spending per day in Settings, Applying.", row="Today's AI spending limit is used. It starts after midnight, or if you raise the limit.", action=SETTINGS_APPLYING)
        if gate.code == "disk_low":
            return _reason("disk_low", "The disk is nearly full", words, "Free up space on this Mac. AXIOM carries on by itself.", row="The disk is nearly full.")
        if gate.code == "browser_unavailable":
            return _reason("browser", "Chrome isn't opening", words, "It tries again by itself. If this keeps up, quit and reopen AXIOM.", row="Chrome isn't opening pages.")
        return _reason(gate.code or "held", "Waiting to start", words, "It carries on by itself.")
    if not control.get("auto_submit") and in_line:
        return _reason(
            "sending_off", "Sending is off", "Sending is off in Settings, so nothing starts and nothing goes to an employer.",
            "Turn on Send applications in Settings, Applying.", row="Sending is off in Settings.", action=SETTINGS_APPLYING,
        )
    hold = db.get_setting("execution_v2_deployment_hold", {}) or {}
    if hold.get("stage") in {"draining", "canary"}:
        return _reason("update_hold", "Held for an update", "New applications wait while an update is checked.", "They start by themselves afterwards.", row="Waiting for an update to finish.")
    return None


def _limit_hold(service, batch, automatic, active):
    """Today's limit or goal: used up, or as good as, by what is running now."""
    db, config = service.db, service.config
    limit = config.daily_application_limit
    with db.session() as s:
        used = sent_today(s, config)
    if used >= limit:
        return _reason(
            "daily_limit", "Daily limit reached", f"{used:,} of your {limit:,} applications a day have gone out, so nothing new starts until midnight.",
            "To send more today, raise the daily limit.", row="Today's limit is used. It starts after midnight, or if you raise the limit.", action=SETTINGS_APPLYING,
        )
    if finished_today(batch, config):
        target = batch.get("target", limit)
        return _reason(
            "goal_reached", "Daily limit reached", f"{_plural(target, 'application')} confirmed today, the number you set, so AXIOM stopped starting jobs by itself.",
            "Press Start tomorrow to apply again, or raise the number and press Start now.", row="Today's goal is reached. Only jobs you queue start now.",
            blocking=False,
        )
    if active:
        left = limit - used
        if automatic:
            from .autopilot import confirmed_since, counting_from

            with db.session() as s:
                confirmed = len(confirmed_since(s, batch.get("started_at") and counting_from(batch.get("started_at"), config.timezone)))
            left = min(left, batch.get("target", limit) - confirmed)
        # The dispatcher's own sum (tick): what is running might all go out, and the limit is never overrun.
        if left - active <= 0:
            return _reason(
                "goal_close", "Finishing today's last applications", f"{_plural(active, 'application')} under way could use up today's limit, so nothing more starts unless one of them doesn't send.",
                "To send more today, raise the daily limit.", row="Waiting: what is running could use up today's limit.", action=SETTINGS_APPLYING,
            )
    return None


def _on_platforms(paced_by):
    """What each platform allows, from the numbers admission uses (application_pacing)."""
    from .startable import gap_words

    if len(paced_by) == 1:
        one = paced_by[0]
        if one["gap"]:
            return f"{one['name']}, which takes one start every {gap_words(one['gap'])}"
        count = one["limit"] or 1
        return f"{one['name']}, which takes {'one application' if count == 1 else f'{count} applications'} at a time"
    named = [f"{p['name']} ({p['count']:,})" for p in paced_by[:3]]
    rest = len(paced_by) - len(named)
    return ", ".join(named) + (f" and {rest} more" if rest else "") + ", which limit how fast they take applications"


def _next_start(value, active):
    from .startable import span_words

    if value.get("startable_now"):
        return "Next start within a few seconds."
    if value.get("next_due"):
        try:
            return f"Next start in {span_words(datetime.fromisoformat(value['next_due']).timestamp() - time.time())}."
        except ValueError:
            pass
    return "Next start when the one under way ends." if active else ""


def _line_hold(service, value, automatic, active):
    """Workers are free, but nothing in line can take one: what the line itself is waiting on."""
    if value is None:
        return None
    from .startable import reading

    total = len(value["next"])
    if not total:
        if active:
            return None
        if automatic:
            said = reading(service).peek() or {}
            return _reason("nothing_ready", "Nothing to run right now", said.get("idle") or "Nothing is ready to apply to yet. AXIOM is still finding and checking jobs.", "It starts by itself when a job is ready.", action=BOARD)
        return _reason(
            "nothing_in_line", "Not applying", "Nothing is in line.",
            "Review a batch to choose what goes next, or press Start to let AXIOM choose.", action=REVIEW,
        )
    # Regular workers no job in line can use right now. A worker for company sites or hard retries never takes a regular new job.
    idle = value.get("rooms", {}).get("normal", 0) - value.get("startable_now", 0)
    if idle <= 0:
        return None  # What can start fills what is free, or every regular worker is busy: the next job takes the next free one.
    paced, ready_now = value.get("paced", 0), value.get("startable_now", 0)
    if paced:
        workers = "1 worker is free" if idle == 1 else f"{idle:,} workers are free"
        if paced + ready_now < total:
            jobs = f"{paced:,} of the {total:,} jobs in line are on"
        else:
            jobs = "the next job is on" if paced == 1 else f"the next {paced:,} jobs are all on"
        return _reason(
            "paced", "Waiting on the job sites' pace",
            f"{workers} because {jobs} {_on_platforms(value.get('paced_by', []))}. {_next_start(value, active)}".strip(),
            "To use them now, queue jobs on other platforms from the Job board.",
            action=BOARD, blocking=not active and not ready_now,
        )
    if ready_now:
        return None  # A short line: what is in it starts, and there just isn't more.
    workers = "1 worker is free" if idle == 1 else f"{idle:,} workers are free"
    count, words = max(
        (
            (value.get("employer_waits", 0), "at employers with an application under way, and only one runs at each employer"),
            (value.get("later", 0), "waiting out a time their site or employer asked for"),
            (value.get("preparing", 0), "having a tailored resume prepared"),
            (value.get("lane_waits", 0), "company-site applications, which run one at a time"),
        ),
        key=lambda option: option[0],
    )
    if not count:
        return None
    return _reason(
        "line_waits", "Waiting on the jobs in line", f"{workers} because {'the job' if count == 1 else f'the {count:,} jobs'} left in line {'is' if count == 1 else 'are'} {words}. {_next_start(value, active)}".strip(),
        "They start by themselves.", blocking=False,
    )


def _asks(service, value, reason):
    """What else is waiting on the person that would give free workers something to do, from the dispatcher's own reading of what it holds,
    each with the place to act on it. Not while a stop or a used-up limit would hold them anyway."""
    from .startable import reading

    if reason and reason["code"] in {"stopped", "workers_off", "no_key", "provider", "ai_budget", "disk_low", "browser", "sending_off", "daily_limit", "goal_close", "update_hold"}:
        return None
    if value and value.get("rooms", {}).get("normal", 0) - value.get("startable_now", 0) <= 0:
        return None  # Every regular worker has work.
    counts = {h["code"]: h["count"] for h in (reading(service).peek() or {}).get("held", [])}
    parts, links = [], []
    if over := counts.get("employer_limit"):
        parts.append(f"{over:,} {'is' if over == 1 else 'are'} over the employer limit")
        links.append({"label": "Over the employer limit", "view": "needs", "filter": "over"})
    if reserved := counts.get("reserved"):
        parts.append(f"{reserved:,} {'is' if reserved == 1 else 'are'} in Reserved")
        links.append({"label": "Reserved", "view": "needs", "filter": "reserved"})
    if not parts:
        return None
    return {"says": " and ".join(parts) + ": approve them to give workers more to do.", "links": links}


def power_note(active):
    """A Mac on battery sleeps when the lid closes or the charge runs out, and nothing moves while it sleeps. pmset is read once a minute."""
    from .native.power import power_status

    if not active:
        return ""
    at = time.monotonic()
    if at - _power["at"] >= POWER_SECONDS:
        _power.update(at=at, value=power_status())
    power = _power["value"]
    if not power.get("battery"):
        return ""
    share = f" ({power['percent']}%)" if power.get("percent") is not None else ""
    return f"Your Mac is on battery{share}. It sleeps when the lid closes or the charge runs out, and applications stop while it sleeps. Plug it in to keep applying."


def slowed(service, running):
    """The host is running late, so fewer run at once: true, and not a reason to worry, but it does explain idle workers."""
    from .provider_health import dispatch_gate

    gate = dispatch_gate(service.db, service.config)
    if gate.limit and gate.limit < service.config.workers and running >= gate.limit:
        if gate.code == "browser_probe":
            return "One application is testing Chrome before the rest start."
        return f"This Mac is running slowly, so only {_plural(gate.limit, 'application')} run{'s' if gate.limit == 1 else ''} at once until it recovers. Closing other apps or plugging in helps."
    return ""


def why(service, value=None):
    """{"reason": {...} or None, "ask": what else waits on the person, "battery": sentence, "slow": sentence, "in_line": rows Up next shows}. `value`: the Queue's plan, when
    the caller has one; without it, the latest plan, re-read in the background when it is old."""
    from .db import Run
    from .jobboard import settings
    from .queue_order import dispatching, plans

    db = service.db
    control = db.get_setting("control", {}) or {}
    batch = db.get_setting("job_board_autopilot", {}) or {}
    value = value or plans(service).recent(service)
    automatic = dispatching(db)
    with db.session() as s:
        running = s.scalar(select(func.count()).select_from(Run).where(Run.state.in_(("running", "submitting")))) or 0
        active = s.scalar(select(func.count()).select_from(Run).where(Run.state.in_(("queued", "running", "submitting")))) or 0
    in_line = len(value["next"]) if value else 0
    reason = _global_hold(service, control, in_line) or _limit_hold(service, batch, automatic, active)
    if reason is None or (reason["code"] == "goal_reached" and in_line):
        # A goal reached ends only the automatic part: what the person queued themselves still starts.
        line = _line_hold(service, value, automatic, active)
        reason = line or (None if reason and reason["code"] == "goal_reached" else reason)
    elif reason["code"] == "goal_reached":
        reason = {**reason, "blocking": True}
    wanted = bool(active or in_line or (settings(db).auto_apply and batch.get("state") == "active"))
    return {
        "reason": reason,
        "ask": _asks(service, value, reason),
        "battery": power_note(wanted),
        "slow": slowed(service, running) if not (reason and reason["blocking"]) else "",
        "in_line": in_line,
        "at": datetime.now(UTC).isoformat(),
    }
