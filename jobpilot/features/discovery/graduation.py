"""Explicit graduation windows in a posting, compared with recorded degree dates.

New-grad postings often state who may apply: "graduating between December 2025
and August 2026", "Class of 2026", "graduated within the last 12 months",
"graduating in 2027 or later" (an internship). Comparing that with the
applicant's degree end dates is deterministic, so it runs before any model or
browser work.

Conservative by design: a job is rejected only when the posting states a window
this parser understands *and* none of the applicant's dated degrees falls inside
any stated window. No window, an unreadable window, or no dated degree means no
decision here; the ranker still reads the full posting.
"""

import re
from datetime import UTC, datetime

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
SEASONS = {"spring": 5, "summer": 8, "fall": 12, "autumn": 12, "winter": 12}
WORDS = {"one": 1, "two": 2, "three": 3, "six": 6, "twelve": 12, "eighteen": 18, "twenty-four": 24}

_MONTH = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|spring|summer|fall|autumn|winter"
)
DATE = rf"(?:(?:{_MONTH})\.?,?\s+(?:of\s+)?20\d\d|\d{{1,2}}/20\d\d|20\d\d)"
# Verb/requirement forms only: "graduates" as a noun ("we hired 50 graduates in 2024") is not a window.
GRAD = r"(?:\bgraduat(?:ing|ed|e|ion)\b|completion of (?:your|their|the) degree|degree completion)"
GAP = r"[^.;:\n]{0,60}?"
DASH = r"(?:and|to|through|until|-|–|—)"

BETWEEN = re.compile(rf"{GRAD}{GAP}\b(?:between|from)\s+({DATE})\s+{DASH}\s+({DATE})", re.I)
RANGE = re.compile(
    rf"{GRAD}(?:{GAP}\b(?:in|during)\s+|(?:\s+(?:date|window|timeline))?\s*:\s*)({DATE})\s*{DASH}\s*({DATE})",
    re.I,
)
UNTIL = re.compile(rf"{GRAD}{GAP}\b(?:by|before|no later than|prior to|on or before)\s+({DATE})", re.I)
AFTER = re.compile(rf"{GRAD}{GAP}\b(?:after|on or after|no earlier than)\s+({DATE})", re.I)
IN = re.compile(
    rf"{GRAD}{GAP}\b(?:in|during)\s+(?:the\s+)?({DATE})(\s+or\s+(?:later|after|earlier|before))?", re.I
)
CLASS = re.compile(r"\bclass of\s+(20\d\d)\b", re.I)
WITHIN = re.compile(
    rf"{GRAD}{GAP}\bwithin\s+(?:the\s+)?(?:last|past|previous)\s+(\d+|{'|'.join(WORDS)})\s+(months?|years?)",
    re.I,
)


def _month(text: str) -> tuple[int, int | None] | None:
    """(year, month or None) from a date phrase."""
    text = text.strip().lower()
    if m := re.fullmatch(r"(\d{1,2})/(20\d\d)", text):
        return int(m[2]), int(m[1]) if 1 <= int(m[1]) <= 12 else None
    year = re.search(r"20\d\d", text)
    if not year:
        return None
    name = re.match(r"[a-z]+", text)
    if not name:
        return int(year[0]), None
    word = name[0]
    month = SEASONS.get(word) or MONTHS.get(word[:3])
    return int(year[0]), month


def _index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def _bounds(date, low: bool) -> int | None:
    """Month index of a date phrase; a bare year spans the whole year."""
    parsed = _month(date) if isinstance(date, str) else date
    if not parsed:
        return None
    year, month = parsed
    return _index(year, month or (1 if low else 12))


def windows(text: str, today: datetime | None = None) -> list[tuple[int | None, int | None, str]]:
    """Graduation windows stated in a posting: (first month, last month, phrase)."""
    today = today or datetime.now(UTC)
    now = _index(today.year, today.month)
    out = []
    for pattern in (BETWEEN, RANGE):
        for m in pattern.finditer(text):
            lo, hi = _bounds(m[1], True), _bounds(m[2], False)
            if lo is not None and hi is not None and lo <= hi:
                out.append((lo, hi, m[0]))
    for m in UNTIL.finditer(text):
        if (hi := _bounds(m[1], False)) is not None:
            out.append((None, hi, m[0]))
    for m in AFTER.finditer(text):
        if (lo := _bounds(m[1], True)) is not None:
            out.append((lo, None, m[0]))
    covered = " ".join(w[2] for w in out)
    for m in IN.finditer(text):
        if m[0] in covered:
            continue
        lo, hi = _bounds(m[1], True), _bounds(m[1], False)
        if lo is None:
            continue
        tail = (m[2] or "").lower()
        if "later" in tail or "after" in tail:
            out.append((lo, None, m[0]))
        elif "earlier" in tail or "before" in tail:
            out.append((None, hi, m[0]))
        else:
            # "Graduating in May 2026": the stated year, never a single month.
            year = (_month(m[1]) or (None,))[0]
            out.append((_index(year, 1), _index(year, 12), m[0]))
    for m in CLASS.finditer(text):
        year = int(m[1])
        # Class-of cohorts conventionally include the preceding fall/winter graduates.
        out.append((_index(year - 1, 9), _index(year, 12), m[0]))
    for m in WITHIN.finditer(text):
        n = int(m[1]) if m[1].isdigit() else WORDS[m[1].lower()]
        months = n * 12 if m[2].lower().startswith("year") else n
        out.append((now - months, None, m[0]))  # upcoming graduates are recent graduates too
    return out


def degree_dates(profile: dict) -> list[tuple[int, int | None]]:
    dates = []
    for row in profile.get("education") or []:
        end = str((row or {}).get("end") or "").strip()
        if m := re.fullmatch(r"(20\d\d)-(\d{1,2})(?:-\d{1,2})?", end):
            dates.append((int(m[1]), int(m[2])))
        elif parsed := _month(end):
            dates.append(parsed)
    return dates


def _inside(date: tuple[int, int | None], window) -> bool:
    lo, hi, _ = window
    first = _index(date[0], date[1] or 1)
    last = _index(date[0], date[1] or 12)
    return (lo is None or last >= lo) and (hi is None or first <= hi)


def _label(index: int | None) -> str:
    return "" if index is None else f"{index % 12 + 1:02d}/{index // 12}"


def graduation_gate(job: dict, profile: dict, today: datetime | None = None) -> str:
    """'' when compatible or undecidable; otherwise a short rejection reason."""
    text = " ".join(str(job.get(k) or "") for k in ("title", "description"))
    stated = windows(text, today)
    dates = degree_dates(profile)
    if not stated or not dates:
        return ""
    if any(_inside(d, w) for d in dates for w in stated):
        return ""
    lo, hi, _ = stated[0]
    span = (
        f"between {_label(lo)} and {_label(hi)}"
        if lo is not None and hi is not None
        else f"by {_label(hi)}"
        if hi is not None
        else f"from {_label(lo)}"
    )
    return f"graduation_window: posting requires graduation {span}"
