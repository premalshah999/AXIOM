"""Owner rule: a company that asks for a national or bank identifier gets no application.

A field counts only when it asks for the identifier itself: a text, number, password or
textarea input whose label names an SSN, SIN, national ID, taxpayer ID, a driver's license
or passport NUMBER, or bank account details. Yes/no questions (radios, checkboxes, selects)
and date-of-birth fields never match. Values are never read or stored; only labels and kinds.
"""

import re
from urllib.parse import urlsplit

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


# An identity check is the same ask as a typed identifier: the page wants a photo of a government ID or a selfie, which
# only the owner holds and which the owner rule drops. Amazon (2026-10-27/29): the last step of its form read "To verify
# your identity, please upload a picture of your government ID and take a photo of yourself in the verification portal"
# with "I consent to identity verification" and "Begin identity verification"; four runs reached it and one pressed Submit
# over it. Found by what the page asks, never by employer: the sentence, the control, or a known verification vendor's page.
ID_PHOTO = re.compile(
    r"\b(?:upload|take|submit|provide|scan|capture|send)\s+(?:a\s+|an\s+|your\s+|the\s+)?(?:clear\s+|current\s+)?"
    r"(?:photo|picture|image|scan|copy)s?\s+of\s+(?:your\s+|a\s+|the\s+)?(?:valid\s+|current\s+)?"
    r"(?:(?:government|state|national|photo)[- ]?(?:issued\s+)?(?:photo\s+)?)?(?:id\b|identification|passport|driver|driving)",
    re.I,
)
SELFIE = re.compile(r"\bselfie\b|\bliveness (?:check|test)\b|\bface (?:scan|match|verification)\b", re.I)
CHECK_CONTROL = re.compile(
    r"^(?:begin|start|complete|continue with|launch)\s+(?:the\s+|your\s+|my\s+)?(?:identity|id)\s+(?:verification|check)\b"
    r"|^i\s+consent\s+to\s+(?:the\s+)?(?:identity|id)\s+(?:verification|check)\b|^verify\s+(?:my|your)\s+identity$",
    re.I,
)
# Third-party identity-verification services; a page of theirs inside an application is the check itself.
ID_VENDOR_HOSTS = (
    "withpersona.com", "persona-id.com", "onfido.com", "veriff.com", "veriff.me", "jumio.com", "au10tix.com",
    "incode.com", "idnow.io", "trulioo.com", "socure.com", "verify.stripe.com", "id.me", "clearme.com", "mitek.com",
)
MAX_SENTENCE = 400


def identity_check(observation):
    """True when the observed page asks for a photo of a government ID or a selfie (see ID_PHOTO). An optional upload,
    or prose longer than a sentence or two, is not the check."""
    host = (urlsplit(str(observation.get("url") or "")).hostname or "").lower()
    if any(host == d or host.endswith("." + d) for d in ID_VENDOR_HOSTS):
        return True
    labels = [str(c.get("label") or "") for c in observation.get("controls") or [] if isinstance(c, dict)]
    labels += [str(f.get("label") or "") for f in observation.get("fields") or [] if isinstance(f, dict)]
    if any(CHECK_CONTROL.search(" ".join(label.split())) for label in labels):
        return True
    for line in str(observation.get("text") or "").splitlines():
        line = " ".join(line.split())
        if (
            line
            and len(line) <= MAX_SENTENCE
            and (ID_PHOTO.search(line) or SELFIE.search(line))
            and not re.search(r"\boptional\b|\bnot required\b", line, re.I)
        ):
            return True
    return False


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
    check = identity_check(observation)
    if not matches and not check:
        return False
    kinds = list(dict.fromkeys(kind for kind, _ in matches))
    labels = [label for _, label in matches][:5]
    if check:
        kinds.append("identity_check")
    engine.emit(
        "sensitive_id_requested",
        "Application asks for a government or bank identifier; the job is removed",
        {"kinds": kinds, "labels": labels},
    )
    engine.result = {
        "state": "cancelled",
        "reason": "Application asks for "
        + ", ".join("a government ID photo or selfie check" if k == "identity_check" else k.replace("_", " ") for k in kinds)
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
