"""Bot walls that block a whole platform for this browser, not one employer.

SmartRecruiters 2026-10-18: after about ten oneclick-ui applications in ten minutes, DataDome answered every tenant with
its "Verification Required" slider (an iframe from geo.captcha-delivery.com over a blank page). Twelve jobs then re-ran
two to seven times each, every run paying the model to describe the blank page, and every request extended the flag.
The flag belongs to this browser's address on the platform, so the platform pauses (`ats_outage:<ats>`) while other
platforms continue, and the stopped run is not one of the job's starts.
"""

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

DATADOME_HOST = re.compile(r"(?:^|\.)captcha-delivery\.com$", re.I)
COOLDOWN_SECONDS = 45 * 60
# A prefix of application_budget.NOTHING_SENT: the wall answers before anything reaches the employer.
PAUSED = "A platform bot check paused"


def datadome_kind(urls):
    """'blocked', 'captcha' (the slider) or 'interstitial' (a device check that clears itself) for a DataDome frame."""
    for url in urls:
        parts = urlsplit(url or "")
        if not DATADOME_HOST.search(parts.hostname or ""):
            continue
        if re.search(r"(?:^|&)t=bv(?:&|$)", parts.query):
            return "blocked"
        return "interstitial" if parts.path.startswith("/interstitial") else "captcha"
    return None


def platform_name(ats):
    return {"smartrecruiters": "SmartRecruiters"}.get(ats, str(ats or "This platform").title())


def pause_platform(db, ats, kind, seconds=COOLDOWN_SECONDS):
    """Hold new starts and resumes on this platform; returns the run result."""
    name = platform_name(ats)
    until = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
    db.set_setting(
        "ats_outage:" + ats,
        {
            "until": until,
            "reason": f"{name} bot verification ({kind})",
            "code": "platform_bot_check",
            "message": f"{name} is showing this browser a bot check, so its applications wait until it clears; "
            "other platforms continue.",
        },
    )
    return {
        "state": "waiting_browser",
        "reason": f"{PAUSED} {name} applications for {seconds // 60} minutes: its DataDome check "
        f"({kind}) answered this browser before the form, so nothing was sent",
        "blocker_type": "site_transient",
        "recoverable": True,
        "recovery_cause": "site_temporary",
        "site_cooldown_seconds": seconds,
    }
