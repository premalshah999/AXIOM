"""One-time grandfathering of preferences that used to be built into the code.

Several applicant preferences (the fallback major, the "how did you hear" order, the salary, travel and
employment-type statements, early-career-only discovery, resubmitting unconfirmed submissions) were once fixed in
the code and are now per-profile settings that default to nothing. An installation that already has application
history was running with the old values, so the first start after the change writes those values into its own
stored profile and settings, only where a key is absent. A fresh installation keeps the neutral defaults. Either
way the run is recorded, so a new installation that later gains runs is never given someone else's preferences.
"""

from sqlalchemy import func, select

from .db import Run, Setting, now

MARKER = "migrated:grandfather_preferences_v1"

# The values the code applied before they became settings.
ANSWER_RULES = {
    "major_fallback": "Computer Science",
    "source_order": [
        "linkedin",
        "company website",
        "ziprecruiter",
        "indeed",
        "internet search",
        "job board",
        "glassdoor",
        "social media",
    ],
    "source_any_channel": True,
    "salary_stance": "Negotiable",
    "travel_willingness": "open to travel",
    "employment_type_sought": "full-time early-career employment",
    "join_talent_communities": False,
}
PROFILE = {"include_internships": False}
BOARD = {"early_career_only": True}
UNKNOWN_RESUBMIT = {"enabled": True}


def fill_absent(target: dict, defaults: dict) -> list:
    """Add the keys target lacks; return their names."""
    added = [k for k in defaults if k not in target]
    for key in added:
        target[key] = defaults[key]
    return added


def grandfather_preferences(db) -> dict:
    """Run once; idempotent. Returns what was written (empty for a fresh installation or a repeat)."""
    with db.exclusive() as s:
        if s.get(Setting, MARKER):
            return {}
        history = bool(s.scalar(select(func.count()).select_from(Run)))
        written: dict = {}
        if history:
            profile = s.get(Setting, "profile")
            if profile:
                value = dict(profile.value)
                rules = dict(value.get("answer_rules") or {})
                written["answer_rules"] = fill_absent(rules, ANSWER_RULES)
                value["answer_rules"] = rules
                written["profile"] = fill_absent(value, PROFILE)
                profile.value = value
            for key, defaults, name in (
                ("job_board_settings", BOARD, "job_board"),
                ("unknown_resubmit_policy", UNKNOWN_RESUBMIT, "unknown_resubmit"),
            ):
                row = s.get(Setting, key)
                value = dict(row.value) if row else {}
                written[name] = fill_absent(value, defaults)
                if row:
                    row.value = value
                else:
                    s.add(Setting(key=key, value=value))
        s.add(Setting(key=MARKER, value={"at": now(), "history": history, "written": written}))
        return written
