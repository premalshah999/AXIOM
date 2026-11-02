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
from ..ats import custom_like
from ..email_alias import same_mailbox
from .constants import LIVE
from .extraction import RESET_URL
from .extraction import UNBRANDED_MAX_CANDIDATES
from .extraction import UNBRANDED_SEARCH_TERMS
from .extraction import VERIFY_URL
from .extraction import extract_message
from .extraction import latest_employer_code
from .extraction import names_employer
from .extraction import newest_reset_link
from .helpers import origin
from .senders import EMPLOYER_SENDER_FAMILIES
from .senders import GENERIC_LABELS
from .senders import KNOWN_SENDERS
from .senders import PUBLIC_MAIL_DOMAINS
from .senders import decorated_senders
from .senders import icims_names
from .senders import oracle_pod
from .senders import sender_domains_overlap
from .senders import tenant_tokens


# Platforms whose mail has no attribution rules of its own, so a message that names the employer is enough to learn them
# (family_learning). Workday learns from an exact tenant link; Oracle, iCIMS and Greenhouse attribute by their own rules.
LEARNABLE_FAMILIES = {"eightfold", "avature", "smartrecruiters", "jobvite", "taleo", "successfactors", "adp"}
# A gap this long between two looks at the clocks is the machine asleep, not a slow poll.
SLEEP_GAP_SECONDS = 4
# Searches a window past its end still gets before it is called empty (a poll that fails does not use them up).
LAST_SEARCHES = 3
# How long a matched message stays claimable after it matched. The window decides what is accepted; once a mail is in
# the vault its owner needs a worker slot to claim it. A SuccessFactors reset (Farmers 2026-10-26) matched 70 s before its
# 180 s window closed and expired unclaimed; Allen Institute's link (2026-10-30) matched 5 s after its run parked and the
# resume was still retrying when the window ended. A reset link outlives a code by hours.
MATCH_HOLD_SECONDS = {"code": 600, "auto": 600, "link": 1800, "password_reset": 1800}


# Hosts that belong to one employer alone on a platform that gives each a host of its own. A link to exactly such an origin
# in an authenticated mail addressed to the applicant, inside a request window, is the employer's own mail whatever domain
# sent it. The shared hosts (jobs.lever.co, job-boards.greenhouse.io, myjobs.adp.com, career4.successfactors.com) are not here:
# many employers link to those.
TENANT_HOSTS = {
    "workday": re.compile(r"[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com"),
    "eightfold": re.compile(r"[a-z0-9-]+\.eightfold\.ai"),
    "taleo": re.compile(r"[a-z0-9-]+\.taleo\.net"),
    "avature": re.compile(r"[a-z0-9-]+\.avature\.net"),
}
# A first label that is the platform's own front door, not a tenant: production's app.eightfold.ai/careers/job/...?domain=<employer>
# serves many employers from one host.
NOT_A_TENANT = GENERIC_LABELS | {"app", "portal", "login", "secure", "hire"}
# How a link or reset mail from a sender nobody listed is found. Gmail matches whole words: "Password activation" (Avature) and
# "Set up a user name and password" (Taleo) carry neither "reset" nor "verify".
LEARNING_SUBJECTS = {
    "password_reset": ["subject:reset", "subject:password", "subject:activation", "subject:activate"],
    "link": ["subject:verify", "subject:verification", "subject:confirm", "subject:activate", "subject:activation", "subject:sign", "subject:password"],
}


def own_site(host):
    """A host no platform in the registry owns: the employer's own career site, whatever platform runs behind it (jobs.ea.com is
    Avature, apply.careers.microsoft.com is Eightfold)."""
    from ..ats import REGISTRY

    return not any(host == d or host.endswith("." + d) for platform in REGISTRY for d in platform.domains)


def employer_only_host(ats, url):
    """Whether this posting's host is used by this employer alone: a platform tenant's own host, or the employer's own career
    site. A short link or a shared portal is nobody's own."""
    from ..admission import SHARED_LINK_HOSTS, SHARED_PORTAL_HOSTS

    try:
        parts = urlsplit(url or "")
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.scheme not in {"https", "http"} or not host or host in SHARED_LINK_HOSTS | SHARED_PORTAL_HOSTS:
        return False
    if own_site(host):
        return True
    pattern = TENANT_HOSTS.get(ats)
    return bool(pattern and pattern.fullmatch(host)) and host.split(".")[0] not in NOT_A_TENANT


def another_employer_on_site(session, job, url):
    """A job of another company on this very origin: a career-site host the registry does not know and several employers use.
    Its links say nothing about whose mail it is. A wrong application needs a second job in this database, so looking there
    closes the case on any install."""

    def squashed(name):
        return re.sub(r"[^a-z0-9]+", "", str(name or "").casefold())

    mine = squashed(job.company)
    others = session.scalars(select(Job.company).where(Job.url.like(origin(url) + "/%"), Job.id != job.id).distinct())
    return any(squashed(name) and squashed(name) != mine for name in others)


def can_learn_sender(session, job, posting, rule, kind):
    """Whether a link in this window can prove a sender nobody listed (employer_step): only on the posting's own origin, on a
    host that belongs to this employer alone. Never a code window: a code has no origin to check."""
    if not job or not rule or kind not in {"link", "password_reset"}:
        return False
    try:
        return bool(
            employer_only_host(job.ats, posting)
            and origin(posting) == rule.get("employer_origin")
            and not (own_site((urlsplit(posting).hostname or "").lower()) and another_employer_on_site(session, job, posting))
        )
    except ValueError:
        return False


def employer_step(link, employer_origin):
    """The proof itself: the mail's one link is on exactly the employer's origin and its address is a verification or reset
    step. The links extract_message accepts can also be a click tracker or SAP's shared hosts (they lead on to the employer once
    the sender is known), and a job alert's link to a posting on the site is no proof either."""
    try:
        return origin(link) == employer_origin and bool(VERIFY_URL.search(link) or RESET_URL.search(link))
    except ValueError:
        return False


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
            box = s.get(Mailbox, challenge.mailbox_id) if challenge else None
            if box and challenge.recipient and not same_mailbox(box.email, challenge.recipient):
                # Nothing else tells someone why: they read their Gmail, and the employer was given another address.
                run.reason = (
                    f"The employer's {what} email did not reach {box.email}: this application used {challenge.recipient}. "
                    "Forward that address to your Gmail, or use your Gmail address in your profile"
                )
            job = s.get(Job, run.job_id)
            if job and job.status == "waiting_email":
                job.status = run.state
            ended.append((run.id, run.reason))
        return ended

    def sleep_credit(self, wall=None, mono=None):
        """Seconds the machine slept since the last look at the clocks. Wall time runs through sleep, time.monotonic()
        (mach_absolute_time on macOS) does not, so their difference is the sleep. A window is time awake: the Mac was
        asleep 86% of 2026-10-31 and 20 of 63 windows ended while it slept, each expiring unsearched."""
        wall = time.time() if wall is None else wall
        mono = time.monotonic() if mono is None else mono
        last_wall, last_mono = self.__dict__.get("awake_mark") or (wall, mono)
        self.awake_mark = (wall, mono)
        gap = (wall - last_wall) - (mono - last_mono)
        return gap if gap > SLEEP_GAP_SECONDS else 0.0

    async def last_search(self, challenge_id):
        """The search a window gets after its end and before it is called empty. A mail that arrived inside the window
        but is read after it (the poll was not running: asleep, or busy) still matches; one that arrived later never
        does (extract_message). poll_once makes the search and expires the window; a sweep already in flight makes it."""
        for _ in range(LAST_SEARCHES):
            with self.db.session() as s:
                row = s.get(MailChallenge, challenge_id)
                if not row or row.state != "pending":
                    return
            await self.poll_once()
            await asyncio.sleep(0.2)

    async def poll_once(self):
        from ..agent_settings import enabled

        self.sleep_owed = self.__dict__.get("sleep_owed", 0.0) + self.sleep_credit()
        if not enabled(self.db, "email"):
            return
        # The active sweep already services all requests. Browser owners must
        # not line up behind it and each repeat the same slow mailbox scan.
        # They can claim its result on the next tick or persist their wait now.
        if self.poll_lock.locked():
            return
        async with self.poll_lock:
            last_chance = []
            with self.db.exclusive() as s:
                current = time.time()
                slept, self.sleep_owed = self.sleep_owed, 0.0
                for c in s.scalars(select(MailChallenge).where(MailChallenge.state.in_(LIVE))):
                    run = s.get(Run, c.run_id)
                    if slept and c.state in {"pending", "matched"}:
                        c.expires += slept
                    proposed = c.expires
                    c.expires = self.window_deadline(run, c.expires)
                    owner_gone = not run or (run.state not in {"running", "submitting"} and not self.waiting_owner(run, c.id))
                    if c.state == "consumed" and c.payload and owner_gone and proposed >= current:
                        # A reset's link handed over and its application stopped (the app quit, the browser died) before it
                        # said it had used it: kept sealed for that application's next attempt (reclaim) until its hold ends.
                        # The hold is the application's, not the attempt's: the stopped run's own deadline does not end it.
                        c.expires = proposed
                        c.state, c.reason = "cancelled", "The application stopped after the email was handed over"
                    elif owner_gone or (
                        c.expires < current
                        and (
                            c.state != "pending"
                            or c.expires < proposed  # the application's own deadline, not the delivery window
                            or self.final_attempts.get(c.id, 0) >= LAST_SEARCHES
                        )
                    ):
                        # A claimed mail (consumed) keeps the reason it was handed over with: Fortinet's code was typed at
                        # 14:27 and the row then read "deadline ended" as if the mail had gone unused (2026-10-31).
                        c.reason = c.reason if c.state == "consumed" else "Application or verification deadline ended"
                        c.state, c.payload = "expired", ""
                    elif c.expires < current:
                        last_chance.append(c.id)
                for kept in s.scalars(
                    select(MailChallenge).where(MailChallenge.state == "cancelled", MailChallenge.payload != "")
                ):
                    if kept.expires < current:
                        kept.payload = ""  # the hold on an unused mail has ended
                ended = self.end_dead_email_waits(s)
                pending = [
                    record(c)
                    for c in s.scalars(select(MailChallenge).where(MailChallenge.state == "pending"))
                ]
            for run_id, why in ended:
                self.db.event(run_id, "email_wait_ended", why)
            searched = set()
            for c in pending:
                with self.db.session() as s:
                    row = s.get(MailRule, c["rule_id"])
                    rule = record(row) if row and row.enabled else None
                    run = s.get(Run, c["run_id"])
                    job = s.get(Job, run.job_id) if run else None
                    # Tenant identity: the employer this run applies to, never message text.
                    c["tenant_tokens"] = tenant_tokens(
                        self.posting_url(job.url if job else "", s) or (rule or {}).get("employer_origin", ""),
                        job.company if job else "",
                        sf_tenant(s, job),
                    )
                    platform = job.ats if job else ""
                    if custom_like(platform) and rule:
                        # A career site on the employer's domain that is a platform underneath is matched as that
                        # platform: Garmin's iCIMS link mail ("User account with Garmin International, Inc.", a
                        # tracking.icims.com link) was refused as "tenant" while the job read as custom (2026-10-27).
                        platform = next(
                            (
                                name
                                for name, domains in KNOWN_SENDERS.items()
                                if name != "custom" and set(domains) & set(rule["sender_domains"])
                            ),
                            "custom",  # a named platform with no sender family of its own is matched as a custom site
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
                                tenant_tokens(self.posting_url(peer_job.url or "", s), peer_job.company or "", sf_tenant(s, peer_job))
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
                    # Some employers send link and reset mail from a domain that names neither them nor their platform
                    # (Workday tenants from the employer's domain, an unfamiliar sender on a custom career site). A link to
                    # exactly the origin only this employer uses can prove that sender; a host shared by many employers
                    # cannot (employer_only_host).
                    posting = self.posting_url(job.url, s) if job else ""
                    c["sender_learning"] = can_learn_sender(s, job, posting, rule, c["kind"])
                if not rule:
                    self.outcome(c["id"], "cancelled", "Verification rule was removed or disabled")
                    continue
                try:
                    scan_started = time.monotonic()
                    senders = list(rule["sender_domains"])
                    if c["employer_senders"]:
                        senders += c["tenant_tokens"] + decorated_senders(c["tenant_tokens"])
                    terms = ["from:" + d for d in senders]
                    if c["ats"] == "oracle" and c["kind"] in {"code", "auto"}:
                        terms.append('subject:"confirm your identity"')
                    query = f"after:{int(c['since'])} to:{c['recipient']} " + "{" + " ".join(terms) + "}"
                    listed = await self.request(
                        c["mailbox_id"],
                        "/messages",
                        {"q": query, "maxResults": 20, "includeSpamTrash": "true"},
                    )
                    searched.add(c["id"])
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
                    if c["sender_learning"]:
                        # Beside the sender search, not inside it: a busy mailbox's "confirm" and "password" mail must never
                        # read as "too many candidates" and fail the window. The newest few are enough for a mail just asked for.
                        worded = await self.request(
                            c["mailbox_id"],
                            "/messages",
                            {
                                "q": f"after:{int(c['since'])} to:{c['recipient']} "
                                + "{"
                                + " ".join(LEARNING_SUBJECTS[c["kind"]])
                                + "}",
                                "maxResults": UNBRANDED_MAX_CANDIDATES,
                                "includeSpamTrash": "true",
                            },
                        )
                        seen = {m["id"] for m in messages}
                        messages += [m for m in worded.get("messages", []) if m["id"] not in seen]
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
                                    proof = (
                                        None
                                        if domain in PUBLIC_MAIL_DOMAINS  # anyone can send from these
                                        else extract_message(msg, proof_rule, c, identity_only=True)
                                    )
                                    if proof and employer_step(proof["value"], rule["employer_origin"]):
                                        token = extract_message(msg, proof_rule, c)
                                        if token:
                                            learned_domain = domain
                            if not token and diagnostics.get("rejection") == "sender" and c["employer_senders"]:
                                learned_domain = self.family_learning(msg, rule, c)
                                if learned_domain:
                                    token = extract_message(msg, {**rule, "sender_domains": [*rule["sender_domains"], *learned_domain]}, c)
                                    learned_domain = learned_domain if token else None
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
                    matches = newest_reset_link(latest_employer_code(matches, c), rule, c)
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
                            learned = [learned_domain] if isinstance(learned_domain, str) else list(learned_domain)
                            conflict = any(
                                other.run_id != c["run_id"]
                                and other.expires > time.time()
                                and (other_rule := s.get(MailRule, other.rule_id))
                                and sender_domains_overlap(learned, other_rule.sender_domains)
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
                            and (row.expires > time.time() or row.id in last_chance)  # extract_message bounded the mail
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
                            # The window decided what was accepted; the matched mail stays claimable (MATCH_HOLD_SECONDS).
                            row.expires = self.window_deadline(
                                owner, max(row.expires, time.time() + MATCH_HOLD_SECONDS.get(row.kind, 600))
                            )
                            if learned_domain:
                                learned = [learned_domain] if isinstance(learned_domain, str) else list(learned_domain)
                                current_rule.sender_domains = list(dict.fromkeys(current_rule.sender_domains + learned))
                                s.add(
                                    Event(
                                        run_id=row.run_id,
                                        kind="mail_sender_learned",
                                        message="Learned an authenticated sender from a mail linking to the employer's own exact origin"
                                        if isinstance(learned_domain, str)
                                        else "Learned the employer's platform sender: its mail names the employer and no other open window's",
                                        data={"sender_domain": learned[0], "sender_domains": learned, "challenge_id": row.id},
                                    )
                                )
                            s.add(MailConsumption(key=key, challenge_id=row.id))
                            meta = s.get(Setting, "mail_request:" + row.id)
                            generation = (meta.value if meta else {}).get("generation", 1)
                            if meta:
                                # sender_blocker stops holding the sender for a match nobody has claimed in a while.
                                meta.value = {**meta.value, "matched_at": time.time()}
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
                except (ValueError, httpx.HTTPError, KeyError, TypeError) as exc:
                    # Do not include provider responses, URLs, codes, or credentials in diagnostics.
                    count = self.note_failure("poll_scan", exc)
                    with self.db.exclusive() as s:
                        row = s.get(MailChallenge, c["id"])
                        box = s.get(Mailbox, c["mailbox_id"])
                        # Only a window that is still open: 16 finished challenges carried "Mailbox request failed" as
                        # their reason although they had matched, expired or been verified (2026-10-31).
                        if row and row.state == "pending":
                            row.reason = "Mailbox request failed; check the connection"
                            if not box or box.state != "connected":
                                row.state, row.payload = "failed", ""
                    if count <= 3 or count % 20 == 0:
                        self.db.event(
                            c["run_id"], "mail_poll_failed", "The mailbox search failed; it is tried again",
                            {"error": type(exc).__name__, "failures_so_far": count},
                        )
            if last_chance:
                # A window past its end got its one search (or three failed ones): it is empty now.
                with self.db.exclusive() as s:
                    for cid in last_chance:
                        self.final_attempts[cid] = self.final_attempts.get(cid, 0) + 1
                        row = s.get(MailChallenge, cid)
                        if row and row.state == "pending" and cid in searched:
                            row.state, row.payload, row.reason = (
                                "expired",
                                "",
                                "Application or verification deadline ended",
                            )
                while len(self.final_attempts) > 200:
                    self.final_attempts.pop(next(iter(self.final_attempts)))

    @staticmethod
    def family_learning(msg, rule, c):
        """The sender domains of the platform a rejected message comes from, when that mail is this employer's.

        AARP's career site (apply.aarp.org) is Eightfold under a label of "custom", so its rule listed only aarp.org; the
        mail ("AARP Careers" <confirm@eightfold.ai>, "Verify your email", 2026-10-31) was refused as `sender` on every poll.
        A platform family's senders belong on the rule of an employer that uses it. The proof is the mail's own: the
        sender is in a family without attribution rules of its own (Workday, Oracle, iCIMS and Greenhouse have theirs), the
        message names this employer in its subject, sender name or snippet, and names none of the other open windows'
        employers. Every other gate (DMARC, recipient, window, tenant label) still applies to the re-read."""
        senders = getaddresses([h["value"] for h in msg["payload"]["headers"] if h["name"].lower() == "from"])
        if len(senders) != 1 or not c.get("tenant_tokens"):
            return None
        domain = senders[0][1].rpartition("@")[2].lower()
        for name, family in KNOWN_SENDERS.items():
            if name in LEARNABLE_FAMILIES and any(domain == d or domain.endswith("." + d) for d in family):
                if names_employer(msg, c["tenant_tokens"]) and not any(
                    names_employer(msg, peer) for peer in c.get("peer_tokens") or []
                ):
                    return [d for d in family if d not in rule["sender_domains"]] or None
        return None

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
        words = LEARNING_SUBJECTS[c["kind"]]
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
        """Reconnect a Gmail mailbox marked reconnect_required once Gmail accepts its login again (checked at most every five
        minutes per mailbox). A transient refusal at wake-up left verification off for five hours (2026-10-22). This healed only
        a mailbox in .env, which is the one installation that has it; one connected in the app, as everyone else's is, stayed
        off until someone noticed the Email page (2026-11-01). A Google sign-in has no password to try and stays as it is."""
        from ..db import Mailbox
        from ..gmail_imap import login_ok

        env_address = (self.config.gmail_address or "").strip().lower()
        recently = datetime.fromtimestamp(time.time() - 300, UTC).isoformat()
        with self.db.session() as s:
            due = [
                (box.id, box.email, box.credentials)
                for box in s.scalars(select(Mailbox).where(Mailbox.state == "reconnect_required"))
                if (box.checked_at or "") <= recently
            ]
        healed_any = False
        for box_id, address, sealed in due:
            try:
                saved = self.vault.open(sealed) or {}
            except Exception:
                saved = {}
            password = saved.get("app_password") or (self.config.gmail_app_password if address == env_address else "")
            password = "".join(str(password or "").split())
            if not password:
                continue
            healed = await asyncio.to_thread(login_ok, address, password)
            with self.db.exclusive() as s:
                if box := s.get(Mailbox, box_id):
                    box.checked_at = datetime.now(UTC).isoformat()
                    if healed:
                        box.state, box.error = "connected", ""
            healed_any = healed_any or healed
        return healed_any

    async def replies_pass(self, check):
        try:
            await check(self)
        except Exception as exc:
            self.note_failure("replies", exc)  # the figure stays as it was; the next pass resumes from its cursor

    async def poll(self):
        last_receipts = last_drafts = last_replies = 0
        receipt_task = draft_task = reply_task = None
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
                    from ..replies import EVERY_SECONDS, check_replies

                    if (
                        enabled(self.db, "email")
                        and time.time() - last_replies > EVERY_SECONDS
                        and (reply_task is None or reply_task.done())
                    ):
                        if reply_task is not None:
                            await asyncio.gather(reply_task, return_exceptions=True)
                        last_replies = time.time()
                        reply_task = asyncio.create_task(self.replies_pass(check_replies))
                except Exception as exc:
                    self.note_failure("poll_loop", exc)  # by class only: public diagnostics never expose raw secrets
                await asyncio.sleep(self.config.mail_poll_seconds)
        finally:
            for task in (receipt_task, draft_task, reply_task):
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
