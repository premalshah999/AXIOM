"""Review a batch (owner, 2026-11-01: "add a batch of jobs for review before applying, and once user accepts the batch, all applications
are queued, and have this setting as default, though keep the current auto apply settings as well").

The app gathers its best new matches into a batch. The person sees them on one screen, unticks any, and accepts: what stays ticked goes
in line as their own pick, each through the same door as a click on Queue (owner_apply.enqueue_many: reserved and over-limit jobs are
refused in words, duplicates are not queued twice), and the next batch is prepared. A job they unticked stays on the Job board and is
not offered again.

The mode is not a new setting. It is the dispatcher's own switch, `auto_apply`: off, the dispatcher starts only what was asked for, and
that is what reviewing is. A new install has it off, so a new install reviews; an existing install has its saved value, unchanged, and
nothing is written on upgrade. A candidate is a Job board row in the state "ready": screened by the dispatcher's own reading, ranked, not
in line, needing no approval.
"""

import threading
import uuid

from .board_view import board
from .db import now
from .jobboard import settings

KEY = "review_batch"  # the open batch: {"id", "at", "size", "job_ids"}
PASSED = "review_passed"  # {"ids": [...]}: jobs unticked in an accepted batch, newest last
KEEP_PASSED = 5000
PER_COMPANY = 3  # one employer's hundreds of postings don't fill a batch; the same number the Job board groups by
ROW = ("id", "title", "company", "location", "url", "ats", "posted_at", "dated", "source", "score", "line", "level")
_gathering = threading.Lock()  # two polls at once must not gather two batches: accepting one would read as "already accepted"


def mode(db):
    return "auto" if settings(db).auto_apply else "review"


def _passed(db):
    return set((db.get_setting(PASSED, {}) or {}).get("ids", []))


def _offered(row, passed):
    return row["state"] == "ready" and row["can_add"] and row["score"] is not None and row["id"] not in passed


def _gather(rows, size, passed, leave=frozenset()):
    """The best fits, newest first among equals, at most PER_COMPANY from one employer."""
    best = sorted((r for r in rows if _offered(r, passed) and r["id"] not in leave), key=lambda r: r["posted_at"] or "", reverse=True)
    best.sort(key=lambda r: -r["score"])  # a stable sort: the newest stays first inside each fit
    chosen, per = [], {}
    for row in best:
        name = row["company"].casefold()
        if per.get(name, 0) < PER_COMPANY:
            per[name] = per.get(name, 0) + 1
            chosen.append(row)
            if len(chosen) == size:
                break
    return chosen, len(best)


def current(service, leave=frozenset()):
    """The open batch and what surrounds it. Gathered when none is open or every job in it has gone elsewhere (`leave`: jobs just decided,
    which the board's last reading may not show yet)."""
    db = service.db
    data = board(service).ready(service)
    cfg = settings(db)
    said = {"mode": mode(db), "size": cfg.review_size, "at": now()}
    if data is None:
        return {**said, "planning": True, "id": "", "rows": [], "more": 0, "checking": 0, "searching": False}
    rows = {r["id"]: r for r in data["rows"]}
    passed = _passed(db)
    with _gathering:
        saved = db.get_setting(KEY, {}) or {}
        live = [rows[j] for j in saved.get("job_ids", []) if j in rows and j not in leave and _offered(rows[j], passed)]
        waiting = sum(1 for r in rows.values() if _offered(r, passed) and r["id"] not in leave)
        if not live or saved.get("size") != cfg.review_size:
            live, waiting = _gather(rows.values(), cfg.review_size, passed, leave)
            saved = {"id": uuid.uuid4().hex[:16], "at": now(), "size": cfg.review_size, "job_ids": [r["id"] for r in live]} if live else {}
            db.set_setting(KEY, saved)
    return {
        **said,
        "id": saved.get("id", ""),
        "rows": [{k: r[k] for k in ROW} for r in live],
        # What the next batches hold, and what isn't ready to be offered yet: the empty screen says which is true.
        "more": max(0, waiting - len(live)),
        "checking": sum(1 for r in rows.values() if r["state"] == "checking"),
        "searching": (data.get("scanning") or 0) > 0,
    }


def accept(service, batch_id, job_ids, changed):
    """Queue the ticked jobs of the open batch and prepare the next. Only jobs of this batch, so a stale screen can't queue what it no
    longer shows; a second press of Accept finds the batch gone and queues nothing."""
    from .owner_apply import enqueue_many

    db = service.db
    with _gathering:
        saved = db.get_setting(KEY, {}) or {}
        if not batch_id or saved.get("id") != batch_id:
            raise ValueError("This batch was already decided. Here is the next one.")
        db.set_setting(KEY, {})
    ticked = [j for j in saved["job_ids"] if j in set(job_ids)]
    out = enqueue_many(service, ticked) if ticked else {"added": [], "already": [], "refused": []}
    unticked = [j for j in saved["job_ids"] if j not in set(ticked)]
    if unticked:
        kept = (db.get_setting(PASSED, {}) or {}).get("ids", [])
        db.set_setting(PASSED, {"ids": [*kept, *unticked][-KEEP_PASSED:]})
    if out["added"]:
        changed(out["added"])
    db.event("", "review_batch", f"Accepted a review batch: {len(out['added'])} queued, {len(unticked)} passed on, {len(out['refused'])} refused", {"batch": batch_id})
    decided = set(saved["job_ids"])
    return {"queued": len(out["added"]), "already": len(out["already"]), "passed": len(unticked), "refused": out["refused"], "next": current(service, decided)}


def resize(service, size):
    """A new size gathers a new batch of that size; nothing in the old one was decided, so nothing is passed on."""
    db = service.db
    db.set_setting("job_board_settings", {**(db.get_setting("job_board_settings", {}) or {}), "review_size": size})
    return current(service)
