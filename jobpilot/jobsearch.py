"""Expand employer coverage from web search and a public new-grad catalog.

Search snippets/catalog dates only identify leads. Employer publication evidence is
still required by jobboard before displaying or applying to a job.
"""

import asyncio
import json
import re
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, quote_plus, urlsplit
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select

from .ats import detect
from .db import Source, now
from .jobboard import managed_ids, settings, us_location
from .network import fetch
from .schemas import Profile

CATALOG_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"
)
SEARCH_SOURCE_URL = "https://duckduckgo.com/?applypilot=employer-discovery"
FREE_PROVIDER = "Free web search (DuckDuckGo / Bing)"
DDG_RETRY_AT = 0
SEARCH_DOMAINS = [
    "jobs.ashbyhq.com",
    "job-boards.greenhouse.io",
    "boards.greenhouse.io",
    "myworkdayjobs.com",
    "jobs.lever.co",
    "jobs.smartrecruiters.com",
    "icims.com",
    "oraclecloud.com",
    "taleo.net",
    "eightfold.ai",
    "apply.workable.com",
    "careers",  # Last query reaches custom career sites.
]


def employer_board(url):
    """Map an individual ATS job to its public employer feed; reject arbitrary hosts."""
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        return None
    parts = p.path.strip("/").split("/")
    kind = detect(url).id
    if kind in {"ashby", "greenhouse", "lever", "smartrecruiters"}:
        if not parts or not re.fullmatch(r"[\w.-]+", parts[0]):
            return None
        slug = parts[0]
        if kind == "greenhouse":
            slug = parse_qs(p.query).get("for", [slug])[0]
        if slug.lower() in {"oneclick-ui", "embed", "v0", "v1", "publications"}:
            return None
        host = {
            "ashby": "jobs.ashbyhq.com",
            "greenhouse": "job-boards.greenhouse.io",
            "lever": "jobs.lever.co",
            "smartrecruiters": "jobs.smartrecruiters.com",
        }[kind]
        return kind, f"https://{host}/{slug}", slug
    if kind == "workday" and p.hostname.endswith(".myworkdayjobs.com"):
        if "job" not in parts:
            return None
        before = parts[: parts.index("job")]
        if before and re.fullmatch(r"[\w.-]+", before[-1]):
            return "workday", f"https://{p.hostname}/" + "/".join(before), p.hostname.split(".")[0]
    return None


def catalog_boards(rows):
    """Historical jobs expand employer coverage, never masquerade as fresh jobs."""
    leads = {}
    # Active, recent entries take precedence for employer names and initial crawl priority.
    for row in sorted(rows, key=lambda r: (bool(r.get("active")), r.get("date_posted", 0)), reverse=True):
        if not any(us_location(loc) for loc in row.get("locations", []) if isinstance(loc, str)):
            continue
        board = employer_board(row.get("url", ""))
        if board:
            kind, url, slug = board
            leads.setdefault(url, {"kind": kind, "url": url, "name": row.get("company_name") or slug})
    return list(leads.values())


def search_links(body):
    soup = BeautifulSoup(body, "html.parser")
    links = []
    for node in soup.select(".result__a[href]"):
        link = node["href"]
        if link.startswith("//"):
            link = "https:" + link
        p = urlsplit(link)
        if p.hostname in {"duckduckgo.com", "www.duckduckgo.com"}:
            link = parse_qs(p.query).get("uddg", [""])[0]
        if link.startswith("https://"):
            links.append(link)
    if not links and (soup.select(".anomaly-modal") or "challenge-form" in body):
        raise RuntimeError("Free search is rate limited; employer catalog discovery continues")
    return list(dict.fromkeys(links))[:20]


async def web_results(config, query):
    global DDG_RETRY_AT
    if config.serper_api_key:
        # Secret is sent only as a header to the fixed provider, never in a URL or trace.
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
            r = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": config.serper_api_key},
                json={"q": query, "gl": "us", "hl": "en", "num": 20, "tbs": "qdr:w"},
            )
            if r.status_code != 200:
                raise RuntimeError(f"Google search provider returned HTTP {r.status_code}")
            return [x["link"] for x in r.json().get("organic", []) if x.get("link")][:20]
    if time.monotonic() >= DDG_RETRY_AT:
        try:
            body, _ = await fetch("https://html.duckduckgo.com/html/?kl=us-en&q=" + quote_plus(query))
            links = search_links(body)
            if links:
                return links
        except Exception:
            DDG_RETRY_AT = time.monotonic() + 3600
    # A personal RSS search fallback. Irrelevant results are not accepted as job leads.
    body, _ = await fetch("https://www.bing.com/search?format=rss&q=" + quote_plus(query))
    return list(
        dict.fromkeys(
            n.findtext("link")
            for n in ElementTree.fromstring(body).findall(".//item")
            if (n.findtext("link") or "").startswith("https://")
        )
    )[:20]


def queries(profile, cursor, count):
    from .features.discovery.roles import DEFAULT_ROLES, search_terms

    roles = [r[:100] for r in search_terms(profile.target_roles or list(DEFAULT_ROLES))]
    # New-grad-first profiles search entry-level postings; others search the role alone.
    level = (
        ' ("new grad" OR "entry level" OR "junior" OR "Engineer I")'
        if getattr(profile, "job_priority", "new_grad_first") == "new_grad_first"
        else ""
    )
    places = [p[:60] for p in getattr(profile, "target_locations", [])[:4] if p.strip()]
    where = " (" + " OR ".join(f'"{p}"' for p in places) + ")" if places else ' ("United States" OR "USA")'
    combinations = []
    for role in roles:
        for domain in SEARCH_DOMAINS:
            scope = f"site:{domain}" if domain != "careers" else "careers job"
            combinations.append(f'{scope} "{role}"{level}{where}')
    return [combinations[(cursor + i) % len(combinations)] for i in range(min(count, len(combinations)))]


def register_boards(db, boards):
    cfg, ids = settings(db), managed_ids(db)
    added = []
    with db.exclusive() as s:
        existing = {row.url: row for row in s.scalars(select(Source))}
        # Hash/URL dedup across manual and discovered sources; respect individual pauses.
        for board in boards:
            if board["url"] in existing:
                continue
            row = Source(
                **board,
                enabled=cfg.enabled,
                auto_queue=False,
                cron=f"0 */{cfg.interval_hours} * * *",
                next_run=(datetime.now(UTC) + timedelta(minutes=5 + len(added) % 110)).isoformat(),
            )
            s.add(row)
            s.flush()
            ids.add(row.id)
            existing[row.url] = row
            added.append(row.id)
    db.set_setting("job_board_sources", {"ids": sorted(ids)})
    return added


def setup_search(db):
    cfg = settings(db)
    if not cfg.web_search:
        with db.session() as s:
            row = s.scalar(select(Source).where(Source.url == SEARCH_SOURCE_URL))
            if row:
                row.enabled = False
        return None
    ids = managed_ids(db)
    with db.exclusive() as s:
        row = s.scalar(select(Source).where(Source.url == SEARCH_SOURCE_URL))
        if not row:
            row = Source(
                name="Web search + new-grad employer catalog",
                kind="web_search",
                url=SEARCH_SOURCE_URL,
                cron=f"0 */{cfg.interval_hours} * * *",
                auto_queue=False,
                enabled=cfg.enabled,
                next_run=now(),
            )
            s.add(row)
            s.flush()
        row.enabled = cfg.enabled and cfg.web_search
        row.cron = f"0 */{cfg.interval_hours} * * *"
        ids.add(row.id)
        sid = row.id
    db.set_setting("job_board_sources", {"ids": sorted(ids)})
    return sid


async def expand_sources(source, db, config):
    if not settings(db).enabled or not settings(db).web_search:
        return []
    key = "job_board_scan:" + source["id"]
    report = {
        "state": "running",
        "started_at": now(),
        "checked": 0,
        "new": 0,
        "matched": 0,
        "queries": 0,
        "errors": [],
        "provider": "Google / Serper" if config.serper_api_key else FREE_PROVIDER,
    }
    db.set_setting(key, report)
    boards, direct = [], []
    try:
        body, _ = await fetch(CATALOG_URL, max_bytes=24_000_000)
        rows = json.loads(body)
        boards.extend(catalog_boards(rows))
        report.update(catalog_rows=len(rows), catalog_employers=len(boards))
    except Exception as exc:
        report["errors"].append("Employer catalog unavailable: " + type(exc).__name__)
    profile = Profile.model_validate(db.get_setting("profile", {}))
    cursor = db.get_setting("job_search_cursor", {}).get("value", 0)
    for query in queries(profile, cursor, config.search_queries_per_sweep):
        try:
            links = await web_results(config, query)
            report["queries"] += 1
            report["checked"] += len(links)
            for link in links:
                if board := employer_board(link):
                    kind, url, name = board
                    boards.append({"kind": kind, "url": url, "name": name})
                elif detect(link).id != "custom" or re.search(
                    r"/(?:job|jobs|careers|positions)/", urlsplit(link).path
                ):
                    direct.append(link)
        except Exception as exc:
            report["errors"].append(str(exc)[:160] if isinstance(exc, RuntimeError) else type(exc).__name__)
            # Stop free-provider queries after a challenge; no paid CAPTCHA or blind retries for search.
            if not config.serper_api_key:
                break
        await asyncio.sleep(0.4)
    db.set_setting("job_search_cursor", {"value": cursor + max(1, report["queries"])})
    added = register_boards(db, boards)
    # Unknown/other ATS links use employer JobPosting JSON-LD, never a search-snippet date.
    for link in dict.fromkeys(direct[:30]):
        if urlsplit(link).hostname in {
            "linkedin.com",
            "www.linkedin.com",
            "indeed.com",
            "www.indeed.com",
            "simplify.jobs",
        }:
            continue
        added += register_boards(db, [{"kind": "career_page", "url": link, "name": urlsplit(link).hostname}])
    report.update(
        state="complete", finished_at=now(), new=len(added), employer_sources=len(managed_ids(db)) - 1
    )
    db.set_setting(key, report)
    # Enqueue new sources gradually, prioritizing the catalog's active recent employers.
    from .workers import enqueue_source

    for sid in added[:150]:
        db.set_setting("job_board_scan:" + sid, {"state": "queued", "started_at": now()})
        await enqueue_source(sid)
    return []  # These are sources, not job IDs eligible for submission.
