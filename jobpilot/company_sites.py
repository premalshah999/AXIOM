"""Company-site jobs (owner, 2026-11-01: "Custom applications are almost always failing, so move custom ats to a new section, and
don't add them to the known ats queue"; 31 confirmed of 399 jobs labelled custom).

A job is on a known platform when its label is one the app has a driver or a name for (ats.REGISTRY, the list Setup > System shows).
Anything labelled custom, a career-site front end that has not named the real platform (phenom), or a label the app does not
recognise is a company-site job. Those leave the Job board's main list for their own section and are never put in line by anything
automatic: not the autopilot, the review batch, ranking, or Select all on the main list. The owner can still send one on purpose,
from that section, and that choice is remembered per job (`company_site_ok:`) so the one door every start goes through
(service.queueing._admit) lets it by. A job relabelled with a known platform (platform_label) is on the main list again by itself.
"""

from sqlalchemy import select

from .ats import REGISTRY, VENDORS
from .db import Job, Run, Setting, now

KEY = "company_site_ok:"
ACTIVE = ("queued", "running", "submitting", "submission_unknown")
SENT = ("confirmed", "already_applied", "submission_unknown")
LINE = "Applications on company sites often fail. AXIOM sends one only when you choose it here."
WHY = "This job is on a company's own site, not a platform AXIOM knows. Send it from Job board > Company sites."
KNOWN = frozenset(a.id for a in REGISTRY) - VENDORS


def is_company_site(ats):
    """True for a label that is custom, empty, a career-site front end or not a platform the app knows."""
    from .pipeline_comparison import normalize_ats

    value = str(ats or "").strip().lower()
    return not value or normalize_ats(value) not in KNOWN


def allowed(session, job_id):
    """The owner chose to send this job from the Company sites section."""
    return session.get(Setting, KEY + job_id) is not None


def allow(db, job_ids):
    """Remember the owner's choice for each company-site job (a job on a known platform needs none). Returns the ids remembered."""
    wanted = list(dict.fromkeys(job_ids))
    done = []
    with db.exclusive() as s:
        for job in s.scalars(select(Job).where(Job.id.in_(wanted), Job.demo.is_(False))):
            if is_company_site(job.ats):
                if s.get(Setting, KEY + job.id) is None:
                    s.add(Setting(key=KEY + job.id, value={"at": now()}))
                done.append(job.id)
    return done


def leave_the_queue(db):
    """Startup: company-site jobs waiting in line (the owner's remembered Apply clicks) leave it. They are not deleted and keep their
    place on the Job board; a choice made in the Company sites section stays. Anything running, sent or being reconciled is left alone.
    Returns how many left."""
    from .owner_apply import KEY as WAITING

    left = 0
    with db.exclusive() as s:
        waiting = {key.removeprefix(WAITING): key for key in s.scalars(select(Setting.key).where(Setting.key.startswith(WAITING, autoescape=True)))}
        if not waiting:
            return 0
        busy = set(s.scalars(select(Run.job_id).where(Run.job_id.in_(list(waiting)), Run.state.in_(ACTIVE))))
        for job in s.scalars(select(Job).where(Job.id.in_(list(waiting)))):
            if job.id in busy or job.status in {*SENT, "running", "submitting", "queued"} or not is_company_site(job.ats):
                continue
            if allowed(s, job.id):
                continue
            s.delete(s.get(Setting, waiting[job.id]))
            left += 1
    return left
