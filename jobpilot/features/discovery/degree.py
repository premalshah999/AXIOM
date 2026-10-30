"""A posting that requires a doctorate, compared with the applicant's recorded degrees.

"Software Engineer Graduate (AI Compute) - 2027 Start (PhD)" says in its minimum qualifications that candidates are
"completing or have recently completed a PhD"; an applicant whose highest degree is a Master's applied to two such
postings again and again (2026-10-29/30). A PhD is required only when the title or the posting's own requirement wording
says so, never when it is preferred, a plus, or one choice among degree levels ("Bachelor's, Master's or PhD").

Conservative: no decision when the profile records no education at all (nothing to compare), and no match when the
sentence offers another way in.
"""

import re

DOCTORATE = re.compile(r"\b(?:ph\.?\s?d|doctor of philosophy|doctorate|doctoral (?:degree|program))\b", re.I)
# A postdoctoral title always means a doctorate; "postdoctoral experience" in a description does not.
POSTDOC = re.compile(r"\bpost[- ]?doc(?:toral)?\b", re.I)
# Degree levels and wording that make a doctorate one option among several, or merely welcome.
ALTERNATIVE = re.compile(
    r"\b(?:bachelor'?s?|master'?s?|associate'?s?|undergraduate|equivalent|or higher|and/or|"
    r"prefer(?:red|ably|ence)?|plus|also|consider\w*|bonus|nice[- ]to[- ]have|desirable|desired|advantag\w+|beneficial|ideal(?:ly)?|"
    r"helpful|an asset|optional|welcome|encouraged|may|might|no|not|without|neither|nor)\b",
    re.I,
)
ABBREVIATION = re.compile(r"(?<![A-Za-z.])(?:B\.?S\.?c?|M\.?S\.?c?|B\.?A\.?|M\.?A\.?|MBA|B\.?Tech|M\.?Tech)(?![A-Za-z])")
# "..., or PhD in a relevant field", "(LPC, LCSW, PhD, or PsyD)": the doctorate follows a conjunction or a list comma.
JOINED = re.compile(r"(?:\b(?:or|and)\b|[,/])\s*(?:an?\s+)?(?:advanced\s+)?$", re.I)
# "2+ years with a PhD" shortens an experience requirement; "PhD/PsyD" and "PhD, or ..." list choices.
YEARS_WITH = re.compile(r"\byears?\b[^,;]{0,25}\bwith\s+(?:an?\s+)?(?:advanced\s+)?(?:ph\.?\s?d|doctor)", re.I)
OR_EXPERIENCE = re.compile(r"\bor\b[^.]{0,30}\b(?:recent graduate|early[- ]career|professional|industry|experience)", re.I)
LISTED_AFTER = re.compile(r"^\s*(?:/|,\s*(?:or|and)\b)")
# A requirement list that offers lower degrees as other ways in ("Bachelor's and 5 years, Master's and 3 years,
# Doctorate and 0 years"): the doctorate line is one option, not the requirement.
LOWER_DEGREE = re.compile(r"\b(?:bachelor'?s?|master'?s?|associate'?s?|undergraduate)\b", re.I)
LEVEL = re.compile(r"(?:ph\.?\s?d|doctoral)[- ]level", re.I)
REQUIRED = re.compile(
    r"\b(?:must|require[sd]?|requirements?|minimum|completing|completed|currently (?:pursuing|enrolled|completing)|"
    r"pursuing|enrolled|candidates?|applicants?|you (?:have|hold|are|will have|possess)|hold(?:s|ing)?|possess(?:es)?|"
    r"obtained|earned|in progress|expected|graduating)\b",
    re.I,
)
# The section a mention sits in decides it when its own sentence has no requirement wording.
MINIMUM_HEADING = re.compile(
    r"(?:minimum|basic|required|essential|mandatory)\s+(?:qualifications|requirements|skills)|requirements|"
    r"what you(?:'ll| will)? need|you(?:'ll| will)? need|must[- ]haves?",
    re.I,
)
OTHER_HEADING = re.compile(
    r"(?:preferred|desired|additional|bonus)\s+(?:qualifications|requirements|skills)|nice[- ]to[- ]haves?|"
    r"bonus points|responsibilities|what you(?:'ll| will)? do|about (?:us|the (?:team|role|company|job))|"
    r"benefits|perks|compensation|equal opportunity|who we are",
    re.I,
)
HEADING_REACH = 1500
SENTENCE = re.compile(r"[^.!?:\n•;]*[^.!?:\n•;]")


def _text(job: dict, key: str) -> str:
    """The field with its line breaks kept: a requirement list's bullets end a sentence."""
    return re.sub(r"[ \t\r\f\v\u00a0]+", " ", re.sub(r"\n\s*\n+", "\n", str(job.get(key) or ""))).strip()


def _normal(text: str) -> str:
    """'Ph.D.' and 'Ph D' as one word, so its periods do not end a sentence; curly apostrophes as plain ones."""
    text = re.sub(r"\bPh\.?\s?D\b\.?", "PhD", text.replace("\u2019", "'"), flags=re.I)
    return re.sub(r"\b([BM])\.\s?([SA])\b\.?", r"\1\2", text)


def _alternative(sentence: str) -> bool:
    return bool(ALTERNATIVE.search(sentence) or ABBREVIATION.search(sentence) or LEVEL.search(sentence))


def doctorate_title(title: str) -> str:
    """The title when it names a doctorate as the posting's level ("... 2027 Start (PhD)"), else ''."""
    title = _normal(title or "")
    if (DOCTORATE.search(title) or POSTDOC.search(title)) and not _alternative(title):
        return title.strip()
    return ""


def doctorate_requirement(job: dict) -> str:
    """The employer's own words requiring a doctorate (its title, else the sentence of its requirements), or ''."""
    if title := doctorate_title(str(job.get("title") or "")):
        return title[:200]
    text = _normal(_text(job, "description"))
    for mention in DOCTORATE.finditer(text):
        start, end = mention.span()
        low, high = max(0, start - 200), min(len(text), end + 200)  # a flattened page has no sentence ends
        found = next((m for m in SENTENCE.finditer(text, low, high) if m.start() <= start < m.end()), None)
        if not found:
            continue
        # A page flattened to one line runs the next heading into the sentence ("... a PhD Preferred qualifications").
        headings = (*MINIMUM_HEADING.finditer(text, end, found.end()), *OTHER_HEADING.finditer(text, end, found.end()))
        cut = min((h.start() for h in headings), default=found.end())
        sentence = text[found.start() : cut].strip()
        before = text[max(0, start - HEADING_REACH) : start]
        marks = [(m.end(), "min") for m in MINIMUM_HEADING.finditer(before)] + [
            (m.end(), "other") for m in OTHER_HEADING.finditer(before)
        ]
        section = max(marks)[1] if marks else ""
        if (
            not sentence
            or section == "other"
            or _alternative(sentence)
            or YEARS_WITH.search(sentence)
            or OR_EXPERIENCE.search(sentence)
            or JOINED.search(text[max(0, start - 25) : start])
            or LISTED_AFTER.search(text[end : end + 12])
        ):
            continue
        if _lower_degrees_around(text, found.start(), cut):
            continue
        if section == "min" or REQUIRED.search(sentence):
            return sentence[:300]
    return ""


def _lower_degrees_around(text: str, start: int, end: int) -> bool:
    """Bachelor's or Master's wording in the requirement block just before or after the sentence."""
    block = text[max(0, start - 300) : start]
    last = max((m.end() for m in (*MINIMUM_HEADING.finditer(block), *OTHER_HEADING.finditer(block))), default=0)
    after = text[end : end + 250]
    first = min((m.start() for m in (*MINIMUM_HEADING.finditer(after), *OTHER_HEADING.finditer(after))), default=len(after))
    around = block[last:] + " " + after[:first]
    return bool(LOWER_DEGREE.search(around) or ABBREVIATION.search(around))


def holds_doctorate(education) -> bool:
    """A recorded doctorate, finished or in progress."""
    for row in education or []:
        degree = row.get("degree") if isinstance(row, dict) else getattr(row, "degree", "")
        if DOCTORATE.search(str(degree or "")):
            return True
    return False


def doctorate_gate(job: dict, profile: dict) -> str:
    """'' unless the posting requires a doctorate and the profile records education without one."""
    education = profile.get("education") if isinstance(profile, dict) else getattr(profile, "education", None)
    if not education or holds_doctorate(education):
        return ""
    quote = doctorate_requirement(job)
    return f"doctorate_required: posting requires a PhD ({quote})" if quote else ""
