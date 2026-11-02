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


def _gather(rows, size, passed, leave=frozenset(), keep=()):
    """The best fits, newest first among equals, at most PER_COMPANY from one employer, after the rows of `keep`. Returns them and how
    many candidates there are in all."""
    best = sorted((r for r in rows if _offered(r, passed) and r["id"] not in leave), key=lambda r: r["posted_at"] or "", reverse=True)
    best.sort(key=lambda r: -r["score"])  # a stable sort: the newest stays first inside each fit
    chosen, per = list(keep), {}
    for row in chosen:
        per[row["company"].casefold()] = per.get(row["company"].casefold(), 0) + 1
    have = {row["id"] for row in chosen}
    for row in best:
        name = row["company"].casefold()
        if len(chosen) >= size:
            break
        if row["id"] not in have and per.get(name, 0) < PER_COMPANY:
            per[name] = per.get(name, 0) + 1
            chosen.append(row)
    return chosen, len(best)


def current(service, leave=frozenset()):
    """The open batch and what surrounds it. It stays the same batch until it is decided, and a batch short of its size takes on more as
    they arrive, at its end: on a new install the first search finds a handful, and that handful must not be the batch for good.
    `leave`: jobs just decided, which the board's last reading may not show yet."""
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
        same_size = saved.get("size") == cfg.review_size
        live = [rows[j] for j in saved.get("job_ids", []) if j in rows and j not in leave and _offered(rows[j], passed)] if same_size else []
        chosen, waiting = _gather(rows.values(), cfg.review_size, passed, leave, live[: cfg.review_size])
        ids = [r["id"] for r in chosen]
        if not ids:
            saved = {}
        elif not live:
            saved = {"id": uuid.uuid4().hex[:16], "at": now(), "size": cfg.review_size, "job_ids": ids}
        elif ids != saved.get("job_ids"):
            saved = {**saved, "job_ids": ids}
        if saved != (db.get_setting(KEY, {}) or {}):
            db.set_setting(KEY, saved)
    return {
        **said,
        "id": saved.get("id", ""),
        "rows": [{k: r[k] for k in ROW} for r in chosen],
        # What the next batches hold, and what isn't ready to be offered yet: the empty screen says which is true.
        "more": max(0, waiting - len(chosen)),
        "checking": sum(1 for r in rows.values() if r["state"] == "checking"),
        "searching": (data.get("scanning") or 0) > 0,
    }


def accept(service, batch_id, job_ids, unticked, changed):
    """Queue the ticked jobs of the open batch, remember what the person unticked, and prepare the next. Only jobs of this batch, so a
    stale screen can't queue what it no longer shows; a second press of Accept finds the batch gone and queues nothing. The screen says
    what it unticked: a job the batch took on since the screen last read is neither ticked nor passed on, and is offered next."""
    from .owner_apply import enqueue_many

    db = service.db
    with _gathering:
        saved = db.get_setting(KEY, {}) or {}
        if not batch_id or saved.get("id") != batch_id:
            raise ValueError("This batch was already decided. Here is the next one.")
        db.set_setting(KEY, {})
    ticked = [j for j in saved["job_ids"] if j in set(job_ids)]
    passing = [j for j in saved["job_ids"] if j in set(unticked) and j not in set(ticked)]
    out = enqueue_many(service, ticked) if ticked else {"added": [], "already": [], "refused": []}
    if passing:
        kept = (db.get_setting(PASSED, {}) or {}).get("ids", [])
        db.set_setting(PASSED, {"ids": [*kept, *passing][-KEEP_PASSED:]})
    if out["added"]:
        changed(out["added"])
    db.event("", "review_batch", f"Accepted a review batch: {len(out['added'])} queued, {len(passing)} passed on, {len(out['refused'])} refused", {"batch": batch_id})
    return {"queued": len(out["added"]), "already": len(out["already"]), "passed": len(passing), "refused": out["refused"], "next": current(service, {*ticked, *passing})}


def resize(service, size):
    """A new size gathers a new batch of that size; nothing in the old one was decided, so nothing is passed on."""
    db = service.db
    db.set_setting("job_board_settings", {**(db.get_setting("job_board_settings", {}) or {}), "review_size": size})
    return current(service)
