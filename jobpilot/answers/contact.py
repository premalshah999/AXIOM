"""Answer patterns: contact."""

import re
from .text import normalize_label_text


def address_location_option(options, address):
    """Select an actual offered city only when its region and country agree."""
    from ..widgets import _alias_group, norm

    if not (address.city and address.state and address.country):
        return None
    matches = []
    for option in options:
        parts = [norm(part) for part in option.split(",")]
        if (
            len(parts) >= 3
            and parts[0] == norm(address.city)
            and any(part in _alias_group(address.state) for part in parts[1:])
            and any(part in _alias_group(address.country) for part in parts[1:])
        ):
            matches.append(option)
    unique = list(dict.fromkeys(matches))
    if len(unique) > 1 and address.county:
        unique = [option for option in unique if norm(address.county) in norm(option)]
    return unique[0] if len(unique) == 1 else None


# "USA (other than New York)", "United States (excluding California)".
REGION_EXCLUDING = re.compile(r"^(?P<region>.+?)\s*\((?:other than|outside(?: of)?|excluding|except)\s+(?P<excluded>.+)\)\s*$", re.I)


def address_region_option(options, address):
    """The address's own region in a closed menu of regions rather than cities ("New York", "USA (other than New York)",
    "Europe", "Other": Atto Trading on Greenhouse, 2026-10-31). Its city or state named outright comes first."""
    from ..widgets import _alias_group, norm

    if not address.country:
        return None
    own = {norm(address.city), norm(address.state), *_alias_group(address.state)} - {""}
    named = [option for option in options if norm(option) in own]
    if named:
        return named[0] if len(named) == 1 else None
    country = _alias_group(address.country)
    held = []
    for option in options:
        excluding = REGION_EXCLUDING.match(option)
        region, excluded = (excluding["region"], excluding["excluded"]) if excluding else (option, "")
        if norm(region) in country and norm(excluded) not in own:
            held.append(option)
    return held[0] if len(held) == 1 else None


CONTACT = {
    "name": {
        "name",
        "full name",
        "legal name",
        "your name",
        "full legal name",
        "candidate name",
        "applicant name",
        "preferred full name",
    },
    "email": {
        "email",
        "email address",
        "your email",
        "e mail",
        "e mail address",
        "confirm email",
        "confirm email address",
        "re enter email",
        "re enter email address",
        "verify email",
        "valid email",
        "retype valid email",
        "retype email",
        "retype email address",
        "re type email address",
        "reenter email address",
        "email or phone",
        "email or mobile number",
        "email phone or skype",
        "primary email",
        "email address primary",
        # iCIMS's PersonProfileFields.Login is the applicant's sign-in email ("Email/Login" at ICE, "Login" at Rovisys
        # and Emory, where the run asked the applicant, 2026-10-19). A username is a separate fact.
        "login",
        "login id",
        "email login",
        "login email",
    },
    "phone": {
        "phone",
        "phone number",
        "mobile",
        "mobile phone",
        "mobile number",
        "telephone",
        "telephone number",
        "cell phone",
        "contact number",
        "primary phone",
        "primary number",
        "primary phone number",
        "phone mobile",
    },
    "linkedin": {
        "linkedin",
        "linkedin profile",
        "linkedin url",
        "linkedin profile url",
        "linkedin profile link",
        "linkedin link",
    },
    "website": {"website", "portfolio", "portfolio url", "personal website", "website url", "portfolio link"},
    "github": {"github", "github url", "github profile", "github link", "github profile url"},
    "location": {
        "location",
        "current location",
        "location city",
        "where are you located",
        "where are you currently based",
        "city state country",
    },
}


NAMES = {
    "first": {"first name", "given name", "legal first name", "first name legal", "first"},
    "last": {
        "last name",
        "family name",
        "surname",
        "legal last name",
        "last name legal",
        "last",
        "preferred last name",
        "preferred family name",
        "preferred surname",
    },
    "middle": {"middle name", "middle initial", "middle"},
    "preferred": {
        "preferred name",
        "preferred first name",
        "nickname",
        "known as",
        "known as first name",
        "known as name",
        "preferred given name",
    },
}


ADDRESS = {
    "line1": {"address", "address line 1", "street address", "street", "address 1", "street address line 1"},
    "line2": {"address line 2", "address 2", "apartment", "apt suite", "street address line 2"},
    "city": {"city", "town", "city town", "mailing city"},
    "state": {"state", "state province", "province", "region", "state region", "state or province"},
    "postal_code": {"postal", "postal code", "zip", "zip code", "zipcode", "postcode", "zip postal code"},
    "country": {
        "country",
        "country region",
        "country of residence",
        "country territory",
        # Microsoft / Corning account and profile forms.
        "country region of residence",
        "country or region of residence",
        "current country of residence",
        "country region where you live",
        "country of current residence",
    },
    "county": {"county"},
}


PERSON_NAME_FIELD = re.compile(
    r"^(?:legal |preferred |your )?(?:first|middle|last|given|family|sur|nick)[ -]?names?"
    r"(?:\s*\(.*\))?$|^middle initial$",
    re.I,
)


def display_name_case(label, value):
    """A personal name saved in capitals is entered in its display casing.

    Portals such as Workday reject names with more than two capitals ("contains more
    than 2 capital letters"), and a name typed in capitals once must not block every
    later application. Mixed-case names (McDonald, DeAndre) are kept exactly.
    """
    text = str(value)
    if (
        PERSON_NAME_FIELD.match(normalize_label_text(label))
        and len(text) > 2
        and text.isupper()
        and re.fullmatch(r"[^\W\d_]+(?:[ '\-.][^\W\d_]+)*\.?", text)
    ):
        return text.title()
    return value
