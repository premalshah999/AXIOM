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
from ..ats import custom_like
from ..network import public_url
from .constants import LIVE
from .extraction import extract_message
from .helpers import origin
from .models import RuleInput
from .senders import KNOWN_SENDERS
from .senders import employer_label
from .senders import seeded_senders
from .senders import tenant_tokens


def connected_mailbox(session, recipient):
    """The connected mailbox that receives this address, a dotted or tagged alias included (email_alias.mailbox_key). A person
    applying as a school or work address that forwards to the one Gmail they connected has mail there under the address they
    applied with, and no rule, no window and no search existed for them on any platform (2026-11-01). With one mailbox
    connected it is the place to look; extract_message still takes only mail addressed to the applicant. Several mailboxes
    and none of them this address: nothing says which, so none."""
    from ..email_alias import same_mailbox

    boxes = list(session.scalars(select(Mailbox).where(Mailbox.state == "connected")))
    exact = next((box for box in boxes if same_mailbox(box.email, recipient)), None)
    return exact or (boxes[0] if len(boxes) == 1 else None)


class RulesMixin:
    def posting_url(self, url, session=None):
        """Where a job really is: the posting a short link points to (the saved answer only, never the network), else the
        url itself. A job entered through go.vansh.nyc/HD had its mail rule keyed on go.vansh.nyc, so the Workday reset it
        asked for (a link on interdigital.wd5.myworkdayjobs.com) was refused on every poll as a foreign origin
        (Interdigital 4c9405de, 2026-10-29), and its sender list was another employer's. The rule, its link origins and the
        tenant tokens follow the posting."""
        from .. import shortlinks

        if not url or not shortlinks.is_shortlink(url):
            return url
        if session is None:
            with self.db.session() as session:
                return self.posting_url(url, session)
        known, final = shortlinks.cached(session, url)
        return final if known and final else url

    def sibling_senders(self, rule):
        """Employer-owned sender domains that other employers' rules learned and this employer's name carries.

        TikTok's rule learned mail.bytedance.net; ByteDance's own rule, with the same name in its host and mail domain,
        never saw it (15 of 15 ByteDance code windows expired, 2026-10-31). A domain is shared only when its registrable
        label is one of this employer's own name tokens, and never when it belongs to a platform family or tenant suffix:
        a shared sender is attributed by tenant identity (extract_message), not by being on a sibling's list."""
        tokens = {t for t in tenant_tokens(rule["employer_origin"]) if len(t) >= 4}
        platform = {d for domains in KNOWN_SENDERS.values() for d in domains}
        have = set(rule["sender_domains"] or [])
        found = []
        with self.db.session() as s:
            for other in s.scalars(select(MailRule).where(MailRule.employer_origin != rule["employer_origin"])):
                for d in other.sender_domains or []:
                    if d in have or d in found or any(d == p or d.endswith("." + p) for p in platform):
                        continue
                    if employer_label(d) in tokens:
                        found.append(d)
        return found

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
        url = self.posting_url(url, s)
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
        job = {**job, "url": self.posting_url(job.get("url", ""))}  # a short link's rule belongs to the posting it opens
        existing = self.rule_for(job["url"])
        if existing:
            # A rule built from its platform's senders gains the senders learned since. L3Harris's rule (2026-10-21)
            # predates ns2cloud.com, SAP's government-cloud sender (added 10-23), so its reset mail could never match.
            # Workday's otp.workday.com was added the same way. A rule naming any other domain is the owner's; kept.
            family = KNOWN_SENDERS.get(job.get("ats"), [])
            current = list(existing["sender_domains"])
            missing = [d for d in family if d not in current]
            from ..accounts.tenants import registrable

            # A career site on the employer's own domain that turns out to be a platform (Garmin's careers.garmin.com is
            # iCIMS, 2026-10-27) had a rule naming only that domain; the platform's verification link was refused as
            # "sender". Its link must still lead back to the employer's origin.
            own_site = current == [registrable(urlsplit(job.get("url", "")).hostname or "")]
            if current and missing and (set(current) <= set(family) or own_site):
                with self.db.exclusive() as s:
                    row = s.get(MailRule, existing["id"])
                    row.sender_domains = [*current, *missing]
                existing["sender_domains"] = [*current, *missing]
            if siblings := self.sibling_senders(existing):
                with self.db.exclusive() as s:
                    row = s.get(MailRule, existing["id"])
                    row.sender_domains = [*existing["sender_domains"], *siblings]
                existing["sender_domains"] = [*existing["sender_domains"], *siblings]
            return existing
        known = KNOWN_SENDERS
        # A named platform that only the generic driver runs (ats.LABEL_ONLY) keeps the custom site's rule: ClearCompany's
        # sign-in link and Pinpoint's mail were matched through it before they had a name.
        own_site = custom_like(job.get("ats")) and (urlsplit(job.get("url", "")).hostname or "")
        if job.get("ats") not in known and not own_site:
            return None
        with self.db.session() as s:
            box = connected_mailbox(s, recipient)
            if not box:
                return None
            mailbox_id = box.id
        url = urlsplit(job["url"])
        prefix = "/" + url.path.strip("/").split("/")[0] if job["ats"] == "greenhouse" else "/"
        # An employer's own career site mails from its own domain. Intuit (2026-10-26) refused the saved password, and
        # with no sender rule its reset was never requested ("Connect Gmail and an email rule for this employer").
        from ..accounts.tenants import registrable

        senders = known.get(job["ats"]) or [registrable(own_site)]
        senders = [*senders, *(d for d in seeded_senders(url.hostname) if d not in senders)]
        return await self.save_rule(
            RuleInput(mailbox_id=mailbox_id, employer_origin=origin(job["url"]) + prefix, sender_domains=senders)
        )

    async def discover_rule(self, job, recipient, since):
        """Learn an unfamiliar sender only from authenticated, employer-bound mail.

        A code alone is insufficient to attribute a shared ATS sender. Require an
        exact employer-origin verification link in the same fresh message.
        """
        if existing := self.rule_for(job["url"]):
            return existing
        with self.db.session() as s:
            box = connected_mailbox(s, recipient)
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
            # A window armed for an activation link before Create Account (Workday) and a page that then asks for an emailed code:
            # the mail is a code. Left as a link window, it refused the code it was waiting for until it ran out.
            if row.state == "pending" and (row.kind == "auto" or (row.kind == "link" and kind == "code")):
                row.kind = kind
