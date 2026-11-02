"""The Queue: what is running, and the order the dispatcher will really start the rest (owner, 2026-11-01: "don't know where my
queue is").

Not a second opinion. The list is the reading the counts come from (startable.py: the dispatcher's own screening), sorted by the
dispatcher's own function (autopilot.dispatch_order), behind the waiting requests in their own order (owner_apply.request_order).
What the dispatcher does while it walks that list is replayed here without side effects: it starts one application per employer
at a time and only what a worker lane has room for, so a second job at an employer waits for the first. The first picks are
exact while nothing changes; past them the replay assumes applications end in the order they started, because how long one takes
cannot be known. The page says so in those words.
"""

import logging
import threading
import time
from collections import deque
from datetime import UTC, datetime

from sqlalchemy import select

TTL = 5  # seconds a plan is reused: every open Queue polls, and the replay walks the whole list
RETRY_TTL = 20  # seconds the "trying again" list is reused


def decides(priority, automatic=True):
    """What decides the order, in plain words."""
    first = "the best fit, then new-grad roles, then the newest" if priority == "best_fit_first" else "new-grad roles, then the best fit, then the newest"
    if not automatic:
        first = "the rest in the order you added them"
    return (
        f"What decides the order: jobs you move to the front go first, then {first}. "
        "Only one application runs at each employer, so a second job there waits for the first, and company sites run one at a time. "
        "How long each takes can't be known, so past the next few the order can shift."
    )


def replay(items, rooms, busy, running):
    """The order `items` (already in the dispatcher's order) start in, as (item, wait) pairs; a tick walks the list from the top
    and takes what has room, so this does too, again each time an application ends. `rooms`: free slots per lane. `busy`: employers
    with an application under way. `running`: (employer, lane) of those, oldest first. What no worker can ever take (a lane with
    no worker) comes back in `stuck`."""
    rooms, busy, flight = dict(rooms), set(busy), deque(running)
    left, out, delayed = list(items), [], {}
    while left:
        taken = []
        for item in left:
            if rooms.get(item["lane"], 0) <= 0:
                delayed.setdefault(item["id"], "lane")
                continue
            if item["employer"] in busy:
                delayed[item["id"]] = "employer"
                continue
            rooms[item["lane"]] -= 1
            busy.add(item["employer"])
            flight.append((item["employer"], item["lane"]))
            out.append((item, delayed.get(item["id"], "")))
            taken.append(item["id"])
        if taken:
            gone = set(taken)
            left = [item for item in left if item["id"] not in gone]
            continue
        if not flight:
            break
        employer, lane = flight.popleft()
        busy.discard(employer)
        rooms[lane] = rooms.get(lane, 0) + 1
    return out, left


def _name(company):
    return " ".join((company or "").split()) or "this employer"


def _item(detail, kind):
    from .admission import employer_partition
    from .pipeline_comparison import custom_site

    return {
        **detail,
        "kind": kind,
        "employer": employer_partition(detail),
        "lane": "custom" if custom_site(detail) else "normal",
    }


def dispatching(db):
    """Whether the dispatcher starts Ready jobs by itself (autopilot.tick): automatic applying on, and its batch not over."""
    from .jobboard import settings

    cfg = settings(db)
    return bool(cfg.enabled and cfg.auto_apply and (db.get_setting("job_board_autopilot") or {"state": "active"}).get("state") == "active")


def plan(service, value=None):
    """Up next, in order, with why each one waits. Reads on the calling (worker) thread."""
    from .admission import attempted_job_ids, capacity
    from .db import Job, Run, Setting, now
    from .owner_apply import KEY, request_order
    from .pipeline_comparison import CUSTOM, NORMAL, pool_capacity
    from .schemas import Profile
    from .startable import reading

    db, config = service.db, service.config
    value = value or reading(service).get(service)
    profile = Profile.model_validate(db.get_setting("profile", {}) or {})
    moment = now()
    with db.session() as s:
        waiting = [
            (row.key.removeprefix(KEY), dict(row.value or {}))
            for row in s.scalars(select(Setting).where(Setting.key.startswith(KEY, autoescape=True)))
        ]
        ids = [job_id for job_id, _ in waiting]
        rows = (
            {row.id: row for row in s.execute(select(Job.id, Job.title, Job.company, Job.url, Job.ats, Job.location, Job.score, Job.status, Job.created_at).where(Job.id.in_(ids)))}
            if ids
            else {}
        )
        attempted = set(s.scalars(select(Run.job_id).where(Run.job_id.in_(ids)).distinct())) if ids else set()
        started = set(s.scalars(select(Run.job_id).where(Run.job_id.in_(ids), Run.state.in_(("queued", "running", "submitting"))))) if ids else set()
        topology = config.topology
        rooms = {
            "normal": pool_capacity(s, NORMAL, topology).available,
            "custom": pool_capacity(s, CUSTOM, topology).available,
        }
        occupied = set(capacity(s, config.workers).employers)
    requests = [(job_id, v) for job_id, v in waiting if job_id in rows and job_id not in started]
    asked = {job_id for job_id, _ in requests}
    # The reading is up to a minute old and this replays every few seconds: a job that has started since is under Applying now, not
    # also in line behind itself (seen 2026-11-02: Up next 20 with the first three already running).
    with db.session() as s:
        gone = attempted_job_ids(s, value.get("ids", []))

    def request_item(job_id, request):
        row = rows[job_id]
        return _item(
            {
                "id": job_id, "title": row.title, "company": row.company, "location": row.location, "url": row.url, "ats": row.ats,
                "score": row.score, "status": row.status, "posted_at": row.created_at or "", "career_level": "",
                "front": bool(request.get("front")), "reason": request.get("reason", ""), "not_before": request.get("not_before", ""), "code": request.get("code", ""),
            },
            "front" if request.get("front") else "retry" if job_id in attempted else "request",
        )

    ordered = request_order(requests, attempted)
    # Asked for and due: first attempts first. A retry waits for what no fresh job can use (autopilot.tick) unless the user moved it
    # to the front, and one whose wait is not over is not tried yet.
    def retry(job_id, request):
        return job_id in attempted and not request.get("front")

    now_asked = [request_item(j, v) for j, v in ordered if not retry(j, v) and (v.get("not_before") or "") <= moment]
    # The line is what the dispatcher will start. With automatic applying off, or its target reached, it starts only what was asked
    # for: 70 jobs read "In queue" here with no way to queue them and no worker coming for them (2026-11-01).
    automatic = dispatching(db)
    fresh = [_item(value["details"][jid], "auto") for jid in value.get("ids", []) if jid not in asked and jid not in gone and jid in value.get("details", {})] if automatic else []
    retries = [request_item(j, v) for j, v in ordered if retry(j, v) and (v.get("not_before") or "") <= moment]
    later = [request_item(j, v) for j, v in ordered if (v.get("not_before") or "") > moment]
    # What the person queued and the platform's pace turned away waits on that pace like the rest of the line: 70 accepted Ashby jobs each
    # hold a `not_before` from their last refusal, and they are why the workers are free.
    turned_away = [item for item in later if item.get("code") == "ats_pacing"]
    later = [item for item in later if item.get("code") != "ats_pacing"]
    preparing = [item for item in fresh if item.get("tailoring")]
    fresh = [item for item in fresh if not item.get("tailoring")]
    # Held by the platform's own pace (one at a time, a pause between starts): a free worker does not take these now, so they are
    # neither "the next free worker" nor counted in the exact picks. They say why and when instead (2026-11-02: workers sat idle
    # behind a line of Ashby jobs and the page promised each one the next free worker). Read now, not from the minute-old reading, and
    # for what the person asked for too, in line order: of ten Ashby jobs with nothing running, one can start.
    pace = Pace(db)
    paced, taking = [], []
    for item in [*now_asked, *fresh, *retries]:
        held = pace.of(item)
        if held:
            paced.append({**item, "paced": held})
        else:
            taking.append(item)
    for item in turned_away:
        paced.append({**item, "paced": pace.of(item) or {"until": item["not_before"], "reason": _later_words(item), "timed": True}})
    picks, stuck = replay(taking, rooms, occupied, [(employer, "normal") for employer in sorted(occupied)])
    free = sum(max(0, n) for n in rooms.values())
    custom_off = not topology.custom
    # What the person moved to the front stays at the front, saying what its platform makes it wait for: "Moved to the front" with the row
    # sitting behind the others read as a button that did nothing.
    first = [item for item in paced if item["kind"] == "front"]
    paced = [item for item in paced if item["kind"] != "front"]
    out = [{**item, "wait": item["paced"]["reason"]} for item in first]
    for item, delayed in picks:
        out.append({**item, "wait": _wait(item, delayed)})
    for item in stuck:
        out.append({**item, "wait": "Company-site applications are off in Settings, so this one waits." if custom_off and item["lane"] == "custom" else "Waiting for a worker."})
    for item in sorted(paced, key=lambda i: i["paced"].get("until") or "9"):
        out.append({**item, "wait": item["paced"]["reason"]})
    for item in preparing:
        out.append({**item, "wait": "Preparing a tailored resume first. It joins the line when that is ready."})
    for item in later:
        out.append({**item, "wait": _later_words(item)})
    for position, item in enumerate(out, 1):
        item["position"] = position
    from .application_pacing import START_GAPS, ats_limit, platform_words

    held_back = [*first, *paced]
    per_platform = {}
    for item in held_back:
        per_platform[item["ats"]] = per_platform.get(item["ats"], 0) + 1
    with db.session() as s:
        paced_by = [
            {"name": platform_words(ats), "count": count, "gap": START_GAPS.get(ats, 0), "limit": ats_limit(s, ats)}
            for ats, count in sorted(per_platform.items(), key=lambda kv: -kv[1])
        ]
    return {
        "at": datetime.now(UTC).isoformat(),
        "next": out,
        "positions": {item["id"]: item["position"] for item in out},
        # How many of the first rows are exact while nothing changes: what a free worker takes on the next pass.
        "exact": len(first) + min(free, len(picks)),
        "free": free,
        "rooms": rooms,
        # What can take a worker the moment one looks: not behind a busy employer, a full lane or a platform's pace (waiting.py).
        "startable_now": sum(1 for _, delayed in picks if not delayed),
        "paced": len(held_back),
        "paced_by": paced_by,
        # When the next one held by a known time can start. None known: it starts when the one under way ends.
        "next_due": min([*(item["paced"]["until"] for item in held_back if item["paced"].get("timed") and item["paced"].get("until")), *(item["not_before"] for item in later)], default=""),
        # What else holds the rest of the line, for the sentence at its top (waiting.py).
        "later": len(later),
        "preparing": len(preparing),
        "employer_waits": sum(1 for _, delayed in picks if delayed == "employer"),
        "lane_waits": sum(1 for _, delayed in picks if delayed == "lane") + len(stuck),
        "decides": decides(profile.job_priority, automatic),
        "reading": value,
    }


def _later_words(item):
    """Why a request waits for a time. A platform's pace is the engine's own wording in `reason` ("1 icims application(s) already queued
    or active"), so it is said again in the words the rest of the line uses."""
    if item.get("code") == "ats_pacing":
        from .application_pacing import platform_words

        return f"{platform_words(item['ats'])} takes a limited number of applications at a time. This one tries again shortly."
    return item.get("reason") or "Waiting before it is tried again."


class Pace:
    """What each platform's own pace holds right now, walked in line order. The platform's limit and gap are one reading per platform
    for one plan; a site limit (Oracle: one per employer site) is the job's own."""

    def __init__(self, db):
        self.db, self.held, self.room, self.behind = db, {}, {}, {}

    def of(self, item):
        """{"until", "reason"} when the platform's pace holds this job, else None. A job the platform has room for takes that room."""
        from .application_pacing import SITE_LIMITS, platform_room
        from .startable import pace_behind, pacing_wait

        ats = item["ats"]
        key = item["id"] if ats in SITE_LIMITS else ats
        if key not in self.held:
            self.held[key] = pacing_wait(self.db, item["id"], ats)
        if self.held[key]:
            return self._behind(ats, self.held[key], pace_behind)
        if ats not in self.room:
            with self.db.session() as s:
                self.room[ats] = platform_room(s, ats)
        if self.room[ats] is None:
            return None
        if self.room[ats] > 0:
            self.room[ats] -= 1
            return None
        return self._behind(ats, {"until": ""}, pace_behind)

    def _behind(self, ats, first, words):
        ahead = self.behind.get(ats, 0)
        self.behind[ats] = ahead + 1
        return first if first.get("reason") and not ahead else words(ats, first, ahead)


def _wait(item, delayed):
    if item["kind"] == "front":
        return "You moved this to the front."
    if item["kind"] == "request":
        return "You asked for this one."
    if item["kind"] == "retry":
        return "Retries wait for what new jobs leave free."
    if delayed == "employer":
        return f"The application before it at {_name(item['company'])}."
    if delayed == "lane" and item["lane"] == "custom":
        return "Company sites run one at a time."
    return ""


class Plans:
    """The latest plan, shared by every poll; one thread replays at a time, and a reader never waits for a stale one. The first
    screening of a cold start takes seconds (startable.read), so the page paints without it and asks again."""

    def __init__(self):
        self.value, self.at, self.busy = None, 0.0, threading.Lock()
        self.tries, self.tried_at, self.trying = None, 0.0, threading.Lock()

    def _fill(self, service):
        try:
            self.value, self.at = plan(service), time.monotonic()
        except Exception:
            logging.getLogger(__name__).exception("The Queue could not be planned")
            self.at = time.monotonic() - TTL / 2  # try again shortly; keep serving the last plan
        finally:
            self.busy.release()

    def ready(self, service):
        """The latest plan at once, or None on a cold start; a stale one is replayed again in the background."""
        if (self.value is None or time.monotonic() - self.at >= TTL) and self.busy.acquire(blocking=False):
            threading.Thread(target=self._fill, args=(service,), name="queue-plan", daemon=True).start()
        return self.value

    def recent(self, service, within=30):
        """For what only needs the gist (Overview's sentence, every few seconds): the latest plan, replayed in the background once it is
        `within` seconds old, so a plain poll doesn't replay the whole line each time."""
        if self.value is None or time.monotonic() - self.at >= within:
            return self.ready(service)
        return self.value

    def current(self, service, within=20):
        """For the Queue's own page, on a worker thread. A plan nobody has read for a while is from before whatever happened since: the
        first read after Start showed "Nothing is in line" over 20 jobs. A cold start still paints at once and asks again."""
        if self.value is not None and time.monotonic() - self.at >= within:
            try:
                return self.now(service)
            except Exception:
                logging.getLogger(__name__).exception("The Queue could not be planned")  # The last plan is still the best answer.
        return self.ready(service)

    def now(self, service):
        """Replay now, on the calling worker thread: the user just moved something and the next read must show it."""
        with self.busy:
            self.value, self.at = plan(service), time.monotonic()
        return self.value

    def _fill_tries(self, service):
        try:
            self.tries, self.tried_at = trying_again(service), time.monotonic()
        except Exception:
            self.tried_at = time.monotonic() - RETRY_TTL / 2
        finally:
            self.trying.release()

    def retrying(self, service):
        """What the app is retrying by itself, read in the background: the classification behind it takes seconds."""
        if (self.tries is None or time.monotonic() - self.tried_at >= RETRY_TTL) and self.trying.acquire(blocking=False):
            threading.Thread(target=self._fill_tries, args=(service,), name="queue-retrying", daemon=True).start()
        return self.tries

    def peek(self):
        return self.value


def plans(service):
    if not hasattr(service, "queue_plans"):
        service.queue_plans = Plans()
    return service.queue_plans


def active_rows(service):
    """Applying now: every job whose latest run has a worker or is about to, with the step it is on, oldest first."""
    from .db import Event, Job, Run, latest_runs
    from .pipeline_comparison import TELLING_EVENTS

    states = ("running", "submitting", "waiting_email", "queued")
    with service.db.session() as s:
        # Each job's newest run, among jobs with a run in one of these states: an old attempt left behind is not work under way.
        latest = latest_runs(Run.job_id.in_(select(Run.job_id).where(Run.state.in_(states))))
        rows = s.execute(
            select(
                Job.id, Job.title, Job.company, Job.url, Job.ats, Job.location,
                latest.c.id.label("run_id"), latest.c.state, latest.c.started_at, latest.c.created_at,
            )
            .join(latest, latest.c.job_id == Job.id)
            .where(latest.c.state.in_(states), Job.demo.is_(False))
            .order_by(latest.c.created_at)
        ).all()
        ids = [row.run_id for row in rows]
        phases = {}
        if ids:
            for run_id, kind in s.execute(select(Event.run_id, Event.kind).where(Event.run_id.in_(ids), Event.kind.in_(TELLING_EVENTS)).order_by(Event.id)):
                phases[run_id] = kind
    order = {"submitting": 0, "running": 1, "waiting_email": 2, "queued": 3}
    return sorted(
        [
            {
                "job_id": row.id, "run_id": row.run_id, "title": row.title, "company": row.company, "url": row.url, "ats": row.ats, "location": row.location,
                "state": row.state, "since": row.started_at or row.created_at, "phase": phases.get(row.run_id, ""),
                "gate": getattr(service, "claim_gates", {}).get(row.run_id, "") if row.state == "queued" else "",
            }
            for row in rows
        ],
        key=lambda item: (order.get(item["state"], 9), item["since"] or ""),
    )


def trying_again(service, limit=100):
    """Stopped on a problem the app retries by itself: Applying's other half (vocabulary.py), each with its own stop reason."""
    from .api import pipeline

    with service.db.session() as s:
        rows = pipeline.page(s, "applying", "", 1, limit)["rows"]
    out = []
    for row in rows:
        run = row.get("run") or {}
        if run.get("state") in ("running", "submitting", "waiting_email", "queued") or row.get("request"):
            continue
        out.append({
            "job_id": row["job"]["id"], "run_id": run.get("id"), "title": row["job"]["title"], "company": row["job"]["company"], "url": row["job"]["url"],
            "ats": row["job"]["ats"], "location": row["job"]["location"], "state": run.get("state", ""), "sentence": row["sentence"],
            "reason": (run.get("reason") or "")[:300], "since": run.get("finished_at") or run.get("started_at") or run.get("created_at"), "tries": row["attempts"],
        })
    return out


def position_of(service, job_id):
    """{position, total, wait} of a job in the latest plan, never waiting: None when it is not in line, or nothing was planned yet."""
    value = plans(service).ready(service)
    if not value or job_id not in value["positions"]:
        return None
    position = value["positions"][job_id]
    item = value["next"][position - 1]
    return {"position": position, "total": len(value["next"]), "wait": item.get("wait", "")}
