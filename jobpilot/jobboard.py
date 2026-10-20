"""Fresh US jobs from public employer feeds. No model calls or automatic submissions.

Publication evidence is required: neither updated_at nor first_seen is a posted date.
Scraping shares the existing durable discovery queue and never occupies browser workers.
"""

import json
import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import unescape
from urllib.parse import urljoin, urlsplit
from typing import Literal
from pathlib import Path

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from sqlalchemy import select

from .db import Job, JobListing, Setting, Source, now, record
from .network import fetch
from .schemas import JobInput, Profile
from .widgets import US_STATES


class BoardSettings(BaseModel):
    enabled: bool = True
    interval_hours: Literal[1, 2, 3, 6] = 2
    early_career_only: bool = True
    web_search: bool = True
    auto_apply: bool = False
    batch_target: int = Field(default=100, ge=1, le=500)
    minimum_auto_fit: int = Field(default=70, ge=0, le=100)
    max_age_hours: Literal[48, 168, 720] = 720


US = {"us", "usa", "united states", "united states of america", "u.s.", "u.s.a."}
US_REGION = re.compile(
    r"\b(?:" + "|".join(re.escape(v) for v in US_STATES.values()) + r")\b|"
    r"[A-Za-z .]+,\s*(?:" + "|".join(US_STATES) + r")\b",
    re.I,
)
US_PLACE = re.compile(
    r"\b(?:United States|USA|US(?=[\s,/()\-]|$)|U\.S\.|US(?:A)? Remote|Remote[ ,:()/\-]+US|"
    r"New York|NYC|San Francisco|San Jose|Los Angeles|San Diego|Seattle|Boston|"
    r"Washington[ ,]+DC|District of Columbia|Chicago|Austin|Dallas|Houston|"
    r"Atlanta|Denver|Pittsburgh|Philadelphia|Phoenix|Charlotte|Raleigh|"
    r"Minneapolis|Salt Lake City|Mountain View|Sunnyvale|Palo Alto|Redmond|"
    r"Arlington[ ,]+(?:VA|Virginia)|"
    r"California|Massachusetts|Maryland|Virginia|Texas|Colorado|New Jersey|"
    r"[A-Za-z .]+,[ ]*(?:CA|NY|WA|MA|VA|MD|IL|TX|CO|AZ|NC|NJ|GA|PA|FL|OH|UT|OR|MI|MN|WI)\b)",
    re.I,
)
FOREIGN = re.compile(
    r"\b(?:India|Canada|Canadian|United Kingdom|London|UK|Germany|Berlin|France|Paris|"
    r"Australia|Singapore|Mexico|Brazil|Ireland|Dublin|Netherlands|Amsterdam|"
    r"Bengaluru|Bangalore|Pune|Hyderabad|Toronto|Vancouver|Poland|Warsaw|Krakow|"
    r"Japan|Tokyo|China|Beijing|Shanghai|Taiwan|Taipei|South Korea|Seoul|Hong Kong|"
    r"Spain|Portugal|Italy|Sweden|Norway|Finland|Denmark|Belgium|Switzerland|Austria|"
    r"Greece|Hungary|Czechia|Czech Republic|Romania|Bulgaria|Ukraine|Serbia|Croatia|"
    r"Argentina|Chile|Colombia|Peru|Costa Rica|Uruguay|Ecuador|South Africa|Nigeria|Kenya|"
    r"Israel|Turkey|Saudi Arabia|United Arab Emirates|Dubai|Indonesia|Malaysia|Philippines|"
    r"Thailand|Vietnam|Pakistan|Bangladesh|Sri Lanka|New Zealand|EMEA|APAC|Europe)\b",
    re.I,
)
SENIOR = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|distinguished|lead|leader|director|head|manager|avp|"
    r"assistant vice president|III|IV|V)\b", re.I
)
ENTRY = re.compile(
    r"\b(?:new[ -]?grad(?:uate)?s?|early[ -]?career|entry[ -]?level|junior|jr\.?|"
    r"university[ -]?grad(?:uate)?|recent graduates?|graduate (?:engineer|program|programme)|"
    r"engineer\s+(?:I|1)|scientist\s+(?:I|1)|associate(?:\s+\w+){0,2}\s+(?:engineer|scientist))\b",
    re.I,
)
ML_ROLE = re.compile(
    r"\b(?:machine learning|artificial intelligence|AI|ML|LLM|deep learning|"
    r"applied scientist|data scientist|research engineer|computer vision|NLP)\b",
    re.I,
)
YEARS = re.compile(
    r"(?P<low>\d{1,2})\s*(?:\+|(?:[-–—]|to)\s*\d{1,2})?\s*"
    r"(?:years?|yrs?)[’']?\s+(?:[a-z][\w/-]*\s+){0,6}(?:experience|building|developing|working|designing|programming)",
    re.I,
)


@dataclass
class Posting:
    url: str
    title: str
    company: str
    location: str
    description: str = ""
    country: str = ""
    posted: str | None = None
    basis: str = ""
    level: str = ""


def text(value):
    return BeautifulSoup(unescape(value or ""), "html.parser").get_text("\n", strip=True)[:50000]


def published(value):
    """UTC evidence; date-only values use the earliest possible instant (+14h timezone)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.fromisoformat(value).replace(tzinfo=UTC) - timedelta(hours=14)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except ValueError:
        return None


def fresh(value, at, max_age_hours=48):
    date = published(value)
    return date is not None and at - timedelta(hours=max_age_hours) <= date <= at


def us_location(location, country=""):
    country = str(country or "").strip().casefold()
    if FOREIGN.search(location or ""):
        return False  # A feed's US/HQ descriptor cannot override an explicit foreign job location.
    if country:
        return country in US
    # A foreign single-location listing wins over an incidental US city/company name.
    if FOREIGN.search(location or ""):
        return False
    return bool(US_PLACE.search(location or "") or US_REGION.search(location or ""))


def role_matches(title, description, profile):
    if any(re.search(r"\b" + re.escape(x) + r"\b", title, re.I) for x in profile.excluded_keywords if x):
        return False
    if re.search(r"\bintern(?:ship)?\b", title, re.I):
        return False
    from .features.discovery.roles import DEFAULT_ROLES, matching_role

    roles = profile.target_roles or list(DEFAULT_ROLES)
    # Synonym-aware: "Software Development Engineer" also matches "Software Engineer II" and "SDE". A profile's
    # own role wins over the generic exclusions below (a product manager's profile targets "Product Manager").
    if matching_role(title, roles):
        return True
    if re.search(r"\b(?:sales|recruiter|account executive|product manager)\b", title, re.I):
        return False
    if any(ML_ROLE.search(role) for role in roles):
        adjacent = bool(re.search(r"\b(?:software|data|backend|full.?stack) engineer\b", title, re.I))
        supported_tools = [
            skill
            for skill in profile.skills
            if len(skill) >= 2 and re.search(r"(?<!\w)" + re.escape(skill) + r"(?!\w)", description, re.I)
        ]
        # Early-career software/data roles can be relevant without "AI" in their title.
        # The unattended batch still needs a strong resume score before queuing them.
        if adjacent and len(supported_tools) >= 3:
            return True
        return bool(ML_ROLE.search(title)) or bool(
            re.search(r"\b(?:software|data) engineer\b", title, re.I)
            and re.search(
                r"\b(?:machine learning|LLM|deep learning|model training|AI agents?)\b", description, re.I
            )
        )
    return False


def workday_searches(profile):
    """Workday tenant search terms from the profile's own target roles (2026-10-17: these were hard-coded to one
    applicant's AI/ML roles), plus "new grad" for profiles that apply to new-grad roles first."""
    from .features.discovery.roles import DEFAULT_ROLES, search_terms

    terms = search_terms(profile.target_roles or list(DEFAULT_ROLES), limit=6)
    if getattr(profile, "job_priority", "new_grad_first") == "new_grad_first":
        terms.append("new grad")
    return list(dict.fromkeys(terms))


def candidate_title(title, profile):
    return (
        role_matches(title, "", profile)
        or bool(ENTRY.search(title) and re.search(r"\b(?:engineer|scientist)\b", title, re.I))
        or bool(
            len(profile.skills) >= 3
            and re.search(r"\b(?:software|data|backend|full.?stack) engineer\b", title, re.I)
        )
    )


def screen(post, profile, settings):
    if not us_location(post.location, post.country):
        return None, "non_us_or_unknown"
    if SENIOR.search(post.title) or not role_matches(post.title, post.description, profile):
        return None, "role_or_seniority"
    years = []
    for match in YEARS.finditer(post.description):
        context = post.description[max(0, match.start() - 70) : match.end() + 50]
        if not re.search(r"preferred|nice.to.have|bonus|ideally", context, re.I):
            years.append(int(match["low"]))
    if years and min(years) > 3:
        return None, "experience"
    entry = bool(ENTRY.search(post.title) or ENTRY.search(post.level))
    new_grad = bool(re.search(r"new[ -]?grad|recent graduate|university[ -]?grad", post.title, re.I))
    description_entry = re.search(
        r"\b(?:new[ -]?grad(?:uate)?|recent graduates?|early[ -]?career|entry[ -]?level)\b",
        post.description,
        re.I,
    )
    if not entry and not (years and min(years) <= 3) and not description_entry:
        if settings.early_career_only:
            return None, "experience_unspecified"
        return ("Experience unspecified", "Relevant role; verify career level before applying"), None
    level = "New grad" if new_grad else "Early career"
    reason = (
        "Explicit new grad / early career posting" if entry else "Posting accepts up to 3 years of experience"
    )
    return (level, reason), None


def settings(db):
    return BoardSettings.model_validate(db.get_setting("job_board_settings", {}))


def managed_ids(db):
    return set(db.get_setting("job_board_sources", {}).get("ids", []))


def is_managed(db, source_id):
    return source_id in managed_ids(db)


async def document_post(url, company):
    body, final = await fetch(url)
    soup = BeautifulSoup(body, "html.parser")
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.string or node.get_text())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop()
            if not isinstance(obj, dict):
                continue
            stack.extend(obj.get("@graph", []))
            if "JobPosting" not in (
                [obj.get("@type")] if isinstance(obj.get("@type"), str) else obj.get("@type", [])
            ):
                continue
            places = obj.get("jobLocation", [])
            places = [places] if isinstance(places, dict) else places
            for place in places or []:
                address = place.get("address", {}) if isinstance(place, dict) else {}
                if not isinstance(address, dict):
                    continue
                country = address.get("addressCountry", "")
                if isinstance(country, dict):
                    country = country.get("name", "")
                loc = ", ".join(
                    str(address[k]) for k in ("addressLocality", "addressRegion") if address.get(k)
                )
                if us_location(loc, country):
                    return Posting(
                        final,
                        obj.get("title", ""),
                        company,
                        loc or "United States",
                        text(obj.get("description")),
                        str(country),
                        obj.get("datePosted"),
                        "Employer datePosted",
                    )
            # Schema.org remote eligibility must explicitly include the USA.
            eligible = obj.get("applicantLocationRequirements", [])
            eligible = [eligible] if isinstance(eligible, dict) else eligible
            if obj.get("jobLocationType") == "TELECOMMUTE" and any(
                str(p.get("name", "")).casefold() in US for p in eligible if isinstance(p, dict)
            ):
                return Posting(
                    final,
                    obj.get("title", ""),
                    company,
                    "Remote — United States",
                    text(obj.get("description")),
                    "US",
                    obj.get("datePosted"),
                    "Employer datePosted",
                )
    return None


async def read_feed(source, profile, cfg, at):
    """Bounded board reads and detail calls; failed feeds never mark listings closed."""
    url = source["url"].rstrip("/")
    parsed = urlsplit(url)
    slug = parsed.path.strip("/").split("/")[0]
    kind, company = source["kind"], source["name"]
    posts, seen = [], set()
    complete = True
    detail_errors = 0

    async def get(url, **kwargs):
        body, _ = await fetch(url, **kwargs)
        # Whole-company boards are megabytes; parse them off the application loop.
        return await asyncio.to_thread(json.loads, body) if len(body) > 100_000 else json.loads(body)

    if kind == "ashby":
        data = await get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", max_bytes=16_000_000)
        for j in data.get("jobs", []):
            if not j.get("isListed", True):
                continue
            link = j.get("applyUrl") or j.get("jobUrl")
            if not link:
                continue
            seen.add(link)
            locations = [
                (
                    j.get("location", ""),
                    ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry", ""),
                )
            ]
            locations += [
                (p.get("location", ""), (p.get("address") or {}).get("addressCountry", ""))
                for p in j.get("secondaryLocations", []) or []
                if isinstance(p, dict)
            ]
            us_places = [loc for loc, country in locations if us_location(loc, country)]
            posts.append(
                Posting(
                    link,
                    j["title"],
                    company,
                    " / ".join(us_places),
                    j.get("descriptionPlain") or text(j.get("descriptionHtml")),
                    "US" if us_places else "unknown",
                    j.get("publishedAt"),
                    "Employer publishedAt",
                )
            )
    elif kind == "greenhouse":
        data = await get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        candidates = []
        for j in data.get("jobs", []):
            link = f"https://job-boards.greenhouse.io/{slug}/jobs/{j['id']}"
            seen.add(link)
            post = Posting(link, j["title"], company, j.get("location", {}).get("name", ""))
            if (
                us_location(post.location)
                and not SENIOR.search(post.title)
                and candidate_title(post.title, profile)
            ):
                candidates.append((j, post))
        candidates.sort(
            key=lambda item: (
                fresh(item[0].get("updated_at"), at, cfg.max_age_hours),
                bool(ENTRY.search(item[1].title)),
                item[0].get("updated_at", ""),
            ),
            reverse=True,
        )
        detail_slots = asyncio.Semaphore(4)

        async def read_detail(item):
            nonlocal detail_errors
            j, post = item
            try:
                async with detail_slots:
                    detail = await get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{j['id']}")
                post.posted, post.basis = detail.get("first_published"), "Employer first_published"
                post.company = detail.get("company_name") or company
                post.description = text(detail.get("content"))
                posts.append(post)
            except Exception:
                detail_errors += 1

        await asyncio.gather(*(read_detail(item) for item in candidates[:30]))
    elif kind == "smartrecruiters":
        after = (at - timedelta(hours=cfg.max_age_hours)).isoformat()
        from urllib.parse import urlencode

        for offset in range(0, 500, 100):
            data = await get(
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?"
                + urlencode({"limit": 100, "offset": offset, "releasedAfter": after})
            )
            rows = data.get("content", [])
            for j in rows:
                link = f"https://jobs.smartrecruiters.com/{slug}/{j['id']}"
                seen.add(link)
                loc = j.get("location", {})
                post = Posting(
                    link,
                    j["name"],
                    company,
                    ", ".join(str(loc[k]) for k in ("city", "region", "country") if loc.get(k)),
                    country=loc.get("country", ""),
                    posted=j.get("releasedDate"),
                    basis="Employer releasedDate",
                )
                post.level = j.get("experienceLevel", {}).get("label", "").replace("_", " ")
                if (
                    fresh(post.posted, at, cfg.max_age_hours)
                    and us_location(post.location, post.country)
                    and not SENIOR.search(post.title)
                    and candidate_title(post.title, profile)
                ):
                    try:
                        detail = await get(
                            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{j['id']}"
                        )
                        sections = detail.get("jobAd", {}).get("sections", {})
                        post.description = "\n".join(
                            text(v.get("text", "")) for v in sections.values() if isinstance(v, dict)
                        )
                    except Exception:
                        detail_errors += 1
                        continue
                    posts.append(post)
            if len(rows) < 100:
                break
        else:
            complete = False
        # releasedAfter filters the feed: absence does not mean a previously seen job is closed.
        complete = False
    elif kind == "workday":
        tenant = parsed.hostname.split(".")[0]
        site = parsed.path.strip("/").split("/")[-1]
        root = f"{parsed.scheme}://{parsed.netloc}/wday/cxs/{tenant}/{site}"
        # Search narrows pages but never substitutes for date/US checks.
        complete = False
        for query in workday_searches(profile):
            for offset in range(0, 100, 20):
                data = await get(
                    root + "/jobs",
                    method="POST",
                    payload={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": query},
                )
                rows = data.get("jobPostings", [])
                for j in rows:
                    path = j.get("externalPath")
                    if not path:
                        detail_errors += 1
                        continue
                    link = url + path
                    if link in seen:
                        continue
                    seen.add(link)
                    # Some tenants list postings without a title (GE Vernova, ICF, M&T, 2026-10-15);
                    # skip that row instead of failing every other posting in the feed.
                    title = j.get("title") or ""
                    if not title:
                        detail_errors += 1
                        continue
                    if SENIOR.search(title) or not candidate_title(title, profile):
                        continue
                    if not re.search(
                        r"Posted (?:Today|Yesterday|[1-7] Days? Ago)", j.get("postedOn", ""), re.I
                    ):
                        continue
                    try:
                        detail = await get(root + path)
                        info = detail.get("jobPostingInfo", {})
                        loc = info.get("location", j.get("locationsText", ""))
                        # The hiring organization's HQ country is not the job's country.
                        country = info.get("country", "")
                        if isinstance(country, dict):
                            country = country.get("descriptor", country.get("id", ""))
                        posts.append(
                            Posting(
                                link,
                                title,
                                company,
                                loc,
                                text(info.get("jobDescription")),
                                country,
                                info.get("startDate"),
                                "Employer startDate (date only)",
                            )
                        )
                    except Exception:
                        detail_errors += 1
                if len(rows) < 20:
                    break
    elif kind == "lever":
        data = await get(f"https://api.lever.co/v0/postings/{slug}?mode=json", max_bytes=16_000_000)
        candidates = []
        for j in data:
            link = j.get("hostedUrl") or j.get("applyUrl")
            if not link:
                continue
            seen.add(link)
            post = Posting(
                link,
                j["text"],
                company,
                j.get("categories", {}).get("location", ""),
                j.get("descriptionPlain", ""),
                j.get("country", ""),
            )
            if screen(post, profile, cfg)[0]:
                candidates.append(post)
        for candidate in candidates[:20]:
            try:
                post = await document_post(candidate.url, company)
                if post:
                    posts.append(post)
                else:
                    # createdAt describes creation, not a verified publication date.
                    posts.append(candidate)
            except Exception:
                detail_errors += 1
    else:
        body, final = await fetch(url)
        soup = BeautifulSoup(body, "html.parser")
        links = list(
            dict.fromkeys(
                urljoin(final, a["href"])
                for a in soup.select("a[href]")
                if re.search(r"/(?:job|jobs|position|positions)/", a["href"])
            )
        )[:30]
        complete = False
        # Search may discover an individual posting rather than a board index.
        if soup.select('script[type="application/ld+json"]'):
            seen.add(final)
            if post := await document_post(final, company):
                posts.append(post)
        for link in links:
            seen.add(link)
            try:
                if post := await document_post(link, company):
                    posts.append(post)
            except Exception:
                detail_errors += 1
    return posts, seen, complete and not detail_errors, detail_errors


async def scrape_source(source, db, config):
    from .ats import canonicalize
    from .discovery import add_job

    cfg = settings(db)
    if not cfg.enabled:
        return []
    at = datetime.now(UTC)
    key = "job_board_scan:" + source["id"]
    report = {
        "state": "running",
        "started_at": at.isoformat(),
        "checked": 0,
        "new": 0,
        "matched": 0,
        "old": 0,
        "unknown_date": 0,
        "excluded": 0,
    }
    db.set_setting(key, report)
    try:
        profile = Profile.model_validate(db.get_setting("profile", {}))
        posts, seen, complete, errors = await read_feed(source, profile, cfg, at)
        report.update(checked=len(seen), detail_errors=errors)
        created = []
        for post in posts:
            if not published(post.posted):
                report["unknown_date"] += 1
                continue
            if not fresh(post.posted, at, cfg.max_age_hours):
                report["old"] += 1
                continue
            match, _ = screen(post, profile, cfg)
            if not match:
                report["excluded"] += 1
                continue
            row, new = add_job(
                db,
                JobInput(
                    url=post.url,
                    title=post.title,
                    company=post.company,
                    location=post.location,
                    description=post.description,
                ),
                origin="source",
            )
            if not new and published(row["created_at"]) < at - timedelta(hours=cfg.max_age_hours):
                # Already seeing this public job earlier proves it isn't a newly posted job.
                report["old"] += 1
                continue
            with db.exclusive() as s:
                entry = s.get(JobListing, row["id"])
                date = published(post.posted).isoformat()
                if entry:
                    # Republishing cannot make an already old stored listing fresh again.
                    date = min(entry.posted_at, date)
                    entry.posted_at, entry.last_seen, entry.active = date, at.isoformat(), True
                    entry.location, entry.career_level, entry.match_reason = post.location, match[0], match[1]
                else:
                    s.add(
                        JobListing(
                            job_id=row["id"],
                            source_id=source["id"],
                            posted_at=date,
                            date_basis=post.basis,
                            location=post.location,
                            career_level=match[0],
                            match_reason=match[1],
                            last_seen=at.isoformat(),
                        )
                    )
            report["matched"] += 1
            if new:
                created.append(row["id"])
        if complete:
            identities = {canonicalize(url)[1] for url in seen}
            with db.session() as s:
                for entry, job in s.execute(
                    select(JobListing, Job)
                    .join(Job, Job.id == JobListing.job_id)
                    .where(JobListing.source_id == source["id"])
                ):
                    if job.identity not in identities:
                        entry.active = False
        report.update(state="complete", finished_at=now(), new=len(created))
        db.set_setting(key, report)
        return created
    except Exception as exc:
        report.update(
            state="error", finished_at=now(), error=f"Board could not be read ({type(exc).__name__})"
        )
        db.set_setting(key, report)
        raise


def board_snapshot(db, limit=500):
    cfg = settings(db)
    at = datetime.now(UTC)
    cutoff = (at - timedelta(hours=cfg.max_age_hours)).isoformat()
    seen_cutoff = (at - timedelta(hours=max(6, cfg.interval_hours * 2 + 1))).isoformat()
    ids = managed_ids(db)
    with db.session() as s:
        jobs = [
            {**{k: v for k, v in record(job).items() if k != "description"}, **record(entry), "id": job.id}
            for entry, job in s.execute(
                select(JobListing, Job)
                .join(Job, Job.id == JobListing.job_id)
                .where(
                    JobListing.active.is_(True),
                    JobListing.country == "US",
                    JobListing.posted_at >= cutoff,
                    JobListing.posted_at <= at.isoformat(),
                    JobListing.last_seen >= seen_cutoff,
                    Job.demo.is_(False),
                    Job.status != "archived",
                )
                .order_by(JobListing.posted_at.desc())
                .limit(limit)
            )
        ]
        paused_jobs = {
            row.key.removeprefix("job_control:")
            for row in s.scalars(select(Setting).where(Setting.key.like("job_control:%")))
            if row.value.get("state") == "paused"
        }
        for job in jobs:
            if job["id"] in paused_jobs and job["status"] not in {
                "confirmed",
                "submission_unknown",
                "submitting",
                "already_applied",
            }:
                job["status"] = "paused"
        if cfg.early_career_only:
            jobs = [j for j in jobs if j["career_level"] != "Experience unspecified"]
        jobs = [j for j in jobs if us_location(j["location"], j["country"])]
        sources = (
            [record(x) for x in s.scalars(select(Source).where(Source.id.in_(ids)).order_by(Source.name))]
            if ids
            else []
        )
    # Thousands of employers must not cause thousands of SQL requests per UI refresh.
    with db.session() as s:
        scans = {
            row.key.removeprefix("job_board_scan:"): row.value
            for row in s.scalars(
                select(Setting).where(Setting.key.in_(["job_board_scan:" + sid for sid in ids]))
            )
        }
    for source in sources:
        source["scan"] = scans.get(source["id"], {})
    searches = [scan["started_at"] for scan in scans.values() if scan.get("started_at")]
    if requested := db.get_setting("job_board_requested", {}).get("at"):
        searches.append(requested)
    return {
        "settings": cfg.model_dump(),
        "jobs": jobs,
        "sources": sources,
        "max_age_hours": cfg.max_age_hours,
        "last_requested_at": max(searches) if searches else None,
        "scanning": sum(
            scan.get("state") in {"queued", "running"}
            and at - datetime.fromisoformat(scan.get("started_at", at.isoformat())) < timedelta(hours=6)
            for scan in scans.values()
        ),
        "autopilot": db.get_setting("job_board_autopilot", {}),
    }


# Public employer boards; individual errors are isolated and visible in the UI.
CATALOG = {
    "ashby": [
        "ramp",
        "notion",
        "openai",
        "cursor",
        "cohere",
        "perplexity",
        "vercel",
        "linear",
        "figma",
        "replit",
        "modal",
        "baseten",
        "anyscale",
        "langchain",
        "pinecone",
        "weaviate",
        "supabase",
        "neon",
        "lovable",
        "sierra",
        "synthesia",
        "elevenlabs",
        "deepgram",
        "happyrobot.ai",
        "fieldguide",
        "quora",
        "mercor",
        ("Grow Therapy", "https://jobs.ashbyhq.com/grow-therapy"),
    ],
    "greenhouse": [
        "stripe",
        "databricks",
        "datadog",
        "cloudflare",
        "scaleai",
        "duolingo",
        "reddit",
        "discord",
        "roblox",
        "instacart",
        "coinbase",
        "affirm",
        "brex",
        "doordashusa",
        "lyft",
        "gusto",
        "robinhood",
        "hubspot",
        "mongodb",
        "elastic",
        "samsara",
        "verkada",
        "andurilindustries",
        "figma",
        "fivetran",
        "waymo",
        "anthropic",
        "togetherai",
    ],
    "smartrecruiters": [
        "ServiceNow",
        "Visa",
        "Walmart",
        "BoschGroup",
        "WesternDigital",
        "PaloAltoNetworks",
        "NielsenIQ",
        "socotec",
    ],
    "lever": ["hive", "AIFund", "palantir", "spotify", "zoox"],
    "workday": [
        ("Amgen", "https://amgen.wd1.myworkdayjobs.com/en-US/careers"),
        ("NVIDIA", "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"),
        ("Adobe", "https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced"),
        ("Salesforce", "https://salesforce.wd12.myworkdayjobs.com/en-US/External_Career_Site"),
        ("Capital One", "https://capitalone.wd12.myworkdayjobs.com/en-US/Capital_One"),
    ],
}


def _extend_catalog():
    hints = json.loads((Path(__file__).parent / "data" / "employer_catalog.json").read_text())
    for kind in ("greenhouse", "ashby"):
        existing = {entry for entry in CATALOG[kind] if isinstance(entry, str)}
        CATALOG[kind].extend(slug for slug in hints[kind] if slug not in existing)
    CATALOG["career_page"] = [tuple(entry) for entry in hints["career_page"]]


_extend_catalog()


def setup_sources(db, cfg=None):
    configure = cfg is not None
    cfg = cfg or settings(db)
    if cfg.interval_hours not in {1, 2, 3, 6}:
        raise ValueError("Choose a 1, 2, 3 or 6 hour refresh interval")
    ids = managed_ids(db)
    with db.exclusive() as s:
        replacements = {
            "https://jobs.ashbyhq.com/anthropic": (
                "greenhouse",
                "https://job-boards.greenhouse.io/anthropic",
                "anthropic",
            ),
            "https://jobs.ashbyhq.com/togetherai": (
                "greenhouse",
                "https://job-boards.greenhouse.io/togetherai",
                "togetherai",
            ),
            "https://jobs.ashbyhq.com/growtherapy": (
                "ashby",
                "https://jobs.ashbyhq.com/grow-therapy",
                "Grow Therapy",
            ),
        }
        retired = {
            "https://job-boards.greenhouse.io/" + slug
            for slug in ["snowflake", "confluent", "rippling", "aetion"]
        }
        retired |= {"https://jobs.lever.co/" + slug for slug in ["netflix", "viking", "welocalize"]}
        for row in s.scalars(select(Source).where(Source.id.in_(ids))):
            if configure:
                row.enabled = cfg.enabled and (cfg.web_search or row.kind != "web_search")
                row.cron, row.auto_queue = f"0 */{cfg.interval_hours} * * *", False
            if row.url in replacements:
                row.kind, row.url, row.name = replacements[row.url]
            elif row.url in retired:
                row.enabled = False
                ids.discard(row.id)
        for kind, entries in CATALOG.items():
            for entry in entries:
                entry_kind = kind
                name, url = (
                    entry
                    if isinstance(entry, tuple)
                    else (
                        entry,
                        {
                            "ashby": "https://jobs.ashbyhq.com/",
                            "greenhouse": "https://job-boards.greenhouse.io/",
                            "lever": "https://jobs.lever.co/",
                            "smartrecruiters": "https://jobs.smartrecruiters.com/",
                        }[kind]
                        + entry,
                    )
                )
                if url in retired:
                    continue
                if url in replacements:
                    entry_kind, url, name = replacements[url]
                row = s.scalar(select(Source).where(Source.url == url))
                if row and row.id not in ids:
                    continue  # Preserve separately configured sources and their policies.
                if not row:
                    row = Source(
                        name=name,
                        kind=entry_kind,
                        url=url,
                        cron=f"0 */{cfg.interval_hours} * * *",
                        auto_queue=False,
                        next_run=(datetime.now(UTC) + timedelta(hours=cfg.interval_hours)).isoformat(),
                    )
                    s.add(row)
                    s.flush()
                if configure:
                    row.enabled = cfg.enabled
                row.cron, row.auto_queue = f"0 */{cfg.interval_hours} * * *", False
                ids.add(row.id)
    db.set_setting("job_board_settings", cfg.model_dump())
    db.set_setting("job_board_sources", {"ids": sorted(ids)})
    from .jobsearch import setup_search

    setup_search(db)
    return len(managed_ids(db))
