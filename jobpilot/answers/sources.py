"""Answer patterns: sources."""

import re
from ..answer_rules import source_patterns
from .text import first_matching


INTENT_SOURCE = re.compile(
    r"how did you (?:hear|learn|find)|where did you (?:hear|find|learn)|source of (?:application|referral)|"
    r"^source(?: type)?$|referral source|^(?:candidate |application )?source\b",
    re.I,
)


# Sources that would claim a relationship or status the applicant does not have.
NOT_A_SOURCE = re.compile(
    r"refer|employee|colleague|recruiter|agency|staffing|internal|displaced|\bloa\b|leave of absence|"
    r"contractor|consultant|rehire|former|alumni|warn period|refugee|current (?:worker|associate)|"
    r"intern(?:ship)? program|campus|career fair|job fair|university|school|event|^other$|^none$|select",
    re.I,
)


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
