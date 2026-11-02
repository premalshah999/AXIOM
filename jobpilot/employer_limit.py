"""An employer's own limit on one applicant's applications, read from what its site says. Pure text work, no database.

TikTok (2026-11-01): "From 2026-07-01 to 2026-12-31, you can submit up to 2 job applications (including referrals) within
the "Bachelor/Master Graduate - 2027 Start" recruitment project. You've reached the application limit." 47 runs in 40
minutes each opened a browser to be told this again: none of the wordings below was read, and the date was ignored.
"""

from datetime import UTC, datetime, timedelta
import re

# When the employer names neither a date nor a window: long enough not to be a loop, short enough that a quarterly or
# monthly limit is not missed by much. One job is tried then; a second refusal starts the wait over.
DEFAULT_WAIT_DAYS = 30
LONGEST_WAIT_DAYS = 400  # A date further off than this is a misread, or a limit no wait is worth planning around.

# "We have reached the maximum number of applications for this role" closes one posting; only the applicant's own limit
# holds the employer.
_NOT_THE_APPLICANT = (
    r"(?<!we have )(?<!we've )(?<!we’ve )(?<!job has )(?<!role has )(?<!position has )(?<!posting has )(?<!opening has )"
    r"(?<!listing has )(?<!vacancy has )(?<!requisition has )(?<!req has )"
)
_NOT_THE_POSTING = r"(?! (?:for|on|to) (?:this|the) (?:job|role|position|posting|opening|listing|vacancy|requisition))"
_KIND = r"(?:job |active |total |open |current )?(?:application|applications|apply|submission|submissions)"

# The applicant's limit is reached, in the page's own words. Only this wording holds an employer on a page that was not a
# refused submission; a page that merely states a limit ("you can submit up to 2") has not refused anybody.
APPLICATION_LIMIT = re.compile(
    # You've reached the application limit. You have hit your job application limit.
    r"\byou(?:['’]ve| have| had)? (?:already )?(?:reached|exceeded|hit|met|used up|used all of) (?:the|your|this|our) "
    r"(?:\w+ ){0,3}?" + _KIND + r" (?:limit|cap|quota|maximum|allowance)\b|"
    # You have reached the maximum number of applications. You've reached the maximum of 3 active applications at X.
    # Exceeded your limit of 3 applications.
    + _NOT_THE_APPLICANT + r"\b(?:reached|exceeded|hit) (?:the|your) (?:maximum|max|limit|allowed number|permitted number|number)\b"
    r"(?: (?:number|amount|limit|of|allowed|permitted|\d+|\w+ly))*(?: \w+)? (?:job |active |total |open )?(?:applications?|submissions?)\b"
    + _NOT_THE_POSTING + r"|"
    # Application limit reached. The application limit has been exceeded.
    r"\b(?:(?:job )?(?:application|applications|submission|apply) limit|limit of (?:job )?applications) "
    r"(?:has been |was |is |already )*(?:reached|exceeded|met)\b|"
    # Maximum number of applications reached. Max applications exceeded.
    r"\b(?:maximum|max)(?: number)?(?: of)? (?:job )?applications? (?:has been |have been |was |were |is |are )?(?:reached|exceeded)\b"
    + _NOT_THE_POSTING + r"|"
    # You have already applied to the maximum number of jobs.
    r"\balready (?:applied (?:to|for)|submitted) (?:the )?(?:maximum|max)(?: number)?(?: of)? (?:\w+ )?"
    r"(?:applications?|jobs?|positions?|roles?)\b|"
    # You cannot submit any more applications. You can't apply to any more jobs. (Never "more than 3": that states a rule.)
    r"\byou (?:cannot|can not|can['’]?t|may not|are not able to|are unable to) (?:submit|send|make|apply (?:to|for)) "
    r"(?:any )?(?:more|further|additional) (?:job )?(?:applications?|jobs?|positions?|roles?)\b(?! than)|"
    # No more applications can be accepted from you.
    r"\bno (?:more|further|additional) applications? (?:can|may|will) be (?:submitted|accepted|made)\b"
    + _NOT_THE_POSTING + r"|"
    # You have submitted too many applications.
    r"\byou(?:['’]ve| have) (?:submitted|sent|made) too many applications\b|"
    # The reason an earlier build wrote for this very refusal (adapters.base.on_closed), read back from saved runs.
    r"\bapplication limit for this applicant is reached\b",
    re.I,
)

# Wording that is a limit only on a page that refused a submission (a posting's own policy note may say it too).
# Ashby, "We couldn't submit your application": "Example has set specific limits on how often and how many roles you can
# apply for." Five jobs of one employer were each tried, refused and tried again 3 hours later (2026-11-01).
REFUSAL_LIMIT = re.compile(
    r"\b(?:has|have) (?:set|put|placed|imposed) (?:\w+ ){0,2}limits? on how (?:often|many)\b[^.]{0,80}\bapply\b", re.I
)

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_COUNT = r"(?P<n>\d+|" + "|".join(_WORDS) + r")"
_UNITS = {"day": 1, "week": 7, "month": 30, "year": 365}
_SPAN = r"(?P<w>\d+|" + "|".join(_WORDS) + r")[ -]?(?P<u>day|week|month|year)s?"
# A stated rule. On a page that refused a submission (forms.rejection) any of these is the employer's reason.
APPLICATION_CAP = (
    # Benchling: "You've reached the maximum of 3 active applications at Benchling."
    re.compile(r"maximum of " + _COUNT + r" (?:\w+ )?applications", re.I),
    # Aurora Innovation: "candidates are limited to 3 applications every 30 days."
    re.compile(r"limited to " + _COUNT + r" applications?\b", re.I),
    # OpenAI: "Candidates may not apply more than 5 times in any 180 day span."
    re.compile(r"(?:may not|cannot|can.t) apply (?:to more than|more than) " + _COUNT + r" (?:times?|roles?|positions?|jobs?)\b", re.I),
    # TikTok: "you can submit up to 2 job applications". "at most 3 applications", "a limit of 5 applications".
    re.compile(r"(?:up to|at most|no more than|limit of) " + _COUNT + r" (?:\w+ )?(?:applications?|submissions?)\b", re.I),
)
WINDOW = re.compile(
    r"\b(?:every|per|each|in any|in a|in|within(?: any| a)?|over(?: any| a)?|during(?: any| a)?|for(?: the next)?|after|"
    r"in the (?:next|last|past))\s+" + _SPAN + r"\b",
    re.I,
)
WINDOW_UNIT = re.compile(r"\b(?:per|every|each|a) (?P<u>day|week|month|year)\b", re.I)

_MONTHS = "january february march april may june july august september october november december".split()
_MONTH = r"(?:" + "|".join(m[:3] + m[3:].join(["(?:", ")?"]) if len(m) > 3 else m for m in _MONTHS) + r")\.?"
DATE = (
    r"(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}/\d{1,2}/\d{4}|" + _MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTH + r",?\s+\d{4})"
)
# (pattern, whether the wait runs through the named day). "From A to B" ends with B; "apply again on B" opens on B.
_ENDS = (
    (re.compile(r"\b(?:from\s+|between\s+)?" + DATE + r"\s*(?:to|through|thru|until|till|and|-|–|—)\s*(?P<d>" + DATE + r")", re.I), True),
    (re.compile(r"\b(?:until|till|through|thru|ending|ends(?: on)?|expires?(?: on)?|valid to)\s+(?P<d>" + DATE + r")", re.I), True),
    (re.compile(r"\b(?:after|following|once)\s+(?P<d>" + DATE + r")", re.I), True),
    (re.compile(r"\b(?:on|from|starting|beginning|begins|reopens?(?: on)?|resets?(?: on)?)\s+(?P<d>" + DATE + r")", re.I), False),
)


def _number(value):
    return _WORDS.get(value.lower()) or int(value)


def _date(text):
    """The named day as a date, or None. A slash date is month first unless the first number cannot be a month."""
    text = re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", text.strip().replace(",", " ").replace(".", " " if re.search(r"[A-Za-z]", text) else "."))
    try:
        if found := re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text):
            return datetime(int(found[1]), int(found[2]), int(found[3]), tzinfo=UTC)
        if found := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text):
            first, second = int(found[1]), int(found[2])
            month, day = (second, first) if first > 12 else (first, second)
            return datetime(int(found[3]), month, day, tzinfo=UTC)
        parts = text.split()
        if parts and parts[0][0].isdigit():
            parts = [parts[1], parts[0], parts[2]]
        month = next(i for i, m in enumerate(_MONTHS, 1) if m.startswith(parts[0][:3].lower()))
        return datetime(int(parts[2]), month, int(parts[1]), tzinfo=UTC)
    except (ValueError, IndexError, StopIteration):
        return None


def period_end(text, at=None):
    """When the period the notice names is over, as an ISO time, or None: past dates and absurd ones say nothing. Noon UTC
    of the first free day: midnight UTC is still the last day of the period on most of the employers' clocks."""
    at = at or datetime.now(UTC)
    for pattern, through in _ENDS:
        for found in pattern.finditer(text or ""):
            day = _date(found["d"])
            if not day:
                continue
            until = (day + timedelta(days=1 if through else 0)).replace(hour=12)
            if at < until <= at + timedelta(days=LONGEST_WAIT_DAYS):
                return until.isoformat()
    return None


def window_days(text):
    """The period the notice states in days ("every 30 days", "in a 6-month period", "per year"), or None."""
    if found := WINDOW.search(text or ""):
        return _number(found["w"]) * _UNITS[found["u"].lower()]
    if found := WINDOW_UNIT.search(text or ""):
        return _UNITS[found["u"].lower()]
    return None


def application_cap(text, at=None, refused=False):
    """{"limit", "days"[, "until"]} when the text is the employer's limit, else None. Any field may be unknown: "You've
    reached the application limit" alone is a limit with no number and no date, and `wait_until` chooses the wait."""
    text = text or ""
    stated = next((found for pattern in APPLICATION_CAP if (found := pattern.search(text))), None)
    if not stated and not APPLICATION_LIMIT.search(text) and not (refused and REFUSAL_LIMIT.search(text)):
        return None
    cap = {"limit": _number(stated["n"]) if stated else None, "days": window_days(text)}
    if until := period_end(text, at):
        cap["until"] = until
    return cap


def wait_until(cap, at=None):
    """When one of the employer's jobs may be tried again: the end of the period it named, else its stated window, else a
    sensible wait."""
    at = at or datetime.now(UTC)
    if until := cap.get("until"):
        return until
    days = min(int(cap.get("days") or DEFAULT_WAIT_DAYS), LONGEST_WAIT_DAYS)
    return (at + timedelta(days=days)).isoformat()


def day_words(iso):
    """"January 1, 2027"."""
    day = datetime.fromisoformat(iso)
    return f"{_MONTHS[day.month - 1].title()} {day.day}, {day.year}"


def waiting_sentence(company, cap, until):
    """What every waiting job of this employer says."""
    name = " ".join((company or "").split()) or "This employer"
    limit, days = cap.get("limit"), cap.get("days")
    if limit and days:
        said = f"{name} accepts at most {limit} application{'' if limit == 1 else 's'} every {days} days"
    elif limit:
        said = f"{name} says you've reached its limit of {limit} application{'' if limit == 1 else 's'}"
    else:
        said = f"{name} says you've reached its application limit"
    if not cap.get("until") and not days:
        return f"{said} and names no date, so its jobs wait until {day_words(until)}, when one is tried again."
    return f"{said}, so its jobs wait until {day_words(until)}."
