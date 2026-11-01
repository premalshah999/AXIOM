"""The Terms of Use and Privacy Policy a person accepts before anything else, and when they did.

The texts are packaged beside the code (jobpilot/legal/, copies of docs/TERMS.md and docs/PRIVACY.md) so the app shows exactly what ships. The
acceptance is one setting, `legal_acceptance`: {version, accepted_at}. A new VERSION asks again; deleting everything removes the setting with
the rest, so a fresh start asks again too.
"""

from pathlib import Path

from .db import now

VERSION = "2026-11-01-beta"
KEY = "legal_acceptance"
FOLDER = Path(__file__).parent / "legal"


def text(name: str) -> str:
    return (FOLDER / f"{name.upper()}.md").read_text(encoding="utf-8")


def status(db) -> dict:
    saved = db.get_setting(KEY, {}) or {}
    return {
        "version": VERSION,
        "accepted": saved.get("version") == VERSION,
        "accepted_version": saved.get("version") or "",
        "accepted_at": saved.get("accepted_at") or "",
    }


def accept(db, version: str, adult: bool) -> dict:
    if version != VERSION:
        raise ValueError("These terms have changed. Reload the page and read the new version.")
    if not adult:
        raise ValueError("You must be 18 or older.")
    db.set_setting(KEY, {"version": VERSION, "accepted_at": now()})
    return status(db)
