"""Company lists -> ATS boards (2026-10-29).

The curated new-grad feeds name every board their postings link to, and `feeds.harvest_boards` already adds those. The
same lists (and the public YC directory) also name hundreds of employers whose postings link to their own career
sites, so their Greenhouse / Lever / Ashby / SmartRecruiters boards were never found. This module probes those
public posting APIs with slugs derived from each company's name and website domain.

A slug that answers is not yet the company: "Interco" -> `inter`, "Apex", "Mill", "Garage" and "Sequoia" all have a
live board that belongs to somebody else. A board is accepted only when its own display name equals the company's
name (legal-form words ignored), and a short one-word name must also show the company's web domain inside the board's
postings. Everything is public and unauthenticated, bounded per run, and a company that yielded nothing is not probed
again for two weeks.

    python -m jobpilot.features.discovery.companies [max_probes]   # read-only; prints what a run would add
"""

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html import unescape
from urllib.parse import urlsplit

import httpx

from .feeds import board_key, board_source_url, field_of, keep, sweep_board

log = logging.getLogger(__name__)
RAW = "https://raw.githubusercontent.com/"
# (name, kind, url): jobright's READMEs link each company to its website, speedyapply's list does too, and the
# yc-oss dataset mirrors the public YC directory (name, website, whether it is hiring).
COMPANY_LISTS = (
    ("jobright-swe", "jobright", RAW + "jobright-ai/2026-Software-Engineer-New-Grad/master/README.md"),
    ("jobright-data", "jobright", RAW + "jobright-ai/2026-Data-Analysis-New-Grad/master/README.md"),
    ("jobright-swe-intern", "jobright", RAW + "jobright-ai/2026-Software-Engineer-Internship/master/README.md"),
    ("jobright-data-intern", "jobright", RAW + "jobright-ai/2026-Data-Analysis-Internship/master/README.md"),
    ("speedyapply-swe", "speedyapply", RAW + "speedyapply/2026-SWE-College-Jobs/main/NEW_GRAD_USA.md"),
    ("speedyapply-ai", "speedyapply", RAW + "speedyapply/2026-AI-College-Jobs/main/NEW_GRAD_USA.md"),
    ("yc-hiring", "yc", "https://yc-oss.github.io/api/companies/hiring.json"),
)
HARVEST_KEY = "discovery_company_harvest"
HARVEST_EVERY = timedelta(hours=20)
RECHECK = timedelta(days=14)  # A company that produced no board is not probed again for this long.
MAX_NEW_BOARDS = 200  # Added per run.
MAX_PROBES = 3000  # Public API calls per run; a company already started is finished.
KINDS = ("greenhouse", "lever", "ashby", "smartrecruiters")
# Words that only name a legal form or a product category; "Notion Labs" and "Notion" are the same employer.
GENERIC = frozenset(
    "the inc incorporated llc ltd limited corp corporation co company technologies technology tech labs lab group "
    "holdings systems solutions software pbc plc gmbh ai io hq jobs careers us usa global".split()
)
# Websites that are not the company's own (a list row sometimes links the company's LinkedIn or code page).
NOT_A_COMPANY_SITE = (
    "linkedin.com", "github.com", "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com", "jobright.ai",
    "greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "myworkdayjobs.com", "wikipedia.org",
)
SHORT_NAME = 8  # A one-word name this short is also an ordinary word; the board must show the company's domain.


@dataclass
class Company:
    name: str
    website: str = ""
    sources: list = field(default_factory=list)


def words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", unescape(name or "").casefold().replace("&", " "))


def core_words(name: str) -> list[str]:
    """The name's words without legal-form and category words at the end or the start."""
    kept = words(name)
    while len(kept) > 1 and kept[-1] in GENERIC:
        kept.pop()
    while len(kept) > 1 and kept[0] in GENERIC:
        kept.pop(0)
    return kept


def core(name: str) -> str:
    """The company's identity as one lowercase run."""
    return "".join(core_words(name))


def company_key(name: str) -> str:
    return "".join(words(name))


def domain_of(website: str) -> str:
    """The registrable domain of a company website ('' for none, or for a site that is not the company's own)."""
    host = (urlsplit(website if "//" in website else "//" + website).hostname or "").casefold().removeprefix("www.")
    parts = host.split(".")
    if len(parts) < 2 or any(host == d or host.endswith("." + d) for d in NOT_A_COMPANY_SITE):
        return ""
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "org", "ac", "gov", "net"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def slug_candidates(name: str, website: str = "", kinds=KINDS) -> list[tuple[str, str]]:
    """(kind, slug) probes: the name joined / hyphenated, without legal-form words, and the domain's first label."""
    w = words(name)
    label = domain_of(website).split(".")[0]
    plain = ["".join(w), "-".join(w), core(name), label]
    slugs = [s for s in dict.fromkeys(plain) if 3 <= len(s) <= 40 and re.fullmatch(r"[a-z0-9][a-z0-9-]*", s)]
    probes = [(kind, s) for s in slugs for kind in kinds if kind != "smartrecruiters"]
    # SmartRecruiters identifiers are case-sensitive and usually the name run together with its capitals.
    sr = "".join(re.findall(r"[A-Za-z0-9]+", unescape(name or "")))
    if "smartrecruiters" in kinds:
        probes += [("smartrecruiters", s) for s in dict.fromkeys([sr, label]) if 3 <= len(s) <= 40 and s]
    return probes


def clean_title(title: str) -> str:
    t = unescape(title or "").strip()
    t = re.sub(r"^(?:jobs|careers|work|open roles|openings)\s+(?:at|@)\s+", "", t, flags=re.I)
    return re.sub(r"\s*(?:[-|:–—]\s*)?(?:jobs|careers|job board|openings|open roles)\s*$", "", t, flags=re.I).strip()


def name_match(board_name: str, company: Company) -> str:
    """'exact' when the board's display name is the company's name, 'prefix' when one is the leading words of the
    other ("CACI" / "CACI International"), '' otherwise."""
    a, b = core_words(board_name), core_words(company.name)
    if not a or not b:
        return ""
    if "".join(a) == "".join(b):
        return "exact"
    short, long = sorted((a, b), key=len)
    return "prefix" if long[: len(short)] == short else ""


def weak_name(name: str) -> bool:
    """A one-word name this short is an ordinary word as well; many unrelated employers share it."""
    return len(core_words(name)) <= 1 and len(core(name)) <= SHORT_NAME


def shows_domain(blob: str, website: str) -> bool:
    """Whether the company's web domain (or a subdomain of it) is written in the board's postings."""
    domain = domain_of(website)
    return bool(domain) and re.search(r"(?<![a-z0-9-])" + re.escape(domain) + r"(?![a-z0-9-])", (blob or "").casefold()) is not None


# ---------- company lists ----------
def parse_jobright(text: str) -> list[Company]:
    out = []
    for line in text.splitlines():
        m = re.match(r"\|\s*\*\*\[([^\]]+)\]\((https?://[^)]*)\)\*\*", line)  # "| ↳ |" continuation rows do not match
        if m:
            out.append(Company(unescape(m.group(1)).strip(), m.group(2)))
    return out


def parse_speedyapply(text: str) -> list[Company]:
    out = []
    for line in text.splitlines():
        m = re.match(r'\|\s*<a href="([^"]*)">\s*<strong>([^<]+)</strong>', line)
        if m:
            out.append(Company(unescape(m.group(2)).strip(), m.group(1)))
    return out


def parse_yc(text: str) -> list[Company]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    return [
        Company(x["name"].strip(), x.get("website") or "")
        for x in data
        if isinstance(x, dict) and x.get("name") and x.get("isHiring") and x.get("status", "Active") == "Active"
    ]


PARSERS = {"jobright": parse_jobright, "speedyapply": parse_speedyapply, "yc": parse_yc}


async def fetch_companies(client, lists=COMPANY_LISTS) -> list[Company]:
    """Every distinct company the lists name, in list order (the curated new-grad lists before the YC directory)."""
    merged: dict[str, Company] = {}
    for name, kind, url in lists:
        try:
            response = await client.get(url)
            response.raise_for_status()
            rows = await asyncio.to_thread(PARSERS[kind], response.text)
        except Exception as exc:
            log.warning("company harvest: %s unreadable (%s)", name, type(exc).__name__)
            continue
        for c in rows:
            key = company_key(c.name)
            if len(key) < 3:
                continue
            have = merged.setdefault(key, Company(c.name, c.website))
            have.website = have.website if domain_of(have.website) else c.website
            have.sources.append(name)
    return list(merged.values())


# ---------- probing ----------
@dataclass
class Board:
    kind: str
    ident: str
    name: str
    company: str
    early: int = 0  # Early-career postings the board holds today.
    early_target: int = 0  # ... of them in software / AI / ML / data.


async def fetch_board(client, kind, ident):
    """(board display name, raw postings text) when the board's public API lists postings; None otherwise."""
    if kind == "greenhouse":
        r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{ident}/jobs")
        if r.status_code != 200 or not r.json().get("jobs"):
            return None
        n = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{ident}")
        return (n.json().get("name") or "" if n.status_code == 200 else ""), r.text
    if kind == "smartrecruiters":
        r = await client.get(f"https://api.smartrecruiters.com/v1/companies/{ident}/postings", params={"limit": 100})
        body = r.json() if r.status_code == 200 else {}
        rows = body.get("content") or []
        return (rows[0].get("company") or {}).get("name") or "", r.text if rows else None
    if kind == "lever":
        r = await client.get(f"https://api.lever.co/v0/postings/{ident}?mode=json")
        page = f"https://jobs.lever.co/{ident}"
        ok = r.status_code == 200 and bool(r.json())
    else:
        r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{ident}")
        page = f"https://jobs.ashbyhq.com/{ident}"
        ok = r.status_code == 200 and bool(r.json().get("jobs"))
    if not ok:
        return None
    p = await client.get(page)  # These two APIs do not name the employer; its public board page does.
    m = re.search(r"<title[^>]*>([^<]*)</title>", p.text, re.I) if p.status_code == 200 else None
    return clean_title(m.group(1)) if m else "", r.text


async def evidence(client, kind, ident, blob) -> str:
    """More of the board's own text for the domain check: Greenhouse's list and SmartRecruiters' list carry no
    descriptions, so one posting's text is read (Lever's and Ashby's lists already hold theirs)."""
    try:
        if kind == "greenhouse":
            return (await client.get(f"https://boards-api.greenhouse.io/v1/boards/{ident}/jobs?content=true")).text
        if kind == "smartrecruiters":
            first = json.loads(blob)["content"][0]["id"]
            return (await client.get(f"https://api.smartrecruiters.com/v1/companies/{ident}/postings/{first}")).text
    except Exception:
        pass
    return ""


async def probe_company(client, company: Company, count, kinds=KINDS) -> list[Board]:
    """Every identity-checked live board of one company; one per ATS."""
    found: dict[str, Board] = {}
    for kind, ident in slug_candidates(company.name, company.website, kinds):
        if kind in found:
            continue
        count()
        try:
            hit = await fetch_board(client, kind, ident)
        except Exception:
            continue
        if not hit or not hit[1]:
            continue
        name, blob = hit
        match = name_match(name, company)
        if not match:
            continue
        if (match == "prefix" or weak_name(company.name)) and not shows_domain(
            blob + await evidence(client, kind, ident, blob), company.website
        ):
            continue
        found[kind] = Board(kind, ident, name, company.name)
    return list(found.values())


async def count_early(client, board: Board, now) -> None:
    """Fill the board's early-career counts with the feed policy (US, entry-level title, not senior or off-target)."""
    try:
        rows = await sweep_board(client, board.kind, board.ident, board.company, now)
    except Exception:
        return
    for p in rows:
        if keep(p, now, 3650, False, early_career_only=True) is None:
            board.early += 1
            board.early_target += field_of(p) in {"Software", "AI/ML/Data"}


def managed_names(db) -> set:
    from sqlalchemy import select

    from ...db import Source

    with db.session() as s:
        return {core(n) for n in s.scalars(select(Source.name)) if n}


def managed_board_keys(db) -> set:
    from .feeds import managed_board_keys as keys

    return keys(db)


async def find_boards(
    client, companies, *, skip=lambda c: False, max_probes=MAX_PROBES, concurrency=8, now=None, kinds=KINDS
):
    """Probe companies in order until the probe budget is spent. Returns (boards, probed company keys, probes)."""
    now = now or datetime.now(UTC)
    gate, used = asyncio.Semaphore(concurrency), [0]
    boards, probed = [], []

    def count():
        used[0] += 1

    async def one(company):
        async with gate:
            if used[0] >= max_probes:
                return None
            return company, await probe_company(client, company, count, kinds)

    pending = [c for c in companies if not skip(c)]
    for result in await asyncio.gather(*(one(c) for c in pending)):
        if result is None:
            continue
        company, found = result
        probed.append(company_key(company.name))
        boards += found
    await asyncio.gather(*(count_early(client, b, now) for b in boards))
    return boards, probed, used[0]


def chosen(boards, have: set, limit: int) -> list[Board]:
    """New boards only (any spelling of a stored one is a duplicate), the ones with the most early-career roles first."""
    seen, out = set(have), []
    for b in sorted(boards, key=lambda b: (-b.early_target, -b.early, b.company.casefold())):
        key = board_key(b.kind, b.ident)
        if key not in seen:
            seen.add(key)
            out.append(b)
    return out[:limit]


def make_client():
    return httpx.AsyncClient(
        timeout=25.0, follow_redirects=True, headers={"User-Agent": "ApplyPilot discovery (public postings)"}
    )


async def harvest_companies(
    db, *, client=None, at=None, lists=COMPANY_LISTS, limit=MAX_NEW_BOARDS, max_probes=MAX_PROBES
) -> dict:
    """Add the live boards of listed companies as managed sources, once a day: bounded, identity-checked and deduplicated."""
    at = at or datetime.now(UTC)
    state = await asyncio.to_thread(db.get_setting, HARVEST_KEY, {}) or {}
    if state.get("at") and datetime.fromisoformat(state["at"]) + HARVEST_EVERY > at:
        return {"state": "skipped"}
    from ...jobboard import add_managed_sources
    from ...jobboard import settings as board_settings

    if not board_settings(db).enabled:
        return {"state": "disabled"}
    focus = await asyncio.to_thread(db.get_setting, "ats_focus", {}) or {}
    kinds = [k for k in KINDS if k not in set(focus.get("exclude", []))]
    checked = {k: v for k, v in (state.get("checked") or {}).items() if datetime.fromisoformat(v) + RECHECK > at}
    have_names = await asyncio.to_thread(managed_names, db)
    have = await asyncio.to_thread(managed_board_keys, db)
    own = client is None
    client = client or make_client()
    try:
        companies = await fetch_companies(client, lists)
        boards, probed, used = await find_boards(
            client,
            companies,
            skip=lambda c: company_key(c.name) in checked or core(c.name) in have_names,
            max_probes=max_probes,
            now=at,
            kinds=tuple(kinds),
        )
    finally:
        if own:
            await client.aclose()
    picked = chosen(boards, have, limit)
    added = await asyncio.to_thread(
        add_managed_sources, db, [(b.kind, b.name or b.company, board_source_url(b.kind, b.ident)) for b in picked]
    )
    # A company whose board was found but did not fit under the cap is probed again tomorrow; every other probed
    # company (nothing found, a mismatch, or already stored) is not asked again for two weeks.
    taken = {board_key(b.kind, b.ident) for b in picked}
    over = {company_key(b.company) for b in boards if board_key(b.kind, b.ident) not in taken | have}
    for key in probed:
        if key not in over:
            checked[key] = at.isoformat()
    result = {
        "state": "done", "at": at.isoformat(), "companies": len(companies), "probed": len(probed), "probes": used,
        "boards": len(boards), "added": added, "early_career": sum(b.early for b in picked), "checked": checked,
    }
    await asyncio.to_thread(db.set_setting, HARVEST_KEY, result)
    return {k: v for k, v in result.items() if k != "checked"}


async def dry_run(have_keys=frozenset(), have_names=frozenset(), max_probes=MAX_PROBES) -> dict:
    """What a run would add, without a database: the boards, their early-career counts and the funnel."""
    async with make_client() as client:
        companies = await fetch_companies(client)
        boards, probed, used = await find_boards(
            client, companies, skip=lambda c: core(c.name) in have_names, max_probes=max_probes
        )
    picked = chosen(boards, set(have_keys), 10**6)
    return {
        "companies": len(companies), "probed": len(probed), "probes": used, "identified": len(boards),
        "new": len(picked), "by_kind": dict(Counter(b.kind for b in picked)),
        "with_early_career": sum(1 for b in picked if b.early), "early_career": sum(b.early for b in picked),
        "early_career_software_ai_data": sum(b.early_target for b in picked),
        "boards": [
            [b.kind, b.ident, b.name, b.company, b.early, b.early_target]
            for b in picked
        ],
    }


if __name__ == "__main__":
    import sys

    print(json.dumps(asyncio.run(dry_run(max_probes=int(sys.argv[1]) if len(sys.argv) > 1 else MAX_PROBES)), indent=1))
