"""The background poll that matches incoming messages to waiting challenges."""

from datetime import UTC, datetime
from email.utils import getaddresses
from sqlalchemy import select
from urllib.parse import urlsplit
import asyncio
import httpx
import re
import time
from ..db import Event
from ..db import Job
from ..db import MailChallenge
from ..db import MailConsumption
from ..db import MailRule
from ..db import Mailbox
from ..db import Run
from ..db import Setting
from ..db import record
from .constants import LIVE
from .extraction import UNBRANDED_MAX_CANDIDATES
from .extraction import UNBRANDED_SEARCH_TERMS
from .extraction import extract_message
from .extraction import latest_employer_code
from .helpers import origin
from .senders import EMPLOYER_SENDER_FAMILIES
from .senders import KNOWN_SENDERS
from .senders import icims_names
from .senders import oracle_pod
from .senders import sender_domains_overlap
from .senders import tenant_tokens


def sf_tenant(session, job):
    """The SuccessFactors company id serving a job: named in its URL, or learned from its career pages (sf_tenant:)."""
    if not job or job.ats != "successfactors":
        return ""
    from ..accounts.tenants import sf_company

    if named := sf_company(job.url or ""):
        return named[1]
    try:
        row = session.get(Setting, "sf_tenant:" + origin(job.url or ""))
    except ValueError:
        return ""
    return str(((row.value if row else None) or {}).get("company") or "")


class PollingMixin:
    @staticmethod
    def end_dead_email_waits(s):
        """A job's latest run parked on an email wait that has ended is waiting for nothing: Intuit's password reset
        never arrived and its run read "waiting for an employer email" for eleven hours, L3Harris's for four days
        (2026-10-27). It becomes a browser recovery with the reason. A superseded run's state drives nothing."""
        ended = []
        for run in s.scalars(select(Run).where(Run.state == "waiting_email")):
            handle = (run.packet or {}).get("wait_handle") or {}
            challenge = s.get(MailChallenge, handle.get("challenge_id", handle.get("id")) or "")
            if challenge is not None and challenge.state not in {"expired", "cancelled", "failed"}:
                continue
            if s.scalar(select(Run.id).where(Run.job_id == run.job_id, Run.created_at > run.created_at).limit(1)):
                continue
            what = str(handle.get("kind") or "verification").replace("_", " ")
            run.state = "waiting_browser"
            run.reason = f"The employer's {what} email did not arrive in time; the draft is kept for recovery"
            job = s.get(Job, run.job_id)
            if job and job.status == "waiting_email":
                job.status = run.state
            ended.append((run.id, run.reason))
        return ended

    async def poll_once(self):
        from ..agent_settings import enabled

        if not enabled(self.db, "email"):
            return
        # The active sweep already services all requests. Browser owners must
        # not line up behind it and each repeat the same slow mailbox scan.
        # They can claim its result on the next tick or persist their wait now.
        if self.poll_lock.locked():
            return
        async with self.poll_lock:
            with self.db.exclusive() as s:
                current = time.time()
                for c in s.scalars(select(MailChallenge).where(MailChallenge.state.in_(LIVE))):
                    run = s.get(Run, c.run_id)
                    c.expires = self.window_deadline(run, c.expires)
                    if (
                        c.expires < current
                        or not run
                        or (run.state not in {"running", "submitting"} and not self.waiting_owner(run, c.id))
                    ):
                        c.state, c.payload, c.reason = (
                            "expired",
                            "",
                            "Application or verification deadline ended",
                        )
                ended = self.end_dead_email_waits(s)
                pending = [
                    record(c)
                    for c in s.scalars(select(MailChallenge).where(MailChallenge.state == "pending"))
                ]
            for run_id, why in ended:
                self.db.event(run_id, "email_wait_ended", why)
            for c in pending:
                with self.db.session() as s:
                    row = s.get(MailRule, c["rule_id"])
                    rule = record(row) if row and row.enabled else None
                    run = s.get(Run, c["run_id"])
                    job = s.get(Job, run.job_id) if run else None
                    # Tenant identity: the employer this run applies to, never message text.
                    c["tenant_tokens"] = tenant_tokens(
                        (job.url if job else "") or (rule or {}).get("employer_origin", ""),
                        job.company if job else "",
                        sf_tenant(s, job),
                    )
                    platform = job.ats if job else ""
                    if platform == "custom" and rule:
                        # A career site on the employer's domain that is a platform underneath is matched as that
                        # platform: Garmin's iCIMS link mail ("User account with Garmin International, Inc.", a
                        # tracking.icims.com link) was refused as "tenant" while the job read as custom (2026-10-27).
                        platform = next(
                            (
                                name
                                for name, domains in KNOWN_SENDERS.items()
                                if name != "custom" and set(domains) & set(rule["sender_domains"])
                            ),
                            platform,
                        )
                    c["employer_senders"] = bool(job and platform in EMPLOYER_SENDER_FAMILIES)
                    c["ats"] = platform
                    c["employer_name"] = job.company if job else ""
                    # Every live request in this mailbox, read now: an unbranded code is
                    # accepted only when this request is the sole open window at arrival.
                    windows = list(
                        s.scalars(
                            select(MailChallenge).where(
                                MailChallenge.mailbox_id == c["mailbox_id"],
                                MailChallenge.state.in_(["pending", "matched"]),
                            )
                        )
                    )
                    c["open_windows"] = [(w.id, w.since, w.expires, w.kind) for w in windows]
                    # Other open windows' employers: an unbranded code is attributed by name only when the mail names
                    # this employer and none of theirs (extraction.extract_message).
                    c["peer_tokens"] = []
                    for w in windows:
                        peer_run = s.get(Run, w.run_id) if w.id != c["id"] else None
                        peer_job = s.get(Job, peer_run.job_id) if peer_run else None
                        if peer_job and peer_job.id != (job.id if job else None):
                            c["peer_tokens"].append(
                                tenant_tokens(peer_job.url or "", peer_job.company or "", sf_tenant(s, peer_job))
                            )
                    c["oracle_pod"] = oracle_pod(job.url) if job and job.ats == "oracle" else ""
                    c["oracle_peers"] = []
                    if job and job.ats == "oracle":
                        for w in windows:
                            peer_run = s.get(Run, w.run_id) if w.id != c["id"] else None
                            peer = s.get(Job, peer_run.job_id) if peer_run else None
                            if (
                                peer
                                and peer.ats == "oracle"
                                and w.kind in {"code", "auto"}
                                and origin(peer.url) != origin(job.url)
                            ):
                                c["oracle_peers"].append((peer.company or "", oracle_pod(peer.url)))
                    c["icims_names"] = sorted(icims_names(job.company, job.url)) if job and platform == "icims" else []
                    c["icims_peers"] = []
                    if job and platform == "icims":
                        for w in windows:
                            peer_run = s.get(Run, w.run_id) if w.id != c["id"] else None
                            peer = s.get(Job, peer_run.job_id) if peer_run else None
                            if peer and peer.ats == "icims" and origin(peer.url) != origin(job.url):
                                c["icims_peers"].extend(sorted(icims_names(peer.company, peer.url)))
                    # Some Workday tenants send reset mail from their employer's
                    # domain. An exact tenant link can prove that sender; a shared
                    # wdN.myworkday.com origin cannot distinguish employers.
                    c["sender_learning"] = bool(
                        job
                        and job.ats == "workday"
                        and re.fullmatch(
                            r"[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com", urlsplit(job.url).hostname or ""
                        )
                        and origin(job.url) == (rule or {}).get("employer_origin")
                        and c["kind"] in {"link", "password_reset"}
                    )
                if not rule:
                    self.outcome(c["id"], "cancelled", "Verification rule was removed or disabled")
                    continue
                try:
                    scan_started = time.monotonic()
                    senders = list(rule["sender_domains"])
                    if c["employer_senders"]:
                        senders += c["tenant_tokens"]
                    terms = ["from:" + d for d in senders]
                    if c["ats"] == "oracle" and c["kind"] in {"code", "auto"}:
                        terms.append('subject:"confirm your identity"')
                    if c["sender_learning"]:
                        terms += (
                            ["subject:reset"]
                            if c["kind"] == "password_reset"
                            else [
                                "subject:verify",
                                "subject:verification",
                                "subject:confirm",
                                "subject:activate",
                            ]
                        )
                    query = f"after:{int(c['since'])} to:{c['recipient']} " + "{" + " ".join(terms) + "}"
                    listed = await self.request(
                        c["mailbox_id"],
                        "/messages",
                        {"q": query, "maxResults": 20, "includeSpamTrash": "true"},
                    )
                    if listed.get("nextPageToken"):
                        self.outcome(
                            c["id"],
                            "failed",
                            "Too many candidate messages to match within the verification budget",
                        )
                        continue
                    messages = list(listed.get("messages", []))
                    matches, rejections, rejected_origins, last_rejected = [], {}, set(), None
                    if c["kind"] in {"code", "auto"}:
                        # Codes from a domain the rule does not know (Hershey's from hersheys.com for its career site
                        # thehersheycompany.com, 2026-10-27) are not found by the sender filter above. Search this
                        # recipient's window for verification wording; extraction accepts one only for the sole open
                        # window or when it names this employer and no other open window's.
                        unbranded = await self.request(
                            c["mailbox_id"],
                            "/messages",
                            {
                                "q": f"after:{int(c['since'])} to:{c['recipient']} "
                                + "{"
                                + " ".join(UNBRANDED_SEARCH_TERMS)
                                + "}",
                                "maxResults": UNBRANDED_MAX_CANDIDATES,
                                "includeSpamTrash": "true",
                            },
                        )
                        if unbranded.get("nextPageToken"):
                            rejections["unbranded_over_limit"] = 1
                        else:
                            seen = {m["id"] for m in messages}
                            messages += [m for m in unbranded.get("messages", []) if m["id"] not in seen]
                    outside = None
                    if not messages and c["kind"] in {"link", "password_reset"}:
                        try:
                            outside = await self.outside_senders(c)
                        except Exception:
                            outside = None  # A diagnosis never fails the verification poll.
                    for m in messages[: 20 + UNBRANDED_MAX_CANDIDATES]:
                        with self.db.session() as s:
                            if s.get(MailConsumption, c["mailbox_id"] + ":" + m["id"]):
                                continue
                        msg = await self.read_message(c["mailbox_id"], m["id"])
                        if msg is None:
                            rejections["unreadable_message"] = rejections.get("unreadable_message", 0) + 1
                            continue
                        with self.db.session() as s:
                            if self.message_consumed(s, c["mailbox_id"], msg, c):
                                rejections["already_consumed"] = rejections.get("already_consumed", 0) + 1
                                continue
                        diagnostics, learned_domain = {}, None
                        try:
                            token = extract_message(msg, rule, c, diagnostics=diagnostics)
                            if (
                                not token
                                and diagnostics.get("rejection") == "sender"
                                and c["sender_learning"]
                            ):
                                senders = getaddresses(
                                    [
                                        h["value"]
                                        for h in msg["payload"]["headers"]
                                        if h["name"].lower() == "from"
                                    ]
                                )
                                if len(senders) == 1:
                                    domain = senders[0][1].rpartition("@")[2].lower()
                                    proof_rule = {
                                        **rule,
                                        "sender_domains": [domain],
                                        "link_origins": [rule["employer_origin"]],
                                    }
                                    if extract_message(msg, proof_rule, c, identity_only=True):
                                        token = extract_message(msg, proof_rule, c)
                                        if token:
                                            learned_domain = domain
                        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                            self.quarantine(c["mailbox_id"], m["id"], "invalid_message")
                            token, diagnostics = None, {"rejection": "invalid_message"}
                        if token:
                            matches.append((m["id"], token, msg, learned_domain))
                        else:
                            rejection = diagnostics.get("rejection", "token_format")
                            rejections[rejection] = rejections.get(rejection, 0) + 1
                            rejected_origins.update(diagnostics.get("rejected_link_origins", []))
                            if diagnostics.get("subject") is not None:
                                last_rejected = {
                                    "subject": diagnostics.get("subject", ""),
                                    "link_hosts": diagnostics.get("link_hosts", []),
                                }
                    scan_seconds = time.monotonic() - scan_started
                    seen_outside = self.__dict__.setdefault("outside_seen", {})
                    if outside is not None:
                        seen_outside[c["id"]] = outside
                        while len(seen_outside) > 200:
                            seen_outside.pop(next(iter(seen_outside)))
                    outside = seen_outside.get(c["id"], [])
                    counts = (
                        len(messages),
                        len(matches),
                        tuple(sorted(rejections.items())),
                        tuple(sorted(rejected_origins)[:20]),
                        tuple(outside),
                    )
                    changed = self.poll_diagnostics.get(c["id"]) != counts
                    self.poll_diagnostics[c["id"]] = counts
                    while len(self.poll_diagnostics) > 200:
                        self.poll_diagnostics.pop(next(iter(self.poll_diagnostics)))
                    if matches or scan_seconds > 5 or changed:
                        self.db.event(
                            c["run_id"],
                            "mail_poll",
                            "Checked the verification mailbox",
                            {
                                "elapsed_seconds": round(scan_seconds, 2),
                                "candidate_count": len(messages),
                                "matching_count": len(matches),
                                "rejection_counts": rejections,
                                "rejected_link_origins": sorted(rejected_origins)[:20],
                                # What the last rejected candidate looked like (subject and link hosts, never its token),
                                # so a new template's format shows in the run without reading the mailbox.
                                "last_rejected": last_rejected,
                                # Sender domains (only) of reset/verification mail to this address in this window that
                                # the sender search did not list; never matched or opened.
                                "outside_senders": outside,
                            },
                        )
                    matches = latest_employer_code(matches, c)
                    if len(matches) != 1:
                        if len(matches) > 1:
                            self.outcome(
                                c["id"], "failed", "Multiple matching messages; no code or link was guessed"
                            )
                        else:
                            with self.db.exclusive() as s:
                                row = s.get(MailChallenge, c["id"])
                                if row and row.state == "pending":
                                    row.reason = (
                                        "No messages in the verification search"
                                        if not messages
                                        else f"Found {len(messages)} messages; none matched the verification identity, time and format checks"
                                    )
                        continue
                    message_id, token, msg, learned_domain = matches[0]
                    matched = None
                    with self.db.exclusive() as s:
                        row = s.get(MailChallenge, c["id"])
                        owner = s.get(Run, row.run_id) if row else None
                        current_rule = s.get(MailRule, c["rule_id"])
                        if learned_domain:
                            # Learning must not create an overlapping active
                            # sender lease while another employer owns it.
                            conflict = any(
                                other.run_id != c["run_id"]
                                and other.expires > time.time()
                                and (other_rule := s.get(MailRule, other.rule_id))
                                and sender_domains_overlap([learned_domain], other_rule.sender_domains)
                                for other in s.scalars(
                                    select(MailChallenge).where(MailChallenge.state.in_(LIVE))
                                )
                            )
                            if conflict or not current_rule or not current_rule.enabled:
                                continue
                        key = c["mailbox_id"] + ":" + message_id
                        if (
                            row
                            and row.state == "pending"
                            and row.expires > time.time()
                            and (
                                row.kind in {"auto", token["kind"]}
                                or row.kind == "password_reset"
                                and token["kind"] in {"link", "code", "password"}
                            )
                            and owner
                            and (
                                owner.state in {"running", "submitting"} or self.waiting_owner(owner, row.id)
                            )
                            and not self.message_consumed(s, c["mailbox_id"], msg, c)
                        ):
                            row.payload, row.message_id, row.state, row.reason = (
                                self.vault.seal(token),
                                message_id,
                                "matched",
                                "A correlated employer email is ready",
                            )
                            if learned_domain:
                                current_rule.sender_domains = list(
                                    dict.fromkeys(current_rule.sender_domains + [learned_domain])
                                )
                                s.add(
                                    Event(
                                        run_id=row.run_id,
                                        kind="mail_sender_learned",
                                        message="Learned an authenticated sender from the exact Workday tenant link",
                                        data={"sender_domain": learned_domain, "challenge_id": row.id},
                                    )
                                )
                            s.add(MailConsumption(key=key, challenge_id=row.id))
                            meta = s.get(Setting, "mail_request:" + row.id)
                            generation = (meta.value if meta else {}).get("generation", 1)
                            matched = (row.run_id, row.id, generation)
                            if self.waiting_owner(owner, row.id):
                                from ..outbox import enqueue

                                enqueue(
                                    s,
                                    "mail_resume",
                                    row.id,
                                    {
                                        "run_id": row.run_id,
                                        "challenge_id": row.id,
                                        "request_id": row.id,
                                        "generation": generation,
                                    },
                                    run_id=row.run_id,
                                )
                    if matched:
                        owner_id, challenge_id, generation = matched
                        self.db.event(
                            owner_id,
                            "EmailMatched",
                            "A correlated employer email is ready",
                            {
                                "challenge_id": challenge_id,
                                "request_id": challenge_id,
                                "generation": generation,
                            },
                        )
                except (ValueError, httpx.HTTPError, KeyError, TypeError):
                    # Do not include provider responses, URLs, codes, or credentials in diagnostics.
                    with self.db.exclusive() as s:
                        row = s.get(MailChallenge, c["id"])
                        box = s.get(Mailbox, c["mailbox_id"])
                        if row:
                            row.reason = "Mailbox request failed; check the connection"
                            if not box or box.state != "connected":
                                row.state, row.payload = "failed", ""

    async def outside_senders(self, c):
        """Diagnosis only, for a link or reset wait whose sender search listed nothing: the sender domains of reset or
        verification mail that reached this address in the request window. Farmers', Bain's and Intuit's resets "did
        not arrive" with no candidate on any poll (2026-10-27), and nothing showed whether the mail came from a domain
        the rule does not name or never came at all. These messages are never matched or opened; at most once a minute
        per request. None when skipped."""
        checked = self.__dict__.setdefault("outside_checked", {})
        if time.monotonic() - checked.get(c["id"], float("-inf")) < 60:
            return None
        checked[c["id"]] = time.monotonic()
        while len(checked) > 200:
            checked.pop(next(iter(checked)))
        words = (
            ["subject:reset", "subject:password"]
            if c["kind"] == "password_reset"
            else ["subject:verify", "subject:verification", "subject:confirm", "subject:activate", "subject:sign"]
        )
        listed = await self.request(
            c["mailbox_id"],
            "/messages",
            {
                "q": f"after:{int(c['since'])} to:{c['recipient']} " + "{" + " ".join(words) + "}",
                "maxResults": 5,
                "includeSpamTrash": "true",
            },
        )
        domains = []
        for m in listed.get("messages", [])[:5]:
            msg = await self.read_message(c["mailbox_id"], m["id"])
            if msg is None:
                continue
            found = getaddresses(
                [h["value"] for h in msg.get("payload", {}).get("headers", []) if h.get("name", "").lower() == "from"]
            )
            domain = found[0][1].rpartition("@")[2].lower() if len(found) == 1 else ""
            if domain and domain not in domains:
                domains.append(domain)
        return domains

    async def heal_mailbox(self):
        """Reconnect a .env Gmail mailbox marked reconnect_required once Gmail accepts its login again (checked at
        most every five minutes). A transient refusal at wake-up left verification off for five hours (2026-10-22)."""
        from ..db import Mailbox
        from ..gmail_imap import login_ok

        address = (self.config.gmail_address or "").strip().lower()
        password = "".join((self.config.gmail_app_password or "").split())
        if not address or not password:
            return False
        with self.db.session() as s:
            box = s.scalar(select(Mailbox).where(Mailbox.email == address, Mailbox.state == "reconnect_required"))
            if box is None or (box.checked_at or "") > datetime.fromtimestamp(time.time() - 300, UTC).isoformat():
                return False
            box_id = box.id
        healed = await asyncio.to_thread(login_ok, address, password)
        with self.db.exclusive() as s:
            if box := s.get(Mailbox, box_id):
                box.checked_at = datetime.now(UTC).isoformat()
                if healed:
                    box.state, box.error = "connected", ""
        return healed

    async def poll(self):
        last_receipts = last_drafts = 0
        receipt_task = draft_task = None
        try:
            while True:
                try:
                    await self.heal_mailbox()
                    await self.poll_once()
                    from ..agent_settings import enabled

                    if (
                        enabled(self.db, "email")
                        and time.time() - last_receipts > 60
                        and (receipt_task is None or receipt_task.done())
                    ):
                        from ..receipts import check_receipts

                        if receipt_task is not None:
                            await asyncio.gather(receipt_task, return_exceptions=True)
                        last_receipts = time.time()
                        # Receipt reconciliation can fetch many older messages. OTP/link
                        # deadlines must keep polling while that independent work runs.
                        receipt_task = asyncio.create_task(check_receipts(self))
                    if (
                        enabled(self.db, "email")
                        and time.time() - last_drafts > 600
                        and (draft_task is None or draft_task.done())
                    ):
                        from ..receipts import reconcile_recent_drafts

                        if draft_task is not None:
                            await asyncio.gather(draft_task, return_exceptions=True)
                        last_drafts = time.time()
                        draft_task = asyncio.create_task(reconcile_recent_drafts(self))
                except Exception:
                    pass  # Public connection/challenge diagnostics never expose raw secrets.
                await asyncio.sleep(self.config.mail_poll_seconds)
        finally:
            for task in (receipt_task, draft_task):
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
