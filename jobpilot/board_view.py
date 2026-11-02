"""The Job board: which real, open postings the app found, in the words a job seeker filters by (owner, 2026-11-01: "the job board
is confusing ... make the job board good and legit"; 2026-10-30: "too sketchy and maybe not trustworthy enough").

Every row is one posting: a real title and company, its place, the date the employer or a list gave (or plainly "found"), where it
came from, the employer's own link, and one line saying why it fits or doesn't. Listings come from the same read the dispatcher
uses (jobboard.board_snapshot: active, in the US, inside the window, seen lately), and what will happen to a row comes from the same
reading and plan (startable.py, queue_order.py), so In queue #4 here is number 4 on the Queue. Left out for good: a blank title or
company, a closed or aged-out posting, a general careers page, one already applied to. The same posting under two links is one row.
"""

import re
import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

# The two letters by every spelling a posting uses; a place is read to its state, or Remote.
STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "Washington, DC", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_NAMES = {name.casefold(): code for code, name in STATES.items() if code != "DC"}
_NAME = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(_NAMES, key=len, reverse=True)) + r")\b", re.I)
_CODE = re.compile(r"(?:^|[,;(/\s-])(" + "|".join(STATES) + r")(?=$|[,;)/\s.]|\s*\d{5})")
_DC = re.compile(r"\bwashington,?\s*d\.?\s*c\b|\bd\.c\.", re.I)
REMOTE = re.compile(r"\bremote\b|\bwork from home\b|\banywhere\b", re.I)
# A place a posting names by a city alone. Only the ones postings commonly do (San Francisco, 3 of 360 on 2026-11-01).
_CITIES = {"san francisco": "CA", "new york": "NY", "seattle": "WA", "boston": "MA", "chicago": "IL", "austin": "TX", "los angeles": "CA", "san jose": "CA", "palo alto": "CA", "denver": "CO", "atlanta": "GA"}

POSTED = {"1": ("Last 24 hours", 24), "3": ("Last 3 days", 72), "7": ("Last 7 days", 168), "30": ("Last 30 days", 720)}
FITS = {"match": "Good fits", "other": "Not a fit", "all": "Everything found"}
SORTS = ("fit", "newest", "company")
TTL = 15  # seconds the cleaned board is reused: it is read per poll, and the listings change on the scale of hours


def places_of(location):
    """Which filter places a posting is in: state codes, and "Remote"."""
    text = location or ""
    found = []
    if REMOTE.search(text):
        found.append("Remote")
    if _DC.search(text):
        found.append("DC")
        text = _DC.sub(" ", text)
    upper = _CODE.findall(text)
    found.extend(code for code in upper if code in STATES)
    found.extend(_NAMES[name.casefold()] for name in _NAME.findall(text))
    if not found:
        low = text.casefold()
        found.extend(code for city, code in _CITIES.items() if city in low)
    return list(dict.fromkeys(found))


def source_of(job):
    """Where a posting was found, in words: the employer's own board, a named new-grad list, or the user's pasted link."""
    basis = (job.get("date_basis") or "").casefold()
    if job.get("origin") == "manual":
        return "You added it"
    if basis.startswith("simplify"):
        return "Simplify list"
    if basis.startswith("speedyapply"):
        return "SpeedyApply list"
    source = job.get("source_id") or ""
    if basis.startswith("board:") or basis.startswith("employer") or (source and not source.startswith("feed:")):
        return "Employer's board"
    if source.startswith("feed:"):
        return "GitHub new-grad list"
    return "Added directly"


def dated(job):
    """True when the date on a row is the employer's or a list's own, not the day this app first saw it."""
    basis = (job.get("date_basis") or "").casefold()
    return bool(basis) and "inferred" not in basis and basis != "added"


# What one source adds to a title that another leaves out: "New Grad 2027:", "(Early Career)", a dash spelt two ways. A year stays unless it
# belongs to the new-grad label, since "Summer 2026" and "Summer 2027" are different postings.
_LABELS = re.compile(
    r"\b20\d\d\s+(?:new\s+)?(?:(?:college|university)\s+)?grad\w*|"
    r"\b(?:(?:new|recent)\s+)?(?:(?:college|university)\s+)?grad(?:uate)?s?\b(?:\s*,?\s*(?:class of\s*)?20\d\d)?|"
    r"\bearly[- ]career\b|\bentry[- ]level\b",
    re.I,
)


def title_key(title):
    """A title as every source spells it the same: without the new-grad label, case, punctuation and word breaks."""
    text = _LABELS.sub(" ", str(title or "").casefold())
    return " ".join(re.findall(r"[a-z0-9+#]+", text))


def key_of(job):
    """One posting whatever link or list it came under: the employer, the title without its labels, and the place."""
    company, location = (" ".join((job.get(k) or "").casefold().split()) for k in ("company", "location"))
    return company, title_key(job.get("title")) or " ".join((job.get("title") or "").casefold().split()), location


def _restricted(reason):
    return (reason or "").lower().startswith(("explicit hiring restriction", "an explicit hiring restriction"))


def _line(reason, source):
    """The one line: the ranker's own first clause, without the engine's bookkeeping."""
    text = re.sub(r"^(?:Owner approved|Applicant approved[^:]*|Admitted by title \(admit_by_title setting\)):\s*", "", (reason or "").strip(), flags=re.I)
    return (text[0].upper() + text[1:]) if text else ""


_LEVELS = {}  # job id -> (title, feed label, level): a posting's level changes only when its title does
LEVEL_CHUNK = 400


def levels_of(db, listed):
    """{job id: level} for the listed rows, read from the title and the posting text (roles.career_level). The text is read once per
    job and remembered, so the board's refresh costs no more than before."""
    from .db import Job
    from .features.discovery.roles import career_level

    todo = [j for j in listed if _LEVELS.get(j["id"], (None,))[0:2] != (j.get("title"), j.get("career_level") or "")]
    for i in range(0, len(todo), LEVEL_CHUNK):
        chunk = todo[i : i + LEVEL_CHUNK]
        with db.session() as s:
            rows = s.execute(select(Job.id, func.substr(Job.description, 1, 20000)).where(Job.id.in_([j["id"] for j in chunk])))
            text = {row[0]: row[1] for row in rows}
        for j in chunk:
            stored = j.get("career_level") or ""
            _LEVELS[j["id"]] = (j.get("title"), stored, career_level(j.get("title"), text.get(j["id"]) or "", stored))
    return {j["id"]: _LEVELS[j["id"]][2] for j in listed}


def collect(service):
    """Every row the board can show, cleaned and classified, with what will happen to each. Reads on a worker thread."""
    from .jobboard import LOCKED, board_snapshot, settings as board_settings, us_places
    from .queue_order import plans
    from .schemas import Profile
    from .service.queueing import fit_blocker
    from .shortlinks import is_shortlink
    from .startable import reading
    from .features.discovery.roles import fitting_roles

    db = service.db
    value = reading(service).get(service)
    planner = plans(service)
    plan = planner.peek() or planner.now(service)
    cfg = board_settings(db)
    profile = Profile.model_validate(db.get_setting("profile", {}) or {})
    board_rules = db.get_setting("job_board_settings", {}) or {}
    listed = board_snapshot(db, limit=None, jobs_only=True)["jobs"]
    from .reserved import held, matcher

    with db.session() as s:
        reserved = set(held(s))
        reserving = matcher(s)  # a row says whether its company is on the user's reserved list, approved or not
    positions = plan["positions"]
    holds = value.get("jobs", {})
    have = {j["id"] for j in listed}
    # In line without a live listing (imported, a pasted link): the Queue names them, so the board does too.
    missing = [jid for jid in positions if jid not in have]
    if missing:
        from .db import Job, JobListing

        with db.session() as s:
            for row in s.execute(
                select(
                    Job.id, Job.title, Job.company, Job.url, Job.ats, Job.status, Job.score, Job.reason, Job.created_at, Job.location,
                    JobListing.posted_at, JobListing.date_basis, JobListing.source_id, JobListing.match_reason, JobListing.career_level,
                )
                .outerjoin(JobListing, JobListing.job_id == Job.id)
                .where(Job.id.in_(missing))
            ):
                listed.append({
                    "id": row.id, "title": row.title, "company": row.company, "url": row.url, "ats": row.ats, "status": row.status,
                    "score": row.score, "reason": row.reason, "created_at": row.created_at, "location": row.location,
                    "posted_at": row.posted_at or row.created_at or "", "date_basis": row.date_basis or "added", "source_id": row.source_id or "",
                    "match_reason": row.match_reason or "", "career_level": row.career_level or "", "attempted": False, "requested": row.id in positions,
                })
    roles_memo, titles_memo, out = {}, {}, {}
    levels = levels_of(db, listed)
    hidden = {"blank": 0, "stale": 0, "closed": 0, "applied": 0, "twin": 0}
    for job in listed:
        jid = job["id"]
        in_line = jid in positions
        if job.get("attempted") and not in_line:
            hidden["applied"] += 1
            continue  # It has a run: Queue, Needs you or Applications holds it.
        if job["status"] in LOCKED and not in_line:
            hidden["closed"] += 1
            continue
        title, company = " ".join((job.get("title") or "").split()), " ".join((job.get("company") or "").split())
        if not title or not company:
            hidden["blank"] += 1
            continue
        hold = holds.get(jid)
        code = (hold or {}).get("code", "")
        if code in {"aged", "protected"} or (code == "gate" and "general careers page" in hold["reason"]) or (code == "gate" and "closed" in hold["reason"]):
            hidden["stale"] += 1
            continue
        reason = job.get("reason") or ""
        source = source_of(job)
        restricted = _restricted(reason)
        if in_line:
            state, fit, sentence = "queue", "match", _line(reason, source) or job.get("match_reason") or "In line."
        elif code == "employer_limit" or code == "reserved" or jid in reserved:
            state, fit, sentence = "approval", "match", (hold or {}).get("reason") or "A reserved employer: nothing is applied there until you approve this job."
        elif hold:
            state, fit, sentence = "held", "other", hold["reason"]
        elif job["status"] == "skipped":
            state, fit, sentence = "below", "other", _line(reason, source) or "Below your minimum fit."
        elif job["status"] == "new":
            blocker = titles_memo.setdefault(title, fit_blocker(profile, board_rules, title))
            if blocker:
                state, fit, sentence = "held", "other", blocker[0] + "."
            else:
                state, fit, sentence = "checking", "match", "Found, and still being checked against your profile."
        else:
            state, fit, sentence = "ready", "match", _line(reason, source)
        roles = roles_memo.get(title)
        if roles is None:
            roles = roles_memo[title] = fitting_roles(title, profile.target_roles)
        row = {
            "id": jid, "title": title, "company": company, "location": " ".join(us_places(job.get("location")).split()), "url": job["url"], "ats": job.get("ats") or "",
            "posted_at": job.get("posted_at") or job.get("created_at") or "", "dated": dated(job), "source": source,
            "score": None if restricted and not job.get("score") else job.get("score"),
            "state": state, "fit": fit, "line": sentence[:400], "position": positions.get(jid), "approval": ("over" if code == "employer_limit" else "reserved") if state == "approval" else "",
            "can_add": state != "queue" and not restricted and code not in {"employer_limit", "reserved"} and jid not in reserved,
            "roles": roles, "places": places_of(job.get("location")), "level": levels.get(jid, "Unknown"), "reserved": (found.employer if (found := reserving(company, job.get("url"))) else ""),
        }
        key = key_of(row)
        kept = out.get(key)
        if kept is None:
            out[key] = row
            continue
        hidden["twin"] += 1
        # The row in line wins, then the better ranked; the employer's own link wins over a list's short link either way.
        rank = lambda r: (r["position"] is not None, r["state"] != "checking", r["score"] if r["score"] is not None else -1)
        first, second = (row, kept) if rank(row) > rank(kept) else (kept, row)
        if is_shortlink(first["url"]) and not is_shortlink(second["url"]):
            first = {**first, "url": second["url"]}
        out[key] = first
    # A company's own site is on its own list (company_sites.py): never among the rows a batch, Select all or the autopilot's board reads.
    from .company_sites import is_company_site

    rows = [row for row in out.values() if not is_company_site(row["ats"])]
    sites = [row for row in out.values() if is_company_site(row["ats"])]
    at = datetime.now(UTC)
    window = at - timedelta(hours=cfg.max_age_hours)
    with db.session() as s:
        from .db import Setting

        started = s.scalar(
            select(func.max(Setting.value["started_at"].as_string())).where(Setting.key.startswith("job_board_scan:", autoescape=True))
        )
        scanning = s.scalar(
            select(func.count()).select_from(Setting).where(
                Setting.key.startswith("job_board_scan:", autoescape=True),
                Setting.value["state"].as_string().in_(("queued", "running")),
                Setting.value["started_at"].as_string() >= (at - timedelta(hours=6)).isoformat(),
            )
        )
        # The boards of the latest search: how far through them it is. Every board of one search shares its start time.
        # Every board of one search was queued at its request time; a board that has run carries its own, later, start time.
        read = total = 0
        wanted = (db.get_setting("job_board_requested", {}) or {}).get("at") or started
        if wanted and scanning:
            ours = (Setting.key.startswith("job_board_scan:", autoescape=True), Setting.value["started_at"].as_string() >= wanted)
            total = s.scalar(select(func.count()).select_from(Setting).where(*ours)) or 0
            read = s.scalar(
                select(func.count()).select_from(Setting).where(*ours, Setting.value["state"].as_string().in_(("complete", "error")))
            ) or 0
    requested = db.get_setting("job_board_requested", {}).get("at")
    return {
        "rows": rows,
        "sites": sites,
        "hidden": hidden,
        "searched_at": max(filter(None, (started, requested)), default=None),
        "scanning": scanning or 0,
        "scan_read": read,
        "scan_total": total,
        "enabled": cfg.enabled,
        "window": cfg.max_age_hours,
        "live_window": cfg.window_hours("greenhouse"),
        "oldest": window.isoformat(),
    }


def with_holds(value, service, positions):
    """The board as a change to the reserved list leaves it: each row's reservation, and what follows from it, without cleaning the board
    again. A row in line stays in line. A row released takes the state its job's status gives until the next read."""
    from .db import Job
    from .reserved import held, matcher

    db = service.db
    with db.session() as s:
        holding, reserving = set(held(s)), matcher(s)
        released = [r["id"] for rows in (value["rows"], value["sites"]) for r in rows if r["approval"] == "reserved" and r["id"] not in holding]
        jobs = {row.id: row for row in s.execute(select(Job.id, Job.status, Job.reason).where(Job.id.in_(released)))} if released else {}

    def changed(row):
        found = reserving(row["company"], row["url"])
        new = {**row, "reserved": found.employer if found else ""}
        if row["id"] in positions:
            return new
        if row["id"] in holding:
            return {**new, "state": "approval", "fit": "match", "position": None, "approval": "reserved", "can_add": False,
                    "line": "A reserved employer: nothing is applied there until you approve this job."}
        job = jobs.get(row["id"])
        if job is None:
            return new
        restricted = _restricted(job.reason)
        if job.status == "skipped":
            state, fit, sentence = "below", "other", _line(job.reason, row["source"]) or "Below your minimum fit."
        elif job.status == "new":
            state, fit, sentence = "checking", "match", "Found, and still being checked against your profile."
        else:
            state, fit, sentence = "ready", "match", _line(job.reason, row["source"])
        return {**new, "state": state, "fit": fit, "line": sentence[:400], "approval": "", "can_add": not restricted}

    return {**value, "rows": [changed(r) for r in value["rows"]], "sites": [changed(r) for r in value["sites"]]}


class Board:
    """The latest cleaned board, shared by every poll."""

    def __init__(self):
        self.value, self.at, self.busy = None, 0.0, threading.Lock()
        self.changes = 0  # counts changes to the reserved list: a board read begun before the last one is out of date when it ends

    def _store(self, service):
        changes = self.changes
        value = collect(service)
        if changes == self.changes or self.value is None:
            self.value, self.at = value, time.monotonic()

    def _fill(self, service):
        try:
            self._store(service)
        except Exception:
            # Said, not swallowed: a read that always failed left a fresh install on a loading screen for good (2026-11-01).
            logging.getLogger(__name__).exception("The job board could not be read")
            self.at = time.monotonic() - TTL / 2  # try again shortly; keep serving the last board
        finally:
            self.busy.release()

    def ready(self, service):
        """The latest board at once, or None on a cold start: the first read waits for the first screening, which takes seconds, and
        the page paints without it. A stale one is read again in the background."""
        if (self.value is None or time.monotonic() - self.at >= TTL) and self.busy.acquire(blocking=False):
            threading.Thread(target=self._fill, args=(service,), name="job-board-read", daemon=True).start()
        return self.value

    def stale(self):
        self.at = 0.0

    def reload(self, service):
        """Clean the board again now, on the calling thread (the user changed what holds a job)."""
        with self.busy:
            self._store(service)

    def holds_changed(self, service, positions=()):
        """The reserved list changed: say so on the rows already read, at once, and leave cleaning the board to the read that follows."""
        self.changes += 1
        if self.value is not None:
            self.value = with_holds(self.value, service, positions)


def board(service):
    if not hasattr(service, "board_view"):
        service.board_view = Board()
    return service.board_view


LOGO_AHEAD = 150  # Companies whose icons are looked up before anyone scrolls to them: the first pages of the default order.


def companies_to_warm(service, limit=LOGO_AHEAD):
    """The companies of the best-ranked open postings, once each, in the order a person meets them. An icon is looked up only
    after a page asks for it, two companies at a time, so a page of 25 filled its letters with logos over a minute and the next
    page started over: 336 of 1,012 companies on the board had one (2026-11-02)."""
    data = board(service).value
    if not data:
        return []
    ranked = sorted((row for row in data["rows"] if row["fit"] == "match"), key=lambda r: (-(r["score"] if r["score"] is not None else -1), r["id"]))
    return list(dict.fromkeys(row["company"] for row in ranked))[:limit]


def warm_logos(service, companies):
    """Queue the icons of these companies for the background lookup (logos.Shelf): never waits on the network. Runs on the event loop."""
    from .db import Job
    from .logos import shelf

    if not service.config.company_logos or not companies:
        return 0

    def postings(company):
        with service.db.session() as s:
            rows = s.execute(select(Job.url, Job.description).where(Job.company == company).order_by(Job.created_at.desc()).limit(6)).all()
        return [(u, (d or "")[:20_000]) for u, d in rows]

    kept = shelf(service.config.data_dir)
    if not kept.scanned:
        kept.scan()
    before = len(kept.waiting)
    for company in companies:
        kept.request(company, postings)
    return len(kept.waiting) - before


PER_COMPANY = 3  # rows one company shows before "N more at X": one employer posting hundreds of roles filled the first pages
LEVEL_NAMES = ("Intern", "New grad", "Entry", "Mid", "Senior", "Unknown")


def page(service, q="", role="all", company="all", place="all", posted="all", platform="all", fit="match", sort="fit", number=1, size=25, level="all", group=True, want="page", section="main"):
    """One page of the board, and each filter's choices with how many rows choosing it would show (every other filter kept).
    `section`: "sites" reads the company-site jobs (company_sites.py), which are never among the main rows.
    `want`: "rows" leaves the filters' counts out (a panel that shows a few rows); "ids" answers only what Select all selects.

    Grouped (the default, while no company is chosen) a company shows its best PER_COMPANY rows in the order asked for, the last
    carrying `more`: how many it hides. Every count here, the total and each filter's, is of the rows shown, so a filter's number is
    the number of rows it opens; choosing the company shows them all and the company filter counts all of them."""
    data = board(service).ready(service)
    if data is None:
        return {
            "planning": True, "rows": [], "total": 0, "page": 1, "pages": 1, "size": size, "all": 0, "sites": 0, "section": section, "hidden": {}, "searched_at": None, "scanning": 0, "scan_read": 0, "scan_total": 0,
            "enabled": True, "window": 720, "selectable": 0, "ids": [], "facets": dict.fromkeys(("fit", "role", "company", "place", "platform", "posted", "level"), []),
        }
    rows = data["sites"] if section == "sites" else data["rows"]
    text = " ".join((q or "").casefold().split())
    now_at = datetime.now(UTC)
    since = (now_at - timedelta(hours=POSTED[posted][1])).isoformat() if posted in POSTED else ""

    def passes(row, skip=""):
        if skip != "fit" and fit != "all" and row["fit"] != fit:
            return False
        if skip != "role" and role != "all" and role not in row["roles"]:
            return False
        if skip != "company" and company != "all" and row["company"] != company:
            return False
        if skip != "place" and place != "all" and place not in row["places"]:
            return False
        if skip != "level" and level != "all" and row["level"] != level:
            return False
        if skip != "platform" and platform != "all" and row["ats"] != platform:
            return False
        if skip != "posted" and since and row["posted_at"] < since:
            return False
        return not text or text in f"{row['title']} {row['company']} {row['location']}".casefold()

    grouped = group and company == "all"

    def capped(per):
        return sum(min(n, PER_COMPANY) if grouped else n for n in per.values())

    def facet(skip, values, full=False):
        """{value: rows choosing it would show}: grouped, a company counts at most PER_COMPANY of its rows toward each value
        (`full`: all of them, for the company list, since choosing a company shows every one of its rows)."""
        seen = {}
        for row in rows:
            if passes(row, skip):
                for v in values(row):
                    per = seen.setdefault(v, {})
                    per[row["company"]] = per.get(row["company"], 0) + 1
        return {v: sum(per.values()) if full else capped(per) for v, per in seen.items()}

    def shown_count(skip, test):
        per = {}
        for row in rows:
            if passes(row, skip) and test(row):
                per[row["company"]] = per.get(row["company"], 0) + 1
        return capped(per)

    if sort == "newest":
        key = lambda r: (r["posted_at"], r["id"])
    elif sort == "company":
        key = lambda r: (r["company"].casefold(), -(r["score"] or -1), r["id"])
    else:
        key = lambda r: (-(r["score"] if r["score"] is not None else -1), r["posted_at"] and -_stamp(r["posted_at"]), r["id"])
    shown = sorted((row for row in rows if passes(row)), key=key, reverse=sort == "newest")
    more = {}
    if grouped:
        # A company's best rows in the order asked for; the last one kept carries how many it hides.
        count, tail, kept = {}, {}, []
        for row in shown:
            count[row["company"]] = count.get(row["company"], 0) + 1
            if count[row["company"]] <= PER_COMPANY:
                kept.append(row)
                tail[row["company"]] = row["id"]
        more = {tail[name]: n - PER_COMPANY for name, n in count.items() if n > PER_COMPANY}
        shown = kept
    # Select all takes what a person can queue, or move up the line: not what waits on an approval, or what the posting rules out.
    actionable = lambda row: row["can_add"] or row["state"] == "queue"
    if want == "ids":
        return {"ids": [row["id"] for row in shown if actionable(row)]}
    size = max(1, min(100, int(size)))
    pages = max(1, -(-len(shown) // size))
    number = max(1, min(int(number), pages))
    by_count = lambda counts: sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))
    keep = ("id", "title", "company", "location", "url", "ats", "posted_at", "dated", "source", "score", "state", "line", "position", "can_add", "level", "approval", "reserved")
    answer = {
        "rows": [{**{k: row[k] for k in keep}, "more": more.get(row["id"], 0)} for row in shown[(number - 1) * size : number * size]],
        "total": len(shown),
        "page": number,
        "pages": pages,
        "size": size,
    }
    if want == "rows":
        return answer
    levels = facet("level", lambda r: [r["level"]])
    return {
        **answer,
        "selectable": sum(1 for row in shown if actionable(row)),
        "all": len(rows),
        "sites": len(data["sites"]),
        "section": section,
        "grouped": grouped,
        "per_company": PER_COMPANY,
        "hidden": data["hidden"],
        "searched_at": data["searched_at"],
        "scanning": data["scanning"],
        "scan_read": data["scan_read"],
        "scan_total": data["scan_total"],
        "enabled": data["enabled"],
        "window": data["window"],
        "facets": {
            "fit": [{"value": k, "label": FITS[k], "count": shown_count("fit", lambda r: k == "all" or r["fit"] == k)} for k in FITS],
            "role": [{"value": k, "label": k, "count": n} for k, n in by_count(facet("role", lambda r: r["roles"]))],
            "level": [{"value": k, "label": k, "count": levels.get(k, 0)} for k in LEVEL_NAMES],
            "company": [{"value": k, "label": k, "count": n} for k, n in by_count(facet("company", lambda r: [r["company"]], full=True))][:300],
            "place": [{"value": k, "label": STATES.get(k, k), "count": n} for k, n in by_count(facet("place", lambda r: r["places"]))],
            "platform": [{"value": k, "label": k, "count": n} for k, n in by_count(facet("platform", lambda r: [r["ats"]]))],
            "posted": [
                {"value": k, "label": label, "count": shown_count("posted", lambda r: r["posted_at"] >= (now_at - timedelta(hours=hours)).isoformat())}
                for k, (label, hours) in POSTED.items()
            ],
        },
    }


def _stamp(value):
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def posting(db, job_id):
    """The job panel's posting: its text, the date and source of its listing, and the employer's own link where a short link is known."""
    from .db import Job, JobListing, Setting
    from .shortlinks import cached, is_shortlink

    with db.session() as s:
        row = s.execute(
            select(Job.url, Job.description, Job.created_at, JobListing.posted_at, JobListing.date_basis, JobListing.source_id, JobListing.match_reason, JobListing.career_level, JobListing.active, JobListing.last_seen)
            .outerjoin(JobListing, JobListing.job_id == Job.id)
            .where(Job.id == job_id)
        ).first()
        if row is None:
            return None
        link = row.url
        if is_shortlink(link):
            known, final = cached(s, link)
            link = final if known and final else link
        origin = s.get(Setting, "job_origin:" + job_id)
    job = {"date_basis": row.date_basis or "", "source_id": row.source_id or "", "origin": (origin.value or {}).get("kind") if origin else ""}
    return {
        "text": (row.description or "").strip()[:40000],
        "link": link,
        "posted_at": row.posted_at or row.created_at,
        "dated": dated(job),
        "source": source_of(job),
        "level": row.career_level or "",
        "listed": row.active,
        "last_seen": row.last_seen,
    }
