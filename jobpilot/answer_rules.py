"""Owner-editable fallback chains for choices that do not list the applicant's exact answer.

Saved on the profile as `answer_rules` and edited in Setup › Profile › When options don't match.
The defaults reproduce the behavior before these rules existed, so nothing changes until the owner
edits them. Every chain is deterministic except "pick3", which only narrows what the answer model
may choose; its answer is still validated against the field's real options.
"""

import difflib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EducationStep = Literal["closest", "pick3", "other", "ask"]
DemographicFallback = Literal["closest", "other", "ask"]

# Self-identification topics with their own rule; any other identity question uses "other".
TOPICS = (
    "gender",
    "race",
    "hispanic",
    "veteran",
    "disability",
    # Owner 2026-10-18 gave answers for these too; before, they all fell under "other" or "gender".
    "sexual_orientation",
    "gender_identity",
    "transgender",
    "pronouns",
    "other",
)


class DemographicRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decline_first: bool = True  # Choose "prefer not to say" whenever it is offered.
    answer: str = ""  # The applicant's own answer; empty uses answers saved in Knowledge.
    when_missing: DemographicFallback = "ask"  # The answer is not among the options.


class AnswerRules(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Degree, major and school when the recorded value is not offered.
    education: list[EducationStep] = Field(default_factory=lambda: ["closest", "pick3", "other", "ask"])
    demographics: dict[str, DemographicRule] = Field(default_factory=dict)
    guest_apply_first: bool = True  # "Apply without an account" before sign-in or sign-up.
    open_agreement_links: bool = True  # Open documents an agreement asks to review before accepting.


def rules_for(profile) -> AnswerRules:
    """The profile's saved rules; unsaved topics follow the profile-wide decline switch."""
    raw = getattr(profile, "answer_rules", None)
    if raw is None:
        raw = (getattr(profile, "model_extra", None) or {}).get("answer_rules")
    try:
        rules = AnswerRules.model_validate(raw or {})
    except ValueError:
        rules = AnswerRules()
    decline = bool(getattr(profile, "decline_demographics", True))
    for topic in TOPICS:
        rules.demographics.setdefault(topic, DemographicRule(decline_first=decline))
    return rules


SYNONYMS = {
    "man": "male",
    "men": "male",
    "woman": "female",
    "women": "female",
    "masters": "master",
    "ms": "master",
    "msc": "master",
    "mtech": "master",
    "mba": "master",
    "bachelors": "bachelor",
    "bs": "bachelor",
    "bsc": "bachelor",
    "ba": "bachelor",
    "be": "bachelor",
    "btech": "bachelor",
    "undergraduate": "bachelor",
    "graduate": "master",
    "phd": "doctorate",
    "doctoral": "doctorate",
    "doctor": "doctorate",
    "univ": "university",
    "yes": "yes",
}
STOP = {"of", "the", "and", "in", "a", "an", "at", "for", "degree", "i", "am", "my", "is", "do", "have"}
NEGATION = re.compile(r"\b(?:not|no|non|none|never|without|don't|dont|do not)\b", re.I)
OTHER = re.compile(
    r"^\s*(?:other|others|not listed|my (?:school|university|college|degree|major) is not listed|"
    r"(?:school|university|college|degree|major|field) not listed|prefer to self.describe|self.describe|"
    r"other \(please specify\)|other - please specify|other: please specify)\s*$",
    re.I,
)


def tokens(text):
    words = re.findall(r"[a-z0-9]+", str(text).casefold().replace("'", ""))
    return {SYNONYMS.get(w, w) for w in words if w not in STOP}


def similarity(value, option):
    """0..1 closeness of an offered option to the applicant's value. A negated statement never
    matches an affirmative one ("I am not a protected veteran" vs "Protected veteran")."""
    a, b = tokens(value), tokens(option)
    if not a or not b:
        return 0.0
    if bool(NEGATION.search(str(value))) != bool(NEGATION.search(str(option))):
        return 0.0
    shared = len(a & b)
    jaccard = shared / len(a | b)
    contained = shared / min(len(a), len(b))
    ratio = difflib.SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio()
    return max(jaccard, 0.85 * contained, 0.8 * ratio)


def ranked(value, options):
    from .widgets import placeholder_option

    usable = [o for o in options if str(o).strip() and not placeholder_option(o) and not OTHER.search(str(o))]
    return sorted(((similarity(value, o), o) for o in usable), key=lambda x: -x[0])


def closest_option(value, options, *, floor=0.55, margin=0.08):
    """The single clearly closest option, else None (absent or ambiguous)."""
    ranking = ranked(value, options)
    if not ranking or ranking[0][0] < floor:
        return None
    if len(ranking) > 1 and ranking[0][0] - ranking[1][0] < margin:
        return None
    return ranking[0][1]


def top_options(value, options, count=3, floor=0.2):
    return [o for score, o in ranked(value, options)[:count] if score >= floor]


def other_option(options):
    found = [o for o in options if OTHER.search(str(o))]
    return found[0] if len(found) == 1 else None
