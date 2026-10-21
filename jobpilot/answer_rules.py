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
    # A major to choose when a form's list lacks the recorded one (for example "Computer Science"). Empty: none.
    major_fallback: str = Field(default="", max_length=100)
    # "How did you hear about us" when the profile's own source is not offered: channels in order of preference
    # (linkedin, company website, ziprecruiter, indeed, internet search, job board, glassdoor, social media, or any
    # other name, matched as a word). Empty: none is chosen and the applicant is asked.
    source_order: list[str] = Field(default_factory=list, max_length=20)
    # What the applicant states about themselves where an application asks a preference. Empty: nothing is claimed
    # and a question that needs one goes to the applicant. Used only when the profile applies autonomously.
    salary_stance: str = Field(default="", max_length=60)  # desired pay, free text (for example "Negotiable")
    travel_willingness: str = Field(default="", max_length=100)  # for example "open to travel"
    # Travel the applicant states: "role" chooses an offered percentage consistent with the role's stated travel;
    # "maximum" chooses the highest offered amount or frequency.
    travel_choice: Literal["role", "maximum"] = "role"
    # A month-only employment or education date on a form that needs a day: "" asks; "first" uses the first day of
    # the recorded month for every employer and school.
    history_day: Literal["", "first"] = ""
    # Each employer's country, in the profile's work order (for example ["United States", "India"]). Empty: a row's
    # country comes from its location.
    employment_countries: list[str] = Field(default_factory=list, max_length=30)
    employment_type_sought: str = Field(default="", max_length=100)  # for example "full-time early-career employment"
    # Opt in to talent communities and "keep my data on file for other roles" asks. False (the default) declines.
    join_talent_communities: bool = False
    source_any_channel: bool = False  # After the list, accept any offered channel that claims no relationship.
    demographics: dict[str, DemographicRule] = Field(default_factory=dict)
    guest_apply_first: bool = True  # "Apply without an account" before sign-in or sign-up.
    open_agreement_links: bool = True  # Open documents an agreement asks to review before accepting.


def major_patterns(name) -> tuple:
    """Patterns for an offered major that is, or contains, the fallback major; none when no fallback is set."""
    name = " ".join(str(name or "").split())
    if not name:
        return ()
    return (re.compile(rf"^{re.escape(name)}$", re.I), re.compile(rf"\b{re.escape(name)}\b", re.I))


def delegated_facts(rules, autonomous, work=()) -> dict:
    """The delegated-preference facts a model answer may cite; one per stated preference, none for a blank one."""
    facts = {}
    if rules.history_day == "first":
        facts["fact:history_date_day_policy"] = (
            "Employment and education dates are recorded to the month. Where a form requires a day, the applicant "
            "states the first day of that month (2025-07 is 07/01/2025), for every employer and school."
        )
    countries = [c.strip() for c in rules.employment_countries]
    if any(countries):
        facts["fact:employment_countries"] = "; ".join(
            f"{getattr(entry, 'company', '') or f'Employer {i + 1}'}: {country}"
            for i, (entry, country) in enumerate(zip(work, countries))
            if country
        ) or "; ".join(c for c in countries if c)
    if not autonomous:
        return facts
    if travel := rules.travel_willingness.strip():
        facts["fact:travel_percentage_choice_policy"] = (
            (
                f"Applicant is {travel} and states the maximum: for a travel percentage or frequency question, choose "
                "the highest offered amount (all of the time). This is willingness, not prior travel history."
            )
            if rules.travel_choice == "maximum"
            else (
                f"Applicant is {travel} and delegates willingness preferences. For a travel percentage question, "
                "choose an offered percentage consistent with the role, using its stated travel requirement where "
                "available. This is willingness, not prior travel history."
            )
        )
    if salary := rules.salary_stance.strip():
        facts["fact:compensation_choice_policy"] = (
            f"The applicant delegates desired compensation preferences to the agent, stating: {salary}. "
            f"Prefer {salary.lower()} when offered; otherwise choose a reasonable offered range "
            "using the role's posted pay range and career level. This does not establish "
            "current or past salary, which must never be invented."
        )
    if kind := rules.employment_type_sought.strip():
        facts["fact:application_role_type_policy"] = (
            f"The applicant is pursuing {kind}. Infer this application's "
            "full-time versus internship status from the job title and description. "
            "Applying for a full-time role is distinct from currently being employed full-time."
        )
    return facts


SOURCE_CHANNELS = {
    "linkedin": r"\blinked\s?in\b",
    "company website": r"company(?:'s)? (?:web ?site|careers?)|careers? (?:page|site|web ?site)|"
    r"corporate (?:web ?site|careers?)|(?:our|employer) (?:web ?site|careers?)|career portal",
    "ziprecruiter": r"\bzip\s?recruiter\b",
    "indeed": r"\bindeed\b",
    "internet search": r"internet|online search|web search|search engine|\bgoogle\b|\bonline\b",
    "job board": r"job (?:board|site|posting|search (?:site|engine))s?",
    "glassdoor": r"\bglassdoor\b",
    "social media": r"social (?:media|network)|professional network",
}


def source_patterns(order) -> tuple:
    """Patterns for the profile's source channels, in order; a name outside the known channels matches as a word."""
    patterns = []
    for name in order or ():
        name = " ".join(str(name or "").split()).casefold()
        if name:
            patterns.append(re.compile(SOURCE_CHANNELS.get(name) or rf"\b{re.escape(name)}\b", re.I))
    return tuple(patterns)


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
