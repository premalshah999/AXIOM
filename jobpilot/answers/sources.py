"""Answer patterns: sources."""

import re
from ..answer_rules import source_patterns
from .text import first_matching


INTENT_SOURCE = re.compile(
    r"how did you (?:hear|learn|find)|where did you (?:hear|find|learn)|source of (?:application|referral)|"
    r"^source(?: type)?$|referral source|^(?:candidate |application )?source\b",
    re.I,
)


# Sources that would claim a relationship or status the applicant does not have. "Other/Non-Referral" (Thrivent) claims none.
NOT_A_SOURCE = re.compile(
    r"(?<!non-)(?<!non )refer|employee|colleague|recruiter|agency|staffing|internal|displaced|\bloa\b|leave of absence|"
    r"contractor|consultant|rehire|former|alumni|warn period|refugee|current (?:worker|associate)|"
    r"intern(?:ship)? program|campus|career fair|job fair|university|school|event|^other$|^none$|select",
    re.I,
)


# An offered source that names the applicant's own employment relationship with the employer. NOT_A_SOURCE is broader (it
# is "cannot be claimed truthfully"): agencies, recruiters, referrals, campus events, alumni and career fairs are ways in for
# an outside applicant even when none is true for this one, so they do not make a posting internal-only.
INTERNAL_CHANNEL = re.compile(
    r"\binternal\b|\bintranet\b|displaced|\bloa\b|leave of absence|warn period|\brehire|"
    r"\b(?:current|existing|former|prior|returning) (?:employee|associate|worker|colleague|team member|contractor)s?\b|"
    r"^\W*(?:employee|associate|worker|colleague|team member|contractor|consultant)s?\W*(?:\(.*\))?\W*$",
    re.I,
)
# A menu's own "Select One" row is not a source.
PLACEHOLDER = re.compile(r"^\W*(?:please )?(?:select|choose)\b|^[\W_]*$", re.I)


def internal_channels_only(options):
    """True when every offered source is an internal channel (displaced or on-leave employees, current or former
    employees) and none is a way in for an outside applicant: the posting is then closed to external applicants. Bank of
    America's employee site offers only "Displaced (US)" and "LOA - Job Search (US)". The test used to be NOT_A_SOURCE,
    which also reads agency, recruiter, referral, campus and event choices as internal: Thrivent's "Employee/Advisor
    Referral" and "Other/Non-Referral" (2026-10-14), GDIT's "Employee Referral" and GE Vernova's "Agency / Search
    Firm", "Current Employee", "GE Vernova Recruiter" were closed although each offers an outside applicant a way in."""
    offered = [o for o in (str(o).strip() for o in options or []) if o and not PLACEHOLDER.match(o)]
    return bool(offered) and all(INTERNAL_CHANNEL.search(o) for o in offered)


# A parent source menu's follow-up asks which one: "Social Media Source", "Job Board Source". It names the channel the
# parent chose.
FOLLOW_UP = re.compile(
    r"(job boards?|job sites?|social media|social networks?|internet|online|advertising) sources?(?: details?| name| type)?",
    re.I,
)
# "-" and the like are a menu's blank row, not a channel.
BLANK_OPTION = re.compile(r"[\W_]*")
# The follow-up offers nothing the applicant can truthfully pick: choose another channel in the parent menu. Short: the
# model reads it in the refusal.
DEAD_END = "{follow} offers no option the applicant can truthfully pick; choose another channel in the parent source menu"


def follow_up_channel(label):
    """The channel a follow-up source question names ("job board" for "Job Board Source"), else ""."""
    found = FOLLOW_UP.fullmatch(" ".join(str(label or "").replace("*", " ").split()))
    return " ".join(found[1].casefold().split()) if found else ""


def same_channel(option, channel):
    """An option is the channel that was marked unusable ("Job Boards" for "job board")."""
    def one(text):
        return re.sub(r"s$", "", " ".join(str(text).casefold().split()))

    return bool(channel) and one(option) == one(channel)


def source_choice(options, rules, employer="", avoid=()):
    """A listed source in the profile's own order (answer_rules.source_order), else, only if the profile allows it
    (source_any_channel), any plain channel. A profile with neither answers nothing here: the applicant is asked.
    `employer` names the company-website channel; a channel in `avoid` had a follow-up with no truthful option."""
    options = [
        o for o in options if not BLANK_OPTION.fullmatch(str(o)) and not any(same_channel(o, a) for a in avoid)
    ]
    if hit := first_matching(options, source_patterns(rules.source_order, employer, options)):
        return hit
    if rules.source_any_channel:
        return next((o for o in options if not NOT_A_SOURCE.search(str(o))), None)
    return None


# A fully observed catalog offers no option for the applicant's saved source. Choosing
# another option cannot be truthful, so the browser stops at the first refusal.
UNOFFERED_SOURCE = "The saved application source is not in the observed catalog"
