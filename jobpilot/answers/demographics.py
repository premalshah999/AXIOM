"""Answer patterns: demographics."""

import re
from .consent import DATA_CONSENT


# What a question names only to set it aside is not what it asks. "Other than your ethnicity, gender, and disability status
# (survey below), is there anything you would like to share with us?" (1Password, Ashby: answered "Male", 29 times) and
# "Please list ... civic activities and offices held: (Exclude those which would reveal sex, race, ... sexual orientation)"
# (Central Hudson, 2026-11-01: answered "Heterosexual or straight"). The clause ends at its sentence's end or where the
# question itself picks up after a comma, so "Regardless of your ethnicity, please select all races" still asks race.
_ASIDE = (
    r"\b(?:other than|aside from|apart from|besides|exclud(?:e|es|ing)\b|except(?: for)?|not includ(?:e|ing)\b|"
    r"(?:do|does|should|must) not (?:include|mention|list|share|disclose|reveal|consider)\b|"
    r"don.?t (?:include|mention|list|share|disclose|reveal|consider)\b|without regard (?:to|for)|regardless of|irrespective of|"
    r"on the basis of|not (?:be )?(?:based )?on (?:the basis of )?(?:race|color|sex|gender|age|religion|disability)\b)"
)
NOT_ASKED = re.compile(
    # A whole parenthesis that sets something aside, or the clause up to the sentence's end or the question picking up.
    r"\([^()]*" + _ASIDE + r"[^()]*\)|" + _ASIDE
    + r".*?(?=,\s*(?:is|are|do|does|did|have|has|will|would|can|what|which|who|how|when|where|please|select|choose|indicate|identify|tell)\b|[.?!]|$)",
    re.IGNORECASE | re.DOTALL,
)


def asked_text(text):
    """A question's words without the clauses that set a subject aside."""
    return NOT_ASKED.sub(" ", str(text or ""))


class _Asked:
    """A pattern matched against what a question asks, not what it mentions in passing."""

    def __init__(self, pattern):
        self.pattern = pattern

    def search(self, text):
        return self.pattern.search(asked_text(text))


DEMOGRAPHIC = _Asked(re.compile(
    # "pronoun" alone also read "How do you pronounce your name?" as a pronouns question (answered "He/him").
    r"\bgender\b|^sex$|\byour sex\b|\bsex:?$|ethnic|\brace\b|racial|veteran|disabilit|sexual orientation|hispanic|latino|spanish culture|\bpronouns?\b|transgender|lgbt|"
    r"first.generation (?:professional|college|student)|socio.?economic|"
    # Instacart: "Do you identify as a Person of Colour?" with "I don't wish to answer".
    r"person of colou?r|people of colou?r|\bbipoc\b|visible minorit|indigenous|aboriginal|first nations|neurodivergen",
    re.IGNORECASE,
))


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
    # A bare definition can be the whole label ("A person of Cuban, Mexican, ... or other Spanish culture or origin").
    "hispanic": re.compile(r"hispanic|latin[oa]|spanish culture", re.I),
    # Before "gender": "What is your gender identity?" is not the sex question.
    "sexual_orientation": re.compile(r"sexual orientation|\bsexuality\b|\borientation\b", re.I),
    "transgender": re.compile(r"transgender|identify as trans\b|\btrans (?:man|woman|person)\b", re.I),
    "gender_identity": re.compile(r"gender identity|gender expression|cisgender", re.I),
    "pronouns": re.compile(r"\bpronouns?\b", re.I),
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
    asked = asked_text(text)
    return next((name for name, rx in DEMOGRAPHIC_TOPICS.items() if rx.search(asked)), None)
