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
from sqlalchemy import and_, case, func, or_, select

from .db import Job, JobListing, Run, Setting, Source, now
from .board_data import clean_location, expired_internship
from .network import fetch
from .schemas import JobInput, Profile
from .widgets import US_STATES


class BoardSettings(BaseModel):
    enabled: bool = True
    interval_hours: Literal[1, 2, 3, 6] = 2
    early_career_only: bool = False  # A setup choice; see Setup › Job board.
    web_search: bool = True
    auto_apply: bool = False
    batch_target: int = Field(default=100, ge=1, le=500)
    review_size: int = Field(default=25, ge=5, le=100)  # how many jobs a review batch gathers (review_batch.py)
    minimum_auto_fit: int = Field(default=70, ge=0, le=100)
    max_age_hours: Literal[48, 168, 720] = 720
    # Postings read from a public board API (Greenhouse, Ashby, Lever, SmartRecruiters) are live when listed, so their
    # window may be longer than the rest: unset, it is 60 days when `max_age_hours` is the 30-day maximum and follows a
    # tighter choice. In a 30-day replay over 11,486 postings of 270 boards, 55% were refused as older; 60 days admits
    # 89% more matches, 22 of 24 sampled pages answered live (2026-11-01).
    live_board_age_hours: Literal[48, 168, 720, 1080, 1440, 2160] | None = None

    def window_hours(self, kind=None):
        """How old a posting from a source of this kind may be. Sources that cannot show a posting is live (custom
        sites, short-link feeds, search results, Workday's undated "30+ days") keep `max_age_hours`."""
        from .features.discovery.policy import LIVE_API_KINDS

        if kind not in LIVE_API_KINDS:
            return self.max_age_hours
        if self.live_board_age_hours:
            return self.live_board_age_hours
        return max(self.max_age_hours, LIVE_BOARD_DEFAULT_HOURS) if self.max_age_hours >= 720 else self.max_age_hours


LIVE_BOARD_DEFAULT_HOURS = 1440


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
    # "New Mexico" is a US state and Dublin a city in Ohio, California and Georgia too (CVS, AIG 2026-10-23).
    r"Australia|Singapore|(?<!New )Mexico|Brazil|Ireland|Dublin(?!,?\s*(?:OH|CA|GA|Ohio|California|Georgia)\b)|"
    r"Netherlands|Amsterdam|"
    # German-speaking listings name places in German ("Bundesweit, DE" is Germany-wide, not Delaware; Sopra Steria
    # 2026-10-27). Only German spellings: Hamburg, Stuttgart and Cologne are US towns too.
    r"Bundesweit|Deutschland|Österreich|Schweiz|München|Köln|Düsseldorf|"
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


# German-language postings mark the role's genders in the title ("AI Backend Developer - m/w/d", "(w/m/d)"). Sopra
# Steria's "Bundesweit, DE" (nationwide, Germany) read as Delaware and five German roles were applied to (2026-10-27).
# The marker always names "d" (divers) or "x"; "M/W/F" alone is a weekday schedule.
GERMAN_ROLE = re.compile(
    r"(?<![\w/])(?:[mwf]\s*/\s*[mwf]\s*/\s*[dx]|[mwf]\s*/\s*[dx]\s*/\s*[mwf]|[dx]\s*/\s*[mwf]\s*/\s*[mwf])(?![\w/])", re.I
)


def us_location(location, country=""):
    # A listing of several locations qualifies when one of them is in the US ("Los Angeles, CA; Toronto, Canada").
    parts = [x for x in re.split(r"\s*(?:;|\||\s/\s)\s*", str(location or "")) if x.strip()]
    if len(parts) > 1:
        return any(us_location(x, country) for x in parts)
    country = str(country or "").strip().casefold()
    if FOREIGN.search(location or ""):
        return False  # A feed's US/HQ descriptor cannot override an explicit foreign job location.
    if country:
        return country in US
    # A foreign single-location listing wins over an incidental US city/company name.
    if FOREIGN.search(location or ""):
        return False
    return bool(US_PLACE.search(location or "") or US_REGION.search(location or ""))


NO_PARTICULAR_PLACE = re.compile(
    r"\b(?:anywhere|nationwide|all locations?|any location|open to (?:any|all)|united states|usa|u\.s\.a?\.?|us)\b", re.I
)
STATE_NAMES = {name.casefold(): code for code, name in US_STATES.items()}


def _place(text):
    """(city, state code) of "Denver, CO", "Denver, Colorado", "Colorado" or "CO", with None for the part it does not name."""
    text = re.sub(r"\s+", " ", re.sub(r"\b(?:hybrid|on-?site|in[- ]office|metro(?:politan)?(?: area)?|area|greater)\b", " ", str(text or ""), flags=re.I)).strip(" ,-–—()")
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) > 1 and parts[-1].casefold() in US:
        parts.pop()  # "Austin, TX, United States".
    if not parts:
        return None, None
    last = parts[-1]
    state = last.upper() if last.upper() in US_STATES and (len(parts) > 1 or last.isupper()) else STATE_NAMES.get(last.casefold())
    if not state:
        return text.casefold(), None
    return (parts[0].casefold() if len(parts) > 1 else None), state


def outside_target_locations(profile, location):
    """Whether a posting names only places the applicant did not ask for. A profile's target locations reached only the
    ranker's prompt, whose instructions favour relocation: someone in one city looking only there would be applied to across
    the country and then answer that they will not relocate. Every confirmed application so far came from a profile set to
    "anywhere", so this never showed (2026-11-01). Same state counts as near enough, since a metro area crosses city names;
    a remote post, a place this cannot read ("2 Locations") and an applicant willing to relocate stay with the ranker."""
    from .answer_context import boolean

    targets = [str(t).strip() for t in getattr(profile, "target_locations", None) or [] if str(t).strip()]
    if not targets or any(NO_PARTICULAR_PLACE.search(t) for t in targets):
        return False
    if boolean((getattr(profile, "facts", None) or {}).get("willing_to_relocate")) is True:
        return False
    wanted = [_place(t) for t in targets if not re.search(r"\bremote\b", t, re.I)]
    if not wanted:
        return False  # Remote only: whether a post can be done remotely is the ranker's reading of the whole posting.
    pieces = [p for p in re.split(r"\s*(?:;|\||\s/\s)\s*", str(location or "")) if p.strip()]
    if not pieces or re.search(r"\bremote\b", str(location), re.I):
        return False
    for piece in pieces:
        city, state = _place(piece)
        if not state:
            return False
        if any((want_state and want_state == state) or (want_city and want_city == city) for want_city, want_state in wanted):
            return False
    return True


def role_matches(title, description, profile):
    if any(re.search(r"\b" + re.escape(x) + r"\b", title, re.I) for x in profile.excluded_keywords if x):
        return False
    # Co-ops and work-study are internships here as in the feeds (an internship stays out unless the applicant asks).
    from .features.discovery.feeds import INTERN

    if not profile.include_internships and INTERN.search(title):
        return False
    from .features.discovery.roles import NOISE, role_fits

    roles = profile.target_roles  # A profile with no roles matches nothing: the applicant sets them in Setup.
    # Synonym-aware: "Software Development Engineer" also matches "Software Engineer II" and "SDE". A profile's
    # own role wins over the generic exclusions below (a product manager's profile targets "Product Manager").
    if role_fits(title, roles):
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
        return bool(ML_ROLE.search(title) and not NOISE.search(title)) or bool(
            re.search(r"\b(?:software|data) engineer\b", title, re.I)
            and re.search(
                r"\b(?:machine learning|LLM|deep learning|model training|AI agents?)\b", description, re.I
            )
        )
    return False


def workday_searches(profile):
    """Workday tenant search terms from the profile's own target roles, plus "new grad" for profiles that apply to
    new-grad roles first."""
    from .features.discovery.roles import search_terms

    terms = search_terms(profile.target_roles, limit=6)
    if getattr(profile, "job_priority", "new_grad_first") == "new_grad_first":
        terms.append("new grad")
    return list(dict.fromkeys(terms))


# Level words the tenant's own search matches in titles and descriptions. Searched first: "Software Engineer" alone
# returned hundreds of experienced postings per tenant while the early-career ones sat behind them.
WORKDAY_LEVEL_TERMS = ("new grad", "entry level", "early career", "university", "junior", "associate")
WORKDAY_LEVEL_PAGES, WORKDAY_ROLE_PAGES = 3, 2


def workday_plan(profile, cfg):
    """(search text, page limit) for one Workday tenant. Level terms lead for a new-grad-first profile or an
    early-career-only board; the profile's own role terms follow with fewer pages (their relevance order is not a date
    order, so deep pages are mostly senior postings)."""
    terms = workday_searches(profile)
    level = []
    if cfg.early_career_only or getattr(profile, "job_priority", "new_grad_first") == "new_grad_first":
        level = list(WORKDAY_LEVEL_TERMS)
    plan = [(q, WORKDAY_LEVEL_PAGES) for q in level]
    plan += [(q, WORKDAY_ROLE_PAGES) for q in terms if q.casefold() not in {x.casefold() for x in level}]
    return plan


WORKDAY_AGE = re.compile(r"Posted (?:(Today|Yesterday)|(\d{1,2}) Days? Ago)", re.I)


def workday_age_days(posted_on):
    """Days since a Workday list row was posted; None for "Posted 30+ Days Ago" and anything else it cannot date."""
    match = WORKDAY_AGE.search(posted_on or "")
    if not match:
        return None
    return 0 if match[1] and match[1].casefold() == "today" else 1 if match[1] else int(match[2])


def candidate_title(title, profile):
    return (
        role_matches(title, "", profile)
        or bool(ENTRY.search(title) and re.search(r"\b(?:engineer|scientist)\b", title, re.I))
        or bool(
            len(profile.skills) >= 3
            and re.search(r"\b(?:software|data|backend|full.?stack) engineer\b", title, re.I)
        )
    )


# Spelled and ranged ways a posting states its years ("Five (5) years", "6-10+ years in data engineering") that YEARS, which
# wants the word experience nearby, does not read. Used only to keep a posting that states no experience out of the
# accepted-years admission when it does name a high minimum.
SPELLED = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
STATED_YEARS = re.compile(
    r"\b(?P<low>\d{1,2}|" + "|".join(SPELLED) + r")\b(?:\s*\(\d{1,2}\))?\s*(?:\+|(?:[-–—]|to)\s*\d{1,2}\+?)?\s*"
    r"(?:years?|yrs?)\b",
    re.I,
)
UNSTATED_MIN_CHARS = 800
HARD_BARRIER = re.compile(
    r"\bcleared\b|security clearance|clearance required|active (?:secret|top secret)|ts/sci|polygraph|"
    r"(?:u\.?s\.?|united states) citizen(?:ship)?\s+(?:is\s+)?required|must be a (?:u\.?s\.?|united states) citizen",
    re.I,
)


def high_minimum_stated(description):
    """Whether a posting names a minimum above three years anywhere, however it words it (preferred ones excluded)."""
    for match in STATED_YEARS.finditer(description):
        context = description[max(0, match.start() - 70) : match.end() + 50]
        if re.search(r"preferred|nice.to.have|bonus|ideally", context, re.I):
            continue
        low = match["low"].casefold()
        if (SPELLED.get(low) or int(low)) > 3:
            return True
    return False


def accepts_unstated_experience(profile, post):
    """A profile that accepts roles asking up to N years of experience also accepts a plain-titled role that asks for
    none: it cannot ask for more than N. Without it, early-career-only refused "Software Engineer", "Data Engineer" and
    "AI Engineer" for saying nothing about years (62 of 1,816 US postings in the 30-day window, 45 matched, 2026-11-01);
    the ranking reads the whole posting against the accepted years. A posting that names a clearance or citizenship
    requirement, or any high minimum, would only be ranked and refused, so it stays out."""
    return (
        int(getattr(profile, "accept_experience_years", 0) or 0) > 0
        # Silence means something only in a posting that was read: a listing's label or a few lines says nothing.
        and len(post.description) >= UNSTATED_MIN_CHARS
        and not high_minimum_stated(post.description)
        and not HARD_BARRIER.search(post.title + "\n" + post.description)
    )


def screen(post, profile, settings):
    if not us_location(post.location, post.country):
        return None, "non_us_or_unknown"
    if (settings.early_career_only and SENIOR.search(post.title)) or not role_matches(
        post.title, post.description, profile
    ):
        return None, "role_or_seniority"
    years = []
    for match in YEARS.finditer(post.description):
        context = post.description[max(0, match.start() - 70) : match.end() + 50]
        if not re.search(r"preferred|nice.to.have|bonus|ideally", context, re.I):
            years.append(int(match["low"]))
    limit = max(3, int(getattr(profile, "accept_experience_years", 0) or 0))
    if years and min(years) > limit:
        # Only a profile that asked for early-career work: this refused every posting above three years for everyone, so
        # an applicant with eight years of experience could never be matched with a role at their own level (2026-11-01).
        # Everyone else's posting goes on to the ranking, which reads the requirement against their own history.
        if settings.early_career_only:
            return None, "experience"
        return ("Experienced", f"Posting asks for {min(years)} or more years of experience"), None
    entry = bool(ENTRY.search(post.title) or ENTRY.search(post.level))
    new_grad = bool(re.search(r"new[ -]?grad|recent graduate|university[ -]?grad", post.title, re.I))
    description_entry = re.search(
        r"\b(?:new[ -]?grad(?:uate)?|recent graduates?|early[ -]?career|entry[ -]?level)\b",
        post.description,
        re.I,
    )
    if not entry and not (years and min(years) <= limit) and not description_entry:
        if settings.early_career_only and not accepts_unstated_experience(profile, post):
            return None, "experience_unspecified"
        return ("Experience unspecified", "Relevant role; verify career level before applying"), None
    level = "New grad" if new_grad else "Early career"
    reason = (
        "Explicit new grad / early career posting" if entry else f"Posting accepts up to {limit} years of experience"
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


DETAIL_SLOTS = 4  # Concurrent posting-detail reads per board.


async def read_feed(source, profile, cfg, at, known=None):
    """Bounded board reads and detail calls; failed feeds never mark listings closed.

    `known` is an async function (links -> links) naming postings that need no detail read: already stored with a
    listing, or read and rejected by this profile recently. Their detail pages were fetched again on every scan (a
    58-posting Workday tenant cost 58 sequential requests each time, 2026-10-29)."""
    url = source["url"].rstrip("/")
    parsed = urlsplit(url)
    slug = parsed.path.strip("/").split("/")[0]
    kind, company = source["kind"], source["name"]
    window = cfg.window_hours(kind)
    posts, seen = [], set()
    complete = True
    detail_errors = 0

    async def get(url, **kwargs):
        body, _ = await fetch(url, **kwargs)
        # Whole-company boards are megabytes; parse them off the application loop.
        return await asyncio.to_thread(json.loads, body) if len(body) > 100_000 else json.loads(body)

    async def unseen(links):
        skip = await known(list(links)) if known else set()
        return [link for link in links if link not in skip]

    detail_slots = asyncio.Semaphore(DETAIL_SLOTS)

    async def slot(work):
        async with detail_slots:
            return await work

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
            # The list names first_published: a posting older than the window needs no detail read.
            published_at = published(j.get("first_published"))
            if (
                us_location(post.location)
                and not (cfg.early_career_only and SENIOR.search(post.title))
                and candidate_title(post.title, profile)
                and (published_at is None or fresh(j["first_published"], at, window))
            ):
                candidates.append((j, post))
        candidates.sort(
            key=lambda item: (
                fresh(item[0].get("updated_at"), at, window),
                bool(ENTRY.search(item[1].title)),
                item[0].get("updated_at", ""),
            ),
            reverse=True,
        )
        wanted = set(await unseen([post.url for _, post in candidates]))
        candidates = [item for item in candidates if item[1].url in wanted]

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
        after = (at - timedelta(hours=window)).isoformat()
        from urllib.parse import urlencode

        for offset in range(0, 500, 100):
            data = await get(
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?"
                + urlencode({"limit": 100, "offset": offset, "releasedAfter": after})
            )
            rows = data.get("content", [])
            pending = []
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
                    fresh(post.posted, at, window)
                    and us_location(post.location, post.country)
                    and not (cfg.early_career_only and SENIOR.search(post.title))
                    and candidate_title(post.title, profile)
                ):
                    pending.append((j, post))
            wanted = set(await unseen([post.url for _, post in pending]))

            async def read_detail(item):
                nonlocal detail_errors
                j, post = item
                try:
                    detail = await slot(
                        get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{j['id']}")
                    )
                    sections = detail.get("jobAd", {}).get("sections", {})
                    post.description = "\n".join(
                        text(v.get("text", "")) for v in sections.values() if isinstance(v, dict)
                    )
                    posts.append(post)
                except Exception:
                    detail_errors += 1

            await asyncio.gather(*(read_detail(item) for item in pending if item[1].url in wanted))
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
        max_days = cfg.max_age_hours // 24
        wanted = {}  # link -> (title, path, row)
        searches = asyncio.Semaphore(3)

        async def search(query, pages):
            nonlocal detail_errors
            for page in range(pages):
                async with searches:
                    data = await get(
                        root + "/jobs",
                        method="POST",
                        payload={"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": query},
                    )
                rows = data.get("jobPostings", [])
                found = 0
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
                    if (cfg.early_career_only and SENIOR.search(title)) or not candidate_title(title, profile):
                        continue
                    # The list dates a posting to the day ("Posted 3 Days Ago"; "30+" is undatable). It used to
                    # cap at 7 days, so a newly added tenant's older postings in the window were never read.
                    age = workday_age_days(j.get("postedOn", ""))
                    if age is None or age > max_days:
                        continue
                    wanted[link] = (title, path, j)
                    found += 1
                # A short page ends the results; a page with nothing new for this profile ends the useful ones.
                if len(rows) < 20 or not found:
                    break

        outcomes = await asyncio.gather(
            *(search(query, pages) for query, pages in workday_plan(profile, cfg)), return_exceptions=True
        )
        failed = [x for x in outcomes if isinstance(x, BaseException)]
        if failed and len(failed) == len(outcomes):
            raise failed[0]  # Every search failed (dead or refusing tenant): the source error, not an empty board.
        detail_errors += len(failed)
        fetch_links = await unseen(list(wanted))

        async def read_detail(link):
            nonlocal detail_errors
            title, path, j = wanted[link]
            try:
                detail = await slot(get(root + path))
                info = detail.get("jobPostingInfo", {})
                # The list's "2 Locations" is a count; the posting's own page names the places (board_data).
                places = [info.get("location") or ""] + [p for p in info.get("additionalLocations") or [] if isinstance(p, str)]
                loc = clean_location(" / ".join(dict.fromkeys(p for p in places if p)) or j.get("locationsText", ""))
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

        await asyncio.gather(*(read_detail(link) for link in fetch_links[:60]))
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
        wanted = set(await unseen([post.url for post in candidates]))

        async def read_document(candidate):
            nonlocal detail_errors
            try:
                post = await slot(document_post(candidate.url, company))
                # createdAt describes creation, not a verified publication date.
                posts.append(post or candidate)
            except Exception:
                detail_errors += 1

        await asyncio.gather(*(read_document(c) for c in [c for c in candidates if c.url in wanted][:20]))
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
        seen.update(links)

        async def read_document(link):
            nonlocal detail_errors
            try:
                if post := await slot(document_post(link, company)):
                    posts.append(post)
            except Exception:
                detail_errors += 1

        await asyncio.gather(*(read_document(link) for link in await unseen(links)))
    # Greenhouse, Ashby and Lever list every open posting before any detail is read, so a failed detail read says nothing
    # about what is still listed (a posting the board no longer lists is closed whatever happened to its neighbours' details).
    whole_list = kind in {"greenhouse", "ashby", "lever"}
    return posts, seen, complete and (whole_list or not detail_errors), detail_errors


REJECTED = "job_board_rejected:"
REJECTED_DAYS = 3  # A posting read and rejected is not read again for this long.
LOOKUP_CHUNK = 400


def screen_fingerprint(profile, cfg):
    """What decides a posting's screen result: a changed profile, board setting or rule reads rejected postings again."""
    import hashlib

    basis = [
        sorted(profile.target_roles), sorted(profile.skills), sorted(profile.excluded_keywords),
        profile.include_internships, cfg.early_career_only, cfg.max_age_hours, cfg.window_hours("greenhouse"),
        profile.accept_experience_years, "screen-3",
    ]
    return hashlib.sha1(json.dumps(basis).encode()).hexdigest()[:16]


def listed_links(db, links):
    """links -> job id for postings this database already holds with a listing (any source)."""
    from .ats import identity_variants

    by_identity = {}
    for link in links:
        try:
            for identity in identity_variants(link):
                by_identity.setdefault(identity, link)
        except ValueError:
            continue  # Not an application URL; it is read like any other.
    found = {}
    identities = list(by_identity)
    with db.session() as s:
        for i in range(0, len(identities), LOOKUP_CHUNK):
            for identity, job_id in s.execute(
                select(Job.identity, Job.id)
                .join(JobListing, JobListing.job_id == Job.id)
                .where(Job.identity.in_(identities[i : i + LOOKUP_CHUNK]))
            ):
                found[by_identity[identity]] = job_id
    return found


def rejected_links(db, source_id, fingerprint, at):
    memo = db.get_setting(REJECTED + source_id, {})
    since = published(memo.get("since"))
    if memo.get("fp") != fingerprint or since is None or at - since > timedelta(days=REJECTED_DAYS):
        return set()
    return set(memo.get("urls", []))


def persist_scan(db, source, posts, seen, complete, cfg, profile, at, report):
    """Store a scan's postings. Runs in a worker thread: a few hundred matches meant hundreds of locked transactions
    and a full same-ATS identity scan per new job on the loop the browsers share (2026-10-29)."""
    from .ats import identity_variants
    from .discovery import add_job

    window = cfg.window_hours(source["kind"])
    created, rejected = [], set()
    for post in posts:
        if not published(post.posted):
            report["unknown_date"] += 1
            rejected.add(post.url)
            continue
        if not fresh(post.posted, at, window):
            report["old"] += 1
            rejected.add(post.url)
            continue
        match, _ = screen(post, profile, cfg)
        if not match or expired_internship(post.title, at):
            # An internship whose named term has ended ("Summer 2026" read in November) is not an opening (board_data).
            report["excluded"] += 1
            rejected.add(post.url)
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
        if not new and published(row["created_at"]) < at - timedelta(hours=window):
            # Already seeing this public job earlier proves it isn't a newly posted job.
            report["old"] += 1
            rejected.add(post.url)
            continue
        with db.exclusive() as s:
            entry = s.get(JobListing, row["id"])
            date = published(post.posted).isoformat()
            if entry:
                # Republishing cannot make an already old stored listing fresh again.
                date = min(entry.posted_at, date)
                # A job closed after a run found its posting gone stays off the board even where a stale board still links it.
                entry.posted_at, entry.last_seen = date, at.isoformat()
                entry.active = entry.active or row["status"] != "closed"
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
    # Postings still on the board that were not read again (already stored): they are still listed. Without this the
    # board's "last seen" lapsed and a listing left the pipeline hours after its last full read.
    stored = {post.url for post in posts}
    still = {k: v for k, v in listed_links(db, seen).items() if k not in stored}
    if still:
        ids = list(still.values())
        with db.exclusive() as s:
            for i in range(0, len(ids), LOOKUP_CHUNK):
                for entry, status in s.execute(
                    select(JobListing, Job.status)
                    .join(Job, Job.id == JobListing.job_id)
                    .where(JobListing.job_id.in_(ids[i : i + LOOKUP_CHUNK]))
                ):
                    entry.last_seen = at.isoformat()
                    entry.active = entry.active or status != "closed"
        report["known"] = len(still)
        report["matched"] += len(still)
    if complete:
        # A job stored before the platform hint was dropped keeps its older identity; both mean "still listed".
        identities = {identity for url in seen for identity in identity_variants(url)}
        with db.session() as s:
            for entry, job in s.execute(
                select(JobListing, Job)
                .join(Job, Job.id == JobListing.job_id)
                .where(JobListing.source_id == source["id"])
            ):
                if job.identity not in identities:
                    entry.active = False
    return created, rejected


async def scrape_source(source, db, config):
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
    await asyncio.to_thread(db.set_setting, key, report)
    try:
        profile = Profile.model_validate(await asyncio.to_thread(db.get_setting, "profile", {}))
        fingerprint = screen_fingerprint(profile, cfg)
        skip = await asyncio.to_thread(rejected_links, db, source["id"], fingerprint, at)

        async def known(links):
            return set(await asyncio.to_thread(listed_links, db, links)) | skip.intersection(links)

        posts, seen, complete, errors = await read_feed(source, profile, cfg, at, known)
        report.update(checked=len(seen), detail_errors=errors, read=len(posts))
        created, rejected = await asyncio.to_thread(
            persist_scan, db, source, posts, seen, complete, cfg, profile, at, report
        )
        # Keep memo entries still on the board plus what this scan rejected; the window restarts when it lapses.
        kept = (skip & seen) | rejected
        memo = await asyncio.to_thread(db.get_setting, REJECTED + source["id"], {})
        since = memo.get("since") if memo.get("fp") == fingerprint and skip else at.isoformat()
        await asyncio.to_thread(
            db.set_setting, REJECTED + source["id"], {"fp": fingerprint, "since": since, "urls": sorted(kept)[:2000]}
        )
        report.update(state="complete", finished_at=now(), new=len(created))
        await asyncio.to_thread(db.set_setting, key, report)
        return created
    except Exception as exc:
        report.update(
            state="error", finished_at=now(), error=f"Board could not be read ({type(exc).__name__})"
        )
        await asyncio.to_thread(db.set_setting, key, report)
        raise


UNREAD_HOURS = 72  # Without any read at all, a listing older than this is no longer called open (the app has not looked in days).


def seen_window_hours(interval_hours):
    return max(6, (interval_hours or 2) * 2 + 1)


def unconfirmed(at, interval_hours):
    """SQL: a listing that is no longer shown as open because it was not seen lately AND that means it is gone.

    Not seen within the window is not enough: a Mac that sleeps, or a scan that works through thousands of boards over hours,
    leaves listings unread for longer than the window while nothing has found them gone, and the board read "No open postings"
    until the scans caught up (production 2026-11-02: 2,710 of 5,270 active listings were past a 6-hour cutoff). A listing is
    gone when its own board, or the new-grad list, was read successfully after it was last seen and did not show it. Until
    then it stays open, for at most UNREAD_HOURS."""
    from .features.discovery.feeds import SWEEP_KEY

    seen = JobListing.last_seen
    sweep = (
        select(Setting.value["finished_at"].as_string())
        .where(Setting.key == SWEEP_KEY, Setting.value["state"].as_string() != "error")
        .scalar_subquery()
    )
    scan = (
        select(Setting.value["finished_at"].as_string())
        .where(Setting.key == "job_board_scan:" + JobListing.source_id, Setting.value["state"].as_string() == "complete")
        .correlate(JobListing)
        .scalar_subquery()
    )
    read_since = func.coalesce(case((JobListing.source_id.startswith("feed:", autoescape=True), sweep), else_=scan), "") > seen
    cutoff = (at - timedelta(hours=seen_window_hours(interval_hours))).isoformat()
    unread = (at - timedelta(hours=UNREAD_HOURS)).isoformat()
    return and_(seen < cutoff, or_(seen < unread, read_since))


LISTED = "listing__"
# A listing in one of these states is already owned or protected (frontend/src/jobboard.tsx LOCKED). "closed" is a posting
# that is gone or whose employer restriction rules the applicant out: not something found and waiting to be applied to
# (Discovered listed such jobs with an Apply button and the word "closed", 2026-11-01).
LOCKED = frozenset(
    {"paused", "queued", "running", "submitting", "confirmed", "submission_unknown", "already_applied", "archived", "closed"}
)
REQUESTED = "owner_apply:"  # owner_apply.KEY


FITS = ("eligible", "uncertain", "unranked", "below")


def fit_of(job, minimum):
    """eligible | unranked | below | uncertain (ranked as eligible with open questions, or under review)."""
    score = _field(job, "score")
    if _field(job, "status") == "skipped" or (score is not None and score < minimum):
        return "below"
    if score is None:
        return "unranked"
    return "eligible" if _field(job, "status") == "ready" else "uncertain"


def _field(job, name):
    return job[name] if isinstance(job, dict) else getattr(job, name)


def within_window(cfg, at):
    """SQL: the listing was posted inside its source kind's window (BoardSettings.window_hours)."""
    from sqlalchemy import and_, or_

    from .features.discovery.policy import LIVE_API_KINDS

    short = (at - timedelta(hours=cfg.max_age_hours)).isoformat()
    live = (at - timedelta(hours=cfg.window_hours("greenhouse"))).isoformat()
    if short == live:
        return JobListing.posted_at >= short
    on_live_board = JobListing.source_id.in_(select(Source.id).where(Source.kind.in_(LIVE_API_KINDS)))
    return or_(
        and_(JobListing.posted_at >= live, on_live_board), and_(JobListing.posted_at >= short, ~on_live_board)
    )


def backed_off(db, ids, at):
    """The boards whose last scan failed permanently (not found, refused: workers.source_failed) and whose next try is still a day or
    more away. Search now and a first search used to ask every one of them again: 287 of 680 default boards on a new install."""
    with db.session() as s:
        rows = s.execute(select(Source.id, Source.next_run, Source.error).where(Source.id.in_(list(ids))))
        later = (at + timedelta(hours=12)).isoformat()
        out = set()
        for sid, next_run, error in rows:
            try:
                due = datetime.fromisoformat(next_run)
            except (TypeError, ValueError):
                continue
            due = due if due.tzinfo else due.replace(tzinfo=UTC)
            if (error or "").startswith("Source failed") and due.isoformat() > later:
                out.add(sid)
    return out


async def request_scan(db, enqueue):
    """Queue a scan of every managed board now: the Search now button, and the first search of a new install.
    Returns (ids of all boards, how many were queued). A board already queued or running is left alone."""
    started = now()

    def claim_scans():
        # Every source's scan flag is a read and a locked write; hundreds of boards ran them one by one on the
        # event loop (2026-10-29). One thread does them all.
        setup_sources(db)
        with db.session() as s:
            wanted = Source.id.in_(managed_ids(db))
            ids = list(s.scalars(select(Source.id).where(wanted, Source.enabled.is_(True))))
        claimed = []
        dead = backed_off(db, ids, datetime.now(UTC))
        for sid in ids:
            if sid in dead:
                continue  # Gone or refusing us: its own back-off (one day, up to two weeks) decides when it is asked again.
            scan = db.get_setting("job_board_scan:" + sid, {})
            if scan.get("state") in {"queued", "running"} and datetime.now(UTC) - datetime.fromisoformat(
                scan.get("started_at", started)
            ) < timedelta(hours=6):
                continue
            db.set_setting("job_board_scan:" + sid, {"state": "queued", "started_at": started})
            claimed.append(sid)
        return ids, claimed

    ids, claimed = await asyncio.to_thread(claim_scans)
    queued = 0
    for sid in claimed:
        try:
            queued += bool(await enqueue(sid))
        except Exception:
            await asyncio.to_thread(
                db.set_setting,
                "job_board_scan:" + sid,
                {"state": "error", "started_at": started, "error": "Could not queue this source"},
            )
    await asyncio.to_thread(db.set_setting, "job_board_requested", {"at": started})
    return ids, queued


def first_search_due(db):
    """A profile with roles and a job board that was never searched: nothing has made the boards yet. They exist only after
    Search now or Start, so a friend who finished setup and opened the Job board saw the new-grad lists at best, and
    'the next scheduled search' was two hours after boards nobody had created (two fresh installs, 2026-11-01)."""
    if not (db.get_setting("profile", {}) or {}).get("target_roles"):
        return False
    if db.get_setting("job_board_requested", {}) or managed_ids(db):
        return False
    return settings(db).enabled


def board_snapshot(db, limit=500, *, jobs_only=False):
    """The fresh listings and, unless `jobs_only`, every source with its last scan and the autopilot's state.

    `jobs_only`: the autopilot reads this up to three times a tick and uses only "jobs"; the sources and scans it read and
    dropped were 22% of the executor threads' interpreter time and most of this function's garbage (py-spy 2026-10-30)."""
    cfg = settings(db)
    at = datetime.now(UTC)
    ids = set() if jobs_only else managed_ids(db)
    with db.session() as s:
        # Columns, not whole rows: every fresh posting's description was read (and dropped) on each board poll and
        # each autopilot tick (2026-10-22). Listing fields win over the job's, as before.
        found = s.execute(
            select(
                *(column for column in Job.__table__.columns if column.name != "description"),
                *(column.label(LISTED + column.name) for column in JobListing.__table__.columns),
                # Already applied to (any run), or the owner's Apply is waiting: Jobs › Pipeline shows those.
                Job.id.in_(select(Run.job_id)).label("attempted"),
                Job.id.in_(
                    select(func.substr(Setting.key, len(REQUESTED) + 1)).where(Setting.key.startswith(REQUESTED, autoescape=True))
                ).label("requested"),
            )
            .select_from(JobListing)
            .join(Job, Job.id == JobListing.job_id)
            .where(
                JobListing.active.is_(True),
                JobListing.country == "US",
                within_window(cfg, at),
                JobListing.posted_at <= at.isoformat(),
                ~unconfirmed(at, cfg.interval_hours),
                Job.demo.is_(False),
                Job.status != "archived",
            )
            .order_by(JobListing.posted_at.desc())
            .limit(limit)
        )
        # Listing columns come after the job's, so a listing field wins a shared name (location) as before; one zip
        # per row instead of two key filters (116 ms of a 142 ms board read with 3,000 listings, 2026-10-23).
        names = [key.removeprefix(LISTED) for key in found.keys()]
        jobs = [dict(zip(names, row, strict=True)) for row in found]
        # The database keeps the paused few; every control row (1,278 of them) used to be loaded per board poll.
        paused_jobs = {
            key.removeprefix("job_control:")
            for key in s.scalars(
                select(Setting.key).where(
                    Setting.key.startswith("job_control:", autoescape=True), Setting.value["state"].as_string() == "paused"
                )
            )
        }
        for job in jobs:
            if job["id"] in paused_jobs and job["status"] not in {
                "confirmed",
                "submission_unknown",
                "submitting",
                "already_applied",
            }:
                job["status"] = "paused"
        if cfg.early_career_only and not int((db.get_setting("profile", {}) or {}).get("accept_experience_years") or 0):
            # A profile that accepts N years admits postings that state none (accepts_unstated_experience).
            jobs = [j for j in jobs if j["career_level"] != "Experience unspecified"]
        jobs = [j for j in jobs if us_location(j["location"], j["country"])]
        # Nearly every source is managed: read them all and keep the managed, without an IN list of 4,479 bound
        # parameters (a 70 KB statement parsed per read) or an ORM object per row.
        sources = (
            [dict(row._mapping) for row in s.execute(select(*Source.__table__.columns).order_by(Source.name)) if row.id in ids]
            if ids
            else []
        )
    if jobs_only:
        return {"settings": cfg.model_dump(), "jobs": jobs}
    # Thousands of employers must not cause thousands of SQL requests per UI refresh.
    with db.session() as s:
        scans = {
            key.removeprefix("job_board_scan:"): value
            for key, value in s.execute(select(Setting.key, Setting.value).where(Setting.key.startswith("job_board_scan:", autoescape=True)))
            if key.removeprefix("job_board_scan:") in ids
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
    for kind in ("greenhouse", "ashby", "smartrecruiters"):
        existing = {entry for entry in CATALOG.setdefault(kind, []) if isinstance(entry, str)}
        CATALOG[kind].extend(slug for slug in hints.get(kind, []) if slug not in existing)
    known = {entry[1] for entry in CATALOG["workday"]}
    CATALOG["workday"].extend(tuple(entry) for entry in hints.get("workday", []) if entry[1] not in known)
    CATALOG["career_page"] = [tuple(entry) for entry in hints["career_page"]]


_extend_catalog()


# Sources the owner switched off (Setup › Sources); setup_sources never turns them back on.
OWNER_DISABLED_KEY = "job_board_owner_disabled"


def owner_switched(db, source_id, enabled):
    """Remember a source the owner switched off, or forget it when switched back on."""
    ids = set(db.get_setting(OWNER_DISABLED_KEY, {}).get("ids", []))
    ids = ids - {source_id} if enabled else ids | {source_id}
    db.set_setting(OWNER_DISABLED_KEY, {"ids": sorted(ids)})


def setup_sources(db, cfg=None):
    configure = cfg is not None
    cfg = cfg or settings(db)
    if cfg.interval_hours not in {1, 2, 3, 6}:
        raise ValueError("Choose a 1, 2, 3 or 6 hour refresh interval")
    ids = managed_ids(db)
    owner_disabled = set(db.get_setting(OWNER_DISABLED_KEY, {}).get("ids", []))
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
                # A board the owner switched off stays off: every settings save re-enabled them all (2026-10-29).
                row.enabled = (
                    cfg.enabled
                    and (cfg.web_search or row.kind != "web_search")
                    and row.id not in owner_disabled
                )
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


def board_identity(url):
    """One key per employer board whatever the spelling: boards.greenhouse.io/x, job-boards.greenhouse.io/x and
    job-boards.eu.greenhouse.io/x/ are the same board, as are jobs.lever.co/X and jobs.eu.lever.co/x."""
    from urllib.parse import unquote

    u = urlsplit(unquote(url).strip())
    host, path = (u.hostname or "").casefold(), u.path.strip("/").casefold()
    first = path.split("/")[0]
    for suffix, kind in (("greenhouse.io", "greenhouse"), ("lever.co", "lever"), ("ashbyhq.com", "ashby"), ("smartrecruiters.com", "smartrecruiters")):
        if first and (host == suffix or host.endswith("." + suffix)):
            return f"{kind}|{first}"
    return unquote(url).casefold().rstrip("/")


def add_managed_sources(db, entries):
    """Add (kind, name, url) boards as managed sources, scanned from the next tick; returns how many were new.
    Existing sources (any URL spelling of the same board, also within `entries`) are left alone, and a retired board
    is never re-added."""
    cfg = settings(db)
    retired = {"https://job-boards.greenhouse.io/" + slug for slug in ["snowflake", "confluent", "rippling", "aetion"]}
    retired |= {"https://jobs.lever.co/" + slug for slug in ["netflix", "viking", "welocalize"]}
    ids, added = managed_ids(db), 0
    with db.exclusive() as s:
        have = {board_identity(url) for url in s.scalars(select(Source.url))}
        for kind, name, url in entries:
            key = board_identity(url)
            if key in have or url in retired:
                continue
            row = Source(
                name=name[:200] or url,
                kind=kind,
                url=url,
                cron=f"0 */{cfg.interval_hours} * * *",
                enabled=cfg.enabled,
                auto_queue=False,
                next_run=now(),
            )
            s.add(row)
            s.flush()
            ids.add(row.id)
            have.add(key)
            added += 1
    if added:
        db.set_setting("job_board_sources", {"ids": sorted(ids)})
    return added
