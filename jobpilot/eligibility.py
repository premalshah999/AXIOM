"""Deterministic public hiring restrictions against saved, explicit applicant facts.

Page text is evidence of an employer policy, never evidence of a personal fact.
No citizenship, visa date, resume or model output can establish sponsorship need.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from sqlalchemy import select

from .answer_context import SPONSOR_KEYS, boolean, us_person
from .db import Run


STATUS_KEYS = {"visa_status", "current_immigration_status_us", "current_visa_status_us"}
VISA = re.compile(r"\bf[ -]?1\b|\b(?:stem[ -]?)?opt\b|\bcpt\b|\bh[ -]?1b\b", re.I)
INELIGIBLE = re.compile(
    r"\b(?:not eligible|ineligible|not accepted|not permitted|excluded)\b|"
    r"\b(?:cannot|will not|do not|does not)\s+(?:hire|consider|accept)\b",
    re.I,
)
SPONSOR_EXCLUSION = re.compile(
    r"\bno\s+(?:(?:visa|immigration|employment|employer|company|work)\s+)?sponsorship\b|"
    r"\bsponsorship\s+(?:(?:is|will be)\s+)?(?:not (?:available|provided|offered)|unavailable)\b|"
    r"\bsponsorship\s+will\s+not\s+be\s+(?:available|provided|offered)\b|"
    r"\b(?:cannot|unable to|not able to|will not|do not|does not)\s+(?:provide\s+)?sponsor(?:ship)?\b|"
    r"\bmust\s+(?:not|never)\s+(?:require|need)\s+(?:\w+\s+){0,3}sponsorship\b|"
    r"\bwithout\s+(?:(?:requiring|needing)\s+)?(?:\w+\s+){0,3}sponsorship\b",
    re.I,
)


# "U.S. citizenship is required", "U.S. Citizenship required", "Must be a U.S. citizen", "U.S. citizens only". A posting
# that closes the job to anyone who is not a citizen is the employer's restriction, whatever the applicant's fit
# (two link-only jobs said so and the engine spent days on password resets for them, 2026-10-30).
US = r"(?:u\.?\s?s\.?a?|united states(?: of america)?)"
CITIZENSHIP_REQUIRED = re.compile(
    rf"\b{US}\s+citizenship\s+(?:is\s+|are\s+)?(?:required|mandatory|needed|necessary)\b|"
    rf"\bcitizenship\s+(?:is\s+|are\s+)?(?:required|mandatory)\b|"
    rf"\b(?:must|need to|have to|should)\s+(?:be|hold|have)\s+(?:an?\s+|the\s+)?(?:current\s+|valid\s+)?{US}\s+citizen(?:ship)?\b|"
    rf"\bonly\s+{US}\s+citizens\b|\b{US}\s+citizens?\s+(?:only|required)\b|"
    rf"\brequires?\s+{US}\s+citizenship\b",
    re.I,
)
CITIZENSHIP_NEGATED = re.compile(r"\b(?:no|not|without|never|neither|nor)\b.{0,40}\bcitizen", re.I)
# Another way in: permanent residents and the like keep the restriction open to a U.S. person; work authorization or
# a visa opens it to the applicant, which the facts here cannot decide.
CITIZEN_OR_RESIDENT = re.compile(
    r"\bor\s+(?:an?\s+)?(?:lawful\s+|legal\s+)?(?:permanent\s+resident|green\s*card|national|lpr|asylee|refugee|protected individual)|"
    r"\b(?:permanent\s+resident|green\s*card)s?\b",
    re.I,
)
CITIZEN_OR_AUTHORIZED = re.compile(r"\b(?:authori[sz]ed|eligible)\s+to\s+work|work\s+(?:authori[sz]ation|permit)|\bvisa\b|\bsponsor", re.I)
# "must be able to obtain and maintain a Secret level DoD security clearance", "Must obtain and maintain a U.S.
# Government Security Clearance", "active TS/SCI clearance required".
CLEARANCE_WORD = re.compile(
    r"\bsecurity\s+clearance\b|\b(?:secret|top[- ]secret|ts/sci|dod|public\s+trust)\s+clearance\b|\bclearance\s+(?:level|eligibility)\b",
    re.I,
)
CLEARANCE_CUE = re.compile(
    r"\b(?:must|required?|requires?|requirements?|obtain|maintain|active|currently|eligib\w+|ability to|able to|need|hold|possess)\b",
    re.I,
)
CLEARANCE_OPTIONAL = re.compile(
    r"\b(?:prefer\w*|plus|bonus|nice[- ]to[- ]have|desirable|ideal\w*|helpful|beneficial|advantag\w+|an asset|"
    r"not\s+(?:required|necessary|needed)|no\s+(?:security\s+)?clearance)\b",
    re.I,
)
# A clearance the applicant must already hold; "obtain"/"eligible" ones are decided by citizenship (DoD grants Secret
# and above to citizens only).
CLEARANCE_HELD = re.compile(r"\b(?:active|current(?:ly)?|existing|already|presently)\b|\b(?:must|should)\s+(?:hold|have|possess)\b", re.I)
CLEARANCE_KEYS = ("has_security_clearance", "active_security_clearance", "security_clearance")


@dataclass(frozen=True)
class PolicyFinding:
    subject: str
    verdict: str
    quote: str
    fact_ids: tuple[str, ...]
    source: str = "posting"


@dataclass(frozen=True)
class EligibilityCheck:
    findings: tuple[PolicyFinding, ...]

    @property
    def conflicts(self):
        return tuple(f for f in self.findings if f.verdict == "unmet")

    @property
    def uncertainties(self):
        return tuple(f for f in self.findings if f.verdict == "unknown")


def _us_words(text):
    """The periods of 'U.S.' and 'U.S.A.' as one-dot leaders, which do not end a sentence and which _normalized turns
    back into periods, so the quote keeps the employer's own spelling."""
    return re.sub(r"\b[Uu]\.\s?[Ss]\.(?:\s?[Aa]\.)?", lambda m: m.group(0).replace(".", "\u2024"), text or "")


def _normalized(text):
    return " ".join(
        unicodedata.normalize("NFKC", text)
        .translate(str.maketrans({"‑": "-", "–": "-", "—": "-", "’": "'"}))
        .split()
    )


def _visa_tokens(text):
    return frozenset(
        re.sub(r"[ -]", "", m.group().casefold()).replace("stemopt", "opt") for m in VISA.finditer(text)
    )


def confirmed_sponsorship(facts):
    """An unresolved/contradictory explicit alias makes this proposition unknown."""
    candidates = []
    for key, raw in facts.items():
        if key.casefold() in SPONSOR_KEYS:
            value = boolean(raw)
            # Accept explicit applicant confirmation, not arbitrary leading No prose.
            if value is None and isinstance(raw, str):
                match = re.fullmatch(
                    r"(yes|no)[,.]?\s*\((?:applicant confirmed|confirmed by applicant)\)", raw.strip(), re.I
                )
                value = match.group(1).casefold() == "yes" if match else None
            candidates.append((key, value))
    values = {v for _, v in candidates}
    if len(values) != 1 or None in values:
        return None, ()
    return next(iter(values)), tuple("fact:" + k for k, _ in candidates)


def confirmed_status(facts):
    candidates = []
    for key, raw in facts.items():
        if key.casefold() not in STATUS_KEYS:
            continue
        text = _normalized(str(raw))
        if re.search(
            r"\b(?:previous(?:ly)?|formerly|expired|may|maybe|pending|unknown|not|no longer)\b", text, re.I
        ):
            candidates.append((key, None))
        else:
            tokens = _visa_tokens(text)
            known_other = re.fullmatch(
                r"(?:u\.?s\.? citizen|citizen|permanent resident|green card)", text, re.I
            )
            candidates.append((key, tokens if tokens or known_other else None))
    values = {v for _, v in candidates}
    if len(values) != 1 or None in values:
        return None, ()
    return next(iter(values)), tuple("fact:" + k for k, _ in candidates)


def fact_boolean(facts, names):
    """(key, boolean) of the first explicit boolean fact among `names`, else (None, None)."""
    for key, raw in facts.items():
        if key.casefold().removeprefix("fact:") in names and (value := boolean(raw)) is not None:
            return key, value
    return None, None


def citizenship_finding(quote, facts, source):
    """Whether the applicant meets a citizenship requirement: unmet on an explicit non-citizen fact, met on a citizen
    one, unknown when the facts are silent or the posting opens the door to work-authorized applicants too."""
    exception = bool(re.search(r"\b(?:unless|except|case.by.case)\b", quote, re.I))
    key, citizen = fact_boolean(facts, ("us_citizen",))
    ids = ("fact:" + key,) if key else ()
    if exception or CITIZEN_OR_AUTHORIZED.search(quote):
        verdict = "unknown"
    elif CITIZEN_OR_RESIDENT.search(quote):
        person = us_person(facts)
        verdict, ids = ("unknown", ids) if person is None else ("met" if person[0] else "unmet", tuple(person[1]))
    else:
        verdict = "unknown" if citizen is None else "met" if citizen else "unmet"
    return PolicyFinding("citizenship", verdict, quote, ids, source)


def clearance_finding(quote, facts, source):
    """A security clearance requirement against the applicant's citizenship (to obtain one) or clearance facts."""
    held = bool(CLEARANCE_HELD.search(quote))
    key, citizen = fact_boolean(facts, ("us_citizen",))
    clearance_key, cleared = fact_boolean(facts, CLEARANCE_KEYS)
    if held and cleared is not None and citizen is not False:
        verdict, ids = ("met" if cleared else "unmet"), ("fact:" + clearance_key,)
    elif citizen is not None:
        verdict, ids = ("met" if citizen else "unmet"), ("fact:" + key,)
    else:
        verdict, ids = "unknown", ()
    return PolicyFinding("security_clearance", verdict, quote, ids, source)


def policy_findings(text, profile, source="posting"):
    sponsorship, sponsor_ids = confirmed_sponsorship(profile.facts)
    status, status_ids = confirmed_status(profile.facts)
    # Paragraph/sentence boundaries avoid attaching an EEO statement to a restriction. "U.S." is one word: its periods
    # ended the sentence of "U.S. citizenship is required".
    for raw in re.split(r"(?<=[.!?])\s+|[\r\n]+", _us_words(text)):
        quote = _normalized(raw)
        if not quote or len(quote) > 2000:
            continue
        if re.search(
            r"\b(?:equal opportunity|without regard|non.?discrimination|discriminat|not ineligible)\b",
            quote,
            re.I,
        ):
            continue
        barred = bool(INELIGIBLE.search(quote))
        exception = bool(re.search(r"\b(?:unless|except|case.by.case)\b", quote, re.I))
        sponsor = bool(SPONSOR_EXCLUSION.search(quote)) or bool(
            barred and re.search(r"\b(?:need(?:ing)?|requir(?:e|ing))\b.{0,60}\bsponsorship\b", quote, re.I)
        )
        if sponsor:
            yield PolicyFinding(
                "sponsorship",
                "unknown" if sponsorship is None or exception else "unmet" if sponsorship else "met",
                quote,
                sponsor_ids,
                source,
            )
        if CITIZENSHIP_REQUIRED.search(quote) and not CITIZENSHIP_NEGATED.search(quote):
            yield citizenship_finding(quote, profile.facts, source)
        if CLEARANCE_WORD.search(quote) and CLEARANCE_CUE.search(quote) and not CLEARANCE_OPTIONAL.search(quote):
            yield clearance_finding(quote, profile.facts, source)
        visas = _visa_tokens(quote)
        if visas and barred:
            # Explicit visa exclusions stand independently of the sponsorship answer.
            yield PolicyFinding(
                "visa_status",
                "unknown" if status is None or exception else "unmet" if status & visas else "met",
                quote,
                status_ids,
                source,
            )


def degree_findings(job, profile, source="posting"):
    """A required PhD against the profile's recorded degrees (features/discovery/degree.py): unmet when education is
    recorded without a doctorate, unknown when none is recorded. Two postings titled "... (PhD)" were applied to
    repeatedly for a Master's applicant (2026-10-29/30)."""
    from .features.discovery.degree import doctorate_requirement, holds_doctorate

    quote = doctorate_requirement(job)
    if not quote:
        return
    education = list(getattr(profile, "education", None) or [])
    verdict = "unknown" if not education else "met" if holds_doctorate(education) else "unmet"
    yield PolicyFinding(
        "degree", verdict, _normalized(quote), tuple(f"education:{i}" for i in range(len(education))), source
    )


def check_eligibility(posting, profile, form_evidence=(), title=""):
    findings = list(policy_findings(posting, profile))
    findings.extend(degree_findings({"title": title, "description": posting}, profile))
    for source, text in form_evidence:
        findings.extend(policy_findings(text, profile, source))
    return EligibilityCheck(tuple(dict.fromkeys(findings)))


def _origin(url):
    parsed = urlsplit(url)
    return parsed.scheme.lower(), parsed.hostname, parsed.port


def observed_form_evidence(session, job, data_dir):
    """Read only owned trace artifacts, never run.reason/packet/model text.

    A DB run must belong to this exact job, paired browser metadata must identify a
    form step on its application origin, and paths must stay within the run folder.
    Returned content is policy sentences only; filled fields/private HTML are discarded.
    """
    if data_dir is None:
        return []
    evidence = []
    root = Path(data_dir) / "runs"
    for run in session.scalars(select(Run).where(Run.job_id == job.id)):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", run.id):
            continue
        folder = root / run.id / "steps"
        if not folder.is_dir() or folder.resolve() != root.resolve() / run.id / "steps":
            continue
        for meta_path in sorted(folder.glob("*-form.json")):
            html_path = meta_path.with_suffix(".html")
            if (
                meta_path.is_symlink()
                or html_path.is_symlink()
                or not html_path.is_file()
                or meta_path.stat().st_size > 1_000_000
                or html_path.stat().st_size > 3_000_000
            ):
                continue
            try:
                meta = json.loads(meta_path.read_text())
                if meta.get("step") != "form" or _origin(meta.get("url", "")) != _origin(job.url):
                    continue
                soup = BeautifulSoup(html_path.read_text(), "html.parser")
                for node in soup.select(
                    "script,style,input,textarea,select,option,[hidden],[aria-hidden='true']"
                ):
                    node.decompose()
                # Oracle policy spans live inside custom form-element-label elements.
                # Scan sentences across inline markup rather than requiring a standard block.
                for text in re.split(r"(?<=[.!?])\s+", _us_words(soup.get_text(" ", strip=True))):
                    if (
                        SPONSOR_EXCLUSION.search(_normalized(text))
                        or INELIGIBLE.search(_normalized(text))
                        or CITIZENSHIP_REQUIRED.search(_normalized(text))
                        or CLEARANCE_WORD.search(_normalized(text))
                    ):
                        evidence.append((f"run:{run.id}:step:{meta_path.stem}", text))
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    return list(dict.fromkeys(evidence))


def eligibility_for_job(session, job, profile, data_dir=None):
    return check_eligibility(
        job.description, profile, observed_form_evidence(session, job, data_dir), title=job.title or ""
    )


def require_eligibility(session, job, profile, data_dir=None):
    """Admission hook: re-evaluate current facts; protection guards run first."""
    from .admission import QueueBlocked

    check = eligibility_for_job(session, job, profile, data_dir)
    if check.conflicts:
        raise QueueBlocked(
            "An explicit hiring restriction conflicts with confirmed applicant facts: "
            + "; ".join(f'{f.subject}: "{f.quote[:240]}"' for f in check.conflicts[:2]),
            code="eligibility_conflict",
        )
    return check
