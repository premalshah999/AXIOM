"""An employer account that holds the applicant's phone number under another email address.

SuccessFactors, after the emailed passcode of a new profile: heading "Account Already Exists", "There is already an account
with the phone number ... Click Sign In to sign in to the account." Signing in with this applicant's email then fails (the
account is under another address), and the run ended as "The employer's password reset email did not arrive" after more
than twenty passcode and reset emails over several days (2026-10-30). No code, reset or sign-in with this email can reach
that account, and the engine never guesses other emails or phone numbers. The page marks the account record
"identity_conflict"; from then on the run stops on the first page it sees, before any Create, reset or code request, with
one owner question naming the employer. Setup > Accounts shows the record; a password the owner saves for it, or
forgetting it, ends the stop.
"""

import re
from urllib.parse import urlsplit

from .accounts.errors import AccountActionError
from .accounts.store import AccountStore
from .db import now

STATE = "identity_conflict"
PHONE_CONFLICT = re.compile(
    r"\balready (?:an )?account (?:with|for|using) (?:the |your |this )?(?:phone|mobile|telephone|cell)\b", re.I
)


def conflict_on_page(observation) -> bool:
    """The page says an account already holds the applicant's phone number."""
    seen = [str(observation.get("text") or ""), *map(str, observation.get("headings") or []),
            *map(str, observation.get("errors") or [])]
    return bool(PHONE_CONFLICT.search(" ".join(seen)))


def employer_name(job) -> str:
    company = re.sub(r"\s+", " ", str((job or {}).get("company") or "")).strip()
    return company or (urlsplit(str((job or {}).get("url") or "")).hostname or "this employer").removeprefix("www.")


def question(job) -> str:
    return (
        f"{employer_name(job)} already has an account that holds your phone number under a different email address, so "
        "this email cannot sign in to it or create its own. Which email address is that account under? No more passcodes "
        "or password resets are requested for it until you answer."
    )


def result(job) -> dict:
    """The run's outcome: waiting on the owner, never retried automatically (retried_automatically; PERSIST_SKIP)."""
    text = question(job)
    return {
        "state": "waiting_browser",
        "reason": "Account identity conflict: " + text,
        "reviews": [{"question": text, "options": [], "key": "manual", "reason": "account identity conflict"}],
        "blocker_type": "account",
        "recovery_cause": "account_identity_conflict",
        "recoverable": False,
    }


def store_for(engine):
    flow = getattr(engine, "account_flow", None)
    if flow is not None and getattr(flow, "store", None) is not None:
        return flow.store
    service = getattr(engine, "service", None)
    if service is None or getattr(service, "config", None) is None or getattr(service, "db", None) is None:
        return None
    profile = getattr(getattr(getattr(engine, "form", None), "resolver", None), "profile", None)
    email = service.config.account_email or getattr(profile, "email", "")
    return AccountStore(service, email, (getattr(engine, "job", None) or {}).get("url", "")) if email else None


async def stop_if_identity_conflict(engine, observation) -> bool:
    """Stop the run on the page that shows the conflict, and on every page of an account already marked with it."""
    job = getattr(engine, "job", None) or {}
    if getattr(engine, "armed", False) or not observation or job.get("demo"):
        return False
    store = store_for(engine)
    if store is None:
        return False
    url = observation.get("url") or getattr(getattr(engine, "page", None), "url", "") or job.get("url", "")
    record = store.record(url) or {}
    marked = record.get("state") == STATE
    if not marked:
        if not conflict_on_page(observation) or record.get("confirmed_at"):
            return False  # an account this email signed in to is not under another address
        try:
            store.save(url, store.get(url) or store.shared(), STATE, identity_conflict_at=now())
        except AccountActionError:
            pass  # the tenant is not observed yet: the run still stops, the next one meets the page again
        engine.emit(
            "account",
            "An account under another email holds this applicant's phone number; no further codes or resets are requested",
            {"host": urlsplit(url).hostname or ""},
        )
    engine.result = result(job)
    return True
