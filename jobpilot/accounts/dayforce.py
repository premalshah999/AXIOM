"""Dayforce ID: its pages, outcomes and signals."""

from urllib.parse import parse_qs
from urllib.parse import urlsplit
import re


# Dayforce ID (dfid.dayforcehcm.com) is one sign-in for every Dayforce employer. Its /globalidentity/account/login path
# serves both registration and sign-in (the returnUrl's action decides), so the title and heading name the step.
DAYFORCE_ID_HOST = "dfid.dayforcehcm.com"


DAYFORCE_ID_PAGES = {
    "create your account": "register",
    "security questions": "security_questions",
    "enter verification code": "verification",
    "multifactor authentication": "mfa",
    "sign in": "sign_in",
}


# The account exists: its security questions were saved (verification_pending), a sign-in worked (authenticated), or
# Dayforce refused to register this email again (exists). Any other record (registering, a legacy signing_in) has no
# account behind it; signing in to it stalled every Dayforce ID run from 10-07 to 10-18.
DAYFORCE_ID_ACCOUNT = {"verification_pending", "authenticated", "exists"}


DAYFORCE_ID_SPECIAL = re.compile(r"""[!\\/"#$%&'()*+,\-.:;<>?@\[\]^_`{|}~]""")


DAYFORCE_ID_OUTCOME_SECONDS = 12


def dayforce_id_page(obs):
    """The Dayforce ID step on screen: register, security_questions, verification, mfa, sign_in, or "" elsewhere."""
    parts = urlsplit(obs.get("url", ""))
    if (parts.hostname or "").lower() != DAYFORCE_ID_HOST:
        return ""
    for name in [str(obs.get("title", "")).split("|")[0], *obs.get("headings", [])]:
        if page := DAYFORCE_ID_PAGES.get(re.sub(r"\s+", " ", str(name)).strip().lower()):
            return page
    path = parts.path.lower()
    for marker, page in (
        ("/securityquestions", "security_questions"),
        ("/users/verification", "verification"),
        ("/mfasetup", "mfa"),
        ("/account/register", "register"),
    ):
        if marker in path:
            return page
    if path.rstrip("/").endswith("/account/login"):
        back = parse_qs(parts.query).get("returnUrl", [""])[0]
        return "register" if parse_qs(urlsplit(back).query).get("action") == ["register"] else "sign_in"
    return ""


def dayforce_id_password_ok(password):
    """Dayforce ID's own password rules (its registration bundle): 8+ characters, an upper and a lower case letter,
    one special character from its list ("=" and space are not on it), and no character three times in a row."""
    return bool(
        len(password) >= 8
        and re.search(r"[A-Z]", password)
        and re.search(r"[a-z]", password)
        and DAYFORCE_ID_SPECIAL.search(password)
        and not re.search(r"(.)\1{2,}", password)
    )


def dayforce_id_outcome(step, page, info):
    """What Dayforce ID's Next did (its bundle, 2026-10-18): the next step, a refusal, a notice, or nothing yet."""
    messages = " ".join([*info.get("validation", []), *info.get("toasts", [])])
    if page and page != step and page in {"security_questions", "verification", "mfa"}:
        return "advanced"
    if re.search(r"registration failed", messages, re.I):
        return "registration_failed"
    if re.search(r"existing account", " ".join(info.get("modal", [])), re.I):
        return "existing_account"
    if re.search(r"request can.t be completed", messages, re.I):
        return "request_failed"
    if info.get("invalid") or re.search(r"required|must|do not match|unique|invalid", messages, re.I):
        return "validation"
    if any(entry.get("pending") for entry in info.get("captcha", [])) or info.get("challenge"):
        return "captcha"
    return "no_response"


# What a Dayforce ID page shows after Next, never values: its validation text (where "Registration failed" appears;
# the scanner's error selectors miss it), toasts, modal, invalid inputs and the invisible reCAPTCHA's state.
DAYFORCE_ID_SIGNALS = r"""() => {
  const shown = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const text = e => (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim();
  const all = s => [...document.querySelectorAll(s)].filter(shown).map(text).filter(Boolean).map(t => t.slice(0, 300));
  const hook = window.__jpTokenCaptcha;
  return {
    validation: all('[class*=evrValidationText]').slice(0, 4),
    toasts: all('.toast.show, [role=alert]').slice(0, 4),
    modal: all('[id^="modal_"], [role=dialog], dialog[open]').slice(0, 2),
    invalid: [...document.querySelectorAll('input[aria-invalid=true]')].filter(shown).map(i => i.id || i.type).slice(0, 8),
    captcha: (hook?.entries || []).slice(-4).map(x => ({invisible: !!x.invisible, pending: !!x.pending,
      delivered: !!x.delivered, generation: x.generation || 0})),
    sequence: hook?.sequence || 0,
  };
}"""
