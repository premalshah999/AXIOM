"""Per-platform aliases for a Gmail applicant address.

iCIMS (0 of 36 by 2026-10-18): the applicant's address was already bound to an iCIMS Central identity that the app
never verified, so every application stopped at a password nobody had. A plus-address (local+icims@gmail.com) is a new
address to iCIMS and reaches the same inbox. A profile names the platforms in `ats_email_aliases`
({"icims": "icims"}); each run of those platforms applies with that address.

ADP folds plus-addresses into the base mailbox: registration with local+adp@gmail.com answered "Another user account
already exists with the provided email address. Login using an alternate mobile number or an email address."
(Avalon, 2026-10-19). A domain alias ({"adp": "@googlemail.com"}) applies as local@googlemail.com, which Gmail delivers
to the same inbox and which is a distinct address to the platform.
"""

import re
from urllib.parse import urlsplit

GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def base_address(address):
    """The mailbox an address delivers to: a Gmail plus-address without its tag, googlemail.com as gmail.com."""
    local, at, domain = str(address or "").strip().lower().partition("@")
    if not at:
        return str(address or "").strip().lower()
    if domain in GMAIL_DOMAINS:
        local, domain = local.split("+", 1)[0], "gmail.com"
    return f"{local}@{domain}"


def mailbox_key(address):
    """The inbox an address reaches: for Gmail its tag, its dots and googlemail.com do not matter."""
    base = base_address(address)
    local, at, domain = base.partition("@")
    return f"{local.replace('.', '')}@{domain}" if at and domain == "gmail.com" else base


def same_mailbox(first, second):
    return bool(first and second) and mailbox_key(first) == mailbox_key(second)


def employer_alias_key(url):
    """The setting that holds one employer's alias tag ({"tag": ...}), keyed by the job's origin: an identity that
    employer bound to the profile address and that can never be signed in to is replaced there only."""
    p = urlsplit(str(url or ""))
    return f"email_alias:{p.scheme}://{(p.hostname or '').lower()}" if p.scheme and p.hostname else ""


EMPLOYER_ALIAS_TAG = "apply"

# A sign-up form that will not take the address itself. SuccessFactors and several custom forms validate the address
# with a pattern that rejects "+"; the alias then cannot work there, and the run must say so instead of retrying it.
ADDRESS_REFUSED = re.compile(
    r"(?:in|not\s+a\s+)?valid\s+e-?mail|e-?mail(?:\s+address)?[^.\n]{0,40}\b(?:invalid|not valid|format|special characters|"
    r"not allowed|not accepted|not supported)\b|(?:invalid|incorrect|malformed|unsupported)\s+e-?mail",
    re.I,
)


def alias_refusal(db, url, email, errors):
    """True when this employer's sign-up form refused the employer alias address in `errors`: the setting is marked, so
    later runs apply with the profile address again and no further identity is created. Only an address that carries the
    employer's alias tag can be the cause."""
    key = employer_alias_key(url)
    value = (db.get_setting(key) if key else None) or {}
    local = str(email or "").partition("@")[0]
    tag = str(value.get("tag") or "")
    if not tag or value.get("refused_at") or f"+{tag}" not in local:
        return False
    # "Invalid email address or password" is a refused sign-in, not a refused address.
    said = next((str(x) for x in errors or [] if ADDRESS_REFUSED.search(str(x)) and not re.search(r"password", str(x), re.I)), "")
    if not said:
        return False
    from .db import now

    db.set_setting(key, {**value, "refused_at": now(), "refusal": said[:160]})
    return True


def ats_address(address, ats, aliases):
    """The address to apply with on this platform, or the profile's own when it has no alias here."""
    tag = str((aliases or {}).get(ats or "") or "")
    local, at, domain = str(address or "").strip().partition("@")
    if not tag or not at or domain.lower() not in GMAIL_DOMAINS or "+" in local:
        return address
    if tag.startswith(".") and tag[1:].isdigit():
        # A dot alias ({"adp": ".4"}: alex.doe@gmail.com for alexdoe@gmail.com). Gmail delivers it to the same
        # inbox; ADP folds plus-addresses and googlemail.com into the address it already holds in another account
        # (2026-10-19, 2026-10-27), so only a dotted spelling is a new address there.
        at_char = int(tag[1:])
        if 0 < at_char < len(local) and "." not in local[at_char - 1 : at_char + 1]:
            return f"{local[:at_char]}.{local[at_char:]}@{domain}"
        return address
    if tag.startswith("@"):
        alias_domain = tag[1:].strip().lower()
        if alias_domain in GMAIL_DOMAINS and alias_domain != domain.lower():
            return f"{local}@{alias_domain}"
        return address
    return f"{local}+{tag}@{domain}"


def applying_email(email, ats, platform_aliases, employer_setting=None):
    """The address a run applies with: the profile's, with its platform's alias, or the employer's own alias (the
    setting Adapter.fresh_identity writes) on any platform. An employer whose sign-up form refused the plus-address
    (alias_refusal) gets the profile's address again."""
    aliases = dict(platform_aliases or {})
    if employer_setting and not employer_setting.get("refused_at"):
        aliases[ats] = employer_setting.get("tag") or aliases.get(ats, "")
    return ats_address(email, ats, aliases)
