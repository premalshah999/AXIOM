"""What the job board reads from a stored job: employer, title, place, liveness, one entry per posting.

The owner's complaint about the board (2026-11-01): jobs with no title or employer, employers shown as a career-site host
("career-schwab.icims.com") or in three spellings ("Johns Hopkins APL" and "Johns Hopkins Applied Physics Laboratory", "C3.ai"
and "C3 AI"), "2 Locations" for a place, closed postings listed as live, one Workday requisition listed under two links, and
6,257 `new` jobs that no screen could ever show. Rules here hold for any employer; nothing is read from the network except in
`identify_online`, which asks the posting's own public ATS API.

Safety, in order: a job with any run behind it is never deleted, merged or re-pointed; its employer name changes only when the
application-limit and receipt identity (`name_aliases`) stays the same, or when it had no usable name. Two names are one
employer only on evidence: the same characters, or an employer tenant / single-employer host both appear under.
"""

import asyncio
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from urllib.parse import urlsplit

LEGAL = (
    r"inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|plc|gmbh|lp|l\.p|llp|pbc|n\.a|s\.a|ag"
)
LEGAL_TAIL = re.compile(rf"[,\s]+(?:{LEGAL})\.?$", re.I)
# A payroll entity code before the legal name ("00002 Citibank, N.A.", "1110 Invesco Group Services, INC."): leading zeros, or
# four or five digits ahead of a legal-form ending. A year ("2020 Companies") or a brand ("1800 Contacts") is a name.
ENTITY_CODE = re.compile(r"^(?:0\d{2,4}|(?!(?:19|20)\d\d\s|1?800\s)\d{4,5}(?=\s.*[,\s](?:inc|llc|ltd|corp|n\.a)\.?$))\s+(?=[A-Za-z])", re.I)
# "Duke Careers", "Perpay - Career's Page", "Five Rings LLC - Careers", "Graphcore Early Careers": the career site's own label.
SITE_LABEL = re.compile(
    r"(?:\s*[-–—|:]\s*|\s+)(?:early\s+careers?|campus\s+recruiting|university\s+recruiting|careers?(?:'s)?\s+(?:page|site|portal)|"
    r"careers?|jobs?\s+(?:board|page|portal)|job\s+listings?)\s*$",
    re.I,
)
PORTAL = re.compile(r"^.{0,60}?\b(?:job listings?|jobs|careers?)\s+at\s+(?P<name>.+)$", re.I)
GENERIC_COMPANY = re.compile(
    r"(?:n/?a|none|null|unknown|company|confidential|careers?|jobs?|apply|home|undefined|test|employer|the company)", re.I
)
HOSTNAME = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", re.I)
# Hosts that run many employers' career sites: a name that is only such a host is no employer.
ATS_DOMAINS = (
    "icims.com", "myworkdayjobs.com", "myworkdaysite.com", "greenhouse.io", "ashbyhq.com", "lever.co", "smartrecruiters.com",
    "workable.com", "bamboohr.com", "jobvite.com", "taleo.net", "oraclecloud.com", "successfactors.com", "successfactors.eu",
    "dayforcehcm.com", "paylocity.com", "ultipro.com", "adp.com", "rippling.com", "eightfold.ai", "avature.net", "csod.com",
    "applytojob.com", "jibeapply.com", "jobs2web.com", "breezy.hr", "recruitee.com", "teamtailor.com", "clearcompany.com",
)
CAREER_LABEL = re.compile(r"(?:careers?|jobs?|apply|talent|recruit\w*|hiring|work)(?:[-.]|$)", re.I)


def tidy(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def host_company(name):
    """A company field that is a career-site host ("career-schwab.icims.com", "careers.ey.com"), not an employer's name.
    "Checkout.com" and "incident.io" are employers: a host counts only on an ATS domain or behind a careers/jobs label."""
    text = tidy(name).casefold()
    if not HOSTNAME.fullmatch(text):
        return False
    labels = text.split(".")
    if any(text == d or text.endswith("." + d) for d in ATS_DOMAINS):
        return True
    return len(labels) >= 3 and bool(CAREER_LABEL.match(labels[0] + "."))


def junk_company(name):
    """Blank, a placeholder word, only a legal suffix, or a career-site host."""
    text = tidy(name)
    return (
        not text
        or bool(GENERIC_COMPANY.fullmatch(text.strip(" .")))
        or not re.search(r"[A-Za-z0-9]", LEGAL_TAIL.sub("", " " + text).strip() or "")
        or host_company(text)
    )


def clean_company(name):
    """The employer's name without whitespace noise, an entity code ("00002 Citibank, N.A."), a portal title ("Job Listings at
    X") or the career site's label ("Duke Careers"). A name that is nothing else is returned unchanged."""
    text = tidy(name)
    if not text:
        return ""
    if match := PORTAL.match(text):
        text = tidy(match["name"])
    text = ENTITY_CODE.sub("", text)
    for _ in range(2):
        stripped = SITE_LABEL.sub("", text).strip(" -–—|:,")
        if len(stripped) < 2:
            break
        text = stripped
    return text


def _words(name):
    text = re.sub(r"\b(?:the)\b", " ", tidy(name).casefold().replace("&", " and "))
    return [w for w in re.findall(r"[a-z0-9]+", text)]


def compact(name):
    """Letters and digits only, "the" dropped: case, spacing, dots and hyphens do not make another employer ("C3.ai" = "C3 AI")."""
    return "".join(_words(name))


def base_words(name):
    text = tidy(name)
    while True:
        stripped = LEGAL_TAIL.sub("", text).strip(" .,")
        if stripped == text or len(stripped) < 2:
            break
        text = stripped
    return _words(text)


def acronym_variant(a, b):
    """"Johns Hopkins APL" and "Johns Hopkins Applied Physics Laboratory": one name ends in an acronym that is the initials of
    the other's last words, and everything before is the same."""
    one, two = base_words(a), base_words(b)
    for short, long_ in ((one, two), (two, one)):
        if len(short) < 2 or len(long_) <= len(short) or not 2 <= len(short[-1]) <= 6:
            continue
        head, acronym = short[:-1], short[-1]
        if long_[: len(head)] == head and "".join(w[0] for w in long_[len(head):]) == acronym and len(long_) - len(head) == len(acronym):
            return True
    return False


def related(a, b):
    """Names that may denote one employer when evidence says they share one: same words once legal suffixes go, or an acronym."""
    one, two = base_words(a), base_words(b)
    return bool(one and one == two) or acronym_variant(a, b)


def prettiness(name):
    """Which spelling to show: mixed case over a slug or shouting, a short all-caps name is an acronym and fine."""
    text = tidy(name)
    if text.lower() != text and text.upper() != text:
        return 2
    if text.upper() == text and len(text) <= 5:
        return 1
    return 0


def clean_title(title, company=""):
    """A job title without the career site's page label ("Software Engineer | Careers | Example Co" -> "Software Engineer")."""
    from .posting_identity import PAGE_TITLE_PART

    text = tidy(title)
    if " | " not in text:
        return re.sub(r"\s+-\s+-\s+", " - ", text)
    parts = [p.strip() for p in text.split(" | ") if p.strip()]
    keep = [p for p in parts if not PAGE_TITLE_PART.fullmatch(p) and compact(p) != compact(company)]
    return keep[0] if keep and len(keep) < len(parts) else text


# --- places -------------------------------------------------------------------------------------------------------------

UNREADABLE_PLACE = re.compile(
    r"(?:\d+\s+locations?|multiple\s+locations?|various(?:\s+locations?)?|locations?|see\s+(?:job|posting|description)|tbd|n/?a)",
    re.I,
)
MORE_PLACES = re.compile(r"\s*\+\s*(\d+)\s*$")


def clean_location(place):
    """A place a person can read: "Hawthorne, CA +2" becomes "Hawthorne, CA (+2 more)", and a count in place of a place
    ("2 Locations") is no place at all (the posting's own page names them: `identify_online`)."""
    text = tidy(place)
    if UNREADABLE_PLACE.fullmatch(text.strip(" .")):
        return ""
    parts = []
    for part in re.split(r"\s*;\s*", text):
        if match := MORE_PLACES.search(part):
            part = part[: match.start()].rstrip() + f" (+{match[1]} more)"
        parts.append(part)
    return "; ".join(p for p in parts if p)


# --- internships ----------------------------------------------------------------------------------------------------------

INTERNSHIP = re.compile(r"\b(?:intern(?:ship)?s?|co-?ops?)\b", re.I)
SEASON_YEAR = re.compile(
    r"\b(spring|summer|fall|autumn|winter)\b[\s,_/-]*(?:semester|term|session)?[\s,_/-]*(?:of\s+)?'?((?:20)?\d\d)\b|"
    r"\b((?:20)\d\d)\b[\s,_/-]*(spring|summer|fall|autumn|winter)\b",
    re.I,
)
SEASON_END = {"spring": (6, 30), "summer": (9, 30), "fall": (12, 31), "autumn": (12, 31), "winter": (3, 31)}
YEAR_INTERN = re.compile(r"\b(20\d\d)\s+(?:\w+\s+){0,2}(?:intern(?:ship)?s?|co-?ops?)\b", re.I)


def expired_internship(title, at=None):
    """An internship whose named term has ended ("Summer 2026 Intern" in November 2026). Only a title that names the term is
    judged; the latest term named counts, and a year with no season ends on 31 December."""
    text = tidy(title)
    if not INTERNSHIP.search(text):
        return False
    at = at or datetime.now(UTC)
    ends = []
    for m in SEASON_YEAR.finditer(text):
        season, year = (m[1], m[2]) if m[1] else (m[4], m[3])
        year = int(year) + (2000 if int(year) < 100 else 0)
        month, day = SEASON_END[season.casefold()]
        ends.append(datetime(year, month, day, 23, 59, tzinfo=UTC))
    if not ends and (m := YEAR_INTERN.search(text)):
        ends.append(datetime(int(m[1]), 12, 31, 23, 59, tzinfo=UTC))
    return bool(ends) and max(ends) < at


# --- one Workday requisition under two links -------------------------------------------------------------------------------

def workday_twin_key(url):
    """(tenant host, place segment, final path segment) of a Workday posting, else None. The locale and the career-site name
    in the path are not the posting's."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").casefold()
    if not (host.endswith(".myworkdayjobs.com") or host.endswith(".myworkdaysite.com")):
        return None
    path = [p for p in parts.path.split("/") if p]
    tail = path[path.index("job") + 1 :] if "job" in path else []
    return (host, "/".join(tail[:-1]).casefold(), tail[-1].casefold()) if tail else None


REPOST_SUFFIX = re.compile(r"^(?P<base>.*\d)-(?P<n>\d{1,2})$")


def twin_groups(rows):
    """{key: [row, ...]} of Workday postings that are one requisition under several links: the same place and final segment
    (the career site's name and the locale differ), or a segment and the same segment with the repost suffix "-1" appended
    when the unsuffixed one exists too. Two different requisitions with the same title never meet: their ids differ."""
    index = defaultdict(list)
    for row in rows:
        if key := workday_twin_key(row["url"]):
            index[key].append(row)
    groups = defaultdict(list)
    for key, members in index.items():
        host, place, segment = key
        match = REPOST_SUFFIX.match(segment)
        target = key
        if match and (host, place, match["base"]) in index:
            target = (host, place, match["base"])
        groups[target].extend(members)
    return {k: v for k, v in groups.items() if len(v) > 1}


# --- the stored rows --------------------------------------------------------------------------------------------------------

UNLISTED_AGE_MINUTES = 60  # A scan creates the job, then its listing, in two transactions.


def _evidence(url, company):
    """What a job's link says about its employer: its ATS tenant, else the host when only this employer is on it."""
    from .features.knowledge.employers import tenant_alias

    return tenant_alias(url, company) or tenant_alias(url, "")


def _hosts(rows):
    """{host: set of employer first words}: a host with many employers (a link shortener, a feed) names none of them."""
    seen = defaultdict(set)
    for row in rows:
        host = (urlsplit(row["url"]).hostname or "").casefold()
        words = _words(row["company"])
        if host and words and not junk_company(row["company"]):
            seen[host].add(words[0])
    return seen


def _single_host(url, hosts):
    host = (urlsplit(url).hostname or "").casefold()
    registrable = ".".join(host.split(".")[-2:])
    if not host or registrable in ATS_DOMAINS or any(host.endswith("." + d) or host == d for d in ATS_DOMAINS):
        return None
    return "host:" + host if len(hosts.get(host, ())) <= 1 else None


def _job_rows(session):
    from sqlalchemy import select

    from .db import Job

    runs = _run_jobs(session)
    return [
        {"id": i, "url": u, "title": t, "company": c, "location": loc, "status": st, "runs": i in runs}
        for i, u, t, c, loc, st in session.execute(
            select(Job.id, Job.url, Job.title, Job.company, Job.location, Job.status).where(Job.demo.is_(False))
        )
    ]


def _run_jobs(session):
    from sqlalchemy import select

    from .db import Run

    return set(session.scalars(select(Run.job_id).distinct()))


def plan_companies(rows):
    """{job id: new company}. Cleaned names first, then a name that names no employer takes the real name its tenant's other
    jobs carry, then spellings of one employer become the most used, best-cased one."""
    from .features.knowledge.employers import name_aliases

    changes = {}
    cleaned = {r["id"]: clean_company(r["company"]) for r in rows}
    hosts = _hosts(rows)
    evidence = {r["id"]: _evidence(r["url"], cleaned[r["id"]]) or _single_host(r["url"], hosts) for r in rows}
    weight = Counter()
    for r in rows:
        if not junk_company(cleaned[r["id"]]):
            weight[cleaned[r["id"]]] += 1 + 10 * r["runs"]
    by_evidence = defaultdict(Counter)
    for r in rows:
        if (ev := evidence[r["id"]]) and not junk_company(cleaned[r["id"]]):
            by_evidence[ev][cleaned[r["id"]]] += 1 + 10 * r["runs"]

    def best(names):
        return max(names, key=lambda n: (prettiness(n), weight[n], -len(n), n))

    canonical = {}
    # Same characters anywhere (case, spacing, dots), or related spellings under one tenant / single-employer host.
    by_compact = defaultdict(set)
    for name in weight:
        by_compact[compact(name)].add(name)
    for names in by_compact.values():
        if len(names) > 1:
            canonical.update({n: best(names) for n in names})
    for names in by_evidence.values():
        names = list(names)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                if compact(a) != compact(b) and related(a, b):
                    pool = {a, b, canonical.get(a, a), canonical.get(b, b)}
                    winner = best(pool)
                    for n in pool:
                        canonical[n] = winner
    for r in rows:
        name = cleaned[r["id"]]
        if junk_company(name):
            peers = by_evidence.get(evidence[r["id"]])
            name = best(list(peers)) if peers else name
        name = canonical.get(name, name)
        if name == r["company"] or junk_company(name):
            continue
        # A job with runs keeps the employer identity its receipts and caps were counted under.
        if r["runs"] and not junk_company(r["company"]):
            if (name_aliases(name) or [""])[-1] != (name_aliases(r["company"]) or [""])[-1]:
                continue
        changes[r["id"]] = name
    return changes


def tidy_rows(db):
    """Company, title and location of stored jobs and their listings. Returns counts."""
    from sqlalchemy import select

    from .db import Job, JobListing

    report = {"company": 0, "title": 0, "location": 0, "listing_location": 0}
    with db.exclusive() as s:
        rows = _job_rows(s)
        companies = plan_companies(rows)
        jobs = {j.id: j for j in s.scalars(select(Job).where(Job.demo.is_(False)))}
        for row in rows:
            job = jobs[row["id"]]
            if job.id in companies:
                job.company = companies[job.id]
                report["company"] += 1
            title = clean_title(job.title, job.company)
            if title != job.title:
                job.title, report["title"] = title, report["title"] + 1
            place = clean_location(job.location)
            if place != job.location:
                job.location, report["location"] = place, report["location"] + 1
        for entry in s.scalars(select(JobListing)):
            place, job = clean_location(entry.location), jobs.get(entry.job_id)
            if not place and job and job.location:
                place = job.location
            if place != entry.location:
                entry.location, report["listing_location"] = place, report["listing_location"] + 1
            if place and job and not job.location:
                job.location, report["location"] = place, report["location"] + 1
    return report


def retire_listings(db, only_jobs=None):
    """Listings of closed jobs (the posting is gone, or the applicant cannot take it), expired internships and the extra links of
    one Workday requisition stop being live. Nothing is deleted or merged: the job and its history stay, and a scan does not
    re-open a closed job's listing. Returns counts."""
    from sqlalchemy import select

    from .db import Job, JobListing, Setting

    report = {"closed": 0, "internship": 0, "duplicate": 0}
    at = datetime.now(UTC)
    with db.exclusive() as s:
        runs = _run_jobs(s)
        protected = {
            key.partition(":")[2]
            for key in s.scalars(
                select(Setting.key).where(Setting.key.startswith("owner_apply:") | Setting.key.startswith("job_control:"))
            )
        }
        live = list(
            s.execute(
                select(JobListing, Job)
                .join(Job, Job.id == JobListing.job_id)
                .where(JobListing.active.is_(True), Job.demo.is_(False))
                .where(Job.id.in_(only_jobs) if only_jobs else Job.id.is_not(None))
            )
        )
        for entry, job in live:
            if job.status == "closed":
                entry.active = False
                report["closed"] += 1
            elif job.id not in runs and expired_internship(job.title, at):
                entry.active = False
                report["internship"] += 1
        rows = [{"id": job.id, "url": job.url, "entry": entry, "job": job} for entry, job in live if entry.active]
        for group in twin_groups(rows).values():
            keeper = max(
                group,
                key=lambda r: (r["job"].id in runs, r["job"].status in {"confirmed", "already_applied"}, r["entry"].last_seen),
            )
            for r in group:
                if (
                    r is not keeper
                    and r["job"].id not in runs
                    and r["job"].id not in protected
                    and r["job"].status in {"new", "skipped", "ready"}
                ):
                    r["entry"].active = False
                    report["duplicate"] += 1
    return report


def drop_unshown_stubs(db):
    """`new` jobs with no listing and no run: an import stored a board's every posting before scans were screened (5,604 in a
    minute, 2026-11-01), and the autopilot and the board read listings only, so none of them could ever be ranked or shown.
    A scan lists the ones that fit its filters again. Housekeeping's own deletion (which also clears the job's settings and
    classification rows) removes them, and a job anyone added by hand, held, requested or paused stays."""
    import sqlalchemy as sa
    from sqlalchemy import select

    from . import housekeeping
    from .db import Job, JobListing, Setting

    cutoff = (datetime.now(UTC).timestamp() - UNLISTED_AGE_MINUTES * 60)
    with db.session() as s:
        runs = _run_jobs(s)
        listed = set(s.scalars(select(JobListing.job_id)))
        protected = housekeeping._protected(s)
        manual = {
            key.partition(":")[2]
            for key, value in s.execute(select(Setting.key, Setting.value).where(Setting.key.startswith("job_origin:")))
            if isinstance(value, dict) and value.get("kind") not in {"source", "new_grad_feed"}
        }
        doomed = [
            job_id
            for job_id, created in s.execute(select(Job.id, Job.created_at).where(Job.status == "new", Job.demo.is_(False)))
            if job_id not in runs and job_id not in listed and job_id not in protected and job_id not in manual
            and _before(created, cutoff)
        ]
        keys = housekeeping._setting_keys(s)
        committed = set()
    if not doomed:
        return 0
    tables = [name for name in housekeeping.JOB_TABLES if sa.inspect(db.engine).has_table(name)]
    removed = 0
    for start in range(0, len(doomed), housekeeping.BATCH):
        jobs, _ = housekeeping._delete_jobs(db, doomed[start : start + housekeeping.BATCH], keys, tables, committed)
        removed += len(jobs)
    return removed


def _before(stamp, cutoff):
    try:
        return datetime.fromisoformat(stamp).timestamp() < cutoff
    except (TypeError, ValueError):
        return True


def backfill(db):
    """The startup pass over stored rows (Service.tidy_board_data). Safe to repeat: a second pass changes nothing."""
    report = {}
    report["stubs_removed"] = drop_unshown_stubs(db)
    report.update(tidy_rows(db))
    report.update({"retired_" + k: v for k, v in retire_listings(db).items()})
    return report


# --- the posting's own words ------------------------------------------------------------------------------------------------

GREENHOUSE_EMBED = re.compile(r"^https://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/embed/job_app\?(?P<q>.*)$")
GREENHOUSE_JOB = re.compile(r"^https://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?P<slug>[^/?#]+)/jobs/(?P<id>\d+)")


def greenhouse_ids(url):
    from urllib.parse import parse_qs

    if m := GREENHOUSE_JOB.match(url):
        return m["slug"], m["id"]
    if m := GREENHOUSE_EMBED.match(url):
        q = parse_qs(m["q"])
        if q.get("for") and q.get("token"):
            return q["for"][0], q["token"][0]
    return None


ASHBY_JOB = re.compile(r"^https://jobs\.ashbyhq\.com/(?P<slug>[^/?#]+)/(?P<id>[0-9a-f-]{36})", re.I)
WORKABLE_JOB = re.compile(r"^https://apply\.workable\.com/(?P<slug>[^/?#]+)/j/(?P<id>[0-9A-F]+)", re.I)


async def _get(client, url):
    try:
        r = await client.get(url)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


async def identify_online(db, limit=150, concurrency=4):
    """Fill a blank title, a blank or host-named employer and an unreadable place ("2 Locations") from the posting's own public
    ATS API: Greenhouse's board and job, Workday's job detail. Only a blank or junk value is replaced, never one a person or a
    page gave; a posting the API no longer lists stays as it was. Returns counts."""
    import httpx
    from sqlalchemy import select

    from .db import Job, JobListing

    report = {"company": 0, "title": 0, "location": 0, "asked": 0}
    with db.session() as s:
        todo = [
            (i, u, t, c, loc)
            for i, u, t, c, loc in s.execute(
                select(Job.id, Job.url, Job.title, Job.company, Job.location).where(Job.demo.is_(False)).order_by(Job.created_at.desc())
            )
            if (not t.strip() or junk_company(c) or not clean_location(loc))
            and (greenhouse_ids(u) or workday_twin_key(u) or ASHBY_JOB.match(u) or WORKABLE_JOB.match(u))
        ][:limit]
        listed = {i: e for i, e in s.execute(select(JobListing.job_id, JobListing.location))}
    gate = asyncio.Semaphore(concurrency)
    boards = {}

    async def one(client, job_id, url, title, company, place):
        found = {}
        async with gate:
            if ids := greenhouse_ids(url):
                slug, posting = ids
                if slug not in boards:
                    boards[slug] = await _get(client, f"https://boards-api.greenhouse.io/v1/boards/{slug}")
                info = await _get(client, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{posting}")
                if boards[slug] and junk_company(company) and (boards[slug].get("name") or "").strip():
                    found["company"] = clean_company(boards[slug]["name"])
                if info and not title.strip() and (info.get("title") or "").strip():
                    found["title"] = clean_title(info["title"], found.get("company", company))
                if info and not clean_location(place) and isinstance(info.get("location"), dict):
                    found["location"] = clean_location(info["location"].get("name", ""))
            elif m := ASHBY_JOB.match(url):
                # Ashby's board lists a title per posting but not the employer's name (that comes from its other jobs).
                if "ashby:" + m["slug"] not in boards:
                    data = await _get(client, f"https://api.ashbyhq.com/posting-api/job-board/{m['slug']}")
                    boards["ashby:" + m["slug"]] = {j.get("id"): j for j in (data or {}).get("jobs", [])}
                job = boards["ashby:" + m["slug"]].get(m["id"].lower()) or boards["ashby:" + m["slug"]].get(m["id"])
                if job and not title.strip() and (job.get("title") or "").strip():
                    found["title"] = clean_title(job["title"], company)
            elif m := WORKABLE_JOB.match(url):
                key = "workable:" + m["slug"]
                if key not in boards:
                    boards[key] = await _get(client, f"https://apply.workable.com/api/v1/widget/accounts/{m['slug']}")
                account = boards[key] or {}
                if junk_company(company) and (account.get("name") or "").strip():
                    found["company"] = clean_company(account["name"])
                posting = next((j for j in account.get("jobs") or [] if str(j.get("shortcode", "")).upper() == m["id"].upper()), None)
                if posting and not title.strip() and (posting.get("title") or "").strip():
                    found["title"] = clean_title(posting["title"], found.get("company", company))
            elif key := workday_twin_key(url):
                host = key[0]
                path = urlsplit(url).path.split("/")
                tenant, parts = host.split(".")[0], [p for p in path if p]
                if "job" in parts and parts.index("job") > 0:
                    site = parts[parts.index("job") - 1]
                    info = await _get(client, f"https://{host}/wday/cxs/{tenant}/{site}/" + "/".join(parts[parts.index("job") :]))
                    posting = (info or {}).get("jobPostingInfo") or {}
                    places = [posting.get("location", "")] + [x for x in posting.get("additionalLocations") or [] if isinstance(x, str)]
                    joined = clean_location(" / ".join(dict.fromkeys(p for p in places if p)))
                    if joined and not clean_location(place):
                        found["location"] = joined
                    if posting.get("title") and not title.strip():
                        found["title"] = clean_title(posting["title"], company)
        return job_id, found

    async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0"}) as client:
        results = await asyncio.gather(*(one(client, *item) for item in todo), return_exceptions=True)
    report["asked"] = len(todo)
    with db.exclusive() as s:
        for result in results:
            if isinstance(result, BaseException):
                continue
            job_id, found = result
            job, entry = s.get(Job, job_id), s.get(JobListing, job_id)
            if not job or not found:
                continue
            if found.get("company") and junk_company(job.company):
                job.company = found["company"]
                report["company"] += 1
            if found.get("title") and not job.title.strip():
                job.title = found["title"]
                report["title"] += 1
            if found.get("location") and not clean_location(job.location):
                job.location = found["location"]
                report["location"] += 1
            if found.get("location") and entry and not clean_location(entry.location):
                entry.location = found["location"]
    return report


BOARD_SLUG = re.compile(
    r"^https://(?:jobs\.ashbyhq\.com|jobs(?:\.eu)?\.lever\.co|apply\.workable\.com|(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io"
    r"|jobs\.smartrecruiters\.com)/(?!embed/)(?P<slug>[A-Za-z0-9][A-Za-z0-9._-]*)(?:[/?#]|$)"
)


def slug_company(url):
    """The employer's board name from its own ATS address ("jobs.ashbyhq.com/nooks/..." -> "Nooks"): the last resort for a job
    whose employer is still blank after its posting's API was asked, shown rather than nothing. A slug that is already cased
    is kept as written."""
    from urllib.parse import parse_qs

    match = BOARD_SLUG.match(url or "")
    slug = match["slug"] if match else (parse_qs(urlsplit(url or "").query).get("for") or [""])[0] if "embed/job_app" in (url or "") else ""
    if not slug or len(slug) < 2:
        return ""
    return slug if slug.lower() != slug else " ".join(w.capitalize() for w in re.split(r"[-_]+", slug) if w)


def name_from_board_slugs(db):
    """Blank or host-named employers that still have no name take their board's name (slug_company). Returns the count."""
    from sqlalchemy import select

    from .db import Job

    count = 0
    with db.exclusive() as s:
        for job in s.scalars(select(Job).where(Job.demo.is_(False))):
            if junk_company(job.company) and (name := slug_company(job.url)):
                job.company, count = name, count + 1
    return count


def measure(db, at=None):
    """Counts of each class the board's data can be wrong in, for a before and after."""
    from sqlalchemy import func, select

    from .db import Job, JobListing, Run
    from .jobboard import us_location

    at = at or datetime.now(UTC)
    with db.session() as s:
        rows = _job_rows(s)
        listings = list(s.execute(select(JobListing.job_id, JobListing.active, JobListing.location, JobListing.posted_at, JobListing.first_seen)))
        statuses = dict(s.execute(select(Job.id, Job.status)).all())
        titles = dict(s.execute(select(Job.id, Job.title)).all())
        total = s.scalar(select(func.count()).select_from(Job))
    listed = {job_id for job_id, *_ in listings}
    live = [x for x in listings if x[1]]
    pretty = defaultdict(set)
    for r in rows:
        if not junk_company(r["company"]):
            pretty[compact(r["company"])].add(r["company"])
    from .posting_identity import placeholder_title

    return {
        "jobs": total,
        "blank_title": sum(not r["title"].strip() for r in rows),
        "blank_company": sum(not r["company"].strip() for r in rows),
        "blank_both_with_runs": sum(not r["title"].strip() and not r["company"].strip() and r["runs"] for r in rows),
        "placeholder_title": sum(bool(r["title"].strip()) and placeholder_title(r["title"]) for r in rows),
        "junk_company": sum(bool(r["company"].strip()) and junk_company(r["company"]) for r in rows),
        "company_dirty": sum(bool(r["company"].strip()) and clean_company(r["company"]) != r["company"] for r in rows),
        "company_case_variants": sum(len(v) > 1 for v in pretty.values()),
        "unreadable_location": sum(not clean_location(r["location"]) or clean_location(r["location"]) != r["location"] for r in rows if r["id"] in listed),
        "unreadable_listing_location": sum(not clean_location(x[2]) or clean_location(x[2]) != x[2] for x in live),
        "closed_but_live": sum(statuses.get(x[0]) == "closed" for x in live),
        "unshown_new": sum(r["status"] == "new" and r["id"] not in listed and not r["runs"] for r in rows),
        "live_listings": len(live),
        "live_without_posting_date": sum(not x[3] for x in live),
        "live_posting_date_is_discovery_time": sum(bool(x[3]) and x[3] == x[4] for x in live),
        "live_not_us": sum(not us_location(x[2], "US") for x in live),
        "live_expired_internship": sum(expired_internship(titles.get(x[0], ""), at) for x in live),
        "live_twin_links": sum(
            len(g) - 1
            for g in twin_groups(
                [{"url": r["url"], "id": r["id"]} for r in rows if r["id"] in {x[0] for x in live}]
            ).values()
        ),
    }
