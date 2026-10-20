"""Deterministic phone-group fields: number, calling-code picker, phone type, extension.

Every decision here uses stored profile facts only (phone, phone_country_code,
address.country, phone_type). Nothing is invented: when the profile lacks a fact or
the website does not offer the matching option, the helpers return None and the
normal answer path decides.
"""

import re

SELECTOR_TYPES = {"combobox", "dropdown", "select", "prompt"}

PHONE_WORD = re.compile(r"\b(?:phone|telephone|mobile|cell)\b", re.I)
# Taleo (Bealls 2026-10-17) labels it "Cellular Number".
PHONE_NUMBER_LABEL = re.compile(r"phone|mobile|telephone|\bcell(?:ular)?\b", re.I)
EXTENSION_LABEL = re.compile(r"\b(?:ext|extension)\b\.?", re.I)
BARE_EXTENSION = re.compile(r"\s*(?:ext|extension)\.?\s*(?:\(optional\))?\s*", re.I)
# "Country code", "Country Phone Code", "Country / Territory Phone Code",
# "Phone country code", "Dialing code", "Calling code".
PHONE_COUNTRY_LABEL = re.compile(
    r"\bcountry(?:\s*(?:/|or|and)?\s*(?:region|territory|country))?\s*(?:phone\s*)?(?:calling\s*)?code\b"
    r"|\bphone\s*(?:country|code|prefix)\b|\bdial(?:ing)?\s*code\b|\bcalling\s*code\b"
    r"|\bcountry\s*(?:calling|dialing)\b",
    re.I,
)
# Starbucks (Eightfold) labels it just "Device type" beside the phone number.
PHONE_TYPE_LABEL = re.compile(
    r"\bphone\b[^?]*\b(?:type|device)\b|\btype of phone\b|^\s*(?:phone )?device type\s*\*?$|"
    # Taleo: "Primary Number" picks which of Home/Cellular/Work Phone is the primary contact number.
    r"^\s*primary (?:contact )?(?:phone )?number\b",
    re.I,
)
MOBILE_OPTION = re.compile(r"\s*(?:mobile|cell(?:ular)?)(?: (?:phone|number))?\s*", re.I)
NOT_PHONE_SCOPE = re.compile(r"citizen|nationalit|birth|permanent.residen", re.I)


def _label(field):
    return str(field.get("label") or "")


def _scope(field):
    return " ".join(str(field.get(k) or "") for k in ("section", "group", "context", "hint"))


def is_phone_country(field):
    """A calling-code selector: explicit label, or a bare Country inside a phone group."""
    if field.get("type") not in SELECTOR_TYPES:
        return False
    label = _label(field)
    if PHONE_COUNTRY_LABEL.search(label):
        return True
    if label.strip().casefold() == "country":
        scope = " ".join(str(field.get(k) or "") for k in ("section", "group"))
        return bool(PHONE_WORD.search(scope)) and not NOT_PHONE_SCOPE.search(_scope(field))
    return False


def is_phone_type(field):
    label = _label(field)
    return bool(PHONE_TYPE_LABEL.search(label)) and not is_phone_country(field)


def is_phone_extension(field):
    """An optional extension. It never receives the phone number."""
    label = _label(field)
    if not EXTENSION_LABEL.search(label):
        return False
    if PHONE_WORD.search(label):
        return True
    return bool(BARE_EXTENSION.fullmatch(label)) and bool(PHONE_WORD.search(_scope(field)))


def is_phone_number(field):
    if is_phone_extension(field) or is_phone_country(field) or is_phone_type(field):
        return False
    if field.get("type") == "tel":
        return True
    return field.get("type") == "text" and bool(PHONE_NUMBER_LABEL.search(_label(field)))


def has_phone_country(fields):
    return any(is_phone_country(f) for f in fields)


def order_phone_fields(fields):
    """Move each phone number directly after the last calling-code or phone-type selector.

    The website may rebuild the number input when its country changes, so the selector is
    committed first and the number is typed afterwards.
    """
    fields = list(fields)
    selectors = [i for i, f in enumerate(fields) if is_phone_country(f) or is_phone_type(f)]
    if not selectors:
        return fields
    numbers = [f for f in fields if is_phone_number(f)]
    if not numbers:
        return fields
    rest = [f for f in fields if not is_phone_number(f)]
    pivot = max(i for i, f in enumerate(rest) if is_phone_country(f) or is_phone_type(f)) + 1
    return rest[:pivot] + numbers + rest[pivot:]


def phone_type_option(options, wanted):
    """The observed option for the saved phone type. Mobile is matched through its synonyms only."""
    wanted = str(wanted or "").strip()
    if not wanted:
        return None
    labels = [o.get("text", "") if isinstance(o, dict) else str(o) for o in options]
    labels = [text for text in labels if text.strip()]
    for text in labels:
        if text.strip().casefold() == wanted.casefold():
            return text
    if re.fullmatch(r"mobile(?: phone)?|cell(?: phone)?", wanted, re.I):
        matches = [text for text in labels if MOBILE_OPTION.fullmatch(text)]
        if len(matches) == 1:
            return matches[0]
    return None


# A displayed digit mask: "Format: 555-555-5555" next to the input, or "does not match the pattern 999-999-9999".
PHONE_MASK = re.compile(r"(?<!\d)(\(?[59]{3}\)?[ .-]?[59]{3}[ .-][59]{4})(?!\d)")


def phone_mask(text):
    """The digit mask a page asks phone numbers to follow, or None."""
    m = PHONE_MASK.search(str(text or ""))
    return m[1] if m else None


def apply_mask(value, mask):
    """The national number written in the mask's shape (Taleo: 555-555-0100), or None if it does not fit."""
    slots = sum(1 for c in mask if c in "59")
    digits = re.sub(r"\D", "", str(value or ""))[-slots:]
    if len(digits) != slots:
        return None
    it = iter(digits)
    return "".join(next(it) if c in "59" else c for c in mask)

