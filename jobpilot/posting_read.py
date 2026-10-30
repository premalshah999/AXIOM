"""A run does not go past the posting page on text it never read.

A link-only job (the owner's Apply on several links, a pasted URL) is queued with an empty description, so the queue-time
eligibility check read nothing. Two postings that said "U.S. citizenship is required" were started for an applicant on
F-1 OPT, and the engine spent days on password resets for them (2026-10-30). At the start of such a run the posting is
read once over HTTP through the ordinary loader (discovery.enrich, which stores its text and title on the job), and once
more from the open page when that read found nothing (a page that renders in the browser). The same checks run on that
text as at queue time: the employer's eligibility wording (citizenship, clearance, sponsorship, PhD), and, for a job
whose title was unknown then, the fit filters the owner's own pick skips. A posting the applicant cannot meet closes
with the employer's words quoted in the reason.
"""

import asyncio

from .db import Job
from .eligibility import check_eligibility
from .features.discovery.policy import unfetched
from .schemas import Profile

READ_SECONDS = 20
STORED_MIN_CHARS = 200  # shorter page text is a label or a login box, not a posting
TITLE_MAX = 120


def _closed(reason):
    return {"state": "closed", "reason": reason[:900], "blocker_type": "ineligible"}


def refusal(db, job, run, text, title, untitled):
    """The closed result for a posting whose own words the applicant cannot meet, else None."""
    profile = Profile.model_validate(run["packet"]["profile"])
    check = check_eligibility(text, profile, title=title)
    if check.conflicts:
        words = "; ".join(f'{f.subject}: "{f.quote[:240]}"' for f in check.conflicts[:2])
        return _closed(f"The posting's own words rule this application out ({words}); nothing was submitted")
    if title and untitled:
        from .owner_apply import picked
        from .service.queueing import fit_blocker

        with db.session() as s:
            owner = picked(s, job["id"])
        if not owner and (blocker := fit_blocker(profile, db.get_setting("job_board_settings", {}) or {}, title)):
            return _closed(f'{blocker[0]} (the posting is titled "{title[:TITLE_MAX]}"); nothing was submitted')
    return None


def skipped(engine, job, run):
    return bool(
        job.get("demo") or engine.config.allow_private_urls or (run.get("packet") or {}).get("verification_only")
    )


def remember(job, text, title):
    """This run's own copy sees what the job now holds, for the answers and cover material written from it."""
    if unfetched(job.get("description")):
        job["description"] = text
    if not job.get("title") and title:
        job["title"] = title


async def refuse_unread_posting(engine, job, run):
    """At the start of a run: read an unfetched posting over HTTP and refuse it when its words rule the job out."""
    if skipped(engine, job, run) or not unfetched(job.get("description")):
        return None
    from .discovery import enrich

    try:
        await asyncio.wait_for(enrich(engine.db, engine.config, job["id"]), READ_SECONDS)
    except Exception:
        return None  # Nothing read: the open page is read next (refuse_unread_page); the platform checks stay as they were.
    with engine.db.session() as s:
        row = s.get(Job, job["id"])
        text, title = (row.description or "", row.title or "") if row else ("", "")
    if unfetched(text):
        return None
    untitled = not job.get("title")
    remember(job, text, title)
    return refusal(engine.db, job, run, text, title, untitled)


async def refuse_unread_page(engine, job, run):
    """Once the job's page is open: its text when the HTTP read found none, stored when it is a posting."""
    with engine.db.session() as s:
        row = s.get(Job, job["id"])
        if skipped(engine, job, run) or not row or not unfetched(row.description):
            return None
    obs = await engine.form.scan()
    text = str(obs.get("text") or "")
    if unfetched(text):
        return None
    title = next((str(h).strip() for h in obs.get("headings") or [] if 0 < len(str(h).strip()) <= TITLE_MAX), "")
    untitled = not job.get("title")
    if len(text) >= STORED_MIN_CHARS and not any(f["type"] == "password" for f in obs.get("fields", [])):
        with engine.db.exclusive() as s:
            row = s.get(Job, job["id"])
            if row and unfetched(row.description):
                row.description = text[:40000]
            if row and not row.title and title:
                row.title = title
        remember(job, text[:40000], title)
    return refusal(engine.db, job, run, text, title if len(text) >= STORED_MIN_CHARS else "", untitled)
