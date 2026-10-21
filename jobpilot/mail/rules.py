"""Employer rules: which sender domains and link origins may satisfy an employer's challenge."""

from email.utils import getaddresses
from sqlalchemy import select
from urllib.parse import urlsplit
import re
import time
from ..db import MailChallenge
from ..db import MailRule
from ..db import Mailbox
from ..db import Run
from ..db import record
from ..email_alias import base_address
from ..network import public_url
from .constants import LIVE
from .extraction import extract_message
from .helpers import origin
from .models import RuleInput
from .senders import KNOWN_SENDERS


class RulesMixin:
    def add_link_origin(self, rule_id, link_origin):
        """Record an employer-owned login origin (careers.qualcomm.com for qualcomm.eightfold.ai)."""
        with self.db.exclusive() as s:
            row = s.get(MailRule, rule_id)
            if row and link_origin not in (row.link_origins or []):
                row.link_origins = [*(row.link_origins or []), link_origin][:20]
            return list(row.link_origins) if row else []

    async def save_rule(self, data):
        employer = origin(data.employer_origin)
        employer_path = urlsplit(data.employer_origin).path.rstrip("/") or "/"
        links = list(dict.fromkeys([employer] + [origin(x) for x in data.link_origins]))
        for url in links:
            if not url.startswith("https://") and not self.config.allow_private_urls:
                raise ValueError("Employer and verification origins must use HTTPS")
            await public_url(url, self.config.allow_private_urls)
        domains = [d.strip().lower() for d in data.sender_domains]
        if any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", d) or ".." in d
            for d in domains
        ):
            raise ValueError("Use exact sender domains, without @, wildcards, or URLs")
        with self.db.exclusive() as s:
            if not s.get(Mailbox, data.mailbox_id):
                raise ValueError("Connect this mailbox first")
            rule = s.scalar(
                select(MailRule).where(
                    MailRule.employer_origin == employer, MailRule.employer_path == employer_path
                )
            )
            if rule and s.scalar(
                select(MailChallenge.id).where(
                    MailChallenge.rule_id == rule.id, MailChallenge.state.in_(LIVE)
                )
            ):
                raise ValueError("Wait for this employer's active verification before changing its rule")
            if not rule:
                rule = MailRule(employer_origin=employer, employer_path=employer_path)
                s.add(rule)
            rule.mailbox_id, rule.sender_domains, rule.link_origins, rule.enabled = (
                data.mailbox_id,
                domains,
                links,
                data.enabled,
            )
            s.flush()
            return record(rule)

    def rule_for(self, url, *, session=None):
        if session is None:
            with self.db.session() as session:
                return self.rule_for(url, session=session)
        s = session
        rows = list(
            s.scalars(
                select(MailRule).where(MailRule.employer_origin == origin(url), MailRule.enabled.is_(True))
            )
        )
        path = urlsplit(url).path
        for rule in sorted(rows, key=lambda r: len(r.employer_path), reverse=True):
            if (
                rule.employer_path == "/"
                or path == rule.employer_path
                or path.startswith(rule.employer_path + "/")
            ):
                return record(rule)
        return None

    async def ensure_rule(self, job, recipient):
        """Known ATS senders are scoped to this employer and the applicant's mailbox."""
        existing = self.rule_for(job["url"])
        if existing:
            if job.get("ats") == "workday" and existing["sender_domains"] == ["myworkday.com"]:
                with self.db.exclusive() as s:
                    row = s.get(MailRule, existing["id"])
                    row.sender_domains = ["myworkday.com", "otp.workday.com"]
                existing["sender_domains"] = ["myworkday.com", "otp.workday.com"]
            return existing
        known = KNOWN_SENDERS
        if job.get("ats") not in known:
            return None
        with self.db.session() as s:
            box = s.scalar(
                select(Mailbox).where(Mailbox.email == base_address(recipient), Mailbox.state == "connected")
            )
            if not box:
                return None
            mailbox_id = box.id
        url = urlsplit(job["url"])
        prefix = "/" + url.path.strip("/").split("/")[0] if job["ats"] == "greenhouse" else "/"
        return await self.save_rule(
            RuleInput(
                mailbox_id=mailbox_id,
                employer_origin=origin(job["url"]) + prefix,
                sender_domains=known[job["ats"]],
            )
        )

    async def discover_rule(self, job, recipient, since):
        """Learn an unfamiliar sender only from authenticated, employer-bound mail.

        A code alone is insufficient to attribute a shared ATS sender. Require an
        exact employer-origin verification link in the same fresh message.
        """
        if existing := self.rule_for(job["url"]):
            return existing
        with self.db.session() as s:
            box = s.scalar(
                select(Mailbox).where(Mailbox.email == base_address(recipient), Mailbox.state == "connected")
            )
            if not box:
                return None
            mailbox_id = box.id
        listed = await self.request(
            mailbox_id,
            "/messages",
            {
                "q": f"after:{int(since)} to:{recipient} {{subject:verify subject:verification subject:confirm subject:activate subject:code}}",
                "maxResults": 10,
                "includeSpamTrash": "true",
            },
        )
        if listed.get("nextPageToken"):
            return None
        matches = set()
        for item in listed.get("messages", []):
            message = await self.read_message(mailbox_id, item["id"])
            if message is None:
                continue
            headers = message.get("payload", {}).get("headers", [])
            senders = getaddresses([h["value"] for h in headers if h["name"].lower() == "from"])
            if len(senders) != 1:
                continue
            domain = senders[0][1].rpartition("@")[2].lower()
            rule = {"sender_domains": [domain], "link_origins": [origin(job["url"])]}
            challenge = {
                "since": since,
                "expires": time.time() + 1,
                "recipient": recipient,
                "kind": "password_reset",
            }
            # Link extraction normally avoids ambiguous code+link messages. Here
            # we only use the link to prove employer ownership, never open it.
            token = extract_message(message, rule, {**challenge, "kind": "link"}, identity_only=True)
            if token and token["kind"] == "link":
                matches.add(domain)
        if len(matches) != 1:
            return None
        return await self.save_rule(
            RuleInput(
                mailbox_id=mailbox_id,
                employer_origin=origin(job["url"]),
                sender_domains=list(matches),
                link_origins=[origin(job["url"])],
            )
        )

    def restrict_kind(self, challenge_id, run_id, kind):
        """Narrow an owned request after its dedicated verification UI appears."""
        if kind not in {"code", "link", "password_reset"}:
            raise ValueError("Unsupported verification kind")
        with self.db.exclusive() as session:
            row = session.get(MailChallenge, challenge_id)
            owner = session.get(Run, run_id)
            if not row or row.run_id != run_id or not owner or owner.state not in {"running", "submitting"}:
                raise ValueError("Verification request does not belong to this active application")
            if row.kind == "auto" and row.state == "pending":
                row.kind = kind
