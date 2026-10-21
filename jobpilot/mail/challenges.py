"""Challenges: begin, wait, claim and consume a code or link, with the shared-sender gate."""

from datetime import datetime
from sqlalchemy import select
import asyncio
import re
import time
from ..db import MailChallenge
from ..db import MailConsumption
from ..db import MailRule
from ..db import Mailbox
from ..db import Run
from ..db import Setting
from ..db import now
from ..db import record
from ..mail_errors import MailPending
from .constants import LIVE
from .constants import STALE_CODE_HOLD
from .helpers import origin
from .helpers import run_company
from .senders import GREENHOUSE_SENDERS
from .senders import ICIMS_SENDERS
from .senders import KNOWN_SENDERS
from .senders import ORACLE_SENDERS
from .senders import SHARED_SENDER_ATS
from .senders import sender_domains_overlap

# Seconds a password-reset window stays open on a platform that mails every employer from one sender (see begin()).
CENTRAL_RESET_WAIT = 180
SHARED_RESET_SENDERS = {"icims.com", "successfactors.com", "successfactors.eu", "sapsf.com"}


class ChallengesMixin:
    def request_handle(self, challenge):
        value = record(challenge) if isinstance(challenge, MailChallenge) else dict(challenge)
        meta = self.db.get_setting("mail_request:" + value["id"], {})
        return {**value, "request_id": value["id"], "generation": meta.get("generation", 1)}

    def persist_wait(self, challenge_id):
        """Extend a live delivery window once, anchored to the original request."""
        with self.db.exclusive() as session:
            row = session.get(MailChallenge, challenge_id)
            if not row or row.state not in LIVE or row.expires <= time.time():
                raise ValueError("Email verification was cancelled or expired")
            durable = row.since + self.config.mail_durable_wait_seconds
            rule = session.get(MailRule, row.rule_id) if row.kind == "password_reset" else None
            if rule and set(rule.sender_domains or []) & SHARED_RESET_SENDERS:
                # Parking the run used to stretch a capped reset to 20 minutes again: Emory's iCIMS Central reset held
                # the one iCIMS sender for 20 minutes five times in a row (2026-10-21).
                durable = min(durable, row.since + CENTRAL_RESET_WAIT)
            row.expires = self.window_deadline(session.get(Run, row.run_id), max(row.expires, durable))
            if row.expires <= time.time():
                raise ValueError("Application deadline ended")
            handle = record(row)
        return self.request_handle(handle)

    @staticmethod
    def waiting_owner(run, challenge_id):
        handle = run.packet.get("wait_handle", {}) if run else {}
        return bool(
            run
            and run.state in {"waiting_browser", "waiting_email", "submission_unknown"}
            and run.packet.get("waiting_kind") == "email"
            and handle.get("challenge_id", handle.get("id")) == challenge_id
        )

    def sender_blocker(self, s, rule, run_id, kind="auto", current=None):
        """Read the same sender lease for admission and acquisition; never claim mail."""
        current = time.time() if current is None else current
        for other in s.scalars(
            select(MailChallenge).where(
                MailChallenge.mailbox_id == rule["mailbox_id"],
                MailChallenge.state.in_(LIVE),
                MailChallenge.expires > current,
            )
        ):
            if other.run_id == run_id:
                continue
            holder = s.get(Run, other.run_id)
            if self.window_deadline(holder, other.expires) <= current:
                continue
            if (
                other.kind in {"code", "auto"}
                and other.state == "pending"
                and current - float(other.since or current) > STALE_CODE_HOLD
            ):
                if holder is None or holder.state not in {"running", "submitting"}:
                    # A parked code wait that never matched (UCSF: two 20-minute windows,
                    # 0 candidates) no longer serializes every other run on this sender.
                    continue
            other_rule = s.get(MailRule, other.rule_id)
            if (
                other_rule
                and set(rule["sender_domains"]) <= GREENHOUSE_SENDERS
                and set(other_rule.sender_domains) <= GREENHOUSE_SENDERS
                and kind in {"code", "auto"}
                and other.kind in {"code", "auto"}
                and run_company(s, other.run_id) != run_company(s, run_id)
            ):
                # Greenhouse codes are accepted only with the employer's whole name in the mail
                # (extract_message), so different employers never share a code. Serializing them
                # made parallel Greenhouse runs fail "Another verification is still active".
                continue
            if (
                other_rule
                and set(rule["sender_domains"]) <= ORACLE_SENDERS
                and set(other_rule.sender_domains) <= ORACLE_SENDERS
                and kind in {"code", "auto"}
                and other.kind in {"code", "auto"}
                and origin(rule["employer_origin"]) != origin(other_rule.employer_origin)
            ):
                # Oracle code mail is attributed to its tenant (oracle_attributed in extract_message).
                continue
            if (
                other_rule
                and set(rule["sender_domains"]) <= ICIMS_SENDERS
                and set(other_rule.sender_domains) <= ICIMS_SENDERS
                and {kind, other.kind} <= {"link", "auto", "code"}
                and origin(rule["employer_origin"]) != origin(other_rule.employer_origin)
            ):
                # iCIMS account mail is attributed to its employer by name (icims_attributed in extract_message).
                # One lease for every iCIMS employer let one verification run at a time: 49 iCIMS runs ended
                # waiting for the mailbox or "sender busy" in five days (2026-10-21). Password resets stay serialized.
                continue
            if (
                other_rule
                and set(rule["sender_domains"]) <= {"eightfold.ai"}
                and set(other_rule.sender_domains) <= {"eightfold.ai"}
                and origin(rule["employer_origin"]) != origin(other_rule.employer_origin)
            ):
                # Each Eightfold tenant mails from its own domain (hiring@jobs.starbucks.com) and
                # its sender is accepted only on that employer's label. Sharing one eightfold.ai
                # lease let Starbucks' code waits lock every Qualcomm run out (2026-10-14).
                continue
            if other_rule and sender_domains_overlap(other_rule.sender_domains, rule["sender_domains"]):
                link_kinds = {"link", "password_reset"}
                if (
                    kind in link_kinds
                    and other.kind in link_kinds
                    and set(rule["link_origins"]) == {origin(rule["employer_origin"])}
                    and set(other_rule.link_origins) == {origin(other_rule.employer_origin)}
                    and origin(rule["employer_origin"]) != origin(other_rule.employer_origin)
                ):
                    continue  # Exact distinct employer link origins provide their own identity.
                return other.id
        return None

    def shared_sender_free(self, session, job):
        """False while another job holds the sender of a platform that mails every employer from one sender."""
        if job.ats not in SHARED_SENDER_ATS or job.demo:
            return True
        rule = self.rule_for(job.url, session=session)
        if rule is None:
            # A tenant with no saved rule yet still shares the platform's sender with every other tenant.
            box = session.scalar(select(Mailbox.id).where(Mailbox.state == "connected"))
            if box is None:
                return True
            rule = {
                "mailbox_id": box,
                "sender_domains": KNOWN_SENDERS[job.ats],
                "employer_origin": origin(job.url),
                "link_origins": [origin(job.url)],
            }
        blocker = self.sender_blocker(session, rule, None)
        challenge = session.get(MailChallenge, blocker) if blocker else None
        holder = session.get(Run, challenge.run_id) if challenge else None
        return holder is None or holder.job_id == job.id

    def recovery_sender_ready(self, session, job, recovery):
        """Wait before starting another browser when its observed blocker is a sender lease."""
        if not recovery or recovery.get("cause") != "sender_busy":
            return True
        rule = self.rule_for(job.url, session=session)
        return not rule or not self.sender_blocker(
            session,
            rule,
            recovery.get("source_run_id"),
            kind=recovery.get("mail_request_kind", "auto"),
            current=time.time(),
        )

    @staticmethod
    def window_deadline(run, proposed):
        """Mail delivery and its lease cannot extend the owning application's deadline."""
        raw = (run.packet if run else {}).get("application_deadline")
        if not raw:
            return proposed  # Legacy/manual mail windows without an application clock.
        return min(proposed, datetime.fromisoformat(raw).timestamp())

    def begin(
        self, run_id, rule, recipient, kind="auto", since=None, *, resume_challenge_id=None, observed=False
    ):
        if kind not in {"auto", "code", "link", "password_reset"} or not re.fullmatch(
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", recipient
        ):
            raise ValueError("Email verification requires a valid applicant email address")
        with self.db.exclusive() as s:
            current = time.time()  # Acquisition time, never the start of a sender-lease wait.
            run = s.get(Run, run_id)
            box = s.get(Mailbox, rule["mailbox_id"])
            if not run or run.state not in {"running", "submitting"} or not box or box.state != "connected":
                raise ValueError("Verification requires a running application and connected mailbox")
            if self.window_deadline(run, current + self.config.mail_wait_seconds) <= current:
                raise ValueError("Application deadline ended")
            resume = run.packet.get("email_wait_resume", {})
            resume_challenge_id = resume_challenge_id or resume.get("challenge_id")
            if resume_challenge_id:
                challenge = s.get(MailChallenge, resume_challenge_id)
                source = s.get(Run, challenge.run_id) if challenge else None
                same_owner = bool(source and source.id == run_id)
                transferred = bool(
                    source
                    and resume.get("source_run_id") == source.id
                    and resume.get("challenge_id") == challenge.id
                    and resume.get("request_id", challenge.id) == challenge.id
                    and self.waiting_owner(source, challenge.id)
                    and source.job_id == run.job_id
                )
                if not (
                    challenge
                    and (same_owner or transferred)
                    and challenge.rule_id == rule["id"]
                    and challenge.mailbox_id == box.id
                    and challenge.recipient == recipient.lower()
                    and challenge.state in {"pending", "matched"}
                    and challenge.expires > current
                    and (kind == "auto" or challenge.kind in {"auto", kind})
                ):
                    raise ValueError(
                        "The saved email request is expired or does not belong to this application"
                    )
                metadata = s.get(Setting, "mail_request:" + challenge.id)
                generation = (metadata.value if metadata else {}).get("generation", 1)
                if transferred and resume.get("generation", generation) != generation:
                    raise ValueError("The saved email request generation changed")
                challenge.run_id = run_id
                return {**record(challenge), "request_id": challenge.id, "generation": generation}
            previous = s.scalar(
                select(MailChallenge).where(MailChallenge.run_id == run_id, MailChallenge.state.in_(LIVE))
            )
            if previous and previous.expires <= current:
                # Past its deadline: close it instead of reusing a window that can no longer match.
                previous.state, previous.payload, previous.reason = (
                    "expired",
                    "",
                    "Verification deadline ended",
                )
                previous = None
            if previous:
                if kind != "auto" and previous.kind not in {"auto", kind}:
                    raise ValueError("The active email request belongs to a different verification step")
                if previous.kind == "auto" and kind != "auto":
                    previous.kind = kind
                meta = s.get(Setting, "mail_request:" + previous.id)
                return {
                    **record(previous),
                    "request_id": previous.id,
                    "generation": (meta.value if meta else {}).get("generation", 1),
                }
            if self.sender_blocker(s, rule, run_id, kind, current):
                raise ValueError("Another application is verifying with this sender; retry after it finishes")
            # A legacy supplied timestamp cannot backdate a new request. Observed
            # activation links may explicitly recover their bounded current-run window.
            request_since = current
            if observed and since is not None:
                if kind not in {"link", "password_reset"} or set(rule["link_origins"]) != {
                    origin(rule["employer_origin"])
                }:
                    raise ValueError("An observed email request needs an exact employer link origin")
                start = datetime.fromisoformat(run.started_at or run.created_at).timestamp()
                request_since = max(
                    start, current - self.config.mail_wait_seconds, min(float(since), current)
                )
            if kind in {"code", "auto"}:
                # Starbucks (Eightfold) 2026-10-14: the code mail arrived 3-9 s after this run's
                # general wait opened, that wait was cancelled ("No email was needed"), and the
                # replacing code wait started after the mail, so after:since never saw it. Inherit
                # the cancelled wait's start from this same run (two minutes at most).
                earlier = [
                    float(x.since)
                    for x in s.scalars(
                        select(MailChallenge).where(
                            MailChallenge.run_id == run_id,
                            MailChallenge.state == "cancelled",
                            MailChallenge.since >= current - 120,
                        )
                    )
                    if x.since
                ]
                if earlier:
                    request_since = min(request_since, min(earlier))
            previous_requests = list(
                s.scalars(select(MailChallenge.id).where(MailChallenge.run_id == run_id))
            )
            generation = len(previous_requests) + 1
            wait = self.config.mail_wait_seconds
            if kind == "password_reset" and set(rule["sender_domains"]) & SHARED_RESET_SENDERS:
                # A reset mails within seconds when the account exists and never when it does not, and an open reset
                # holds the platform's one sender for every employer: Atlassian and Mass Markets (iCIMS Central) and
                # Westinghouse (SuccessFactors) each stopped all of their platform's sign-ups for 20 minutes waiting for
                # a reset that never came (2026-10-21).
                wait = min(wait, CENTRAL_RESET_WAIT)
            c = MailChallenge(
                run_id=run_id,
                rule_id=rule["id"],
                mailbox_id=box.id,
                recipient=recipient.lower(),
                kind=kind,
                since=request_since,
                expires=self.window_deadline(run, current + wait),
            )
            s.add(c)
            s.flush()
            s.add(
                Setting(
                    key="mail_request:" + c.id,
                    value={"request_id": c.id, "generation": generation, "at": now()},
                )
            )
            return {**record(c), "request_id": c.id, "generation": generation}

    def narrow(self, challenge_id, kind):
        """The website now shows what it sent (a code field, or a check-your-email page): an open
        "auto" request window accepts only that kind from here on."""
        if kind not in {"code", "link"}:
            return
        with self.db.exclusive() as s:
            c = s.get(MailChallenge, challenge_id)
            if c and c.state == "pending" and c.kind == "auto":
                c.kind = kind

    def outcome(self, challenge_id, state, reason=""):
        with self.db.exclusive() as s:
            if (c := s.get(MailChallenge, challenge_id)) and c.state in LIVE:
                c.state, c.reason, c.payload = state, reason[:200], ""
                # A consumed continuation must not keep pointing at its terminal
                # request when the website requires a fresh bounded resend.
                owner = s.get(Run, c.run_id)
                if owner and owner.packet.get("email_wait_resume", {}).get("challenge_id") == c.id:
                    packet = dict(owner.packet)
                    packet.pop("email_wait_resume", None)
                    owner.packet = packet

    def claim(self, challenge_id):
        from ..agent_settings import require

        require(self.db, "email")
        with self.db.exclusive() as s:
            c = s.get(MailChallenge, challenge_id)
            if not c or c.state != "matched" or c.expires < time.time():
                return None
            owner = s.get(Run, c.run_id)
            if self.window_deadline(owner, c.expires) <= time.time():
                c.state, c.payload, c.reason = "expired", "", "Application deadline ended"
                return None
            if not owner or (
                owner.state not in {"running", "submitting"} and not self.waiting_owner(owner, c.id)
            ):
                c.state, c.payload, c.reason = "cancelled", "", "Application no longer owns this verification"
                return None
            token = self.vault.open(c.payload)
            c.state, c.payload, c.reason = "consumed", "", "Matched email handed to its application owner"
            return token

    async def wait(self, challenge_id, *, max_wait_seconds=None):
        deadline = time.monotonic() + max_wait_seconds if max_wait_seconds is not None else None
        while True:
            with self.db.session() as s:
                c = s.get(MailChallenge, challenge_id)
                if not c or c.state not in {"pending", "matched"}:
                    raise ValueError("Email verification was cancelled or expired")
                expires = c.expires
            if token := self.claim(challenge_id):
                return token
            if time.time() >= expires:
                self.outcome(
                    challenge_id, "expired", "No uniquely matching verification email arrived in time"
                )
                raise ValueError("Verification email did not arrive before the deadline")
            if deadline is not None and time.monotonic() >= deadline:
                raise MailPending(self.persist_wait(challenge_id))
            # Browser verification should see fresh delivery without depending
            # on the background poller's position in its sleep interval.
            await self.poll_once()
            if token := self.claim(challenge_id):
                return token
            remaining = deadline - time.monotonic() if deadline is not None else self.config.mail_poll_seconds
            await asyncio.sleep(
                min(self.config.mail_poll_seconds, max(0.05, expires - time.time()), max(0.01, remaining))
            )

    def message_consumed(self, session, mailbox_id, message, challenge):
        """Preserve claims when replacing legacy Inbox UIDs with Gmail IDs.

        Archived mail has no recoverable old Inbox UID. Within the same rule,
        recipient and mailbox, its legacy request window remains unavailable
        until the old claim is reconciled to an immutable message identity.
        """
        mid = message["id"]
        aliases = message.get("legacyInboxIds", []) if mid.startswith("gm:") else []
        if any(session.get(MailConsumption, mailbox_id + ":" + value) for value in [mid, *aliases]):
            return True
        if not mid.startswith("gm:"):
            return False
        try:
            received = int(message["internalDate"]) / 1000
        except (ValueError, TypeError, KeyError, OverflowError):
            return True
        legacy = session.execute(
            select(MailConsumption.key)
            .join(MailChallenge, MailConsumption.challenge_id == MailChallenge.id)
            .where(
                MailChallenge.mailbox_id == mailbox_id,
                MailChallenge.rule_id == challenge["rule_id"],
                MailChallenge.recipient == challenge["recipient"].lower(),
                MailChallenge.since <= received,
                MailChallenge.expires >= received,
            )
        )
        return any(key.removeprefix(mailbox_id + ":").isdigit() for (key,) in legacy)
