"""What came back after an application: a decline, an interview request or an assessment, read from the mail the app already
reads for codes and receipts. Whether an application led anywhere is the only outcome that matters, and until now nothing measured
it: 3,000 confirmed applications and not one number for what followed them (review of 2026-11-01).

Deterministic rules, no model and no extra spend. Reading is read-only (BODY.PEEK on a read-only folder, as for receipts), and no
mail text is kept: only which job, which kind and when. It counts a message only when its sender passes DMARC, it names an employer
the applicant applied to from that employer's or its ATS's address, and it arrived after the application. An acknowledgement is not
a reply, and neither is an acknowledgement's "we'll contact you to schedule an interview if..."; rules favour missing a reply over
inventing one, so the number is a floor: English only, and a recruiter writing from a personal address is not seen.
"""

import asyncio
import re
import time
from datetime import datetime
from email.utils import getaddresses

from sqlalchemy import func, select

from .db import Job, Mailbox, Run
from .mail import authenticated_sender, digest
from .receipts import CHALLENGE, UNFINISHED, employer_named, observed_words, parse_content, receipt_messages, title_in, trusted_sender

KEY = "application_replies"
CURSOR = "reply_mail_page:"
# A first sweep reads back to the first application, bounded per pass so it never competes with a live code search.
EVERY_SECONDS = 180
MAX_MESSAGES = 60
SECONDS = 15
# Once caught up, a look every 15 minutes covers the last day: employers reply over days, not seconds.
CAUGHT_UP_SECONDS = 900
OVERLAP_SECONDS = 86400

# Every phrase below carries one of these words, so the mailbox search asks Gmail for them only (test_replies holds them together).
# Gmail's own promotions and social tabs hold newsletters and network invitations, not an employer's answer.
SEARCH = (
    '(interview OR "phone screen" OR unfortunately OR regret OR selected OR shortlisted OR unsuccessful OR proceed OR proceeding OR '
    'progressing OR "moving forward" OR "move forward" OR "moving ahead" OR "other candidates" OR "other applicants" OR pursue OR '
    'filled OR "no longer" OR "been closed" OR "unable to offer" OR assessment OR hirevue OR codility OR hackerrank OR codesignal OR '
    '"coding challenge" OR availability OR invite OR invitation OR "like to" OR "love to") -category:promotions -category:social'
)

# In the sentence around a phrase: it depends on something, so it isn't an answer yet. "May" the month is not a condition.
CONDITIONAL = re.compile(
    r"\b(?:if|should|might|could|would you|whether|unless|in the event|in case|only (?:candidates|applicants|those)|"
    r"(?:those|candidates?|applicants?) (?:who are |who have been )?(?:selected|chosen|shortlisted)|"
    r"selected (?:candidates|applicants)|qualified (?:candidates|applicants))\b|\bmay(?! \d)\b"
)
# A promise about later ("a recruiter will contact you to schedule an interview") is what an acknowledgement says.
PROMISE = re.compile(
    r"\b(?:will|we'll|you'll|they'll)\b.{0,40}\b(?:contact|reach out|be in touch|get back|follow up|call|email|review|invited?)\b"
)
# A newsletter's or a survey's invitation, from an employer's real address.
NOT_AN_ANSWER = re.compile(r"talent (?:community|network|pool|pipeline)|job alert|newsletter|survey|feedback|webinar|career (?:fair|event)|open house|join our")

DECLINED = re.compile(
    r"\bregret to (?:inform|tell|advise|let)\b|"
    r"\bnot (?:be )?(?:moving|proceeding|going|progressing)\b.{0,30}\b(?:forward|further|ahead|your application|your candidacy)\b|"
    r"\b(?:won't|will not|wouldn't) (?:be )?(?:moving|proceeding|progressing|going) (?:forward|further|ahead|with your|your)\b|"
    r"\bunable to (?:offer you|move forward|proceed with your)\b|"
    r"\bnot (?:been |be )?(?:selected|chosen|shortlisted)\b|"
    r"\b(?:application|candidacy) (?:was|has been|is) (?:not successful|unsuccessful)\b|\bunsuccessful (?:on this occasion|at this time)\b|"
    r"\bno longer (?:under consideration|being considered|considering you|in consideration|moving forward)\b|"
    r"\bdecided (?:not to (?:proceed|move|progress|pursue|continue|advance)|to (?:pursue|move forward with|proceed with|go with|move ahead with|continue with) "
    r"(?:other|another|a different|different|more))\b|"
    r"\b(?:moving|move|proceeding|proceed|going|go)(?: forward| ahead)? with (?:other|another|different) (?:candidates?|applicants?|individuals?|qualified)\b|"
    r"\bpursu(?:e|ing) (?:other|another|different) (?:candidates?|applicants?)\b|"
    r"\b(?:position|role|job|opening|requisition) (?:has been|is now|was|has) (?:filled|closed|cancelled|canceled)\b"
)
ASSESSMENT = re.compile(
    r"\b(?:complete|take|finish|start|begin)\b.{0,50}\b(?:assessment|coding (?:challenge|test|exercise)|online test|technical (?:challenge|test|exercise)|"
    r"skills test|video interview)\b|"
    r"\b(?:invite|inviting|invitation)\b.{0,60}\b(?:assessment|coding (?:challenge|test)|online test|hirevue|codility|hackerrank|codesignal)\b|"
    r"\b(?:hirevue|codility|hackerrank|codesignal)\b.{0,60}\b(?:assessment|challenge|test|interview)\b"
)
INTERVIEW = re.compile(
    r"\b(?:invite|inviting|invitation)\b.{0,60}\b(?:interview|phone screen|phone call|video call|conversation|chat|call)\b|"
    r"\b(?:would|'d) (?:like|love) to\b.{0,40}\b(?:schedule|set up|arrange|invite|interview|speak|talk|chat|meet|connect|move you|move forward with you|"
    r"learn more about you)\b|"
    r"\b(?:schedule|book|set up|arrange)\b.{0,30}\b(?:your|an|a)\b.{0,20}\b(?:interview|phone screen|recruiter (?:call|screen)|intro(?:ductory)? call)\b|"
    r"\b(?:share|send|provide|let us know|what is|what's) your availability\b|"
    r"\b(?:selected|chosen|shortlisted|advanced|moved forward)\b.{0,30}\b(?:for|to)\b.{0,30}\b(?:interview|next (?:round|stage|step)|phone screen)\b|"
    r"\bmove you (?:forward|to the next)\b"
)
# A subject that says it outright. "How to ace your interview" says nothing about this applicant.
INTERVIEW_SUBJECT = re.compile(
    r"\binterview (?:invitation|invite|request|confirmation|scheduled?|availability|details)\b|\b(?:invitation|invite|request) (?:to|for) (?:an? )?interview\b|"
    r"\bphone screen\b|^(?:re: |fwd: )?interview (?:with|for|at)\b"
)
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
# The strongest reading wins by kind, in this order: a decline that also says "we'd love to keep in touch" is a decline.
KINDS = ("declined", "assessment", "interview")


def _plain(text):
    return re.sub(r"\s+", " ", text.casefold().replace("’", "'")).strip()


def _answers(pattern, sentence, promise=False):
    """Whether a sentence states this kind: a match with no condition before it or inside it ("candidates selected for an
    interview"). An invitation also fails on one after it ("to schedule an interview if your experience fits") or a promise of
    later; a decline's sentence can run on into an unpunctuated footer ("...your application If you have questions"), so what
    follows a decline says nothing about it."""
    for match in pattern.finditer(sentence):
        before, after = sentence[max(0, match.start() - 80) : match.start()], sentence[match.end() : match.end() + 60]
        if CONDITIONAL.search(sentence[max(0, match.start() - 80) : match.end()]):
            continue
        if promise and (CONDITIONAL.search(after) or PROMISE.search(before) or NOT_AN_ANSWER.search(sentence)):
            continue
        return True
    return False


def kind_of(subject, text):
    """What a message says about an application: "declined", "assessment", "interview", or None for anything else (an
    acknowledgement, a code, a newsletter). Only ever given mail from an employer; it reads the wording, not who sent it."""
    subject = _plain(subject)
    sentences = [_plain(s) for s in SENTENCE.split(text or "") if s.strip()]
    sentences.append(subject)
    if any(_answers(DECLINED, s) for s in sentences):
        return "declined"
    if any(_answers(ASSESSMENT, s, promise=True) for s in sentences):
        return "assessment"
    if INTERVIEW_SUBJECT.search(subject) or any(_answers(INTERVIEW, s, promise=True) for s in sentences):
        return "interview"
    return None


def _facts(message):
    """The reading of one message, or None when it can't be a reply: the same authenticated sender the receipts ask for, then its
    wording. Parsed with no shared cache, in the worker thread."""
    headers = {}
    for h in message.get("payload", {}).get("headers", []):
        headers.setdefault(h["name"].lower(), []).append(h["value"])
    sender = getaddresses(headers.get("from", []))
    if len(sender) != 1:
        return None
    domain = sender[0][1].rpartition("@")[2].lower()
    auth = next((a for a in headers.get("authentication-results", []) if re.match(r"\s*mx\.google\.com\s*;", a, re.I)), "")
    if not authenticated_sender(auth, domain):
        return None
    text, links = parse_content(message)
    subject = headers.get("subject", [""])[0]
    body = " ".join([subject, *text]).casefold()
    if CHALLENGE.search(body) or UNFINISHED.search(body):
        return None
    kind = kind_of(subject, "\n".join(text))
    if kind is None:
        return None
    return {
        "kind": kind,
        "domain": domain,
        "address": sender[0][1],
        "subject": subject,
        "received": int(message.get("internalDate", "0")) / 1000,
        "compact_body": re.sub(r"[^a-z0-9]", "", body),
        "observed": observed_words(body),
        "links": {link.rstrip("/") for link in links},
    }


def reply_of(message, applied):
    """{"job", "kind", "at"} when this message answers an application in `applied` ({id, company, title, url, ats, at}), else None.
    The job it names by link or title, or else the latest application to that employer before the message arrived."""
    facts = _facts(message)
    if facts is None:
        return None
    best, rank = None, None
    for job in applied:
        if job["at"] > facts["received"] or not job["company"]:
            continue
        if not employer_named(job, facts) or not trusted_sender(facts["domain"], job):
            continue
        named_job = bool(job["url"] and job["url"].rstrip("/") in facts["links"]) or title_in(job["title"], facts["observed"])
        # Dayforce's notices never name the employer: only the exact role says whose this is.
        if facts["domain"] in {"dayforce.com", "dayforcehcm.com"} and not named_job:
            continue
        if rank is None or (named_job, job["at"]) > rank:
            best, rank = job, (named_job, job["at"])
    return {"job": best["id"], "kind": facts["kind"], "at": int(facts["received"])} if best else None


def applied_jobs(session):
    """Every real job with a settled confirmed run and when it was first sent (one definition with the Applications list)."""
    from .api.pipeline import settled_confirmation

    rows = session.execute(
        select(Job.id, Job.company, Job.title, Job.url, Job.ats, func.min(Run.created_at))
        .join(Run, Run.job_id == Job.id)
        .where(Job.demo.is_(False), settled_confirmation())
        .group_by(Job.id, Job.company, Job.title, Job.url, Job.ats)
    )
    return [
        {"id": i, "company": c or "", "title": t or "", "url": u or "", "ats": a or "", "at": datetime.fromisoformat(at).timestamp()}
        for i, c, t, u, a, at in rows
    ]


def read_replies(messages, applied):
    """{message digest: reply} for a batch of messages. CPU only: called in a worker thread, off the workers' event loop."""
    found = {}
    for message in messages:
        if reply := reply_of(message, applied):
            found[digest(message["id"])] = reply
    return found


async def check_replies(mail):
    """One bounded pass over each connected mailbox: a resumable sweep back to the first application, then a daily window."""
    saved = mail.db.get_setting(KEY, {})
    if saved.get("complete") and time.time() - saved.get("checked_at", 0) < CAUGHT_UP_SECONDS:
        return
    with mail.db.session() as s:
        boxes = list(s.scalars(select(Mailbox.id).where(Mailbox.state == "connected")))
        applied = applied_jobs(s)
    if not boxes or not applied:
        return
    found, read_to = dict(saved.get("found", {})), dict(saved.get("read_to", {}))
    first = min(job["at"] for job in applied)
    for box in boxes:
        report = {}
        # The floor holds until a sweep finishes, so the search and its saved cursor stay the same from pass to pass.
        since = read_to.get(box, first)
        messages = await receipt_messages(
            mail, box, (), since, max_messages=MAX_MESSAGES, seconds=SECONDS, report=report, search=SEARCH, cursor=CURSOR
        )
        found.update(await asyncio.to_thread(read_replies, messages, applied))
        if report.get("done"):
            read_to[box] = time.time() - OVERLAP_SECONDS
    mail.db.set_setting(
        KEY, {"found": found, "read_to": read_to, "complete": all(b in read_to for b in boxes), "checked_at": time.time()}
    )


def summary(db):
    """The Applications page's figure. A job counts once however many messages came; `replies` is the jobs with any answer, and
    the kinds beside it can overlap (an assessment, then a decline). `complete`: the first sweep has reached the first application."""
    from .api.pipeline import confirmed_jobs

    saved = db.get_setting(KEY, {})
    with db.session() as s:
        connected = s.scalar(select(Mailbox.id).where(Mailbox.state == "connected").limit(1)) is not None
        confirmed = set(s.scalars(confirmed_jobs()))
    kinds = {}
    for reply in saved.get("found", {}).values():
        if reply["job"] in confirmed:
            kinds.setdefault(reply["job"], set()).add(reply["kind"])
    return {
        "connected": connected,
        "complete": bool(saved.get("complete")),
        "replies": len(kinds),
        **{kind: sum(kind in k for k in kinds.values()) for kind in KINDS},
        "job_ids": sorted(kinds),
    }
