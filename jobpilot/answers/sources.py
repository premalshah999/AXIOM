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


def source_choice(options, rules):
    """A listed source in the profile's own order (answer_rules.source_order), else, only if the profile allows it
    (source_any_channel), any plain channel. A profile with neither answers nothing here: the applicant is asked."""
    options = [o for o in options if str(o).strip()]
    if hit := first_matching(options, source_patterns(rules.source_order)):
        return hit
    if rules.source_any_channel:
        return next((o for o in options if not NOT_A_SOURCE.search(str(o))), None)
    return None


# A fully observed catalog offers no option for the applicant's saved source. Choosing
# another option cannot be truthful, so the browser stops at the first refusal.
UNOFFERED_SOURCE = "The saved application source is not in the observed catalog"
