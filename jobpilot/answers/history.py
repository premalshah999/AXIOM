"""Answer patterns: history."""

import re
from .eligibility import US_STATES
from .text import normalize, option_value


WORK = {
    "title": re.compile(r"job title|position|role title|^title$", re.I),
    "company": re.compile(r"company|employer|organi[sz]ation", re.I),
    "location": re.compile(r"location|city", re.I),
    "start": re.compile(r"^from$|start date|start|from date|date from", re.I),
    "end": re.compile(r"^to\b|end date|^end$|to date|date to", re.I),
    # iCIMS asks each row "Still Employed?" (America's Tire 2026-10-27): left blank beside a blank end date, the
    # profile page failed with "Invalid Data Error".
    "current": re.compile(
        r"currently work|current(ly)? (employ|job|position|role)|i currently|still employed|still work(?:ing)? (?:here|there)",
        re.I,
    ),
    "description": re.compile(r"description|responsibilit|summary|duties", re.I),
}


# History rows: contact details the profile never records for an employer or school, and region pickers that only the
# row's own location may answer (Atlassian 2026-10-19).
HISTORY_CONTACT = re.compile(
    r"\b(?:address|street|zip|zip ?code|postal|post ?code|postcode|phone|telephone|fax)\b", re.I
)


HISTORY_REGION = re.compile(r"(state|province|state province|region|country)")
# One box for a history row's whole period: "Start & end date", "Dates of employment", "From - To" (the scanner's
# DATE_RANGE).
HISTORY_PERIOD = re.compile(
    r"start\s*(?:&|and|/|-|–)\s*end dates?|dates? of (?:employment|attendance)|employment dates|(?:time )?period|"
    r"from\s*[-–]\s*to",
    re.I,
)


# A question that classifies a history row's employer or school ("Type of Company", "Company size", "Industry") is not
# its name.
HISTORY_CLASS = re.compile(
    r"\b(?:type|kind|size|sector|industry|category) of (?:the )?(?:company|employer|organi[sz]ation|institution|school)\b|"
    r"\b(?:company|employer|organi[sz]ation|institution|school) (?:type|kind|size|sector|industry|category)\b|^industry$"
)


HISTORY_UNKNOWN = "policy:history-contact-unknown"


def history_region(location, country):
    """The State (or Country) a history row's own location states, else ""."""
    parts = [p.strip() for p in str(location or "").split(",") if p.strip()]
    if len(parts) < 2:
        return ""
    last = parts[-1]
    code = (
        last.upper()
        if last.upper() in US_STATES
        else next((c for c, name in US_STATES.items() if name.casefold() == last.casefold()), "")
    )
    if code:
        return "United States" if country else US_STATES[code]
    if re.fullmatch(r"(?:usa|us|u\.s\.a?\.?|united states(?: of america)?)", last, re.I):
        if country:
            return "United States"
        return US_STATES.get(parts[-2].upper(), parts[-2]) if len(parts) >= 3 else ""
    if country:
        return last
    return parts[-2] if len(parts) >= 3 else ""


EDUCATION = {
    "school": re.compile(r"school|university|institution|college", re.I),
    "degree": re.compile(r"degree|qualification|level of education|education level", re.I),
    "field_of_study": re.compile(r"field of study|major|discipline|area of study|concentration", re.I),
    "gpa": re.compile(r"\bgpa\b|grade", re.I),
    "start": re.compile(r"^from(?: date)?$|start|first year|from year", re.I),
    "end": re.compile(r"^to\b|\bend\b|last year|graduat|to year|completion|expected", re.I),
}


# When a catalog does not offer the recorded degree, choose one of these offered equivalents rather than stopping the
# application. The major's fallback is the profile's own (answer_rules.major_fallback).
DEGREE_FALLBACK = {
    "bachelor": (
        re.compile(r"^bachelor'?s?(?: degree)?$", re.I),
        re.compile(r"^bachelor of (?:science|engineering|technology)\b", re.I),
        re.compile(r"^b\.?\s?(?:s\.?c?|e|tech)\.?(?: degree)?$", re.I),
        re.compile(r"\bbachelor'?s?\b(?!.*\b(?:arts?|fine|music|laws?|education|business|program work|course ?work)\b)", re.I),
        re.compile(r"^undergraduate(?: degree)?$|4.year (?:college )?degree", re.I),
        # One option for the level's common types: "BA/BS", "BS/BA" (EPIC Retirement Plan Services, 2026-10-25).
        re.compile(r"^(?:b\.?\s?a\.?\s*/\s*)?b\.?\s?s\.?c?\.?(?:\s*/\s*b\.?\s?a\.?)?$", re.I),
    ),
    "master": (
        re.compile(r"^master'?s?(?: degree)?$", re.I),
        re.compile(r"^master of science\b", re.I),
        re.compile(r"^m\.?\s?s\.?c?\.?(?: degree)?$", re.I),
        # "Master's Program Work" is study without the degree (Emory's catalog lists it beside "Master's Level Degree").
        re.compile(r"\bmaster'?s?\b(?!.*\b(?:arts?|fine|music|laws?|education|business|mba|program work|course ?work)\b)", re.I),
        re.compile(r"^graduate degree$", re.I),
        # "MA/MS": the plain master's level, never "MBA" (EPIC committed MBA for a Master of Science, 2026-10-25).
        re.compile(r"^(?:m\.?\s?a\.?\s*/\s*)?m\.?\s?s\.?c?\.?(?:\s*/\s*m\.?\s?a\.?)?$", re.I),
    ),
}


def degree_level(text):
    t = normalize(str(text))
    # "ms", "m s", "msc", "m sc": the space before "c" is optional, not the one after "s" ("MA/MS" read as no level).
    if re.search(r"\bmaster|\bm ?s ?c?\b|\bm ?eng\b|\bm ?tech\b", t):
        return "master"
    if re.search(r"\bbachelor|\bb ?s ?c?\b|\bb ?e\b|\bb ?eng\b|\bb ?tech\b|\bundergraduate\b", t):
        return "bachelor"
    return None


MAJOR_STOP = {"and", "of", "in", "the", "for", "with", "studies", "study", "program", "general"}


# Words a taxonomy may add to a major without changing it: Workday's default Field of Study catalog names Computer
# Science "Computer and Information Science" and has no literal "Computer Science" (REA, 2026-10-25: 26 of 31 failing
# tenants), so the owner's fallback major was never found there.
MAJOR_SOFT = {"information"}


def major_equivalent(target, options):
    """The one offered major that is `target` in the catalog's own words; None when absent or ambiguous."""

    def words(text):
        return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in normalize(str(text)).split()} - MAJOR_STOP

    want = words(target)
    if not want:
        return None
    hits = [o for o in options if (have := words(o)) >= want and have - want <= MAJOR_SOFT]
    return hits[0] if len(hits) == 1 else None


def major_part(recorded, options):
    """An offered major named only with the recorded major's words (at least two): "Information Technology" for
    "Information & Communication Technology". Not "Data Processing" for "Data Science" (Slate Auto 2026-10-17)."""
    have = set(normalize(recorded).split()) - MAJOR_STOP
    hits = [
        o for o in options if len(words := set(normalize(str(o)).split()) - MAJOR_STOP) >= 2 and words <= have
    ]
    return hits[0] if len(hits) == 1 else None


def major_option(recorded, options):
    """The offered name of the recorded major. A shorter name counts only with two of its words, as in major_part:
    Ensemble's Workday catalog lists a bare "Science", which "Data Science" matched by containment, and its search
    never showed that row among dozens of "... Science" results (2026-10-28)."""
    hit = option_value(recorded, options)
    if hit is None:
        return None
    want = set(normalize(recorded).split()) - MAJOR_STOP
    have = set(normalize(str(hit)).split()) - MAJOR_STOP
    return None if have < want and len(have) < 2 else hit


DEGREE_IN_PROGRESS = re.compile(
    r"\bin progress\b|\bpursuing\b|\bcurrently enrolled\b|\bnot (?:yet )?(?:completed|awarded)\b|\bexpected\b",
    re.I,
)


DEGREE_COMPLETED = re.compile(r"\bcompleted?\b|\bearned\b|\bawarded\b|\bgraduated\b|\bobtained\b", re.I)


def degree_status_options(options, end, today):
    """Offered degrees that state a status ("Bachelors in Progress", "Bachelors Completed") narrowed to the recorded
    one: completed once the degree's end month has passed. Lennar's Workday got "Bachelors in Progress" for a BE
    finished in June 2024 because it is listed first (2026-10-18)."""
    from ..widgets import parse_date

    try:
        month, _, year = parse_date(end)
        finished = (int(year), int(month)) < (today.year, today.month)
    except (TypeError, ValueError):
        return options

    def recorded(option):
        if DEGREE_IN_PROGRESS.search(str(option)):
            return not finished
        if DEGREE_COMPLETED.search(str(option)):
            return finished
        return True

    narrowed = [o for o in options if recorded(o)]
    return narrowed or options


def month_year(value):
    """'2021-06' -> '06/2021', '2021' -> '2021'. Precision is never invented."""
    v = str(value).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", v)
    if m:
        return f"{int(m[2]):02d}/{m[1]}"
    return v
