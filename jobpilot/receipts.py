"""Match authenticated acknowledgement emails to an already attempted application."""

import asyncio
import base64
import re
import time
import unicodedata
from datetime import datetime, timedelta, UTC
from email.utils import getaddresses
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from sqlalchemy import and_, case, func, or_, select

from .db import Event, Job, Mailbox, MailChallenge, Run, record
from .mail import authenticated_sender, digest
from .mail_errors import MailCursorUpgrade, MailMessageError, MailOperationError, MailTransportError


# Senders that deliver ATS acknowledgements; employers' own domains are accepted by identity.
RECEIPT_SENDERS = [
    "greenhouse.io",
    "greenhouse-mail.io",
    "lever.co",
    "ashbyhq.com",
    "smartrecruiters.com",
    "workablemail.com",
    "workable.com",
    "bamboohr.com",
    "jobvite.com",
    "myworkday.com",
    "workday.com",
    "icims.com",
    "oraclecloud.com",
    "oracle.com",
    "taleo.net",
    "successfactors.com",
    "successfactors.eu",
    "sapsf.com",
    "eightfold.ai",
    "avature.net",
    "phenompeople.com",
    "rippling.com",
    "breezy.hr",
    "recruitee.com",
    "dayforcehcm.com",
    "dayforce.com",
]


def trusted_sender(domain, job):
    from .mail import sender_allowed, tenant_tokens

    if sender_allowed(
        domain, RECEIPT_SENDERS, tenant_tokens(job.get("url", ""), job.get("company", "")), True
    ):
        return True
    # The employer's own domain, by exact name: "zip.co" for Zip Co ("zip"), "ramp.com" for Ramp.
    # Prefix look-alikes ("ziprecruiter.com", "stripe-careers-notify.com") are not the employer.
    from .mail import employer_label

    name = employer_label(domain)
    words = [w for w in re.split(r"[^a-z0-9]+", (job.get("company") or "").casefold()) if w]
    compact = "".join(words)
    first = words[0] if words else ""
    # A two-letter label only as the whole company name: "ey.com" for EY, never "ge.com" for GE Vernova.
    return (len(name) == 2 and name == compact) or (
        len(name) >= 3
        and name
        in {
            compact,
            first,
            compact.removesuffix("co"),
            compact.removesuffix("inc"),
        }
    )


def title_matches(title, body):
    # Ignore presentation punctuation and whitespace while preserving every word,
    # including level suffixes: Engineer I must never match Engineer II.
    def words(text):
        return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()

    # Corning's SuccessFactors subject runs the title into the next word:
    # "Application for Data Engineerrequisition number 74967."
    body = re.sub(r"(?<=[a-z])(?=requisition\b)", " ", body, flags=re.I)
    expected, observed = words(title), words(body)
    return bool(expected and f" {expected} " in f" {observed} ")


def receipt_employer_key(session, job):
    """Read-only identity: display punctuation/suffixes cannot split competitors."""
    from .features.knowledge.employers import name_aliases, resolve

    identity = resolve(session, job.get("company", ""), job.get("url", ""), create=False)
    if identity:
        return "employer:" + identity.id
    names = name_aliases(job.get("company", ""))
    if names:
        return "name:" + names[-1]
    from .admission import employer_partition

    return "origin:" + employer_partition(job)


# Parsed (text, links) per message. Messages are immutable, and the receipt poller compares each one with
# every candidate run at that employer: re-parsing its HTML for every pair on the shared event loop kept
# the application workers' loop busy (py-spy, 2026-10-15: /healthz 10 s with two applications running).
_CONTENT: dict = {}
_CONTENT_LIMIT = 4000


def message_content(message):
    key = (message.get("id"), message.get("internalDate")) if message.get("id") else None
    if key in _CONTENT:
        return _CONTENT[key]
    text, links = [], []
    size = 0

    def walk(part, depth=0):
        nonlocal size
        if depth > 15 or size > 256000:
            return
        if part.get("mimeType") in {"text/plain", "text/html"}:
            data = part.get("body", {}).get("data", "")
            if len(data) < 350000:
                raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
                size += len(raw)
                if size <= 256000:
                    soup = BeautifulSoup(raw, "html.parser")
                    text.append(soup.get_text(" ", strip=True))
                    links.extend(a.get("href", "") for a in soup.find_all("a", href=True))
                    links.extend(re.findall(r"https?://[^\s<>\"\']+", raw))
        for p in part.get("parts", [])[:30]:
            walk(p, depth + 1)

    walk(message.get("payload", {}))
    content = (tuple(text), tuple(links))
    if key is not None:
        if len(_CONTENT) >= _CONTENT_LIMIT:
            _CONTENT.pop(next(iter(_CONTENT)))
        _CONTENT[key] = content
    return content


def acknowledgement(message, job, run, allow_missing_title=False):
    headers = {}
    for h in message.get("payload", {}).get("headers", []):
        headers.setdefault(h["name"].lower(), []).append(h["value"])
    sender = getaddresses(headers.get("from", []))
    if len(sender) != 1:
        return None
    domain = sender[0][1].rpartition("@")[2].lower()
    auth = next(
        (
            a
            for a in headers.get("authentication-results", [])
            if re.match(r"\s*mx\.google\.com\s*;", a, re.I)
        ),
        "",
    )
    if not authenticated_sender(auth, domain):
        return None
    trusted = trusted_sender(domain, job)
    recipient = run["packet"]["profile"]["email"].lower()
    if recipient not in {
        a.lower() for _, a in getaddresses(headers.get("to", []) + headers.get("delivered-to", []))
    }:
        return None
    if int(message.get("internalDate", "0")) / 1000 < datetime.fromisoformat(run["created_at"]).timestamp():
        return None
    text, links = (list(part) for part in message_content(message))
    subject = headers.get("subject", [""])[0]
    body = " ".join([subject, *text]).casefold()
    # Challenge messages stay exclusively in the verification flow. Inspect links
    # before stripping HTML; even a bland anchor label can carry a reset secret.
    if re.search(
        r"security code|verification code|email verification|email confirmation|verify your email|confirm your email|"
        r"resubmit your application|(?:activate|verify|confirm) (?:your )?(?:account|profile)|"
        r"reset (?:your )?password|password reset|one[ -]?time (?:password|passcode|code)|"
        r"(?:login|sign[ -]?in|authentication) (?:code|link)|(?:your|the) (?:code|pin) is|\botp\b",
        body,
    ):
        return None
    for link in links:
        parts = urlsplit(link)
        if re.search(r"(?:^|/)(?:verify|activate|reset|confirm)(?:[/_.-]|$)", parts.path, re.I) or (
            {k.casefold() for k in parse_qs(parts.query)}
            & {"token", "code", "ticket", "access_token", "verificationcode"}
        ):
            return None
    company = re.sub(r"[^a-z0-9]", "", job["company"].casefold())
    compact_body = re.sub(r"[^a-z0-9]", "", body)
    first = job["company"].split()[0].casefold() if job["company"].split() else ""
    # Workday can omit the employer name from the message text. Its authenticated
    # tenant sender (snapchat@myworkday.com for snapchat.wd1.myworkdayjobs.com)
    # is a precise match when the title and receipt wording also match.
    host = (urlsplit(job.get("url", "")).hostname or "").casefold()
    tenant = host.split(".")[0]
    sender_local = sender[0][1].partition("@")[0].split("+")[0].casefold()
    if domain == "myworkday.com" and host.endswith(".myworkdayjobs.com") and sender_local != tenant:
        return None
    workday_tenant_match = (
        domain == "myworkday.com"
        and host.endswith(".myworkdayjobs.com")
        and len(tenant) >= 4
        and not re.fullmatch(r"wd\d+", tenant)
        and sender_local == tenant
    )
    # Dayforce's applicant notifications never name the employer ("We have received your application
    # for the position - Data Engineer II", notify@dayforce.com). The exact title, the run's own window and
    # the one-possible-job rule below identify it.
    dayforce_notice = domain in {"dayforce.com", "dayforcehcm.com"} and job.get("ats") == "dayforce"
    company_match = (
        dayforce_notice
        or company in compact_body
        or (len(first) >= 3 and bool(re.search(r"\bto\s+" + re.escape(first) + r"\s*[!.]?$", subject, re.I)))
        or workday_tenant_match
    )
    if not company_match:
        from .features.knowledge.employers import name_aliases

        company_match = any(
            len(re.sub(r"[^a-z0-9]", "", alias)) >= 4 and title_matches(alias, body)
            for alias in name_aliases(job["company"])
        )
    job_url_match = bool(job.get("url") and job["url"].rstrip("/") in {link.rstrip("/") for link in links})
    role_match = title_matches(job["title"], body)
    if (
        not job["company"]
        or not job["title"]
        or not company_match
        or (not role_match and not job_url_match and not allow_missing_title)
    ):
        return None
    if not re.search(
        r"thank (?:you|you!) for (?:applying|your application)|thanks for applying|"
        r"(?:received|reviewing) your application|application.{0,60}(?:received|submitted|landed successfully|is in good hands)|"
        r"(?:confirms?|acknowledges?)\s+(?:our\s+)?receipt of your (?:application|resume)|"
        r"\breceipt of your (?:application|resume)\b|we(?:'ve| have)? received your (?:application|resume)",
        body,
    ):
        return None
    return {
        "message_id": message["id"],
        # Authenticated is not enough to confirm a submission: the sender must also be this
        # employer or an ATS that sends on its behalf.
        "sender_trusted": trusted,
        "authenticated": True,
        "attribution_basis": "job_url" if job_url_match else "title" if role_match else "submit_window",
        "job_id": job.get("id", ""),
        "run_id": run.get("id", ""),
        "from": sender[0][1],
        "subject": subject,
        "received_at": datetime.fromtimestamp(int(message["internalDate"]) / 1000, UTC).isoformat(),
    }


def verification_pending(mail, mailbox_id):
    with mail.db.session() as s:
        return (
            s.scalar(
                select(MailChallenge.id)
                .where(
                    MailChallenge.mailbox_id == mailbox_id,
                    MailChallenge.state == "pending",
                    MailChallenge.expires > time.time(),
                )
                .limit(1)
            )
            is not None
        )


async def receipt_messages(mail, mailbox_id, recipient, since, *, max_pages=2, max_messages=100):
    """One bounded mailbox search per cycle, with durable progress through older pages."""
    # Bucket only the search boundary. acknowledgement() independently enforces
    # each run's exact timestamp, so this cannot accept earlier mail for a run.
    since = int(since) // 3600 * 3600
    # One mailbox can receive a profile address and its per-platform plus-addresses (email_alias.py).
    recipients = sorted({recipient} if isinstance(recipient, str) else set(recipient))
    to = f"to:{recipients[0]}" if len(recipients) == 1 else "{" + " ".join(f"to:{r}" for r in recipients) + "}"
    query = f"after:{since} {to}"
    key = "receipt_mail_page:" + mailbox_id
    query_id = digest(query)
    saved = mail.db.get_setting(key, {})
    state = dict(saved) if saved.get("query_id") == query_id else {"query_id": query_id}
    messages, seen = [], set()
    fetched_pages = 0
    upgraded = False
    deadline = time.monotonic() + 15
    while len(seen) < max_messages and time.monotonic() < deadline:
        ids = list(state.get("remaining_ids", []))
        if not ids:
            if fetched_pages >= max_pages:
                break
            params = {"q": query, "maxResults": 50, "includeSpamTrash": True}
            if state.get("page_token"):
                params["pageToken"] = state["page_token"]
            try:
                listed = await mail.request(mailbox_id, "/messages", params)
            except MailCursorUpgrade:
                if upgraded:
                    break
                state, upgraded = {"query_id": query_id}, True
                continue
            except (MailTransportError, MailOperationError):
                break  # Keep the cursor; a mailbox transport outage changes no receipt.
            fetched_pages += 1
            ids = [item["id"] for item in listed.get("messages", []) if item.get("id")]
            state["remaining_ids"] = ids
            state["next_page_token"] = listed.get("nextPageToken")
            if not ids:
                state = {"query_id": query_id}
                break
        mid = ids[0]
        try:
            message = await mail.read_message(mailbox_id, mid)
        except MailCursorUpgrade:
            if upgraded:
                break
            state, upgraded = {"query_id": query_id}, True
            continue
        except (MailTransportError, MailOperationError):
            break
        except MailMessageError:
            message = None
        if mid not in seen:
            seen.add(mid)
            if message is not None:
                messages.append(message)
        state["remaining_ids"] = ids[1:]
        if not state["remaining_ids"]:
            token = state.pop("next_page_token", None)
            if token:
                state["page_token"] = token
            else:
                state = {"query_id": query_id}
                break  # Start a new sweep next cycle, including newly arrived mail.
    mail.db.set_setting(key, {**state, "updated_at": time.time()})
    return messages


def receipt_claimed(session, message, receipt):
    """Canonical Gmail ids and proven Inbox aliases share one claim authority.

    An archived legacy UID cannot be resolved safely. Its saved delivery timestamp
    remains a conservative barrier to reusing that same message under a new id.
    """
    ids = {message["id"], *message.get("legacyInboxIds", [])}
    current = session.get(Run, receipt.get("run_id", ""))
    current_job = session.get(Job, current.job_id) if current else None
    recipient = current.packet.get("profile", {}).get("email", "").casefold() if current else ""
    for row in session.scalars(select(Run).where(Run.receipt["email"].as_string().is_not(None))):
        claimed = row.receipt.get("email") or {}
        old_recipient = row.packet.get("profile", {}).get("email", "").casefold()
        if recipient and old_recipient and recipient != old_recipient:
            continue
        if claimed.get("message_id") in ids:
            return True
        if str(claimed.get("message_id", "")).isdigit() and message["id"].startswith("gm:"):
            from .admission import employer_partition

            old_job = session.get(Job, row.job_id)
            # An unidentified legacy claim reserves its own employer context,
            # not every future acknowledgement in the entire mailbox. Missing
            # identity stays conservative; positive unrelated identity can pass.
            if current_job and old_job:
                from .features.knowledge.employers import name_aliases, resolve

                old_names = set(name_aliases(old_job.company))
                names = set(name_aliases(current_job.company))
                old_identity = resolve(session, old_job.company, old_job.url, create=False)
                identity = resolve(session, current_job.company, current_job.url, create=False)
                same_identity = bool(old_identity and identity and old_identity.id == identity.id)
                if (
                    old_names
                    and names
                    and not old_names.intersection(names)
                    and not same_identity
                    and employer_partition(old_job) != employer_partition(current_job)
                ):
                    continue
            try:
                old_at = datetime.fromisoformat(claimed["received_at"])
            except (KeyError, TypeError, ValueError):
                # A legacy claim with neither immutable identity nor delivery
                # evidence cannot be distinguished from rediscovered mail.
                return True
            if old_at == datetime.fromisoformat(receipt["received_at"]):
                return True
    return False


async def check_receipts(mail, run_id=None):
    from .agent_settings import enabled

    if not enabled(mail.db, "email"):
        return
    cutoff = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    with mail.db.session() as s:
        email = Run.receipt["email"].as_string()
        unmatched = or_(
            email.is_(None),
            email.in_(["", "{}"]),
            Run.receipt["email"]["sender_trusted"].as_boolean().is_(False),
        )
        # Choose the batch by id first: sorting full rows (large packets) to
        # keep twenty took seconds of database time per poll.
        query = (
            select(Run.id)
            .join(Job, Run.job_id == Job.id)
            .where(
                Run.state.in_(["confirmed", "submission_unknown"]),
                or_(
                    Run.state == "submission_unknown",
                    Run.created_at > cutoff,
                    Run.receipt["evidence_reconciliation"].as_string() == "pending",
                ),
                Job.demo.is_(False),
                unmatched,
            )
        )
        cursor = mail.db.get_setting("receipt_poll_cursor", {})
        if cursor.get("created_at") and cursor.get("run_id"):
            after = or_(
                Run.created_at > cursor["created_at"],
                and_(Run.created_at == cursor["created_at"], Run.id > cursor["run_id"]),
            )
            query = query.order_by(case((after, 0), else_=1))
        query = query.order_by(Run.created_at, Run.id).limit(20)
        ids = [run_id] if run_id else list(s.scalars(query))
        rows = (
            s.execute(
                select(Run, Job).join(Job, Run.job_id == Job.id).where(Run.id.in_(ids), Job.demo.is_(False))
            ).all()
            if ids
            else []
        )
        rows.sort(key=lambda row: ids.index(row[0].id))
        candidates = [
            (record(r), record(j))
            for r, j in rows
            # Done once a trusted acknowledgement is recorded (legacy email receipts were trusted).
            if not (r.receipt.get("email") and r.receipt["email"].get("sender_trusted", True))
            and r.state in {"confirmed", "submission_unknown"}
        ]
        boxes = {
            b.email.lower(): b.id for b in s.scalars(select(Mailbox).where(Mailbox.state == "connected"))
        }
    if not run_id and rows:
        # Rotate even when no employer email has arrived. Persistent missing
        # receipts must not monopolize the bounded poll batch forever.
        last = rows[-1][0]
        mail.db.set_setting("receipt_poll_cursor", {"created_at": last.created_at, "run_id": last.id})
    found = 0
    messages = {}
    from .email_alias import base_address

    for recipient, mailbox_id in boxes.items():
        owned = [r for r, _ in candidates if base_address(r["packet"]["profile"]["email"]) == recipient]
        if owned:
            addresses = sorted({recipient} | {r["packet"]["profile"]["email"].lower() for r in owned})
            with mail.db.session() as session:
                earliest = session.scalar(
                    select(func.min(Run.created_at)).where(
                        Run.state.in_(["confirmed", "submission_unknown"]),
                        Run.packet["profile"]["email"].as_string().in_(addresses),
                        unmatched,
                        or_(
                            Run.state == "submission_unknown",
                            Run.created_at > cutoff,
                            Run.receipt["evidence_reconciliation"].as_string() == "pending",
                        ),
                    )
                )
            since = datetime.fromisoformat(earliest or min(r["created_at"] for r in owned)).timestamp()
            messages[mailbox_id] = await receipt_messages(
                mail, mailbox_id, addresses if len(addresses) > 1 else recipient, since
            )

    # All competing jobs matter, including those outside this rotating batch.
    # Considering only the 20 selected runs can turn ambiguous mail into proof.
    def load_competitors():
        with mail.db.session() as s:
            # Only identity, timing and recipient are needed. Loading every full run
            # packet (hundreds of MB in total) each minute stalled the shared event loop.
            competitors = [
                (
                    {
                        "id": rid,
                        "job_id": job_id,
                        "state": state,
                        "created_at": created_at,
                        "packet": {"profile": {"email": recipient or ""}},
                    },
                    record(j),
                )
                for rid, job_id, state, created_at, recipient, j in s.execute(
                    select(
                        Run.id,
                        Run.job_id,
                        Run.state,
                        Run.created_at,
                        Run.packet["profile"]["email"].as_string(),
                        Job,
                    )
                    .join(Job, Run.job_id == Job.id)
                    .where(
                        Job.demo.is_(False),
                        or_(
                            Run.state.in_(["confirmed", "submission_unknown"]),
                            Run.packet["match_approved"].as_boolean().is_(True),
                        ),
                    )
                )
            ]
            submit_times = dict(
                s.execute(
                    select(Event.run_id, func.min(Event.created_at))
                    .where(Event.kind == "submitting")
                    .group_by(Event.run_id)
                ).all()
            )
        return competitors, submit_times

    # Off the shared application loop: hundreds of rows every minute.
    competitors, submit_times = await asyncio.to_thread(load_competitors)
    for run, _ in competitors:
        if run["id"] in submit_times:
            run["created_at"] = submit_times[run["id"]]
            run["submitted_at"] = submit_times[run["id"]]
    # Messages fetched for this batch are matched against every unconfirmed
    # run, not only the 20-run batch: a receipt read while its own run sat
    # outside the batch was consumed and never matched again. The assignment
    # still requires one possible job per message and skips matched rows.
    target_ids = {r["id"] for r, _ in candidates} | {
        r["id"] for r, _ in competitors if r["state"] in {"confirmed", "submission_unknown"}
    }

    def group_competitors():
        groups, keys = {}, {}
        with mail.db.session() as session:
            for run, job in competitors:
                recipient = run.get("packet", {}).get("profile", {}).get("email", "").lower()
                if job["id"] not in keys:
                    keys[job["id"]] = receipt_employer_key(session, job)
                groups.setdefault((recipient, keys[job["id"]]), []).append((run, job))
        return groups

    groups = await asyncio.to_thread(group_competitors)

    def match_and_record():
        """Matching parses and compares every message with every candidate run; off the shared loop."""
        found = 0
        assignments = []
        possible_jobs = {}
        for (recipient, company), group in groups.items():
            if recipient not in boxes or not any(r["id"] in target_ids for r, _ in group):
                continue
            known_jobs = {j["id"] for _, j in group}
            for msg in messages.get(boxes[recipient], []):
                matches = {}
                for run, job in group:
                    try:
                        receipt = acknowledgement(msg, job, run, allow_missing_title=True)
                    except (ValueError, TypeError, KeyError, RecursionError):
                        mail.quarantine(boxes[recipient], msg.get("id", ""), "invalid_message")
                        continue
                    if not receipt:
                        continue
                    if not receipt["sender_trusted"]:
                        # Retain authenticated context without promoting an unrelated
                        # sender to proof. Challenge bodies were rejected above.
                        if run["id"] in target_ids:
                            with mail.db.exclusive() as s:
                                row = s.get(Run, run["id"])
                                if row and not row.receipt.get("email_untrusted"):
                                    row.receipt = {**row.receipt, "email_untrusted": receipt}
                        continue
                    matches.setdefault(job["id"], []).append((run, receipt))
                exact = {
                    jid: rs
                    for jid, rs in matches.items()
                    if any(r[1]["attribution_basis"] != "submit_window" for r in rs)
                }
                urls = {
                    jid: rs
                    for jid, rs in exact.items()
                    if any(r[1]["attribution_basis"] == "job_url" for r in rs)
                }
                possible = urls or exact or matches
                possible_jobs.setdefault(msg["id"], set()).update(possible)
                if len(possible) == 1:
                    jid, options = next(iter(possible.items()))
                    source, receipt = max(options, key=lambda item: item[0]["created_at"])
                    if (
                        source["id"] in target_ids
                        and (source["state"] != "submission_unknown" or source.get("submitted_at"))
                        and (receipt["attribution_basis"] != "submit_window" or len(known_jobs) == 1)
                    ):
                        assignments.append((source, receipt, msg, (recipient, company)))
                # Distinct ids and even a unique perfect matching do not establish
                # that two mails refer to different jobs. A follow-up can acknowledge
                # an already confirmed job again. Every message must identify exactly
                # one possible job independently, including confirmed competitors.
        # Independent job identity is required. A fabricated time cutoff cannot rule
        # out a late receipt/follow-up from an earlier or already confirmed job.
        assignments = [a for a in assignments if len(possible_jobs[a[2]["id"]]) == 1]
        cohort_counts = {}
        for source, receipt, _, key in assignments:
            if source["state"] == "submission_unknown" and receipt["attribution_basis"] != "submit_window":
                cohort_counts.setdefault(key, set()).add(source["job_id"])
        for source, receipt, msg, key in assignments:
            counted = (
                receipt["attribution_basis"] != "submit_window" and len(cohort_counts.get(key, set())) > 1
            )
            with mail.db.exclusive() as s:
                row = s.get(Run, source["id"])
                if (
                    not row
                    or row.state not in {"confirmed", "submission_unknown"}
                    or row.receipt.get("email_confirmed")
                    or receipt_claimed(s, msg, receipt)
                ):
                    continue
                row.receipt = {
                    **row.receipt,
                    "email": receipt,
                    "email_confirmed": True,
                    "evidence_reconciliation": "verified",
                    "website_confirmed": bool(row.receipt.get("website_confirmed")),
                    **({"count_matched": True} if counted else {}),
                }
                if row.state == "submission_unknown":
                    row.state = "confirmed"
                    row.reason = "Submission confirmed by authenticated employer acknowledgement email"
                    s.get(Job, row.job_id).status = "confirmed"
            mail.db.event(source["id"], "email_receipt", "Company acknowledgement email received", receipt)
            found += 1
        return found

    found += await asyncio.to_thread(match_and_record)
    return {"emails_confirmed": found}


# Message id -> jobs it was compared with and matched none of. The recurring draft
# sweep would otherwise fetch every application email of the window on each pass.
_DRAFT_COMPARED: dict = {}
_DRAFT_COMPARED_LIMIT = 5000

# Latest-run states where a submission may have happened without proof on the page.
UNPROVEN_STATES = [
    "waiting_browser",
    "waiting_answer",
    "paused",
    "failed",
    "timed_out",
    "needs_review",
    "cancelled",
    "submission_unknown",
]


async def reconcile_recent_drafts(mail, days=2):
    """An employer's receipt confirms an application whose confirmation page the browser missed.

    EY and Corning acknowledged four SuccessFactors applications by email while their runs
    stayed in waiting_browser (2026-10-16); the minute receipt poll only reads confirmed and
    submission_unknown runs. Same identity, window and exact-role rules as the draft match.
    """
    from .agent_settings import enabled

    if not enabled(mail.db, "email"):
        return {"emails_confirmed": 0}
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

    def recent():
        with mail.db.session() as s:
            return sorted(
                set(
                    s.scalars(
                        select(Run.job_id)
                        .join(Job, Run.job_id == Job.id)
                        .where(
                            Run.created_at > cutoff,
                            Run.mode == "submit",
                            Run.state.in_(UNPROVEN_STATES),
                            Job.demo.is_(False),
                            Job.status != "confirmed",
                        )
                    )
                )
            )

    job_ids = await asyncio.to_thread(recent)
    if not job_ids:
        return {"emails_confirmed": 0}
    return await reconcile_approved_drafts(mail, job_ids, max_pages=4)


async def reconcile_approved_drafts(mail, job_ids, max_pages=8):
    """Match manually completed drafts before retrying them; one email search per mailbox.

    Old attempts can precede the latest retry. Match within this job's original
    approved attempt window, require an exact role, and never reuse a receipt
    belonging to another job. No LLM receives message bodies or credentials.
    """
    def load():
        candidates = []
        with mail.db.session() as s:
            boxes = {
                b.email.lower(): b.id
                for b in s.scalars(select(Mailbox).where(Mailbox.state == "connected"))
            }
            for jid in set(job_ids):
                job = s.get(Job, jid)
                if not job or job.demo:
                    continue
                runs = list(s.scalars(select(Run).where(Run.job_id == jid).order_by(Run.created_at.desc())))
                if (
                    not runs
                    or any(r.state == "confirmed" for r in runs)
                    or runs[0].state in {"queued", "running", "submitting"}
                ):
                    continue
                approved = [r for r in runs if r.mode == "submit" and r.packet.get("match_approved")]
                if not approved:
                    continue
                first = min(approved, key=lambda r: r.created_at)
                email = runs[0].packet.get("profile", {}).get("email")
                if not email:
                    continue
                # Matching needs identity, state and recipient, not the full packet.
                latest = {
                    "id": runs[0].id,
                    "state": runs[0].state,
                    "created_at": runs[0].created_at,
                    "packet": {"profile": {"email": email}},
                }
                window_start = first.created_at
                if latest["state"] == "submission_unknown":
                    window_start = s.scalar(
                        select(func.min(Event.created_at)).where(
                            Event.run_id == latest["id"], Event.kind == "submitting"
                        )
                    )
                    if not window_start:
                        continue
                candidates.append((record(job), latest, window_start))
        return candidates, boxes

    # Every run of every job, packets included: off the shared application loop.
    candidates, boxes = await asyncio.to_thread(load)
    found = 0
    ambiguous = set()
    scanned = 0
    truncated = False
    for recipient, mailbox_id in boxes.items():
        owned = [
            (j, r, start)
            for j, r, start in candidates
            if r["packet"]["profile"]["email"].lower() == recipient
        ]
        if not owned:
            continue
        since = min(int(datetime.fromisoformat(start).timestamp()) for _, _, start in owned)
        owned_jobs = frozenset(j["id"] for j, _, _ in owned)
        token = None
        seen = set()
        for _ in range(max_pages):
            params = {
                "q": f"after:{since} to:{recipient} (application OR applying)",
                "maxResults": 50,
                "includeSpamTrash": True,
            }
            if token:
                params["pageToken"] = token
            listed = await mail.request(mailbox_id, "/messages", params)
            for item in listed.get("messages", []):
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                compared = _DRAFT_COMPARED.get(item["id"], frozenset())
                if owned_jobs <= compared:
                    continue  # Mail is immutable: it already matched none of these jobs.
                msg = await mail.request(mailbox_id, "/messages/" + item["id"], {"format": "full"})
                scanned += 1
                matches = []
                for job, run, start in owned:
                    match_run = {**run, "created_at": start}
                    receipt = acknowledgement(msg, job, match_run, allow_missing_title=False)
                    if receipt and receipt["sender_trusted"]:
                        matches.append((job, run, receipt))
                if len(matches) > 1:
                    ambiguous.update(j["id"] for j, _, _ in matches)
                    continue
                if not matches:
                    if len(_DRAFT_COMPARED) >= _DRAFT_COMPARED_LIMIT:
                        _DRAFT_COMPARED.pop(next(iter(_DRAFT_COMPARED)))
                    _DRAFT_COMPARED[item["id"]] = compared | owned_jobs
                    continue
                job, source, receipt = matches[0]
                with mail.db.exclusive() as s:
                    if receipt_claimed(s, msg, receipt):
                        continue
                    latest = s.scalar(
                        select(Run).where(Run.job_id == job["id"]).order_by(Run.created_at.desc())
                    )
                    if (
                        not latest
                        or latest.id != source["id"]
                        or latest.state in {"confirmed", "queued", "running", "submitting"}
                    ):
                        continue
                    latest.receipt = {
                        **latest.receipt,
                        "email": receipt,
                        "email_confirmed": True,
                        "evidence_reconciliation": "verified",
                        "website_confirmed": bool(latest.receipt.get("website_confirmed")),
                        "matched_existing_application": True,
                    }
                    latest.state = "confirmed"
                    latest.reason = (
                        "Existing application confirmed by authenticated employer acknowledgement email"
                    )
                    s.get(Job, job["id"]).status = "confirmed"
                mail.db.event(
                    source["id"],
                    "receipt_reconciliation",
                    "Existing application matched to a trusted employer receipt",
                )
                found += 1
            next_token = listed.get("nextPageToken")
            if not next_token or next_token == token:
                break
            token = next_token
        else:
            truncated = bool(token)
    return {
        "emails_confirmed": found,
        "messages_checked": scanned,
        "ambiguous_job_ids": sorted(ambiguous),
        "mail_scan_truncated": truncated,
    }
