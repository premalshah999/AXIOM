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

GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def base_address(address):
    """The mailbox an address delivers to: a Gmail plus-address without its tag, googlemail.com as gmail.com."""
    local, at, domain = str(address or "").strip().lower().partition("@")
    if not at:
        return str(address or "").strip().lower()
    if domain in GMAIL_DOMAINS:
        local, domain = local.split("+", 1)[0], "gmail.com"
    return f"{local}@{domain}"


def ats_address(address, ats, aliases):
    """The address to apply with on this platform, or the profile's own when it has no alias here."""
    tag = str((aliases or {}).get(ats or "") or "")
    local, at, domain = str(address or "").strip().partition("@")
    if not tag or not at or domain.lower() not in GMAIL_DOMAINS or "+" in local:
        return address
    if tag.startswith("@"):
        alias_domain = tag[1:].strip().lower()
        if alias_domain in GMAIL_DOMAINS and alias_domain != domain.lower():
            return f"{local}@{alias_domain}"
        return address
    return f"{local}+{tag}@{domain}"
