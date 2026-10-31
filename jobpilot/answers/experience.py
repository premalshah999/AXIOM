"""Yes/no questions that ask for N+ years of experience, alone or inside a degree compound, evaluated from the profile.

Apptronik (Greenhouse, 2026-10-30): "Do you have an MS with 3+ years of relevant experience, or a recent PhD, in Robotics,
CS, EE, or a related field?" was answered by the model, and the same profile got Yes six times and No twice across runs;
its first answer cited an attestation that says nothing about duration. A years claim is arithmetic on the work history,
so it is computed here, never recalled: full-time professional months are the union of the work rows' months (an overlap
counts once), and internships, co-ops, student and part-time rows count only where the question does not exclude them.

Every clause is three-valued (True, False, None = the profile cannot say). A compound is the Kleene "or" of its
alternatives, each the "and" of its clauses, so a False clause settles it and an unknown one never turns into Yes.
Stdlib only: the audit of confirmed applications runs this module inside the app container.
"""

import re
from dataclasses import dataclass, field
from datetime import date

NUMBER_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten".split())}
YEARS = re.compile(
    r"(?P<cmp>\b(?:at least|a minimum of|minimum(?: of)?|more than|over|greater than|less than|fewer than|under|up to|"
    r"no more than|at most|maximum(?: of)?)\s+)?"
    r"\b(?P<n>\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten)\b\s*(?P<plus>\+|or more|or greater|plus)?\s*"
    r"(?P<range>(?:-|–|to)\s*\d{1,2}\s*)?(?:years?|yrs?)\b",
    re.I,
)
UPPER_BOUND = re.compile(r"less than|fewer than|under|up to|no more than|at most|maximum", re.I)
EXPERIENCE = re.compile(r"\bexperience[ds]?\b", re.I)
# A number of years that is a time window or an age, not a length of experience: "in the past 3 years", "18 years of age".
NOT_A_DURATION = re.compile(r"(?:past|last|previous|prior|next|following|within(?: the)?)\s+$", re.I)
AGE_OR_AGO = re.compile(r"\s*(?:of age|old\b|ago\b|or (?:older|younger))", re.I)
RANGE = re.compile(r"\d\s*(?:-|\u2013|\u2014|to)\s*\d{1,2}\s*\+?\s*(?:years?|yrs?)\b", re.I)
# Words between "N years of" and "experience" that name no specialty.
GENERIC = set(
    "of relevant professional industry full time work working related total overall hands on direct prior previous paid "
    "practical real world post graduate similar applicable the a an in this field and or full-time hands-on real-world "
    "post-graduate work-related".split()
)
# Rows that are not full-time professional work.
NOT_PROFESSIONAL = re.compile(
    r"\b(?:intern(?:ship)?s?|co-?ops?|apprentice(?:ship)?|trainee|externship|student|(?:graduate|research|teaching) "
    r"assistant|part[- ]time)\b",
    re.I,
)
# A question that says which experience counts ("professional", "full-time", ...) excludes those rows.
EXCLUDES_NOT_PROFESSIONAL = re.compile(
    r"full[- ]time|professional|industry|post[- ]?(?:graduat|degree|college|university)|\bpaid\b|excluding|not including|"
    r"other than|(?:no|not) (?:intern|co-?op)",
    re.I,
)
ABBREVIATED = r"(?:ph\.?\s?d\.?|m\.?\s?s\.?c?\.?|mba|m\.?\s?eng\.?|b\.?\s?s\.?c?\.?|b\.?\s?a\.?|b\.?\s?eng\.?)"
DEGREE = re.compile(
    r"(?<![\w.])(?P<d>doctorate|doctoral(?: degree)?|master'?s?(?: degree)?|bachelor'?s?(?: degree)?|"
    + ABBREVIATED
    + r"(?=\s*(?:degree\b|with\b|in\b|or\b|and\b|,|\?|\)|/|$)))(?!\w)",
    re.I,
)
FIELD_ALIASES = {
    "cs": "computer science",
    "ee": "electrical engineering",
    "ece": "electrical computer engineering",
    "ce": "computer engineering",
    "it": "information technology",
    "ml": "machine learning",
    "ai": "artificial intelligence",
}
RELATED = re.compile(r"related|relevant|similar|equivalent|comparable|discipline", re.I)


@dataclass
class Verdict:
    result: bool | None  # the question's answer from the profile; None when a clause cannot be evaluated
    possible: bool  # Yes is not ruled out: some alternative has no clause the profile contradicts
    reason: str = ""
    work: list = field(default_factory=list)  # indexes of the work rows the figure comes from
    education: list = field(default_factory=list)  # indexes of the education rows the degree clauses read


def month_index(value, *, end=False, high=False):
    """Months since year 0 for a "YYYY-MM" or "YYYY" date; None when unreadable. A bare year is widest or narrowest:
    `high` picks the end of the span that makes a row longer (start in January, end in December)."""
    m = re.fullmatch(r"\s*(\d{4})(?:-(\d{1,2}))?(?:-\d{1,2})?\s*", str(value or ""))
    if not m:
        return None
    year, month = int(m[1]), int(m[2]) if m[2] else None
    if month is None:
        month = (12 if end else 1) if high else (1 if end else 12)
    return year * 12 + month


def work_months(work, asof):
    """((lo_all, hi_all), (lo_pro, hi_pro), complete, rows): months of experience as of `asof`, counting every row and
    only full-time professional rows. lo/hi are the narrowest and widest readings of the recorded dates (a row's last
    month is not counted in lo and is in hi); an overlap counts once. `complete` is False when a row has no readable
    start date, so the figures are then only lower bounds."""
    now = asof.year * 12 + asof.month
    spans = {"all": [[], []], "pro": [[], []]}
    complete, rows = True, []
    for i, row in enumerate(work):
        start = month_index(row.get("start"))
        if start is None:
            complete = False
            continue
        current = bool(row.get("current")) or not str(row.get("end") or "").strip()
        for slot, high in enumerate((False, True)):
            first = month_index(row.get("start"), high=high)
            last = now if current else month_index(row.get("end"), end=True, high=high)
            if last is None:
                complete = False
                continue
            last = min(last + (1 if high and not current else 0), now + (1 if high else 0))
            if last <= first:
                continue
            spans["all"][slot].append((first, last))
            if not NOT_PROFESSIONAL.search(str(row.get("title") or "")):
                spans["pro"][slot].append((first, last))
        rows.append(i)

    def union(intervals):
        total, edge = 0, None
        for first, last in sorted(intervals):
            if edge is None or first > edge:
                total += last - first
                edge = last
            elif last > edge:
                total += last - edge
                edge = last
        return total

    return (
        (union(spans["all"][0]), union(spans["all"][1])),
        (union(spans["pro"][0]), union(spans["pro"][1])),
        complete,
        rows,
    )


def level_of(token):
    t = re.sub(r"[^a-z]", "", token.lower())
    if t.startswith(("phd", "doctor")):
        return "doctor"
    if t.startswith(("master", "ms", "msc", "mba", "meng")):
        return "master"
    return "bachelor"


def degrees_held(education, asof):
    """({level: True (held) | False | None}, {level: education rows}) from the education rows: a degree with an end date
    not after `asof` is held, one ending later is not yet held, one without an end date cannot be told. A level with no
    row is False."""
    rows = {"bachelor": [], "master": [], "doctor": []}
    for i, row in enumerate(education):
        text = str(row.get("degree") or "")
        if re.search(r"ph\.?\s?d|doctor", text, re.I):
            rows["doctor"].append(i)
        elif re.search(r"master|\bm\.?\s?s\.?c?\b|\bmba\b|\bm\.?\s?eng\b|\bm\.?\s?tech\b", text, re.I):
            rows["master"].append(i)
        elif re.search(r"bachelor|\bb\.?\s?s\.?c?\b|\bb\.?\s?a\b|\bb\.?\s?e\b|\bb\.?\s?eng\b|\bb\.?\s?tech\b", text, re.I):
            rows["bachelor"].append(i)
    now = asof.year * 12 + asof.month
    held = {}
    for level, indexes in rows.items():
        ends = [month_index(education[i].get("end"), end=True) for i in indexes]
        held[level] = (
            True if any(e is not None and e <= now for e in ends) else (None if None in ends else False)
        )
    return held, rows


def field_matches(qualifier, education_rows):
    """True when a degree's recorded field is one of the fields the question names; None when it cannot be said (a
    different field may still be "related", which only a person can judge)."""
    named = []
    for part in re.split(r",|\bor\b|\band\b|/", qualifier, flags=re.I):
        word = re.sub(r"[^a-z ]", " ", part.lower()).strip()
        if word and not RELATED.search(word):
            named.append(FIELD_ALIASES.get(word, word))
    for row in education_rows:
        have = re.sub(r"[^a-z ]", " ", str(row.get("field_of_study") or "").lower()).split()
        if any(set(name.split()) <= set(have) for name in named if name):
            return True
    return None


def duration_clauses(text):
    """The "N years ... experience" phrases in `text`: a number of years with the word experience after it, that is not
    a time window or an age."""
    return [
        m
        for m in YEARS.finditer(text)
        if EXPERIENCE.search(text, m.end(), m.end() + 90)
        and not NOT_A_DURATION.search(text[: m.start()][-14:])
        and not AGE_OR_AGO.match(text, m.end())
    ]


def years_needed(match):
    word = match["n"].lower()
    count = int(word) if word.isdigit() else NUMBER_WORDS[word]
    return count * 12 + (1 if re.match(r"more than|over|greater", match["cmp"] or "", re.I) else 0)


PLAIN_QUALIFIER = set(GENERIC) | set("field industry role position company team us u s united states setting".split())


def evaluate(label, work, education, asof):
    """The verdict for a yes/no question asking N+ years of experience (alone or with degree clauses), or None when the
    question is not one or has a shape this does not read (a range, an upper bound, "or equivalent")."""
    text = re.sub(r"\s+", " ", str(label or "")).strip()
    years = duration_clauses(text)
    if (
        not years
        or RANGE.search(text)
        or re.search(r"\bbetween\s+\d|\bor (?:equivalent|comparable)\b|\bequivalent (?:work |practical |professional )?experience", text, re.I)
        or any(UPPER_BOUND.search(m["cmp"] or "") or m["range"] for m in years)
    ):
        return None
    # Alternatives: "an MS with 3+ years of experience, or a recent PhD" (the "or" must introduce a degree or years).
    lead = rf"(?:(?:an?|the)\s+)?(?:(?:recent|relevant)\s+)*(?:{DEGREE.pattern}|\d{{1,2}}\+?\s*years?)"
    parts, last = [], 0
    for sep in re.finditer(r",?\s+or\s+", text, re.I):
        if re.match(lead, text[sep.end() :], re.I):
            parts.append(text[last : sep.start()])
            last = sep.end()
    parts.append(text[last:])
    if len(parts) > 3:
        return None
    qualifier = re.search(r"\b(?:in|within)\s+(?P<f>[a-z][^?]*?)\s*\??$", text, re.I)
    named = re.split(r"\bwith\b|\d", qualifier["f"], maxsplit=1, flags=re.I)[0] if qualifier else ""
    if qualifier and all(w in PLAIN_QUALIFIER for w in re.findall(r"[a-z]+", named.lower())):
        qualifier = None  # "in the field", "in the US": names no specialty
    (lo_all, hi_all), (lo_pro, hi_pro), complete, rows = work_months(work, asof)
    if not rows:
        return None  # No dated work row: nothing to compute from, and no work history is not proof of none.
    lo, hi = (lo_pro, hi_pro) if EXCLUDES_NOT_PROFESSIONAL.search(text) else (lo_all, hi_all)
    held, edu_rows = degrees_held(education, asof)
    used, alternatives = [], []
    for part in parts:
        degrees = list(DEGREE.finditer(part))
        if len({level_of(m["d"]) for m in degrees}) > 1:
            return None
        clauses = []  # (value, Yes not ruled out)
        for m in degrees[:1]:
            level = level_of(m["d"])
            value = held[level]
            if value and re.search(r"\brecent\b", part[: m.start()][-20:], re.I):
                value = None  # how recent is "recent" is the employer's call
            if value and qualifier and qualifier.start() > m.start():
                value = True if field_matches(named, [education[i] for i in edu_rows[level]]) else None
            used += edu_rows[level]
            clauses.append((value, value is not False))
        for m in duration_clauses(part):
            need = years_needed(m)
            exp = EXPERIENCE.search(part, m.end())
            between = part[m.end() : exp.start()] if exp else ""
            after = part[exp.end() :] if exp else ""
            tail = after.strip(" ?.!")
            specialty = (
                any(w not in GENERIC for w in re.findall(r"[a-z-]+", between.lower()))
                # What follows "experience" names it ("experience developing apps", "in the field" does not).
                or bool(tail and not re.match(r"[,;(]|(?:required|needed|preferred|or more|in (?:the|this) (?:field|industry))\b|in a (?:related|relevant)\b", tail, re.I))
                or bool(qualifier and qualifier.start() > m.start())
            )
            value = True if lo >= need else (False if hi < need and complete else None)
            # The profile records duration, not which specialty a row was spent on: never True or False for one, but
            # fewer months in all than the question asks rules Yes out.
            clauses.append((None, hi >= need) if specialty else (value, value is not False))
        alternatives.append(clauses)
    if not any(alternatives):
        return None
    values = []
    for clauses in alternatives:
        values.append(
            False if any(v is False for v, _ in clauses) else (True if all(v is True for v, _ in clauses) else None)
        )
    result = True if True in values else (False if all(v is False for v in values) else None)
    possible = result is not False and any(all(p for _, p in clauses) for clauses in alternatives)
    reason = (
        f"{lo_pro} to {hi_pro} full-time professional months, {lo_all} to {hi_all} counting internships, "
        "from the recorded work history"
    )
    return Verdict(result, possible, reason, rows, sorted(set(used)))


# What a Yes about years of experience may rest on. The attestation that the work history is complete and the education rows
# say nothing about a duration or a specialty: Apptronik's Yes cited them alone (2026-10-30).
EXPERIENCE_EVIDENCE = re.compile(
    r"^(?:profile:work\.\d+|profile:skills|fact:kb_(?:employment|project|skill|experience)|resume:|evidence:|knowledge:)"
)
COMPUTED = "Computed from the work history"
