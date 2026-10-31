"""New-grad aggregation across the public internet.

Three layers, all public and unauthenticated:

1. Curated new-grad feeds (SimplifyJobs, speedyapply …): broad, human-curated,
   with direct employer apply links.
2. A sweep of every employer board those feeds reference (Greenhouse, Lever,
   Ashby, Workday), plus the application's built-in catalog, using each ATS's
   public posting API. This finds new-grad roles that no list has picked up yet.
3. Strict policy: US location, posted within the window (verified feed/board
   timestamps or inferred relative ages; never first-seen alone), no
   internships or senior roles, and requisition-level dedup that keeps every
   source URL as evidence.

`collect()` only reads the internet. `ingest()` writes through the canonical
job import and is meant to be scheduled by the executor (see
DEVELOPER.md).
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from html import unescape
from urllib.parse import urlsplit

import httpx

from .policy import SENIOR, requisition_key

log = logging.getLogger(__name__)
FEEDS = [
    (
        "simplify",
        "json",
        "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    ),
    (
        "vanshb03",
        "json",
        "https://raw.githubusercontent.com/vanshb03/New-Grad-2026/dev/.github/scripts/listings.json",
    ),
    (
        "speedyapply-swe",
        "markdown",
        "https://raw.githubusercontent.com/speedyapply/2026-SWE-College-Jobs/main/NEW_GRAD_USA.md",
    ),
    (
        "speedyapply-ai",
        "markdown",
        "https://raw.githubusercontent.com/speedyapply/2026-AI-College-Jobs/main/NEW_GRAD_USA.md",
    ),
]
NEW_GRAD = re.compile(
    r"\b(?:new[ -]?(?:college[ -]?)?grad(?:uate)?s?|university[ -]?(?:grad(?:uate)?|hire)|recent[ -]?grad(?:uate)?s?|"
    r"early[ -]?career|entry[ -]?level|campus|graduate (?:engineer|program|programme|software|developer|analyst)|"
    r"class of 20\d\d|20(?:26|27) (?:start|grad)|junior|jr\.?|apprentice|rotational|"
    r"(?:engineer|developer|scientist|analyst)\s*(?:i|1)\b|level\s*(?:1|i)\b|associate (?:software|data|machine|ml)\w*)",
    re.I,
)
INTERN = re.compile(r"\bintern(?:ship)?s?\b|\bco-?op\b|\bwork[ -]?study\b", re.I)
# Roles outside software/AI/data that these feeds still list. Tuned on 407 ranked titles
# (2026-10-07): it removed 64, 63 of them scored below 40 and none the ranker found eligible.
# Deliberately absent: marketing, customer service, analyst, test, systems (real ML/SWE roles use them).
OFF_TARGET = re.compile(
    r"\b(?:technician|phlebotom\w*|nurs(?:e|ing)|pharmac\w*|medical assistant|welder|"
    r"sales (?:representative|associate|development)|account executive|business development|"
    r"recruiter|procurement|buyer|accountant|auditor|underwrit\w*|teller|personal banker|call center|warehouse|driver|"
    r"mechanical (?:design )?engineer|electrical (?:design |test |component )?engineer|civil engineer|chemical engineer|"
    r"structural engineer|manufacturing engineer|process engineer|equipment engineer|field service|"
    r"asic|fpga|analog|rf engineer|hardware engineer|pcb|serdes|ts/sci|polygraph|paralegal)\b",
    re.I,
)
WORKDAY_QUERIES = ("new grad", "university graduate", "early career", "entry level")


@dataclass
class Posting:
    company: str
    title: str
    url: str
    locations: list[str]
    posted_at: str  # ISO UTC
    basis: str  # verified | inferred
    source: str
    category: str = ""
    salary: str = ""
    sponsorship: str = ""
    sources: list[str] = field(default_factory=list)
    key: str = ""

    def row(self):
        d = asdict(self)
        d["locations"] = "; ".join(self.locations)
        d["sources"] = ", ".join(self.sources or [self.source])
        return d


# ---------- helpers ----------
def _strip(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html or ""))).strip()


def _age(text: str, now: datetime) -> datetime | None:
    m = re.search(r"(\d+)\s*(mo|m|w|d|h)\b", text or "", re.I)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    delta = {
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "w": timedelta(weeks=n),
        "mo": timedelta(days=30 * n),
        "m": timedelta(days=30 * n),
    }[unit]
    return now - delta


def _posted_on(text: str, now: datetime) -> datetime | None:
    """Workday 'Posted Today' / 'Posted 3 Days Ago'; '30+ Days Ago' is not datable."""
    t = (text or "").casefold()
    if "+" in t:
        return None
    if "today" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\s+day", t)
    return now - timedelta(days=int(m.group(1))) if m else None


def is_us(locations: list[str]) -> bool:
    from ...jobboard import us_location

    return any(
        us_location(loc)
        or re.search(r"\b(?:usa|united states|u\.s\.|remote in usa|us remote|remote,? us)\b", loc or "", re.I)
        for loc in locations
    )


# ---------- layer 1: curated feeds ----------
async def simplify_feed(client, name, url, now) -> list[Posting]:
    data = (await client.get(url)).json()
    out = []
    for x in data:
        if not x.get("active") or not x.get("is_visible", True) or not x.get("date_posted"):
            continue
        out.append(
            Posting(
                company=x.get("company_name", ""),
                title=x.get("title", ""),
                url=x.get("url", ""),
                locations=list(x.get("locations") or []),
                posted_at=datetime.fromtimestamp(int(x["date_posted"]), UTC).isoformat(),
                basis="verified",
                source=name,
                category=x.get("category", ""),
                sponsorship=x.get("sponsorship", ""),
            )
        )
    return out


async def markdown_feed(client, name, url, now) -> list[Posting]:
    text = (await client.get(url)).text
    out, header = [], None
    for line in text.splitlines():
        if not line.startswith("|"):
            header = None if not line.strip() else header
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        if header is None and any(
            c.casefold() in {"company", "position", "role", "job title"} for c in cells
        ):
            header = [c.casefold() for c in cells]
            continue
        if not header or len(cells) < len(header):
            continue
        row = dict(zip(header, cells, strict=False))
        company = _strip(row.get("company", "")) or ""
        title = _strip(row.get("position") or row.get("role") or row.get("job title") or "")
        links = re.findall(
            r'href="([^"]+)"|\]\((https?://[^)]+)\)',
            row.get("posting") or row.get("application") or row.get("apply") or line,
        )
        hrefs = [a or b for a, b in links]
        apply = next((h for h in reversed(hrefs) if h.startswith("http")), "")
        posted = _age(row.get("age") or row.get("date posted") or row.get("posted") or "", now)
        if not (company and title and apply and posted):
            continue
        out.append(
            Posting(
                company=company,
                title=title,
                url=apply,
                locations=[_strip(row.get("location", ""))],
                posted_at=posted.isoformat(),
                basis="inferred",
                source=name,
                salary=_strip(row.get("salary", "")),
            )
        )
    return out


# ---------- layer 2: employer board sweep ----------
def boards_from(postings: list[Posting]) -> set[tuple]:
    boards = set()
    for p in postings:
        u = urlsplit(p.url)
        host, parts = (u.hostname or "").casefold(), [x for x in u.path.split("/") if x]
        if host.endswith("greenhouse.io") and parts and parts[0] not in {"embed", "v1"}:
            boards.add(("greenhouse", parts[0].casefold(), p.company))
        elif host == "jobs.lever.co" and parts:
            boards.add(("lever", parts[0].casefold(), p.company))
        elif host == "jobs.ashbyhq.com" and parts:
            boards.add(("ashby", parts[0], p.company))
        elif host == "jobs.smartrecruiters.com" and parts:
            boards.add(("smartrecruiters", parts[0], p.company))
        elif host.endswith("myworkdayjobs.com") and parts:
            parts = parts[1:] if re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]) else parts
            if parts and parts[0] != "job":
                boards.add(("workday", f"{host}|{host.split('.')[0]}|{parts[0]}", p.company))
    return boards


def catalog_boards() -> set[tuple]:
    try:
        from ...jobboard import CATALOG
    except Exception:
        return set()
    out = set()
    for entry in CATALOG.get("smartrecruiters", []):
        slug = entry if isinstance(entry, str) else str(entry[0])
        out.add(("smartrecruiters", slug, slug))
    for entry in CATALOG.get("workday", []):
        if not isinstance(entry, str) and len(entry) > 1:
            fake = Posting(
                str(entry[0]), "", str(entry[1]).rstrip("/") + "/job/x", [], "", "verified", "catalog"
            )
            out |= boards_from([fake])
    for kind in ("greenhouse", "lever", "ashby"):
        for entry in CATALOG.get(kind, []):
            if isinstance(entry, str):
                slug = name = entry
            else:
                name, url = entry
                slug = urlsplit(url).path.strip("/").split("/")[0]
            out.add((kind, slug if kind == "ashby" else slug.casefold(), name))
    return out


async def sweep_board(client, kind, ident, company, now, queries=WORKDAY_QUERIES) -> list[Posting]:
    out = []
    if kind == "greenhouse":
        data = (await client.get(f"https://boards-api.greenhouse.io/v1/boards/{ident}/jobs")).json()
        for j in data.get("jobs", []):
            if j.get("first_published"):
                out.append(
                    Posting(
                        j.get("company_name") or company,
                        j.get("title", ""),
                        j.get("absolute_url", ""),
                        [(j.get("location") or {}).get("name", "")],
                        _iso(j["first_published"]),
                        "verified",
                        "board:greenhouse",
                    )
                )
    elif kind == "lever":
        data = (await client.get(f"https://api.lever.co/v0/postings/{ident}?mode=json")).json()
        for j in data if isinstance(data, list) else []:
            cats = j.get("categories") or {}
            locs = cats.get("allLocations") or [cats.get("location", "")]
            # createdAt describes creation, not verified publication.
            out.append(
                Posting(
                    company,
                    j.get("text", ""),
                    j.get("hostedUrl", ""),
                    locs,
                    datetime.fromtimestamp(j.get("createdAt", 0) / 1000, UTC).isoformat(),
                    "inferred",
                    "board:lever",
                )
            )
    elif kind == "ashby":
        data = (await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{ident}")).json()
        for j in data.get("jobs", []):
            if not j.get("isListed", True) or not j.get("publishedAt"):
                continue
            locs = [j.get("location", "")] + [
                x.get("location", "") for x in j.get("secondaryLocations") or []
            ]
            country = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "")
            if country:
                locs.append(country)
            out.append(
                Posting(
                    company,
                    j.get("title", "").strip(),
                    j.get("jobUrl", ""),
                    locs,
                    _iso(j["publishedAt"]),
                    "verified",
                    "board:ashby",
                )
            )
    elif kind == "smartrecruiters":
        seen = set()
        for query in ("graduate", "new grad", "entry level", "early career", "university"):
            r = await client.get(
                f"https://api.smartrecruiters.com/v1/companies/{ident}/postings",
                params={"limit": 100, "q": query},
            )
            if r.status_code != 200:
                break
            for j in r.json().get("content", []):
                if j.get("id") in seen or not j.get("releasedDate"):
                    continue
                seen.add(j["id"])
                loc = j.get("location") or {}
                out.append(
                    Posting(
                        company,
                        j.get("name", ""),
                        f"https://jobs.smartrecruiters.com/{ident}/{j['id']}",
                        [loc.get("fullLocation", ""), "United States" if loc.get("country") == "us" else ""],
                        _iso(j["releasedDate"]),
                        "verified",
                        "board:smartrecruiters",
                    )
                )
    elif kind == "workday":
        host, tenant, site = ident.split("|")
        seen = set()
        for query in queries:
            body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": query}
            r = await client.post(f"https://{host}/wday/cxs/{tenant}/{site}/jobs", json=body)
            if r.status_code != 200:
                break
            for j in r.json().get("jobPostings", []):
                path = j.get("externalPath", "")
                posted = _posted_on(j.get("postedOn", ""), now)
                if not path or path in seen or not posted:
                    continue
                seen.add(path)
                out.append(
                    Posting(
                        company,
                        j.get("title", ""),
                        f"https://{host}/en-US/{site}{path}",
                        [j.get("locationsText", "")],
                        posted.isoformat(),
                        "inferred",
                        "board:workday",
                    )
                )
    return out


def _iso(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat()


FIELDS = [
    ("Quant", r"quant|trad(?:er|ing)|market maker|\bhft\b"),
    (
        "AI/ML/Data",
        r"machine learning|\bml\b|\bai\b|artificial intelligence|data (?:scien|engineer|analy)|analytics|"
        r"deep learning|\bnlp\b|computer vision|research scien|\bllm|generative",
    ),
    (
        "Hardware",
        r"hardware|asic|fpga|silicon|\bdft\b|verification|circuit|electrical|embedded|firmware|rf\b|"
        r"mechanical|manufacturing|test engineer|systems engineer|design engineer|physical design",
    ),
    ("Product", r"product manager|\bapm\b|program manager|product analyst"),
    (
        "Software",
        r"software|developer|engineer|sde\b|swe\b|devops|\bsre\b|site reliability|full[ -]?stack|backend|"
        r"front[ -]?end|platform|cloud|security|ios|android|mobile|web",
    ),
]


def field_of(p: "Posting") -> str:
    simplify = {
        "Software": "Software",
        "AI/ML/Data": "AI/ML/Data",
        "Hardware": "Hardware",
        "Quant": "Quant",
        "Product": "Product",
        "Data Science, AI & Machine Learning": "AI/ML/Data",
    }
    if p.category in simplify:
        return simplify[p.category]
    for name, pattern in FIELDS:
        if re.search(pattern, p.title, re.I):
            return name
    return "Other"


# ---------- policy ----------
def keep(
    p: Posting,
    now: datetime,
    window_days: int,
    curated: bool,
    *,
    internships: bool = False,
    early_career_only: bool = False,
) -> str | None:
    """Reason to drop, or None. Internships and the early-career filters follow the applicant's settings."""
    try:
        posted = datetime.fromisoformat(p.posted_at)
    except ValueError:
        return "undated"
    if posted < now - timedelta(days=window_days) or posted > now + timedelta(hours=14):
        return "stale"
    if INTERN.search(p.title) and not internships:
        return "internship"
    if early_career_only and SENIOR.search(p.title):
        return "senior"
    if OFF_TARGET.search(p.title):
        return "off_target"
    if early_career_only and not curated and not NEW_GRAD.search(p.title):
        return "not_new_grad"
    if not p.locations or not is_us(p.locations):
        return "non_us"
    if not p.url.startswith("http"):
        return "no_link"
    return None


def merge(postings: list[Posting]) -> list[Posting]:
    """One row per requisition; earliest verified date wins; every source kept."""
    rank = {"verified": 2, "inferred": 1}
    by_key: dict[str, Posting] = {}
    for p in postings:
        p.key = requisition_key(p.url, p.title, "; ".join(p.locations), "", p.company)
        first = p.locations[0] if p.locations else ""
        alt = "t:" + re.sub(r"\W+", " ", f"{p.company} {p.title} {first}".casefold()).strip()
        cur = by_key.get(p.key) or by_key.get(alt)
        if cur is None:
            p.sources = [p.source]
            by_key[p.key] = by_key[alt] = p
            continue
        if p.source not in cur.sources:
            cur.sources.append(p.source)
        better = rank[p.basis] > rank[cur.basis] or (
            rank[p.basis] == rank[cur.basis] and p.posted_at < cur.posted_at
        )
        if better:
            cur.posted_at, cur.basis = p.posted_at, p.basis
        cur.locations = list(dict.fromkeys(cur.locations + p.locations))
        cur.category = cur.category or p.category
        cur.salary = cur.salary or p.salary
    unique = {id(p): p for p in by_key.values()}
    return sorted(unique.values(), key=lambda p: p.posted_at, reverse=True)


async def collect(
    window_days=7,
    *,
    feeds=FEEDS,
    sweep=True,
    concurrency=16,
    timeout=20.0,
    max_boards=1500,
    workday_queries=WORKDAY_QUERIES,
    extra_boards=(),
    internships=False,
    early_career_only=False,
):
    now = datetime.now(UTC)
    started = time.perf_counter()
    report = {"feeds": {}, "boards": {"swept": 0, "failed": 0, "postings": 0}, "dropped": {}}
    headers = {
        "User-Agent": "ApplyPilot discovery (public postings)",
        "Accept": "application/json, text/plain",
    }
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(
        timeout=timeout, headers=headers, limits=limits, follow_redirects=True
    ) as client:
        curated: list[Posting] = []
        for name, kind, url in feeds:
            try:
                rows = await (simplify_feed if kind == "json" else markdown_feed)(client, name, url, now)
                report["feeds"][name] = {"rows": len(rows)}
                curated += rows
            except Exception as exc:
                report["feeds"][name] = {"error": type(exc).__name__}
        swept: list[Posting] = []
        if sweep:
            unique = {}
            for kind, ident, company in boards_from(curated) | catalog_boards() | set(extra_boards):
                unique.setdefault((kind, ident), company)
            boards = [(k, i, c) for (k, i), c in sorted(unique.items())][:max_boards]
            gate = asyncio.Semaphore(concurrency)

            async def one(board):
                async with gate:
                    try:
                        rows = await sweep_board(client, *board, now, workday_queries)
                        report["boards"]["swept"] += 1
                        return rows
                    except Exception:
                        report["boards"]["failed"] += 1
                        return []

            for rows in await asyncio.gather(*(one(b) for b in boards)):
                swept += rows
            report["boards"]["postings"] = len(swept)
    kept = []
    for group, is_curated in ((curated, True), (swept, False)):
        for p in group:
            reason = keep(
                p, now, window_days, is_curated, internships=internships, early_career_only=early_career_only
            )
            if reason:
                report["dropped"][reason] = report["dropped"].get(reason, 0) + 1
            else:
                kept.append(p)
    result = merge(kept)
    for p in result:
        p.category = field_of(p)
    report["kept"] = len(result)
    report["by_field"] = {}
    for p in result:
        report["by_field"][p.category] = report["by_field"].get(p.category, 0) + 1
    report["by_source"] = {}
    for p in result:
        for s in p.sources:
            report["by_source"][s] = report["by_source"].get(s, 0) + 1
    report["only_from_board_sweep"] = sum(1 for p in result if all(s.startswith("board:") for s in p.sources))
    report["seconds"] = round(time.perf_counter() - started, 1)
    return result, report


def ingest(db, postings: list[Posting], source_id="feed:new-grad") -> dict:
    """Write through the canonical import (dedup, duplicate guard) with posting provenance."""
    from ...db import JobListing
    from ...discovery import add_job
    from ...schemas import JobInput
    from .policy import protected, register

    from ...schemas import Profile
    from .roles import matching_role, related_role

    from ... import shortlinks

    counts = {"created": 0, "existing": 0, "duplicate": 0, "protected": 0, "invalid": 0, "off_role": 0}
    # Each profile's target roles decide which curated postings become jobs (no target roles: all of them).
    roles = Profile.model_validate(db.get_setting("profile", {}) or {}).target_roles
    lookups = MAX_SHORTLINK_LOOKUPS
    for p in postings:
        if roles and not (matching_role(p.title, roles) or related_role(p.title, roles)):
            counts["off_role"] += 1
            continue
        # A feed's short link (go.vansh.nyc/XO) is stored as the posting it points to, so the job is the employer's own
        # posting: its identity matches an application already made there. Answers are saved, so a sweep asks only for
        # links it has not seen, a few per sweep; the rest keep the link and are resolved when first queued.
        url = p.url
        if shortlinks.is_shortlink(url):
            with db.session() as s:
                known, final = shortlinks.cached(s, url)
            if not known and lookups > 0:
                lookups -= 1
                final = shortlinks.resolve(url, db)
            url = final or url
        try:
            job, created = add_job(
                db,
                JobInput(
                    url=url,
                    title=p.title[:500],
                    company=p.company[:300],
                    location="; ".join(p.locations)[:500],
                ),
                origin="new_grad_feed",
            )
        except Exception:
            counts["invalid"] += 1
            continue
        with db.exclusive() as s:
            if protected(s, url):
                counts["protected"] += 1
                continue
            canonical, duplicate = register(
                s, {**job, "location": "; ".join(p.locations)}, source_id, p.posted_at, "Feed " + p.basis
            )
            if duplicate:
                counts["duplicate"] += 1
                continue
            listing = s.get(JobListing, job["id"])
            values = dict(
                source_id=source_id,
                posted_at=p.posted_at,
                date_basis=f"{p.source} ({p.basis})",
                location="; ".join(p.locations)[:500],
                country="US",
                career_level="New grad",
                match_reason="Curated new-grad feed"
                if not p.source.startswith("board:")
                else "New-grad title on the employer's board",
                active=True,
                last_seen=datetime.now(UTC).isoformat(),
            )
            if listing is None:
                s.add(JobListing(job_id=job["id"], **values))
            else:
                for k, v in values.items():
                    setattr(listing, k, v)
        counts["created" if created else "existing"] += 1
    return counts


if __name__ == "__main__":  # python -m jobpilot.features.discovery.feeds [days] > postings.json
    import sys

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    rows, rep = asyncio.run(collect(days))
    print(json.dumps({"report": rep, "postings": [p.row() for p in rows]}, indent=1))


SWEEP_KEY = "discovery_feed_run"

# The full listing histories, inactive rows included, and the internship repos: companies that hire interns post
# new-grad roles too, and a closed row still names a live board. (vanshb03's lists link through go.vansh.nyc
# redirects, which name no board.)
HISTORY_FEEDS = (
    ("simplify-history", "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"),
    ("simplify-internships", "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json"),
)
MAX_SHORTLINK_LOOKUPS = 60  # Short links asked per ingest (one HEAD each, at most one a second per host).
HARVEST_KEY = "discovery_board_harvest"
HARVEST_EVERY = timedelta(hours=20)
HARVEST_RECHECK = timedelta(days=14)  # A board that failed validation is not asked again for this long.
MAX_NEW_BOARDS = 150  # Validated and added per harvest.
MAX_VALIDATIONS = 400  # Public API calls per harvest.
BOARD_URL = {
    "greenhouse": "https://job-boards.greenhouse.io/{ident}",
    "lever": "https://jobs.lever.co/{ident}",
    "ashby": "https://jobs.ashbyhq.com/{ident}",
    "smartrecruiters": "https://jobs.smartrecruiters.com/{ident}",
}


def board_source_url(kind, ident):
    """The managed-source URL for a board ident (workday idents are host|tenant|site)."""
    if kind == "workday":
        host, _tenant, site = ident.split("|")
        return f"https://{host}/{site}"
    return BOARD_URL[kind].format(ident=ident)


def board_key(kind, ident):
    from urllib.parse import unquote

    return f"{kind}|{unquote(ident).casefold()}"


async def validate_board(client, kind, ident) -> bool:
    """Whether the board's own public API answers and lists at least one posting."""
    try:
        if kind == "greenhouse":
            r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{ident}/jobs")
            return r.status_code == 200 and bool(r.json().get("jobs"))
        if kind == "lever":
            r = await client.get(f"https://api.lever.co/v0/postings/{ident}?mode=json&limit=1")
            return r.status_code == 200 and bool(r.json())
        if kind == "ashby":
            r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{ident}")
            return r.status_code == 200 and bool(r.json().get("jobs"))
        if kind == "smartrecruiters":
            # An unknown company answers 200 with totalFound 0.
            r = await client.get(f"https://api.smartrecruiters.com/v1/companies/{ident}/postings", params={"limit": 1})
            return r.status_code == 200 and (r.json().get("totalFound") or 0) > 0
        if kind == "workday":
            host, tenant, site = ident.split("|")
            body = {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}
            r = await client.post(f"https://{host}/wday/cxs/{tenant}/{site}/jobs", json=body)
            return r.status_code == 200 and (r.json().get("total") or 0) > 0
    except Exception:
        return False
    return False


async def history_boards(client, feeds=HISTORY_FEEDS) -> dict:
    """{(kind, ident): company} for every board the feeds' full histories link to."""
    boards = {}
    for name, url in feeds:
        try:
            response = await client.get(url)
            data = await asyncio.to_thread(response.json)  # 13 MB of JSON, parsed off the loop
        except Exception as exc:
            log.warning("board harvest: %s unreadable (%s)", name, type(exc).__name__)
            continue
        rows = [
            Posting(x.get("company_name", ""), "", x["url"], [], "", "verified", name)
            for x in data
            if isinstance(x, dict) and x.get("url")
        ]
        for kind, ident, company in boards_from(rows):
            boards.setdefault((kind, ident), company)
    return boards


def managed_board_keys(db) -> set:
    from sqlalchemy import select

    from ...db import Source

    with db.session() as s:
        rows = s.execute(select(Source.kind, Source.url)).all()
    return {
        board_key(kind, ident)
        for kind, ident, _ in boards_from([Posting("", "", url + ("/job/x" if kind == "workday" else ""), [], "", "verified", "s") for kind, url in rows if url])
    }


async def harvest_boards(db, *, client=None, at=None, feeds=HISTORY_FEEDS, limit=MAX_NEW_BOARDS) -> dict:
    """Add every live employer board the feeds' histories reference as a managed source, once a day.

    Bounded (validations and additions per run), deduplicated against every stored source, and each board is proven
    live by its own public API first. This replaces adding such boards by hand (230 of 3,800 referenced boards were
    still unmanaged on 2026-10-29)."""
    at = at or datetime.now(UTC)
    state = await asyncio.to_thread(db.get_setting, HARVEST_KEY, {}) or {}
    last = state.get("at")
    if last and datetime.fromisoformat(last) + HARVEST_EVERY > at:
        return {"state": "skipped"}
    from ...jobboard import settings as board_settings

    if not board_settings(db).enabled:
        return {"state": "disabled"}
    focus = await asyncio.to_thread(db.get_setting, "ats_focus", {}) or {}
    excluded = set(focus.get("exclude", []))
    checked = {k: v for k, v in (state.get("checked") or {}).items() if datetime.fromisoformat(v) + HARVEST_RECHECK > at}
    own = client is None
    client = client or httpx.AsyncClient(
        timeout=25.0, follow_redirects=True, headers={"User-Agent": "ApplyPilot discovery (public postings)"}
    )
    try:
        found = await history_boards(client, feeds)
        have = await asyncio.to_thread(managed_board_keys, db)
        todo = [
            (kind, ident, company)
            for (kind, ident), company in sorted(found.items())
            if kind not in excluded and board_key(kind, ident) not in have and board_key(kind, ident) not in checked
        ][:MAX_VALIDATIONS]
        gate = asyncio.Semaphore(8)

        async def check(board):
            async with gate:
                return board, await validate_board(client, board[0], board[1])

        live = []
        for (kind, ident, company), ok in await asyncio.gather(*(check(b) for b in todo)):
            if ok:
                live.append((kind, ident, company))
            else:
                checked[board_key(kind, ident)] = at.isoformat()
    finally:
        if own:
            await client.aclose()
    from ...jobboard import add_managed_sources

    # Live boards over the bound are validated again, and added, by the next harvest.
    entries = [(kind, company or ident, board_source_url(kind, ident)) for kind, ident, company in live[:limit]]
    added = await asyncio.to_thread(add_managed_sources, db, entries)
    result = {
        "state": "done", "at": at.isoformat(), "referenced": len(found), "validated": len(todo), "live": len(live),
        "added": added, "checked": checked,
    }
    await asyncio.to_thread(db.set_setting, HARVEST_KEY, result)
    return {k: v for k, v in result.items() if k != "checked"}


def known_boards(db, kinds=("workday", "smartrecruiters"), limit=20000):
    """Employer boards of the focused ATSs that this database already knows. The scheduled sweep covered only boards
    named in the curated feeds and the static catalog, so tenants imported by hand or by earlier sweeps were never
    revisited (2026-10-17: 45 such Workday tenants held 58 fresh postings)."""
    from sqlalchemy import select

    from ...db import Job

    exclude = set((db.get_setting("ats_focus", {}) or {}).get("exclude", []))
    wanted = [kind for kind in kinds if kind not in exclude]
    if not wanted:
        return set()
    with db.session() as s:
        rows = s.execute(
            select(Job.company, Job.url).where(Job.ats.in_(wanted)).order_by(Job.created_at.desc()).limit(limit)
        ).all()
    return boards_from([Posting(company or "", "", url, [], "", "verified", "known") for company, url in rows if url])


def workday_queries(db):
    """Level terms plus the profile's own role terms, so tenants' "Data Scientist I" or "Associate ML Engineer"
    postings surface too (2026-10-17). Results still pass keep()'s entry-level title check."""
    from ...schemas import Profile
    from .roles import search_terms

    roles = Profile.model_validate(db.get_setting("profile", {}) or {}).target_roles
    return tuple(dict.fromkeys([*WORKDAY_QUERIES, *search_terms(roles, limit=4)]))


async def sweep(db, window_days: int = 7) -> dict:
    """Collect from the internet and write into the app's job board. Safe to run on a schedule."""
    from ...agent_settings import require

    require(db, "discovery")
    started = datetime.now(UTC).isoformat()
    db.set_setting(
        SWEEP_KEY, {**(db.get_setting(SWEEP_KEY, {}) or {}), "state": "running", "started_at": started}
    )
    try:
        from ...schemas import Profile

        board = db.get_setting("job_board_settings", {}) or {}
        postings, report = await collect(
            window_days,
            workday_queries=workday_queries(db),
            extra_boards=await asyncio.to_thread(known_boards, db),
            internships=Profile.model_validate(db.get_setting("profile", {}) or {}).include_internships,
            early_career_only=bool(board.get("early_career_only", False)),
        )
        counts = await asyncio.to_thread(ingest, db, postings)
        # Jobs should reach ranking with their description; HTML pages are often blocked.
        try:
            from .postings import fill_missing_text

            text = await fill_missing_text(db)
        except Exception as exc:  # Text loading is an optimisation; the HTML loader remains.
            text = {"error": type(exc).__name__}
        try:
            harvest = await harvest_boards(db)
        except Exception as exc:  # Finding more boards is an extra; the sweep result stands without it.
            harvest = {"error": type(exc).__name__}
        try:
            from .companies import harvest_companies

            company_harvest = await harvest_companies(db)  # Boards of the listed companies the postings never linked to.
        except Exception as exc:
            company_harvest = {"error": type(exc).__name__}
        result = {
            "state": "done",
            "harvest": harvest,
            "company_harvest": company_harvest,
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "window_days": window_days,
            "found": report["kept"],
            "by_field": report.get("by_field", {}),
            "by_source": report.get("by_source", {}),
            "feeds": len(report["feeds"]),
            "boards": report["boards"]["swept"],
            "only_from_boards": report["only_from_board_sweep"],
            "ingested": counts,
            "text": text,
        }
    except Exception as exc:  # A network outage leaves the board as it was.
        result = {
            "state": "error",
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "error": type(exc).__name__,
        }
    db.set_setting(SWEEP_KEY, result)
    return result
