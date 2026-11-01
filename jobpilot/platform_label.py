"""The platform a job really runs on, written to the job (2026-11-01).

A run that found its platform behind a career site already wrote it to the job (BrowserEngine.persist_observed_ats), but
only after a run: of the jobs that ran in the last three weeks as career sites, 70 whose saved pages showed Greenhouse,
iCIMS, Workday, SuccessFactors, Oracle or Ashby kept the "custom" label (first-try success 33% against 60-68% on those
platforms' own drivers; 4.2 runs per job against 1.5), and every one of them waited in the single custom-site lane. The same
rule now also runs when a job is added (the page `enrich` fetches anyway, the address's own platform parameter) and once at
startup over the hand-offs earlier runs recorded.

A label written here is always an observation (observed_ats:<job>, "was" the career site), exactly as a run's: the next
run starts as the career site and finds the platform itself, so nothing keyed on the address, the account or the mail rule
moves, and no second account or resend can follow from a label change. Only a better label replaces a worse one.
"""

import re
import time

from sqlalchemy import select

from .ats import (
    LABEL_ONLY,
    REGISTRY,
    VENDORS,
    classify_embedded,
    custom_like,
    detect,
    entries_from_html,
    unsettled,
    url_platform,
)
from .db import Event, Job, Run

# A job in one of these has a run in flight, an application that may still be reconciled, or an archived draft: its label
# is not touched from outside the run. A confirmed or already-applied job may be relabelled (nothing runs for it again), so
# the scoreboard that groups jobs by platform reads true (52 confirmed career-site jobs had a platform on record).
BUSY = frozenset({"queued", "running", "submitting", "submission_unknown", "archived"})


def rank(name):
    return 0 if name == "custom" or name in VENDORS else 1 if name in LABEL_ONLY else 2


def record_observed_platform(db, job_id, target, emit=None, *, outside_run=False):
    """Write the platform observed behind a career site to the job row; True when the label changed.

    Only a label that had not found its platform is replaced, and only by a better one (custom < named platform < one with
    a driver). The address stays the career site's. The observation is remembered (observed_ats:<job>) because the label
    then comes from a page, not from the address: the next run starts as a career site and finds the platform itself.
    `outside_run` (a label written when a job is added or at startup, not by its own run) leaves jobs that are busy alone."""
    if not db or not job_id or unsettled(target) and target not in LABEL_ONLY:
        return False
    marker = "observed_ats:" + job_id
    seen = db.get_setting(marker, {})
    with db.session() as s:
        row = s.get(Job, job_id)
        if row is None or row.ats == target or row.demo or (outside_run and row.status in BUSY):
            return False
        observed = seen.get("ats") == row.ats
        was = seen.get("was") if observed else row.ats
        if rank(target) < rank(row.ats) or (rank(target) == rank(row.ats) and not observed):
            return False
        if outside_run and not custom_like(row.ats) and row.ats not in VENDORS:
            return False  # A label from the address (or from a run's observation) is the run's to correct, not ours.
        row.ats = target
    db.set_setting(marker, {"ats": target, "was": was, "at": time.time()})
    if emit:
        emit("ats_observed", f"This job's platform is {target}", {"was": was, "ats": target})
    return True


def page_platform(html, url):
    """(platform, how) for a fetched posting page, or (None, ""): where the fetch landed, what the page's own application
    markup shows (classify_embedded), then what the address says."""
    landed = detect(url).id
    if not unsettled(landed):
        return landed, "url"
    try:
        found, _, how = classify_embedded(entries_from_html(html, url))
    except Exception:  # noqa: BLE001 - unreadable markup is no evidence.
        found, how = None, ""
    if found and found not in VENDORS:
        return found, how
    if found := url_platform(url):
        return found, "address"
    return None, ""


def label_job_from_page(db, job_id, html, url):
    """A fetched posting page names its platform: write it. Returns the platform written, else None."""
    with db.session() as s:
        row = s.get(Job, job_id)
        if row is None or not (custom_like(row.ats) or row.ats in VENDORS):
            return None
        listing = row.url
    found, _ = page_platform(html, url)
    found = found or url_platform(listing)
    if found and record_observed_platform(db, job_id, found, outside_run=True):
        return found
    return None


HANDOFF = re.compile(r"(?:uses|moved to) ([a-z]+)\b")


def label_jobs_from_history(db):
    """Startup: career-site jobs take the platform an earlier run recorded for them (its hand-off event), and the
    platform their address names. Returns how many labels changed."""
    drivers = {a.id for a in REGISTRY} - LABEL_ONLY - VENDORS
    changed = 0
    with db.session() as s:
        rows = [
            (j.id, j.url)
            for j in s.scalars(select(Job).where(Job.demo.is_(False)))
            if (custom_like(j.ats) or j.ats in VENDORS) and j.status not in BUSY
        ]
    ids = {job_id for job_id, _ in rows}
    seen = {}
    if ids:
        with db.session() as s:
            events = s.execute(
                select(Run.job_id, Event.message, Event.created_at)
                .join(Run, Run.id == Event.run_id)
                .where(Event.kind == "handoff", Run.job_id.in_(ids))
                .order_by(Event.created_at)
            )
            for job_id, message, _ in events:
                if (m := HANDOFF.search(message or "")) and m[1] in drivers:
                    seen[job_id] = m[1]  # the latest hand-off wins
    for job_id, url in rows:
        target = seen.get(job_id) or url_platform(url)
        if target and record_observed_platform(db, job_id, target, outside_run=True):
            changed += 1
    return changed
