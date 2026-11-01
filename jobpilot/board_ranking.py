"""Fit scores on the Job board without anyone pressing Start.

A new install found hundreds of postings and showed every one of them as "Checking fit" for good: ranking lived only in the
autopilot's tick, which does nothing until Start is pressed (and not at all with "Don't send anything yet"), so a friend who
finished setup saw no fit anywhere (clean-install run, 2026-11-01). Ranking is reading, not applying, so it runs whichever
mode is on, but on a new person's key it is bounded: the best candidates first, a few model calls a minute, and no more than
BOARD_RANK_LIMIT postings in all until the autopilot is the one asking for more. A model call ranks up to 24 postings.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select

BOARD_RANK_LIMIT = 120  # postings ranked for the board with the autopilot off: five model calls
PER_TICK = 24  # one model call a minute


def ranking_ready(db, config):
    """Why nothing is ranked now, or '' when it may be: the person has a key, a resume and the roles they want."""
    from .db import Resume
    from .provider_health import dispatch_gate

    control = db.get_setting("control", {}) or {}
    if control.get("paused"):
        return "stopped"
    if not getattr(config, "llm_api_key", ""):
        return "no key"
    if not (db.get_setting("profile", {}) or {}).get("target_roles"):
        return "no roles"
    with db.session() as s:
        if not s.scalar(select(func.count()).select_from(Resume).where(Resume.demo.is_(False))):
            return "no resume"
    if dispatch_gate(db, config).code == "provider_unavailable":
        return "model cooling down"
    return ""


def autopilot_ranks(db):
    """The autopilot's own tick ranks what it needs once the person has started it."""
    from .jobboard import settings

    cfg, control = settings(db), db.get_setting("control", {}) or {}
    return bool(cfg.enabled and cfg.auto_apply and control.get("auto_submit"))


def ranked_so_far(db):
    from .features.discovery.tables import DcClassification

    with db.session() as s:
        return s.scalar(select(func.count()).select_from(DcClassification).where(DcClassification.status.in_(("ok", "failed")))) or 0


def best_first(jobs, profile):
    """Unranked board jobs, the ones whose title is one of the person's roles (or its family) first, then the newest."""
    from .features.discovery.roles import role_fits

    roles = profile.get("target_roles") or []
    cache = {}

    def fits(title):
        if title not in cache:
            cache[title] = bool(role_fits(title, roles))
        return cache[title]

    return sorted(jobs, key=lambda j: (not fits(j.get("title") or ""), -(_stamp(j.get("posted_at")))))


def _stamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def candidates(db, jobs, profile, limit):
    """Up to `limit` ids to rank next: unranked, not already classified (or due a retry), best first."""
    from .features.discovery.classify import outcomes

    at = datetime.now(UTC).isoformat()
    fresh = [j for j in jobs if j.get("status") == "new" and j.get("score") is None and not j.get("attempted")]
    seen = outcomes(db, fresh, profile)
    waiting = [
        j for j in fresh
        if j["id"] not in seen or (seen[j["id"]]["status"] == "failed" and (seen[j["id"]]["retry_at"] or "") <= at)
    ]
    return [j["id"] for j in best_first(waiting, profile)[:limit]]


def due_ids(db, config, jobs):
    """The ids to hand to the ranking workflow this minute, or []."""
    if autopilot_ranks(db) or ranking_ready(db, config):
        return []
    room = BOARD_RANK_LIMIT - ranked_so_far(db)
    if room <= 0:
        return []
    return candidates(db, jobs, db.get_setting("profile", {}) or {}, min(PER_TICK, room))
