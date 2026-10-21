"""Answer patterns: demographics."""

import re
from .consent import DATA_CONSENT


DEMOGRAPHIC = re.compile(
    r"\bgender\b|^sex$|\byour sex\b|\bsex:?$|ethnic|\brace\b|racial|veteran|disabilit|sexual orientation|hispanic|latino|pronoun|transgender|lgbt|"
    r"first.generation (?:professional|college|student)|socio.?economic|"
    # Instacart: "Do you identify as a Person of Colour?" with "I don't wish to answer".
    r"person of colou?r|people of colou?r|\bbipoc\b|visible minorit|indigenous|aboriginal|first nations|neurodivergen",
    re.IGNORECASE,
)


DECLINE = re.compile(
    # Bunge (SuccessFactors) 2026-10-16 spells it "I dont wish to answer".
    r"decline|undisclosed|^undeclared$|prefer not|do not (?:wish|want)|don.?t (?:wish|want)|choose not|not (?:to )?(?:disclose|answer|self.identify)|"
    r"rather not|wish not|not (?:declared|declaring|disclosed|specified|provided)|no declaration|"
    # Cone Health offers "Withhold"; others "Opt out" or "Prefer to not say".
    r"\bwithh[eo]ld|\bopt(?:ing)?.?out\b|prefer to not|not (?:to )?(?:say|state)\b",
    re.IGNORECASE,
)


# Topic of a voluntary self-identification question, used to reuse only an
# answer the applicant confirmed for the same topic.
DEMOGRAPHIC_TOPICS = {
    "hispanic": re.compile(r"hispanic|latin[oa]", re.I),
    # Before "gender": "What is your gender identity?" is not the sex question.
    "sexual_orientation": re.compile(r"sexual orientation|\bsexuality\b|\borientation\b", re.I),
    "transgender": re.compile(r"transgender|identify as trans\b|\btrans (?:man|woman|person)\b", re.I),
    "gender_identity": re.compile(r"gender identity|gender expression|cisgender", re.I),
    "pronouns": re.compile(r"pronoun", re.I),
    "gender": re.compile(r"\bgender\b|^sex$|\byour sex\b|\bsex:?$", re.I),
    "race": re.compile(r"\brace\b|racial|ethnicit", re.I),
    "veteran": re.compile(r"veteran", re.I),
    "disability": re.compile(r"disab", re.I),
}


FACT_ANNOTATION = re.compile(r"\s*\((?:applicant confirmed|confirmed by (?:the )?applicant)[^)]*\)\.?", re.I)


def strip_fact_annotations(value):
    """Internal provenance notes ("(applicant confirmed)") never reach an employer's form (OmegaHires 2026-10-14)."""
    return FACT_ANNOTATION.sub("", value).strip() if isinstance(value, str) else value


# An option that asserts the status before declining its details ("I am a protected veteran, but I choose not to
# self-identify the classifications to which I belong") is an answer, not a decline.
ASSERTED_STATUS = re.compile(r"^\s*(?:I am|I'm|I identify as)\b[^,]{0,60},?\s*but\b", re.I)


def declines(option):
    return bool(DECLINE.search(str(option))) and not ASSERTED_STATUS.search(str(option))


def demographic_topic(text):
    if DATA_CONSENT.search(text or ""):
        return None
    return next((name for name, rx in DEMOGRAPHIC_TOPICS.items() if rx.search(text or "")), None)
