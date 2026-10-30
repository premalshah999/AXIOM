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

from .answer_context import SPONSOR_KEYS, boolean
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


def policy_findings(text, profile, source="posting"):
    sponsorship, sponsor_ids = confirmed_sponsorship(profile.facts)
    status, status_ids = confirmed_status(profile.facts)
    # Paragraph/sentence boundaries avoid attaching an EEO statement to a restriction.
    for raw in re.split(r"(?<=[.!?])\s+|[\r\n]+", text or ""):
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
                for text in re.split(r"(?<=[.!?])\s+", soup.get_text(" ", strip=True)):
                    if SPONSOR_EXCLUSION.search(_normalized(text)) or INELIGIBLE.search(_normalized(text)):
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
            "An explicit hiring restriction conflicts with confirmed applicant facts",
            code="eligibility_conflict",
        )
    return check
