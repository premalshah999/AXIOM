"""What can start: the never-tried Ready jobs, sorted into those the dispatcher will start and those it holds, with the reason.

2026-11-01 05:20 UTC: nothing running, one queued, 62 never-tried Ready jobs, about 4 startable (24 at the employer cap, 18
internships, 11 for the owner's approval, 9 outside the target roles). The screens still said "Ready" and "between
applications, the next one starts as soon as a job is ready" with eight of nine runners idle.

The screening is the dispatcher's own, not a second copy: `autopilot.screen_job` (platform focus, target roles, fit, the
posting's age, the discovery gate, the tailored resume), `service.queueing.fit_blocker` (internships, seniority),
`reserved.held_ids` and `application_pacing.require_company_limit` (the employer cap). What the dispatcher itself last
recorded for a job that no check here can reproduce (an archived or duplicate posting, an employer's restriction) is read
from the batch's `rejections`. Read on a worker thread and remembered for a minute: the shared event loop never waits for it.
"""

import threading
import time
from datetime import UTC, datetime

from sqlalchemy import select

from .admission import QueueBlocked, attempted_job_ids
from .db import Job, Setting

TTL = 60  # seconds a reading is reused; a stale one is served while the next is read

# code -> (phrase for a count in the runner line, what happens next). Order is the order the runner line lists them in.
HOLDS = {
    "employer_limit": (
        "over the employer limit",
        "It starts if you approve it in Needs you, if that employer's count drops, or if you raise the limit in Settings.",
    ),
    "employer_cap": ("held by the employer's own limit", "It starts by itself when that period ends. Nothing opens a browser until then."),
    "loop_guard": ("held after repeated failures at the employer", "It starts by itself after that, after an app update, or if you press Try again on the employer."),
    "employer_wait": ("waiting because the employer's site asked for a wait", "It starts by itself when that wait is over."),
    "internship": ("internships", "Internships are skipped. Turn on internships in your profile to allow them."),
    "reserved": ("waiting for your approval", "It starts after you approve it in Needs you."),
    "role": ("outside your target roles", "It starts if you add the role to your profile's target roles."),
    "location": ("outside your target locations", "It starts if you add the place to your target locations, or Apply to it yourself."),
    "fit": ("below your minimum fit", "It starts if you lower the minimum fit, or Apply to it yourself."),
    "seniority": ("too senior for your early-career setting", "It stays held while early-career roles only is on."),
    "focus": ("on a platform you set aside", "It starts when you take the platform out of your platform focus."),
    "gate": ("not a match for your search rules", "It is not retried: the posting itself does not fit the rules."),
    "aged": ("no longer fresh", "It is left alone: the posting is older than your window or no longer on its board."),
    "protected": ("already applied to", "Applying again would duplicate an application, so it is never started."),
    "excluded": ("ruled out by the posting's requirements", "It is not retried: the posting's own requirements rule you out."),
    "twin": ("the same posting under another link", "The copy ahead of it in the queue is the one applied to."),
}
EMPLOYER_STOPS = {"employer_cap", "loop_guard", "employer_wait"}
# Plain words for the discovery gate's codes (features/discovery/policy.gate).
GATE_WORDS = {
    "generic_careers_page": "The link is a general careers page, not one job.",
    "closed": "The posting says it is closed.",
    "non_us_or_unknown": "The posting is outside the United States or has no clear place.",
    "seniority": "The posting is for a senior role.",
    "experience_required": "The posting asks for more years of experience than your limit.",
}


def limit_sentence(company, count, limit):
    """What Needs you says of a job over the employer limit: how many applications the employer already has."""
    name = " ".join((company or "").split()) or "This employer"
    return f"{name} already has {count:,} application{'' if count == 1 else 's'}; your limit is {limit:,}."


def span_words(seconds):
    """A wait in plain words: 'about a minute', 'about 8 minutes', 'about 3 hours'."""
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "about a minute"
    if seconds < 90 * 60:
        return f"about {round(seconds / 60)} minutes"
    hours = round(seconds / 3600)
    return f"about {hours} hour{'' if hours == 1 else 's'}"


def pace_sentence(ats, exc, now_ts=None):
    """Why a job the dispatcher would otherwise start waits on its platform's pace, and when it is due, in plain words."""
    name = {"smartrecruiters": "SmartRecruiters", "icims": "iCIMS", "adp": "ADP", "successfactors": "SuccessFactors"}.get(ats, (ats or "This platform").title())
    detail = exc.detail or {}
    try:
        due = datetime.fromisoformat(exc.not_before).timestamp() - (now_ts or time.time()) if exc.not_before else 60
    except ValueError:
        due = 60
    when = f"It is due in {span_words(due)}."
    kind = detail.get("pace")
    if kind == "gap":
        gap = detail.get("seconds", 0)
        return f"{name} takes one application at a time, {span_words(gap).removeprefix('about ')} apart, so employers there are not flooded. {when}"
    if kind == "throttle":
        return f"{name} refused the last application as too many, so new ones wait until it accepts again. {when}"
    if kind == "limit":
        n = detail.get("limit", 1)
        return f"{name} takes only {n} application{'' if n == 1 else 's'} at a time, and {'it is' if n == 1 else 'they are'} busy. {when}"
    if kind == "site":
        return f"Another application on this employer's site is verifying its email first. {when}"
    return f"Another {name} application is under way, and {name} takes them one at a time. {when}"


def pacing_wait(db, job_id, ats):
    """{"until", "reason"} when the platform's own pace (application_pacing.require_ats_admission, the one the dispatcher
    applies) holds this job for now, else None. Workers sit idle for these: the Queue says so instead of promising a start."""
    from .application_pacing import SITE_LIMITS, START_GAPS, ats_limit, require_ats_admission

    with db.session() as s:
        if ats not in START_GAPS and ats not in SITE_LIMITS and ats_limit(s, ats) is None:
            return None
        try:
            require_ats_admission(s, s.get(Job, job_id), owner_pick=True)
        except QueueBlocked as exc:
            if exc.code == "ats_pacing":
                return {"until": exc.not_before or "", "reason": pace_sentence(ats, exc)}
    return None


def classify(message):
    """(code, plain sentence) for a refusal the dispatcher's screen raised."""
    text = str(message)
    if text.startswith("Outside the profile's target roles"):
        title = text.split(": ", 1)[1].strip() if ": " in text else ""
        return "role", f"Outside your target roles ({title})." if title else "Outside your target roles."
    if text.startswith("Outside your target locations"):
        return "location", text + "."
    if "below the unattended threshold" in text:
        return "fit", text.replace("Resume fit", "Fit").replace("the unattended threshold", "your minimum for automatic applications")
    if text.startswith("Posting aged out"):
        return "aged", "The posting is older than your search window or no longer on its board."
    if text in GATE_WORDS:
        return ("seniority" if text == "seniority" else "gate"), GATE_WORDS[text]
    return "excluded", text[:240]


def read(service):
    """One reading, on the calling (worker) thread: {"ready", "startable", "waiting", "held": [...], "jobs": {id: {...}}}."""
    from .autopilot import dispatch_order, screen_job, unlisted_ready
    from .execution_services import enabled as services_enabled, master_policy
    from .jobboard import board_snapshot, settings
    from .reserved import held_ids
    from .schemas import Profile
    from .service.queueing import fit_blocker

    db = service.db
    cfg = settings(db)
    profile = Profile.model_validate(db.get_setting("profile", {}) or {})
    with db.session() as s:
        rows = list(
            s.execute(select(Job.id, Job.title, Job.company, Job.url, Job.ats, Job.status, Job.score, Job.created_at, Job.location).where(
                Job.status == "ready", Job.demo.is_(False)
            ))
        )
        attempted = attempted_job_ids(s, [r.id for r in rows])
        marks = {
            row.key: (row.value or {}).get("state")
            for row in s.scalars(select(Setting).where(Setting.key.startswith("job_control:", autoescape=True)))
        }
        requested = {k.removeprefix("owner_apply:") for k in s.scalars(select(Setting.key).where(Setting.key.startswith("owner_apply:", autoescape=True)))}
    ready = [
        r for r in rows
        if r.id not in attempted and r.id not in requested and marks.get("job_control:" + r.id) not in {"paused", "deleted"}
    ]
    board = board_snapshot(db, limit=None, jobs_only=True)["jobs"]
    listed = {j["id"]: j for j in board}
    unlisted = {j["id"]: j for j in unlisted_ready(db, cfg, set(listed))}
    # Pasted links have no board listing; the dispatcher lists them itself (autopilot.tick, "manual").
    manual = {r.id for r in ready if db.get_setting("job_origin:" + r.id, {}).get("kind") == "manual"}
    board_ids = set(listed) | set(unlisted) | manual
    integrated, use_master = services_enabled(db), bool(master_policy(db))
    reserved = held_ids(db)
    rejections = db.get_setting("job_board_autopilot", {}).get("rejections") or {}
    board_settings = db.get_setting("job_board_settings", {}) or {}
    data_dir = service.config.data_dir
    jobs, tally, can, details = {}, {}, [], {}
    waiting = 0
    from .application_pacing import employer_identity, require_company_limit
    from .loop_guard import held_employers, stop_of

    with db.session() as s:
        stopped_employers = held_employers(s)
        identity_of = {}

    for r in ready:
        job = listed.get(r.id) or unlisted.get(r.id) or {
            "id": r.id, "title": r.title, "company": r.company, "location": r.location, "country": "", "url": r.url,
            "ats": r.ats, "status": r.status, "score": r.score, "posted_at": r.created_at or "", "career_level": "",
        }
        verdict, more, state = None, {}, ""  # verdict: (code, sentence)
        if r.id in reserved:
            verdict = ("reserved", "A reserved employer: nothing is applied there until you approve this job.")
        else:
            try:
                state, _, _ = screen_job(
                    db, cfg, profile, job, frozenset(),
                    board_ids=board_ids, integrated=integrated, use_master=use_master, data_dir=data_dir,
                )
            except ValueError as exc:
                verdict = classify(exc)
            else:
                if state == "previous":
                    continue
                if state == "focus":
                    verdict = ("focus", "Its platform is set aside in your platform focus.")
                elif state in {"busy", "tailoring"}:
                    waiting += 1  # starts by itself: another application there is running, or its resume is being prepared
        if verdict is None and (blocker := fit_blocker(profile, board_settings, r.title)):
            code = {"internship": "internship", "seniority": "seniority", "target_role": "role"}.get(blocker[1], "excluded")
            verdict = (code, blocker[0] + ".")
        if verdict is None:
            with db.session() as s:
                try:
                    row = s.get(Job, r.id)
                    # Whatever stops the employer as a whole, as admission reads it (service.queueing).
                    if stopped := stop_of(s, row):
                        raise stopped
                    service.check_site_wait(s, row)
                    require_company_limit(s, row)
                except QueueBlocked as exc:
                    if exc.code == "loop_guard":
                        verdict = ("loop_guard", str(exc))
                    elif exc.code in {"site_circuit", "employer_cooldown", "employer_wait"}:
                        verdict = ("employer_wait", str(exc))
                    elif exc.code in {"ats_maintenance", "platform_bot_check"}:
                        waiting += 1  # The whole platform waits for itself; no employer's doing.
                    elif exc.code == "company_limit" and exc.detail:
                        # The user's own limit (Settings): the one the owner may approve past (Jobs › Over employer limit).
                        count, cap = exc.detail["count"], exc.detail["limit"]
                        verdict = ("employer_limit", limit_sentence(job.get("company") or r.company, count, cap))
                        more = {"count": count, "limit": cap}
                    else:  # The employer's own stated cap: its rule, not the user's setting; no approval lifts it.
                        verdict = ("employer_cap", str(exc))
        if verdict is None and (why := rejections.get(r.id)) and why.get("outcome") in {"excluded", "protected"}:
            verdict = ("protected" if why["outcome"] == "protected" else "excluded", str(why.get("reason", ""))[:240])
        if verdict is None:
            can.append(r.id)
            pace = pacing_wait(db, r.id, r.ats)
            # What Queue needs: the fields the dispatcher sorts on, and the job's employer and lane.
            details[r.id] = {
                **({"paced": pace} if pace else {}),
                "id": r.id, "title": r.title, "company": r.company, "location": job.get("location") or r.location, "url": r.url, "ats": r.ats,
                "score": r.score, "status": r.status, "posted_at": job.get("posted_at") or "", "career_level": job.get("career_level") or "",
                "tailoring": state == "tailoring",
            }
            continue
        code, sentence = verdict
        jobs[r.id] = {"code": code, "reason": sentence, "next": HOLDS.get(code, HOLDS["excluded"])[1], **more}
        tally[code] = tally.get(code, 0) + 1
        if code in EMPLOYER_STOPS:
            with db.session() as s:
                identity_of[r.id] = employer_identity(s, s.get(Job, r.id))
    # The dispatcher's order, on the same starting list as its own: the board's, then pasted links, then the unlisted.
    place = {jid: i for i, jid in enumerate([*listed, *sorted(manual - set(listed)), *unlisted])}
    can.sort(key=lambda jid: place.get(jid, len(place)))
    can = [j["id"] for j in dispatch_order([details[jid] for jid in can], profile.job_priority)]
    # The same posting under two links (a list's short link and the employer's own): counted, listed and applied to once. The
    # later one waits behind the first, and the duplicate guard refuses it once the first has gone (122 pairs, 2026-11-01).
    seen, kept = set(), []
    for jid in can:
        d = details[jid]
        same = (" ".join((d["company"] or "").casefold().split()), " ".join((d["title"] or "").casefold().split()), " ".join((d["location"] or "").casefold().split()))
        if all(same) and same in seen:
            jobs[jid] = {"code": "twin", "reason": "The same posting is ahead of it in the queue under another link.", "next": HOLDS["twin"][1]}
            tally["twin"] = tally.get("twin", 0) + 1
            continue
        seen.add(same)
        kept.append(jid)
    can, startable = kept, len(kept)
    held = [{"code": c, "count": tally[c], "phrase": HOLDS[c][0]} for c in HOLDS if tally.get(c)]
    # What stops every application at once comes first: a job that "can start" cannot while the model is unreachable.
    stopped = everything_stopped(service)
    if not stopped and not startable and (outside := outside_search_area(profile)):
        stopped = outside
    # One item per employer: dozens of jobs held for one reason are one thing to read. The jobs it holds come with it.
    per_employer = {}
    for job_id, identity in identity_of.items():
        per_employer[identity] = per_employer.get(identity, 0) + 1
    employers = [{**item, "jobs": per_employer.get(item["key"].split(":", 1)[1], 0)} for item in stopped_employers]
    return {
        "at": datetime.now(UTC).isoformat(),
        "employers": employers,
        "ready": len(jobs) + startable,
        "startable": startable,
        "waiting": waiting,
        "held": held,
        "jobs": jobs,
        "ids": can,
        "details": {jid: details[jid] for jid in can},
        "idle": stopped or idle_line(startable, held),
        "stopped": stopped,
    }


LISTING = ("id", "identity", "url", "title", "company", "location", "ats", "status", "score", "reason", "created_at")


def rows(db, value, which="", hold=""):
    """The Ready jobs of one reading as Discovered rows: "ready" (can start), "held" (optionally one reason), or all of them.
    The same jobs the counts name, so a number opens exactly that many rows."""
    wanted = [] if which == "held" else list(value.get("ids", []))
    if which != "ready":
        wanted += [jid for jid, h in value.get("jobs", {}).items() if not hold or h["code"] == hold]
    if not wanted:
        return []
    with db.session() as s:
        found = {row.id: row for row in s.execute(select(*(getattr(Job, c) for c in LISTING)).where(Job.id.in_(wanted)))}
    out = []
    for jid in wanted:
        row = found.get(jid)
        if row is None:
            continue
        item = {c: getattr(row, c) for c in LISTING}
        item.update(
            job_id=jid, posted_at=row.created_at or "", date_basis="added", country="", career_level="", match_reason="",
            attempted=False, requested=False, reserved=value.get("jobs", {}).get(jid, {}).get("code") == "reserved",
        )
        if hold_ := value.get("jobs", {}).get(jid):
            item["hold"] = hold_
        out.append(item)
    return out


def everything_stopped(service):
    """The sentence for what no job can get past right now, or None: no model key, a refused key, an account out of credit, a
    nearly full disk, a browser that will not open."""
    from .agent_settings import for_role, missing_key_message
    from .provider_health import dispatch_gate

    for role in ("answers", "navigator"):
        config = for_role(service.db, service.config, role)
        if not config.llm_api_key:
            return missing_key_message(config.llm_provider)
    gate = dispatch_gate(service.db, service.config)
    return (gate.reason + ".") if not gate.limit and gate.reason else None


def outside_search_area(profile):
    """Discovery finds jobs in the United States only (discovery policy `us_only`). Someone living elsewhere saw "discovery is
    still finding and ranking jobs" for ever (2026-11-01)."""
    from .jobboard import US

    country = " ".join(str(profile.address.country or "").split())
    if not country or country.casefold() in US:
        return None
    return f"AXIOM finds jobs in the United States only so far, so none will appear here for {country}. Links you paste still work."


def over_limit(db, value, q=""):
    """The jobs of Jobs › Over employer limit, best fit first: Ready jobs the dispatcher holds only because their employer
    has reached the user's limit. The same reading as the counts, so the tab's number is exactly these rows (the search
    narrows the rows, never the number)."""
    items = rows(db, value, "held", "employer_limit")
    needle = " ".join((q or "").lower().split())
    if needle:
        items = [i for i in items if needle in " ".join(str(i.get(k) or "") for k in ("company", "title", "location", "ats")).lower()]
    return sorted(items, key=lambda i: (-(i["score"] if i["score"] is not None else -1), i["company"] or "", i["id"]))


def over_limit_count(value):
    return sum(1 for h in (value or {}).get("jobs", {}).values() if h["code"] == "employer_limit")


def idle_line(startable, held):
    """Why runners sit idle, in one sentence, from the same tally: None when something can start."""
    if startable:
        return None
    if not held:
        return "Nothing is ready to apply to yet. Discovery is still finding and ranking jobs."
    parts = [f"{h['count']:,} {h['phrase']}" for h in held[:4]]
    return "Nothing to run: " + ", ".join(parts) + "." + (f" {sum(h['count'] for h in held):,} held in all." if len(held) > 4 else "")


def without(value, job_ids):
    """The reading with these jobs taken out, every count following: they were approved, moved or removed, and wait no longer."""
    gone = {j for j in job_ids if j in value["jobs"] or j in value.get("ids", ())}
    if not gone:
        return value
    jobs = {j: h for j, h in value["jobs"].items() if j not in gone}
    ids = [j for j in value.get("ids", []) if j not in gone]
    tally = {}
    for h in jobs.values():
        tally[h["code"]] = tally.get(h["code"], 0) + 1
    held = [{"code": c, "count": tally[c], "phrase": HOLDS[c][0]} for c in HOLDS if tally.get(c)]
    return {
        **value, "jobs": jobs, "ids": ids, "held": held, "ready": len(jobs) + len(ids), "startable": len(ids),
        "idle": value.get("stopped") or idle_line(len(ids), held),
    }


class Reading:
    """The latest reading, shared by every request; one thread reads at a time."""

    def __init__(self):
        self.value, self.at, self.busy = None, 0.0, threading.Lock()
        self.guard, self.dropped = threading.Lock(), {}

    def get(self, service):
        age = time.monotonic() - self.at
        if self.value is not None and age < TTL:
            return self.value
        if self.value is not None:
            # Stale: serve it now, read again in the background.
            if self.busy.acquire(blocking=False):
                threading.Thread(target=self._fill, args=(service,), name="startable-read", daemon=True).start()
            return self.value
        with self.busy:  # the first reading: the one caller waits, the rest wait behind it
            if self.value is None:
                self._load(service)
        return self.value

    def _load(self, service):
        value = read(service)
        with self.guard:
            now = time.monotonic()
            self.dropped = {j: at for j, at in self.dropped.items() if now - at < 120}
            self.value, self.at = without(value, self.dropped), now

    def _fill(self, service):
        try:
            self._load(service)
        except Exception:
            self.at = time.monotonic() - TTL / 2  # try again in half a minute, serve the old reading meanwhile
        finally:
            self.busy.release()

    def drop(self, job_ids):
        """Take jobs out of the reading now: they were approved and no longer wait. A reading already under way when they
        were approved would bring them back, so for two minutes every reading leaves them out; the next reading, started
        at once in the background, finds them started or requested."""
        with self.guard:
            self.dropped.update(dict.fromkeys(job_ids, time.monotonic()))
            if self.value is not None:
                self.value = without(self.value, job_ids)
                self.at = 0.0  # stale: the next get() serves this and reads again in the background

    def reload(self, service):
        """Read again now, on the calling thread: the user's own change (a reservation, an approval) must show on the next read, not
        after the minute a reading is reused."""
        with self.busy:
            self._load(service)

    def peek(self):
        """The reading if there is one: never reads."""
        return self.value


def reading(service):
    if not hasattr(service, "startable_reading"):
        service.startable_reading = Reading()
    return service.startable_reading
