"""Contact fields that belong to someone else: an employer, a supervisor, a reference, an emergency contact.

Owner 2026-10-29: employer-address fields were filled with the applicant's own address. The history-row rules
(history_rules.py) already guard a numbered "Work Experience 2" row, but a single "Current Employer" block, a "Supervisor"
section or a "Reference 1" group is not a numbered row: its bare "Address", "Zip Code" or "Phone" fell through to the
applicant's own contact facts (and the model was shown those facts too). The profile records no street address, postal code
or phone for an employer, supervisor or reference, so those stay blank (optional) or go to the owner as a complete
question (required); the city, state and country of the applicant's *current* employer come only from that work row.
"""

import re

from ..schemas import Answer
from .history import HISTORY_UNKNOWN, history_region

# What of a third party the field asks for.
PART = re.compile(
    r"\b(?:address|street|city|town|state|province|region|zip|postal|post ?code|postcode|country|county|"
    r"phone|telephone|tel|mobile|cell|fax)\b",
    re.I,
)
LOCATION_PART = re.compile(r"\b(?:city|town|state|province|region|country)\b", re.I)
# Whose it is, said by the label itself ("Employer Address", "Supervisor Phone", "Reference 2 Email").
OWNER_IN_LABEL = re.compile(
    r"\b(?:employer|company|supervisor|manager|reference|referee|referrer|emergency|next of kin|spouse|parent|"
    r"guardian|relative|beneficiary|organi[sz]ation|school|university|institution)s?(?:'s|’s)?\b",
    re.I,
)
# ... or by the block the field sits in. Only blocks that are plainly about another party: "Employment Eligibility" is not.
OWNER_IN_BLOCK = re.compile(
    r"\b(?:(?:current|present|previous|prior|former|last|most recent|recent)\s+(?:employer|employment|company|job|position)|"
    r"employer(?:\s+(?:information|details|contact|address))?|supervisor|reference|referee|emergency|next of kin|"
    r"employment history|work (?:history|experience))\b",
    re.I,
)
# A field about the applicant, whatever its block: "Your phone", "Applicant's address", "Home address", "Personal email".
OWN = re.compile(r"\b(?:your|my|home|personal|applicant|candidate|permanent|mailing|residential|residence)\b", re.I)
# A numbered or named history row ("Work Experience 2", "Education at Example University").
NUMBERED_ROW = re.compile(
    r"(?:work experience|experience|employment|education)\s*\d+\b|\b(?:education|experience|work experience|employment)\s+at\s+\S",
    re.I,
)
CURRENT_EMPLOYER = re.compile(r"\b(?:current|present)\s+(?:employer|employment|company|job|position)\b", re.I)


def third_party(f):
    """Who a contact field belongs to ("employer", "reference", ...), or "" when it is the applicant's own."""
    if f.get("history_kind") or f.get("history_row") or NUMBERED_ROW.search(f"{f.get('group') or ''} {f.get('section') or ''}"):
        return ""  # A placed history row is answered from its own profile entry (history_rules).
    label = str(f.get("label") or "")
    if not PART.search(label) or len(label.split()) > 8 or "?" in label:
        return ""  # A contact field's label is a few words; a long question merely mentions a state or an organization.
    if OWN.search(label) and not OWNER_IN_LABEL.search(label):
        return ""
    own = OWNER_IN_LABEL.search(label)
    if own:
        return re.sub(r"['’]s$", "", own[0].lower())
    block = " ".join(str(f.get(k) or "") for k in ("section", "group"))
    found = OWNER_IN_BLOCK.search(block)
    return found[0].lower() if found else ""


def current_row(profile, f):
    """The one work entry a "current employer" field describes, else None."""
    text = " ".join(str(f.get(k) or "") for k in ("label", "section", "group"))
    if not CURRENT_EMPLOYER.search(text):
        return None
    rows = [(i, w) for i, w in enumerate(profile.work) if w.current]
    return rows[0] if len(rows) == 1 else None


def guard(resolver, f):
    """An Answer for a contact field that is someone else's, else None (the field is the applicant's own).

    A location part (city, state, country) of the current employer comes from its work row; every other part is blank when
    optional and a complete question when required. The applicant's own address and phone are never used."""
    who = third_party(f)
    if not who or f.get("type") in {"file", "checkbox", "password"}:
        return None
    label = str(f.get("label") or "")
    row = current_row(resolver.profile, f) if LOCATION_PART.search(label) else None
    if row:
        index, work = row
        location = str(getattr(work, "location", "") or "")
        part = LOCATION_PART.search(label)[0].lower()
        value = ""
        if part in {"city", "town"}:
            value = location.split(",")[0].strip() if "," in location else ""
        else:
            value = history_region(location, part == "country")
        if value and (answer := resolver._ans(f, value, [f"profile:work.{index}.location"], "The employer's location")):
            return answer
    detail = {"employer": "an employer", "company": "an employer", "supervisor": "a supervisor"}.get(who.split()[-1], "a " + who)
    return Answer(
        field_id=f["id"],
        disposition="review" if f.get("required") else "skip",
        evidence_ids=[HISTORY_UNKNOWN],
        reason=f"This asks for {detail}'s contact detail, which the profile does not record; the applicant's own is never used",
        blocker_type="missing_fact",
    )
