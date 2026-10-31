"""Owner rule: a company that asks for a national or bank identifier gets no application.

A field counts only when it asks for the identifier itself: a text, number, password or
textarea input whose label names an SSN, SIN, national ID, taxpayer ID, a driver's license
or passport NUMBER, or bank account details. Yes/no questions (radios, checkboxes, selects)
and date-of-birth fields never match. Values are never read or stored; only labels and kinds.
"""

import re

ENTRY_TYPES = {"text", "textarea", "number", "password", "contenteditable", "tel", ""}
MAX_LABEL = 160
LABEL_OUT = 60

KINDS = (
    ("ssn", re.compile(r"\bssn\b|\bsocial\s+security\s*(?:number|num\b|no\b|#|id\b|\(|$)", re.I)),
    (
        "sin",
        re.compile(r"\bsocial\s+insurance\s*(?:number|num\b|no\b|#|$)|\bsin\s*(?:number|num\b|no\b|#)", re.I),
    ),
    (
        "national_id",
        re.compile(
            r"\bnational\s+(?:id|identity|identification|insurance)\b"
            # Maersk 2026-10-15: "Please confirm if you have a valid Aadhar card / PAN card."
            r"|\baadh?aa?r\b|\bpan\s+card\b|\bpermanent\s+account\s+number\b",
            re.I,
        ),
    ),
    (
        "taxpayer_id",
        re.compile(
            r"\btaxpayer\s+(?:id|identification|identifying)\b|\btax\s+id(?:entification)?\b"
            r"|\btin\s*(?:number|num\b|no\b|#|$)",
            re.I,
        ),
    ),
    (
        "drivers_license_number",
        re.compile(
            r"\bdriv(?:er|ing)?['’]?s?\s*licen[cs]e\s*(?:number|num\b|no\b|#|id\b)"
            r"|\bdl\s*(?:number|num\b|no\b|#)",
            re.I,
        ),
    ),
    ("passport_number", re.compile(r"\bpassport\s*(?:number|num\b|no\b|#|id\b)", re.I)),
    (
        "bank_account",
        re.compile(r"\bbank\s+(?:account|acct\b|routing)|\brouting\s*(?:number|num\b|no\b|#)|\biban\b", re.I),
    ),
)


def _label(field):
    return " ".join(str(field.get("label") or "").split())


# Unambiguous at any length: BCBST (2026-10-12) asked in a 274-character paragraph for "the last four digits of
# your Social Security Number", which the length limit below let through to the owner as a question.
SSN_STRONG = re.compile(
    r"last\s+(?:four|4)\s+digits\s+of\s+(?:your\s+|the\s+)?social\s+security\s+number|\(ssn\)", re.I
)


def sensitive_field(field):
    """The identifier kind a single observed field asks for, or None."""
    if not isinstance(field, dict) or field.get("type") not in ENTRY_TYPES:
        return None
    label = _label(field)
    if label and len(label) > MAX_LABEL and SSN_STRONG.search(label):
        return "ssn"
    if not label or len(label) > MAX_LABEL:
        return None  # Paragraph help text is not the field's question.
    for kind, pattern in KINDS:
        if pattern.search(label):
            return kind
    return None


def sensitive_matches(fields):
    """(kind, short label) for every observed field that asks for an identifier."""
    out = []
    for field in fields or []:
        kind = sensitive_field(field)
        if kind:
            out.append((kind, _label(field)[:LABEL_OUT]))
    return out


async def stop_if_sensitive(engine, observation):
    """Stop the run and remove the job when this owned page asks for an identifier.

    Returns True when the run was stopped. Never fires while the submission is armed:
    an attempted submission is never abandoned by this rule.
    """
    if getattr(engine, "armed", False) or not observation:
        return False
    # Every owned page passes here: an account the employer holds under another email stops the run as well.
    from .account_identity import stop_if_identity_conflict

    if await stop_if_identity_conflict(engine, observation):
        return True
    matches = sensitive_matches(observation.get("fields") or [])
    if not matches:
        return False
    kinds = list(dict.fromkeys(kind for kind, _ in matches))
    labels = [label for _, label in matches][:5]
    engine.emit(
        "sensitive_id_requested",
        "Application asks for a government or bank identifier; the job is removed",
        {"kinds": kinds, "labels": labels},
    )
    engine.result = {
        "state": "cancelled",
        "reason": "Application asks for "
        + ", ".join(kinds).replace("_", " ")
        + "; the job was removed under the owner rule",
        "blocker_type": "sensitive_id",
        "recoverable": False,
    }
    from .job_control import manage

    try:
        await manage(engine.service, engine.job["id"], "delete")
    except Exception as exc:  # The run still stops; a failed removal is visible in the reason.
        engine.result["reason"] += " (removal failed: " + type(exc).__name__ + ")"
    return True
