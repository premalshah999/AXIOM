"""Reserved employers: FAANG, MANGO and the Big Four apply only with the owner's permission.

Owner, 2026-10-27: "make a different section for FAANG, MANGO, Big four consulting, and these scrapers should not
apply before my permission." Their postings are still discovered, ranked and listed (Jobs › Reserved, the dashboard
group "reserved"), but nothing starts an application for one until the owner approves that job:

- queue admission refuses it (`require_approval`, code "reserved_employer", outcome "protected"), so the autopilot,
  retries and draft recovery, the repair agent's re-queue, uncertain-submission resubmits and remembered Apply clicks all
  stop at the same gate, and a refusal that is not "deferred" is never retried by any of them;
- the autopilot leaves held jobs out of its candidates and stalled retries, so they never take a slot, and neither the
  repair agent's diagnosis nor resume tailoring is spent on them (ranking still runs: the section shows each job's fit);
- a run of one queued before the hold is stopped before it starts, and the final submit reservation checks again.

Which employers are reserved is the owner's setting `reserved_employers` (sections of employers, each with other names
and career sites); DEFAULT is only its first value and nothing outside it names an employer. A job matches an employer
by its company name, word by word, or by its posting's host and path:

- the company's words must begin with one of the employer's names, and every further word must be a legal form, a
  place or division word (GENERIC) or a word of the employer's own names: "Amazon Web Services (AWS)",
  "PricewaterhouseCoopers (PwC)" and "Deloitte US" match; "Metaview", "Applebee's" and "Apple Leisure Group" do not;
- a site is a host, matched with its subdomains ("amazon.jobs", "careers.microsoft.com"), optionally with "*" for one
  part of a shared platform's host ("nvidia.*.myworkdayjobs.com") and a leading path ("jobs.ashbyhq.com/openai").

The first section that lists the employer is the job's section (Apple and Google are FAANG before MANGO).
"""

from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache
import json
import re
import unicodedata
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import func, or_, select

from .admission import QueueBlocked
from .db import Job, Setting, now

KEY = "reserved_employers"
# reserved_approval:<job id> — the owner's permission for that one job ({"at", "section", "employer"}).
APPROVAL = "reserved_approval:"
CODE = "reserved_employer"
# Words that may follow an employer's name and still mean that employer: legal forms, places, divisions.
GENERIC = frozenset(
    "the and of a an formerly inc incorporated llc llp lp ltd limited corp corporation co company plc gmbh ag sa "
    "us usa u s america americas global worldwide international com services service technologies technology "
    "platforms labs research cloud web careers jobs consulting advisory tax audit assurance".split()
)
SITE = re.compile(r"^[a-z0-9*][a-z0-9*.-]*\.[a-z0-9*-]+(/[a-z0-9._~%/-]*)?$")


def words(text):
    """Lowercase ASCII words; "&" reads as "and" ("Ernst & Young" is "ernst and young")."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().casefold()
    return re.findall(r"[a-z0-9]+", text.replace("&", " and "))


def _clean(values, limit=80):
    seen = []
    for value in values:
        value = " ".join(str(value).split())[:limit]
        if value and value.casefold() not in {v.casefold() for v in seen}:
            seen.append(value)
    return seen


class Employer(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=40)
    sites: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        value = " ".join(value.split())
        if not words(value):
            raise ValueError("An employer name needs letters or digits")
        return value

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, values):
        return [value for value in _clean(values) if words(value)]

    @field_validator("sites")
    @classmethod
    def _sites(cls, values):
        cleaned = []
        for value in _clean(values, 200):
            site = re.sub(r"^[a-z]+://", "", value.strip().casefold()).rstrip("/")
            if not SITE.match(site) or site.split("/")[0].strip("*.") == "":
                raise ValueError(f"Not a site (a host, optionally with a path): {value}")
            cleaned.append(site)
        return cleaned


class Section(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    label: str = Field(min_length=1, max_length=40)
    employers: list[Employer] = Field(default_factory=list, max_length=80)


class Rules(BaseModel):
    """The owner's reserved employers (setting `reserved_employers`). Off unless the owner turns it on in Jobs ›
    Reserved (owner 2026-10-28: "no blockages till I tell you")."""

    enabled: bool = False
    sections: list[Section] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _unique(self):
        ids = [section.id for section in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("Each section needs its own id")
        return self


_APPLE = {"name": "Apple", "sites": ["jobs.apple.com"]}
_GOOGLE = {
    "name": "Google",
    "aliases": ["Alphabet", "Google DeepMind", "DeepMind"],
    "sites": [
        "careers.google.com",
        "google.com/about/careers",
        "deepmind.google",
        "deepmind.com",
        "greenhouse.io/deepmind",
    ],
}
DEFAULT = {
    "enabled": False,
    "sections": [
        {
            "id": "faang",
            "label": "FAANG",
            "employers": [
                {
                    "name": "Meta",
                    "aliases": ["Meta Platforms", "Facebook"],
                    "sites": ["metacareers.com", "facebook.com/careers", "meta.com/careers"],
                },
                _APPLE,
                {
                    "name": "Amazon",
                    "aliases": ["Amazon.com", "Amazon Web Services", "AWS", "Amazon Development Center"],
                    "sites": ["amazon.jobs", "amazon.com"],
                },
                {"name": "Netflix", "sites": ["netflix.com", "netflix.net"]},
                _GOOGLE,
            ],
        },
        {
            "id": "mango",
            "label": "MANGO",
            "employers": [
                {
                    "name": "Microsoft",
                    "sites": ["careers.microsoft.com", "microsoft.eightfold.ai"],
                },
                _APPLE,
                {
                    "name": "Nvidia",
                    "sites": ["nvidia.com", "nvidia.*.myworkdayjobs.com", "*.myworkdaysite.com/recruiting/nvidia"],
                },
                _GOOGLE,
                {"name": "OpenAI", "sites": ["openai.com", "jobs.ashbyhq.com/openai"]},
            ],
        },
        {
            "id": "big_four",
            "label": "Big Four",
            "employers": [
                {"name": "Deloitte", "aliases": ["Deloitte & Touche"], "sites": ["deloitte.com", "deloitte*.avature.net"]},
                {
                    "name": "PwC",
                    "aliases": ["PricewaterhouseCoopers"],
                    "sites": ["pwc.com", "pwc.*.myworkdayjobs.com"],
                },
                {
                    "name": "EY",
                    "aliases": ["Ernst & Young", "EY-Parthenon"],
                    "sites": ["ey.com", "eyglobal.yello.co"],
                },
                {"name": "KPMG", "sites": ["kpmg.com", "kpmguscareers.com"]},
            ],
        },
    ],
}


@dataclass(frozen=True)
class Match:
    section: str
    label: str
    employer: str

    def as_dict(self):
        return {"section": self.section, "label": self.label, "employer": self.employer}


def _term(site):
    """Text every matching URL contains, for the SQL superset: the whole site; for a site with "*", its first part when
    the site starts with text ("nvidia" of "nvidia.*.myworkdayjobs.com"), else its last ("myworkdaysite.com/recruiting/
    nvidia" of "*.myworkdaysite.com/recruiting/nvidia"). Never a shared platform's bare domain, which every one of its
    employers' URLs contains."""
    pieces = [piece for piece in site.split("*") if piece.strip("./")]
    return (pieces[0] if not site.startswith("*") else pieces[-1]).strip(".")


class Matcher:
    """The compiled rules: company names indexed by their first word, sites by host."""

    def __init__(self, rules: Rules):
        self.enabled = rules.enabled
        self.names = {}  # first word -> [(order, name words, allowed words, match)]
        self.hosts = {}  # exact host suffix -> [(order, path, match)]
        self.wildcards = []  # (order, host pattern, path, match)
        company_terms, site_terms = set(), set()
        order = 0
        for section in rules.sections if rules.enabled else ():
            for employer in section.employers:
                match = Match(section.id, section.label, employer.name)
                names = [words(name) for name in (employer.name, *employer.aliases)]
                allowed = GENERIC | {word for name in names for word in name}
                for name in names:
                    self.names.setdefault(name[0], []).append((order, tuple(name), allowed, match))
                    company_terms.add(name[0])
                for site in employer.sites:
                    host, _, path = site.partition("/")
                    if "*" in host:
                        self.wildcards.append((order, host, path, match))
                    else:
                        self.hosts.setdefault(host, []).append((order, path, match))
                    site_terms.add(_term(site))
                order += 1
        self.company_terms = sorted(company_terms)
        self.site_terms = sorted(site_terms)

    def __bool__(self):
        return bool(self.names or self.hosts or self.wildcards)

    def company_hit(self, company):
        """(order, Match) of the first employer whose name the company is, or None."""
        found = words(company)
        if found[:1] == ["the"]:
            found = found[1:]
        best = None
        for order, name, allowed, match in self.names.get(found[0], ()) if found else ():
            if tuple(found[: len(name)]) == name and all(word in allowed for word in found[len(name) :]):
                if best is None or order < best[0]:
                    best = (order, match)
        return best

    def site_hit(self, url):
        """(order, Match) of the first employer whose career site the URL is on, or None."""
        try:
            parsed = urlsplit(url or "")
            host = (parsed.hostname or "").casefold().rstrip(".")
        except ValueError:
            return None
        if not host:
            return None
        path = parsed.path.casefold().strip("/")

        def under(prefix):
            return not prefix or path == prefix or path.startswith(prefix + "/")

        best = None
        labels = host.split(".")
        for index in range(len(labels)):
            for order, prefix, match in self.hosts.get(".".join(labels[index:]), ()):
                if under(prefix) and (best is None or order < best[0]):
                    best = (order, match)
        for order, pattern, prefix, match in self.wildcards:
            if (fnmatchcase(host, pattern) or fnmatchcase(host, "*." + pattern)) and under(prefix):
                if best is None or order < best[0]:
                    best = (order, match)
        return best

    def __call__(self, company, url):
        """The job's section and employer, or None."""
        hits = [hit for hit in (self.company_hit(company), self.site_hit(url)) if hit]
        return min(hits, key=lambda hit: hit[0])[1] if hits else None


def parse(value):
    """The saved rules; DEFAULT when nothing is saved. An unreadable value keeps DEFAULT: it fails closed."""
    if value is None:
        return Rules.model_validate(DEFAULT)
    try:
        return Rules.model_validate(value)
    except ValidationError:
        return Rules.model_validate(DEFAULT)


@lru_cache(maxsize=8)
def _compiled(text):
    return Matcher(parse(json.loads(text) if text else None))


def matcher(session):
    row = session.get(Setting, KEY)
    return _compiled(json.dumps(row.value, sort_keys=True) if row is not None else "")


def rules(session):
    row = session.get(Setting, KEY)
    return parse(row.value if row is not None else None)


def _get(job, key, default=None):
    return job.get(key, default) if isinstance(job, dict) else getattr(job, key, default)


def match(session, job):
    """The reserved section and employer of a job, approved or not; None for other employers and demo jobs."""
    if job is None or _get(job, "demo"):
        return None
    return matcher(session)(_get(job, "company", ""), _get(job, "url", ""))


def approved(session, job_id):
    return session.get(Setting, APPROVAL + job_id) is not None


def held_match(session, job):
    """The reservation holding this job back: a reserved employer the owner has not approved it for."""
    found = match(session, job)
    return found if found and not approved(session, _get(job, "id", "")) else None


def require_approval(session, job):
    """Queue admission's gate. "protected": no unattended path retries it; the owner's approval lifts it."""
    if found := held_match(session, job):
        raise QueueBlocked(
            f"{found.employer} is a reserved employer ({found.label}). Nothing is applied there until you approve "
            "this job in Jobs › Reserved.",
            outcome="protected",
            code=CODE,
        )


def matched(session, *where):
    """{job id: Match} for every non-demo, non-archived job at a reserved employer, approved or not.

    A cheap SQL superset first (the names' first words in the company, the sites' hosts in the URL), then the word and
    host rules in Python, so a poll never reads every job's row."""
    rules_ = matcher(session)
    if not rules_:
        return {}
    company, url = func.lower(Job.company), func.lower(Job.url)
    candidates = [company.contains(term, autoescape=True) for term in rules_.company_terms]
    candidates += [url.contains(term, autoescape=True) for term in rules_.site_terms]
    rows = session.execute(
        select(Job.id, Job.company, Job.url).where(
            Job.demo.is_(False), Job.status != "archived", or_(*candidates), *where
        )
    )
    return {row.id: found for row in rows if (found := rules_(row.company, row.url))}


def approvals(session, job_ids=None):
    """Job ids the owner approved: all of them, or those among `job_ids`."""
    key = Setting.key.like(APPROVAL + "%") if job_ids is None else Setting.key.in_([APPROVAL + j for j in job_ids])
    return set(session.scalars(select(func.substr(Setting.key, len(APPROVAL) + 1)).where(key)))


def held(session, *where):
    """{job id: Match} for reserved-employer jobs the owner has not approved."""
    found = matched(session, *where)
    if not found:
        return {}
    allowed = approvals(session)
    return {job_id: hit for job_id, hit in found.items() if job_id not in allowed}


def held_ids(db):
    with db.session() as s:
        return set(held(s))


def approve(session, job_id, by="owner"):
    """Record the owner's permission for one job. Raises ValueError for a job that is not at a reserved employer."""
    job = session.get(Job, job_id)
    if job is None:
        raise ValueError("Job not found")
    found = match(session, job)
    if found is None:
        raise ValueError("This job is not at a reserved employer")
    value = {"at": now(), "by": by, **found.as_dict()}
    row = session.get(Setting, APPROVAL + job_id)
    if row is None:
        session.add(Setting(key=APPROVAL + job_id, value=value))
    else:
        row.value = value
    return found


def revoke(session, job_id):
    """Withdraw a permission: the job is held again. A queued run is stopped before it starts, and a run already filling
    the form is refused at its final submit (service.execution)."""
    row = session.get(Setting, APPROVAL + job_id)
    if row is None:
        return False
    session.delete(row)
    return True
