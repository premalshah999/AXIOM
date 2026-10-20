"""Canonical employer identity with explicit aliases.

Resolution order: ATS tenant from the application URL, then an exact normalized
name alias. A name is never matched by substring ("Meta" ≠ "Metabase").
"""

import re
from urllib.parse import urlsplit

from sqlalchemy import select

from .._store import norm
from .models import EmployerIdentity
from .tables import KnAlias, KnEmployer

LEGAL_SUFFIX = re.compile(
    r",?\s+(?:inc\.?|incorporated|llc|l\.l\.c\.|ltd\.?|limited|corp\.?|corporation|co\.?|company|plc|"
    r"gmbh|ag|s\.a\.|group|holdings|technologies|technology)$",
    re.I,
)


def name_aliases(name: str) -> list[str]:
    full = norm(name).strip(" .,")
    if not full:
        return []
    base = full
    while True:
        stripped = LEGAL_SUFFIX.sub("", base).strip(" .,")
        if stripped == base or len(stripped) < 2:
            break
        base = stripped
    return list(dict.fromkeys(a for a in (full, base) if len(a) >= 2))


# Trailing words a question routinely drops when it names the employer ("Texas Capital"
# for Texas Capital Bank, "MGM" for MGM Resorts International).
DESCRIPTOR = re.compile(
    r"\s+(?:bank|bancorp|bancshares|financial|financial services|resorts|international|worldwide|"
    r"global|usa|us|america|north america|enterprises|systems|solutions|services|labs?|"
    r"careers?|jobs|cooperative|co-op|association|partners)$",
    re.I,
)
CONNECTIVES = {"of", "and", "&", "the", "for", "a", "an", "at", "in"}
# Acronyms that are also ordinary words would turn unrelated questions into employer questions.
COMMON_ACRONYMS = {"aim", "act", "art", "can", "are", "all", "any", "one", "use", "new", "who", "how", "per"}


def mention_aliases(names) -> list[str]:
    """Ways a question may name this employer, for recognizing it inside question text only.

    Broader than `name_aliases` and never stored: stored aliases stay exact so that
    resolution cannot merge two employers, while a question on this employer's own
    application may say "Texas Capital", "Fanatics, Inc." or "NISC".
    """
    out: list[str] = []
    for name in [names] if isinstance(names, str) else names:
        out += name_aliases(name)
        branded = re.split(r"\s+[-–—|]\s+", str(name or ""), maxsplit=1)
        if len(branded) == 2 and re.fullmatch(r"[A-Z0-9]{2,8}", branded[0].strip()):
            words = [w for w in re.findall(r"[a-z0-9]+", norm(branded[1])) if w not in CONNECTIVES]
            if words and "".join(w[0] for w in words).upper() == branded[0].strip():
                # An explicit acronym-plus-expanded-name display supplies both
                # observed names, e.g. WGU - Western Governors University.
                out += name_aliases(branded[1])
        # "Perpay - Career's Page", "Acme (US)": the employer is the leading part.
        lead = re.split(r"\s+[-–—|]\s+|\s*\(", str(name or ""))[0]
        for full in name_aliases(lead):
            out.append(full)
            phrases, base = [full], full
            while (cut := DESCRIPTOR.sub("", base).strip(" .,")) != base and len(cut) >= 3:
                if cut.split()[-1] in CONNECTIVES:
                    break  # "Bank of America" is not "Bank of"
                base = cut
                out.append(base)
            if (m := re.fullmatch(r"(?:the )?(.+?) of (?:the )?.+", full)) and len(m[1].split()) >= 2:
                out.append(m[1])
                phrases.append(m[1])
            for phrase in phrases:
                words = [w for w in re.findall(r"[a-z0-9]+", phrase) if w not in CONNECTIVES]
                # Only long names are referred to by acronym ("NISC", "BCBS"); short ones
                # would collide with ordinary abbreviations.
                if len(words) >= 4 and all(w[0].isalpha() for w in words):
                    acronym = "".join(w[0] for w in words)
                    if len(acronym) <= 6 and acronym not in COMMON_ACRONYMS:
                        out.append(acronym)
    return [a for a in dict.fromkeys(out) if len(a) >= 2]


def dayforce_tenant(parts) -> str | None:
    """The client namespace in /en-US/<namespace>/<board>/jobs/<id> (the locale is optional)."""
    if parts and re.fullmatch(r"[a-z]{2}-[a-z]{2}", parts[0], re.I):
        parts = parts[1:]
    return parts[0].casefold() if parts else None


def tenant_alias(url: str) -> str | None:
    """A stable ATS tenant key: the part of the URL that identifies the employer."""
    try:
        p = urlsplit((url or "").strip())
    except ValueError:
        return None
    host = (p.hostname or "").casefold()
    parts = [x for x in p.path.split("/") if x]
    if not host:
        return None
    if host.endswith("myworkdayjobs.com") or host.endswith("myworkdaysite.com"):
        tenant = host.split(".")[0]
        return f"workday:{tenant}"
    if host.endswith("greenhouse.io") and parts:
        return f"greenhouse:{parts[0].casefold()}"
    if host == "jobs.lever.co" and parts:
        return f"lever:{parts[0].casefold()}"
    if host == "jobs.ashbyhq.com" and parts:
        return f"ashby:{parts[0].casefold()}"
    if host.endswith("smartrecruiters.com") and parts:
        return f"smartrecruiters:{parts[0].casefold()}"
    if host.endswith(".icims.com"):
        return f"icims:{host.split('.')[0]}"
    if "oraclecloud.com" in host:
        return f"oracle:{host.split('.')[0]}"
    if host == "jobs.dayforcehcm.com" and (tenant := dayforce_tenant(parts)):
        # Every Dayforce employer shares this host; "domain:dayforcehcm.com" had merged them all
        # into one employer (Sarnova) for caps and employer-scoped answers (2026-10-16).
        return f"dayforce:{tenant}"
    labels = host.removeprefix("www.").split(".")
    domain = ".".join(labels[-2:]) if len(labels) >= 2 else ""
    # Hosts that serve many employers identify no single tenant.
    if not domain or domain in SHARED_DOMAINS or any(host.endswith(h) for h in SHARED_HOST_SUFFIXES):
        return None
    return "domain:" + domain


SHARED_DOMAINS = {
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "simplify.jobs",
    "builtin.com",
    "wellfound.com",
    "google.com",
    "example.com",
    "example.test",
    "monster.com",
    "dice.com",
    "handshake.com",
    "joinhandshake.com",
    "workable.com",
    "bamboohr.com",
    "jobvite.com",
    "breezy.hr",
    "recruitee.com",
    "teamtailor.com",
    "rippling.com",
    "paylocity.com",
    "ultipro.com",
    "adp.com",
    "successfactors.com",
    "successfactors.eu",
    "dayforcehcm.com",
    "taleo.net",
    "eightfold.ai",
    "avature.net",
    "github.io",
}
SHARED_HOST_SUFFIXES = (".greenhouse.io", ".lever.co", ".ashbyhq.com", ".smartrecruiters.com")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm(text)).strip("-")[:60] or "unknown"


def aliases_of(s, employer_id: str) -> tuple[str, ...]:
    return tuple(
        a.alias
        for a in s.scalars(select(KnAlias).where(KnAlias.employer_id == employer_id, KnAlias.kind == "name"))
    )


def add_alias(s, employer_id: str, alias: str, kind: str = "name", evidence: dict | None = None):
    alias = alias if kind == "tenant" else norm(alias)
    row = s.get(KnAlias, alias)
    if row is None:
        s.add(KnAlias(alias=alias, employer_id=employer_id, kind=kind, evidence=evidence or {}))
        s.flush()
    return row.employer_id if row else employer_id


def resolve(s, name: str = "", url: str = "", create: bool = True) -> EmployerIdentity | None:
    tenant = tenant_alias(url) if url else None
    names = name_aliases(name)
    found, how = None, "none"
    if tenant:
        row = s.get(KnAlias, tenant)
        if row:
            found, how = row.employer_id, "tenant"
    if not found:
        for alias in names:
            row = s.get(KnAlias, alias)
            if row and row.kind == "name":
                found, how = row.employer_id, "name"
                break
    if not found:
        if not create or not (names or tenant):
            return None
        base = names[-1] if names else tenant.split(":", 1)[1]
        found, how = "emp:" + slug(base), "created"
        if s.get(KnEmployer, found) is None:
            s.add(KnEmployer(id=found, display=name.strip() or base))
            s.flush()
    # Record what we learned. An alias already owned by another employer is
    # left untouched: explicit evidence wins over a later guess.
    if create:
        for alias in names:
            add_alias(s, found, alias, "name", {"from": "name"})
        if tenant:
            add_alias(s, found, tenant, "tenant", {"url_host": urlsplit(url).hostname})
    display = (s.get(KnEmployer, found).display if s.get(KnEmployer, found) else name) or found
    return EmployerIdentity(id=found, display=display, matched_by=how, aliases=aliases_of(s, found))


def merge(s, keep: str, other: str):
    """Maintainer action: point all of `other`'s aliases and facts at `keep`."""
    from .tables import KnFact

    for alias in s.scalars(select(KnAlias).where(KnAlias.employer_id == other)):
        alias.employer_id = keep
    for fact in s.scalars(select(KnFact).where(KnFact.employer_id == other)):
        fact.employer_id = keep
    row = s.get(KnEmployer, other)
    if row:
        s.delete(row)
