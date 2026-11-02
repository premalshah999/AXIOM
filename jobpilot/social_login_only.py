"""A sign-in or sign-up page that offers only a third-party identity provider is a dead end, said once and plainly.

The owner's rule: no application is made with Microsoft, LinkedIn, Google, Facebook, Apple or GitHub logins. Microsoft's
Eightfold modal (2026-11-01) lists only those four under both "Sign in" and "Sign up"; its config lists an email form
nowhere in either ordering. "Create an account" opens a country and consent step whose Submit returns to the same
provider list, and the run clicked between the two until the recovery budget ran out (14 decisions, 48 s). Found by what
the page offers, never by employer.
"""

import re

PROVIDER = re.compile(
    r"^(?:(?:sign|log)[\s-]?(?:in|up)|register|continue)\s+(?:using|with)\s+(?:your\s+)?"
    r"(?:microsoft|linkedin|google|facebook|apple|github|twitter|x)\b",
    re.I,
)
SIGN_UP = re.compile(r"^(?:sign|log)[\s-]?up\b|^register\b", re.I)
OTHER_WAY = re.compile(r"e-?mail|password|username|user name|phone|mobile|passkey|one[- ]time|code", re.I)
SEEN_LIMIT = 3  # provider-only pages seen in one run before the loop is called a dead end


def provider_only(observation):
    """(providers, sign_up) when the page offers third-party providers and nothing else to sign in with, else None."""
    if not isinstance(observation, dict) or observation.get("fields"):
        return None
    labels = [" ".join(str(c.get("label") or "").split()) for c in observation.get("controls") or [] if isinstance(c, dict)]
    providers = [label for label in labels if PROVIDER.search(label)]
    if len(providers) < 2 or any(OTHER_WAY.search(label) for label in labels if label not in providers):
        return None
    return providers, any(SIGN_UP.search(label) for label in providers)


def stop_if_social_login_only(engine, observation):
    """Stop the run, nothing sent, when the only way in is a third-party login. True when it stopped."""
    if getattr(engine, "armed", False) or getattr(engine, "result", None):
        return False
    found = provider_only(observation)
    if not found:
        return False
    providers, sign_up = found
    seen = engine.__dict__["social_login_pages"] = getattr(engine, "social_login_pages", 0) + 1
    if not sign_up and seen < SEEN_LIMIT:
        return False
    engine.emit(
        "social_login_only",
        "The page offers only third-party logins; nothing was tried with them",
        {"providers": providers[:6], "seen": seen},
    )
    engine.result = {
        "state": "closed",
        "reason": "This employer's sign-in and sign-up pages offer only third-party logins (" + ", ".join(
            re.sub(r"^.*?\b(?:using|with)\s+", "", p, flags=re.I) for p in providers[:5]
        ) + "), and AXIOM does not use those; nothing was submitted",
        "blocker_type": "social_login_only",
        "recoverable": False,
    }
    return True
