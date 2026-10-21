"""Gmail OAuth and narrowly scoped verification. No sending or inbox export."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime
from email.utils import getaddresses
from urllib.parse import urlencode, urlsplit

import httpx
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
from pydantic import BaseModel, Field
from sqlalchemy import select

from .db import (
    Event,
    Job,
    Mailbox,
    MailChallenge,
    MailConsumption,
    MailOAuth,
    MailRule,
    Run,
    Setting,
    now,
    record,
)
from .mail_errors import (
    MailAuthenticationError,
    MailCursorUpgrade,
    MailMessageError,
    MailOperationError,
    MailPending,
    MailTransportError,
)
from .email_alias import base_address
from .network import public_url

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
LIVE = {"pending", "matched", "consumed"}
# Oracle codes arrive in 14 s (median), 59 s (p90): an unmatched wait older than this, whose run
# has stopped, must not hold the shared sender for its whole durable window.
STALE_CODE_HOLD = 300


def run_company(session, run_id):
    """The normalized employer name of a run's job ("" when unknown never matches another)."""
    run = session.get(Run, run_id)
    job = session.get(Job, run.job_id) if run else None
    name = re.sub(r"[^a-z0-9]+", " ", str(getattr(job, "company", "") or "").casefold()).strip()
    return name or "unknown:" + str(run_id)


GREENHOUSE_SENDERS = {"greenhouse.io", "us.greenhouse-mail.io", "eu.greenhouse-mail.io", "anz.greenhouse.io"}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def origin(url):
    p = urlsplit(url)
    if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
        raise ValueError("Provide an http(s) origin without embedded credentials")
    host = p.hostname.lower()
    port = p.port
    if ":" in host:
        host = f"[{host}]"
    return f"{p.scheme}://{host}" + (
        f":{port}" if port and port != (443 if p.scheme == "https" else 80) else ""
    )


# iCIMS wraps the buttons of its account mail in its own click tracker: Schwab's "Reset your password" button went
# through clicks.icims.com and was rejected as token_format on every poll (2026-10-18/19).
CLICK_TRACKERS = {"https://clicks.icims.com", "https://tracking.icims.com"}


def tracker_allowed(link_origin, link_origins):
    """A tracker hop is followed only for iCIMS rules; open_link still lands only on the rule's own origins."""
    return link_origin in CLICK_TRACKERS and any(
        (urlsplit(o).hostname or "").endswith("icims.com") for o in link_origins
    )


# Multi-tenant ATS sender domains. Mail from them is attributed to one employer only by tenant
# identity (Workday names the tenant in the sender, e.g. mimecast@myworkday.com) or, when the
# message names no tenant, by the shared-sender lease that begin() holds for one run at a time.
TENANT_SENDERS = {"myworkday.com", "otp.workday.com"}
TENANT_SUFFIXES = ("oraclecloud.com", "icims.com", "taleo.net", "successfactors.com", "eightfold.ai")
GENERIC_LOCAL = re.compile(
    r"^(?:no-?reply|do-?not-?reply|donotreply|noreply\d*|notifications?|notify|mailer|info|support|"
    r"careers?|jobs?|recruit(?:ing|ment|er)?s?|talent|hr|otp|workday|alerts?|system|admin|apply|"
    r"candidates?|hello|team|accounts?|security|verify|verification)$"
)
GENERIC_LABELS = {
    "www",
    "careers",
    "career",
    "jobs",
    "job",
    "apply",
    "recruiting",
    "external",
    "candidate",
    "myworkdayjobs",
    "myworkdaysite",
    "myworkday",
    "workday",
    "oraclecloud",
    "oracle",
    "icims",
    "taleo",
    "successfactors",
    "sapsf",
    "eightfold",
    "jobs2web",
    "com",
    "net",
    "org",
    "edu",
    "gov",
    "hcmui",
    "candidateexperience",
    "sites",
    "inc",
    "corp",
    "corporation",
    "company",
    "group",
    "holdings",
    "technologies",
    "technology",
    "solutions",
    "services",
    "international",
    "global",
    "the",
    "and",
    "limited",
    "llc",
    "partners",
}
# Families whose verification mail often comes from the employer's own domain via the ATS.
EMPLOYER_SENDER_FAMILIES = {
    "oracle",
    "icims",
    "taleo",
    "successfactors",
    "eightfold",
    "smartrecruiters",
    "avature",
    "jobvite",
    "phenom",
    "custom",
}


CODE_CUE = re.compile(r"verification code|security code|one.time|passcode|\bpin\b|\botp\b|\bcode\b", re.I)


def tenant_tokens(url, company=""):
    """Employer identity tokens from the job URL (tenant host/path) and the company name."""
    p = urlsplit(url)
    words = re.split(r"[^a-z0-9]+", (p.hostname or "").lower())
    if re.search(r"myworkdaysite\.com$|myworkdayjobs\.com$", p.hostname or ""):
        # wd1.myworkdaysite.com/recruiting/<tenant>/<site>
        words += re.split(r"[^a-z0-9]+", p.path.lower())[:4]
    words += re.split(r"[^a-z0-9]+", (company or "").lower())
    # "HealthEdge" -> healthedge, "My Funded Futures" -> myfundedfutures (an employer's domain label).
    words.append(re.sub(r"[^a-z0-9]", "", (company or "").lower()))
    out = []
    for w in words:
        if len(w) >= 3 and w not in GENERIC_LABELS and not re.fullmatch(r"wd\d+|fa|ocs|\d+", w):
            if w not in out:
                out.append(w)
    return out[:8]


def sender_allowed(domain, domains, tokens=(), employer_senders=False):
    """Exact rule domain or its subdomain; with employer senders, a domain the employer owns."""
    if any(domain == d or domain.endswith("." + d) for d in domains):
        return True
    if not employer_senders:
        return False
    # The employer's own domain: its registrable label equals the tenant or company name exactly
    # ("healthedge.com" for HealthEdge). Prefix look-alikes ("healthedge-careers.com") never pass.
    return employer_label(domain) in {t for t in tokens if len(t) >= 4}


# Oracle Recruiting's standard one-time-code mail. Each employer's site may send it
# from its own domain (uthscsa.edu) rather than oraclecloud.com.
ORACLE_OTP = re.compile(r"confirm your identity using the one[- ]time pass ?code", re.I)

# Unbranded codes: a verification-specific cue is required ("promo code" never qualifies).
UNBRANDED_CODE_CUE = re.compile(
    r"verification|one.time|passcode|\b(?:security|login|sign.in|authentication|confirmation|access|verify) code\b|\botp\b",
    re.I,
)
UNBRANDED_NOT_CODE = re.compile(r"\b(?:promo|coupon|discount|unsubscribe|gift card)\b", re.I)
# Widened poll terms, always combined with this recipient and the request window.
UNBRANDED_SEARCH_TERMS = [
    '"verification code"',
    '"security code"',
    '"one-time code"',
    '"one time code"',
    '"one-time passcode"',
    '"login code"',
    '"sign-in code"',
    '"authentication code"',
    '"confirmation code"',
    "passcode",
    "otp",
]
UNBRANDED_MAX_CANDIDATES = 10


def sender_domains_overlap(first, second):
    """Sender rules include subdomains, so their leases must cover the same subtree."""
    return any(a == b or a.endswith("." + b) or b.endswith("." + a) for a in first for b in second)


def employer_label(domain):
    labels = domain.lower().split(".")
    if len(labels) >= 3 and labels[-2] in {"co", "com", "org", "net", "ac", "gov"} and len(labels[-1]) == 2:
        return labels[-3]
    return labels[-2] if len(labels) >= 2 else ""


# Oracle tenants mostly mail their codes under their own identity (mailbox audit, 2026-10-18):
# - the employer's domain (recruitingnoreply@ford.com, noreply@honeywell.com, careers@talent.saic.com);
# - the tenant's pod address (edel.fa.sender@workflow…oraclecloud.com, exvu.fa.sender@oracle.gmfinancial.com);
# - the display name ("Goldman Sachs Recruiting" <gsrecruiting@oracle.com>).
# Codes are attributed per tenant, so tenants no longer wait for each other's verification.
ORACLE_SENDERS = {"oraclecloud.com", "oracle.com"}
ORACLE_INFRA = re.compile(r"(?:^|\.)(?:oracle|oraclecloud)\.com$")
NAME_STOP = {"the", "of", "and", "at", "for", "inc", "llc", "ltd", "co", "corp", "corporation", "company", "group", "plc"}
GENERIC_FIRST = {"university", "bank", "national", "international", "global", "american", "united", "first", "general"}
PLATFORM_NAMES = {"oracle", "oraclecloud"}


def oracle_pod(url):
    """The tenant pod in an Oracle Recruiting host: edel.fa.us2… -> edel, fa-exvu-saasfaprod1.fa.ocs… -> exvu."""
    host = (urlsplit(url).hostname or "").lower()
    if not host.endswith("oraclecloud.com"):
        return ""
    first = host.split(".")[0]
    pod = re.fullmatch(r"fa-([a-z0-9]+)-saasfaprod\d+", first)
    return pod[1] if pod else first


def employer_names(company):
    """Compact names an employer signs its mail with: "fordmotor", "ford", "ucsf" (initials of 3+ words)."""
    words = [w for w in re.findall(r"[a-z0-9]+", (company or "").lower()) if w not in NAME_STOP]
    names = set()
    if words:
        names.add("".join(words))
        if len(words[0]) >= 4 and words[0] not in GENERIC_FIRST:
            names.add(words[0])
        if len(words) >= 3:
            names.add("".join(w[0] for w in words))
    return {n for n in names if len(n) >= 3}


def oracle_attributed(address, display, text, company, pod, peers):
    """Whether an Oracle code mail is this tenant's while other tenants' Oracle code windows are open.

    Exact signals: this tenant's pod address or the employer's own domain. Name signals (display name, then body)
    count only for names no open peer shares."""
    local, _, domain = address.lower().partition("@")
    names = employer_names(company)
    if pod and re.fullmatch(re.escape(pod) + r"\.fa\.sender", local):
        return True
    if not ORACLE_INFRA.search(domain) and employer_label(domain) in names:
        return True
    shared = set().union(*(employer_names(other) for other, _ in peers)) if peers else set()
    mine = {n for n in names - shared if len(n) >= 4}
    shown = re.sub(r"[^a-z0-9]", "", (display or "").lower())
    if any(n in shown for n in mine):
        return True
    # "Powered by Oracle" footers make the platform's own name noise in a body.
    body = re.sub(r"[^a-z0-9]", "", (text or "").lower())
    peer_long = {n for other, _ in peers for n in employer_names(other) if len(n) >= 6} - PLATFORM_NAMES
    return any(n in body for n in mine - PLATFORM_NAMES if len(n) >= 6) and not any(n in body for n in peer_long)


def tenant_conflict(address, domain, tokens):
    """True when a shared ATS sender visibly names a different tenant than this employer."""
    if not tokens:
        return False
    local = address.rpartition("@")[0].lower().split("+")[0]
    if domain in TENANT_SENDERS:
        compact = re.sub(r"[._-]", "", local)  # no_reply, no.reply, talent-acquisition
        return not (GENERIC_LOCAL.match(local) or GENERIC_LOCAL.match(compact)) and not any(
            t in local or t in compact for t in tokens
        )
    # Oracle's documented workflow transport names a data center, not an
    # employer. Recipient, DMARC, request window, one-use and shared-sender
    # lease checks still apply. Do not generalize to arbitrary Oracle hosts.
    if re.fullmatch(
        r"workflow\.(?:mail\.[a-z]{2,3}\d{1,2}\.oraclecloud\.com|"
        r"email\.[a-z]{2}-[a-z]+-\d{1,2}\.ocs\.oraclecloud\.com)",
        domain,
    ):
        return False
    for suffix in TENANT_SUFFIXES:
        if domain.endswith("." + suffix):
            labels = [x for x in domain[: -len(suffix) - 1].split(".") if x and x not in GENERIC_LABELS]
            # Region/pod labels (us2, em3, eu1) are infrastructure, not tenants.
            named = [
                x for x in labels if not re.fullmatch(r"wd\d+|fa|ocs|mail|email|[a-z]{2,3}\d{1,2}|\d+", x)
            ]
            return bool(named) and not any(t in x or x in t for x in named for t in tokens)
    return False


# Sender domains each ATS family uses for verification, reset and receipt mail. Subdomains of
# these are accepted (DMARC-aligned); multi-tenant ones additionally need tenant identity.
KNOWN_SENDERS = {
    "greenhouse": ["greenhouse.io", "us.greenhouse-mail.io", "eu.greenhouse-mail.io", "anz.greenhouse.io"],
    "workday": ["myworkday.com", "otp.workday.com"],
    "icims": ["icims.com"],
    "oracle": ["oraclecloud.com", "oracle.com"],
    "taleo": ["taleo.net"],
    "successfactors": ["successfactors.com", "successfactors.eu", "sapsf.com"],
    "eightfold": ["eightfold.ai"],
    "smartrecruiters": ["smartrecruiters.com"],
    "avature": ["avature.net"],
    "jobvite": ["jobvite.com"],
    "adp": ["adp.com"],
    # Dayforce ID (dfid.dayforcehcm.com) registration and sign-in codes.
    "dayforce": ["dayforcehcm.com", "dayforce.com"],
}
# Platforms whose leases span employers on one sender rule. A start waits while another job holds its lease (Oracle:
# since per-tenant attribution, only a run for the same tenant), instead of starting a browser that can only defer at
# its own code step ("email sender busy", 2026-10-17/18).
SHARED_SENDER_ATS = {"oracle", "icims"}


class RuleInput(BaseModel):
    mailbox_id: str
    employer_origin: str = Field(max_length=2048)
    sender_domains: list[str] = Field(min_length=1, max_length=20)
    link_origins: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True


class Vault:
    def __init__(self, config):
        path = config.data_dir / "mail-key"
        if config.mail_encryption_key:
            key = config.mail_encryption_key.encode()
        else:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as f:
                    f.write(Fernet.generate_key())
            key = path.read_bytes().strip()
        self.cipher = Fernet(key)

    def seal(self, value):
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def open(self, value):
        return json.loads(self.cipher.decrypt(value.encode()))


def authenticated_sender(receiving_auth, domain):
    if not re.search(r"\bdmarc=pass\b", receiving_auth, re.I):
        return False
    if re.search(r"\bheader\.from=" + re.escape(domain) + r"(?:\s|;|$)", receiving_auth, re.I):
        return True
    # Workday's receiving result names workday.com for From: otp.workday.com.
    # Accept this parent alignment only with a passing DKIM signature for the
    # exact sender domain; a sibling/look-alike signature is insufficient.
    parent = re.search(r"\bheader\.from=([a-z0-9.-]+)(?:\s|;|$)", receiving_auth, re.I)
    return bool(
        parent
        and domain.endswith("." + parent[1].lower())
        and re.search(
            r"\bdkim=pass\b[^;]*\bheader\.i=@" + re.escape(domain) + r"(?:\s|;|$)",
            receiving_auth,
            re.I,
        )
    )


def extract_message(message, rule, challenge, *, identity_only=False, diagnostics=None):
    """Return one unambiguous token; message text never becomes model instructions."""

    def reject(reason):
        if diagnostics is not None:
            diagnostics["rejection"] = reason
        return None

    try:
        received = int(message.get("internalDate", "0")) / 1000
    except (ValueError, TypeError, OverflowError):
        return reject("invalid_message")
    if received < challenge["since"] or received > challenge["expires"]:
        return reject("request_window")
    headers = message.get("payload", {}).get("headers", [])
    values = {}
    for h in headers:
        values.setdefault(h["name"].lower(), []).append(h["value"])
    senders = getaddresses(values.get("from", []))
    if len(senders) != 1:
        return reject("sender")
    address = senders[0][1].lower()
    domain = address.rpartition("@")[2]
    tokens = challenge.get("tenant_tokens") or []
    # An Oracle code mail from a domain outside the rule is accepted only after its
    # body proves to be that template (checked once the text is read), and still
    # needs aligned DKIM/SPF, this recipient and this request's window.
    oracle_template = challenge.get("ats") == "oracle" and challenge["kind"] in {"code", "auto"}
    outside_rule = False
    # Unbranded code: a sender outside the rule is decided only after its body is read,
    # and only when exactly one code window is open for this recipient at arrival.
    unbranded_sender = False
    if not domain or not sender_allowed(
        domain, rule["sender_domains"], tokens, challenge.get("employer_senders", False)
    ):
        if oracle_template and domain:
            outside_rule = True
        elif domain and challenge.get("open_windows") is not None and challenge["kind"] in {"code", "auto"}:
            unbranded_sender = True
        else:
            return reject("sender")
    if (
        not (outside_rule or unbranded_sender)
        and tenant_conflict(address, domain, tokens)
        and not (
            # Links are opened only on this employer's own origins (link_origins
            # below), so another tenant's link can never be used. The sender-name
            # heuristic rejected 133 Workday verification mails; none of the
            # verified Workday runs had a tenant rejection.
            challenge.get("kind") in {"link", "password_reset"}
            and rule.get("link_origins")
            and all(not o.endswith((".myworkday.com", "//myworkday.com")) for o in rule["link_origins"])
        )
    ):
        return reject("tenant")
    recipients = getaddresses(values.get("to", []) + values.get("delivered-to", []))
    if challenge["recipient"].lower() not in {a.lower() for _, a in recipients}:
        return reject("recipient")
    # Only trust Google's receiving authentication result with aligned DMARC.
    receiving_auth = next(
        (
            value
            for value in values.get("authentication-results", [])
            if re.match(r"\s*mx\.google\.com\s*;", value, re.I)
        ),
        "",
    )
    if not authenticated_sender(receiving_auth, domain):
        return reject("authentication")
    reset = challenge["kind"] == "password_reset"
    link_cue = (
        r"reset.{0,30}password|password.{0,30}reset"
        if reset
        else r"verif|confirm|sign.?in|log.?in|continue|activate"
    )
    message_link_cue = bool(re.search(link_cue, " ".join(values.get("subject", [])), re.I))
    texts, links, size = [], [], 0
    cued = set()  # Anchors whose own text is the cue, not only the subject.

    def walk(part, depth=0):
        nonlocal size
        if depth > 15 or size > 256_000:
            return
        # Attached/forwarded messages are separate requests, even when the outer
        # envelope authenticates. Only parse the employer's inline message.
        if (
            part.get("mimeType") == "message/rfc822"
            or part.get("filename")
            or any(
                h.get("name", "").lower() == "content-disposition"
                and re.match(r"\s*attachment\b", h.get("value", ""), re.I)
                for h in part.get("headers", [])
            )
        ):
            return
        encoded = part.get("body", {}).get("data", "")
        if encoded and part.get("mimeType") in {"text/plain", "text/html"}:
            if len(encoded) > 350_000:
                return
            try:
                raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8", "replace")
            except (ValueError, TypeError):
                return
            size += len(raw)
            if size > 256_000:
                return
            soup = BeautifulSoup(raw, "html.parser") if part["mimeType"] == "text/html" else None
            if soup:
                for hidden in soup.select("script, style, template, blockquote"):
                    hidden.decompose()
            text = soup.get_text(" ", strip=True) if soup else raw
            texts.append(text)
            if soup:
                for a in soup.select("a[href]"):
                    anchor_cue = bool(re.search(link_cue, a.get_text(" "), re.I))
                    if anchor_cue:
                        cued.add(a["href"])
                    if anchor_cue or message_link_cue:
                        links.append(a["href"])
            # Workday also sends bare URLs inside HTML, without <a> elements.
            # Require the same nearby reset/verification cue as plain text.
            for match in re.finditer(r"https://[^\s<>\"']+", text):
                if message_link_cue or re.search(
                    link_cue, text[max(0, match.start() - 120) : match.end()], re.I
                ):
                    links.append(match.group().rstrip(".,)"))
        for child in part.get("parts", [])[:30]:
            walk(child, depth + 1)

    walk(message.get("payload", {}))
    # Some employers label the OTP in the subject. It has passed the same
    # envelope, tenant, recipient and window checks as the message body.
    text = "\n".join(values.get("subject", []) + texts)
    if outside_rule and not ORACLE_OTP.search(text):
        return reject("sender")
    if oracle_template and challenge.get("oracle_peers") and not oracle_attributed(
        address, senders[0][0], text, challenge.get("employer_name", ""), challenge.get("oracle_pod", ""),
        challenge["oracle_peers"],
    ):
        return reject("tenant")
    # SAP's SuccessFactors account mail labels its code "Passcode: …" (2026-10-16).
    codes = set(
        re.findall(
            r"(?i:\b(?:verification code|security code|authentication code|one.time (?:code|passcode|password)|confirmation code|login code|sign.in code|passcode|otp|code|pin)\s*(?:(?:is|below)\s*)?[:#-]?\s*)\b([A-Z0-9]{4,10})\b",
            text,
        )
    )
    codes.update(
        re.findall(
            r"(?i:security code field on your application\s*:)\s*([A-Za-z0-9]{8})\b",
            text,
        )
    )
    # Individually styled digits become spaces after HTML extraction. Accept
    # that layout only immediately after an explicit OTP label, never by
    # joining unrelated numbers elsewhere in the message.
    codes.update(
        re.sub(r"[ \t\u200b\u200c\u200d]", "", value)
        for value in re.findall(
            r"(?i:\b(?:verification code|security code|authentication code|one.time (?:code|passcode|password)|confirmation code|login code|sign.in code|passcode|otp|pin)\s*(?:is\s*)?[:#-]?\s*)"
            r"(?<!\d)([0-9](?:[ \t\u200b\u200c\u200d]*[0-9]){3,9})(?![ \t\u200b\u200c\u200d]*[0-9])",
            text,
        )
        if all(len(group) == 1 for group in re.split(r"[ \t\u200b\u200c\u200d]+", value))
        or not re.search(r"[ \t\u200b\u200c\u200d]", value)
    )
    codes.update(
        re.findall(
            r"\b([0-9]{4,10})\b(?i:\s+is your (?:verification|security|authentication|one.time|login|sign.in) (?:code|passcode|password))",
            text,
        )
    )
    codes -= {"PLEASE", "BELOW", "EXPIRES", "VALID", "YOUR", "REQUEST"}
    fallback_code = False
    # An "auto" wait (code or link not yet known) may use it only when the mail has no link at all
    # (Starbucks' Eightfold code mail was rejected as token_format, 2026-10-13).
    if (
        not codes
        and (challenge["kind"] == "code" or (challenge["kind"] == "auto" and not links))
        and CODE_CUE.search(text)
        and not unbranded_sender
    ):
        # "...code to continue your application with Acme: 482915." One standalone number only;
        # years, dates and phone fragments are not codes. Never used when a link may be meant.
        numbers = {
            n
            for n in re.findall(r"(?<![\d$.,:/+-])\b(\d{4,8})\b(?![\d.,/-]\d)", text)
            if not 1900 <= int(n) <= 2100
        }
        if len(numbers) == 1:
            codes = numbers
            fallback_code = True
    # Unbranded OTP: this recipient's request is the only open code window in the
    # mailbox when the mail arrived, the text has a verification cue, and exactly one
    # labeled code with a digit is present. Links never take this path.
    covering = [
        window for window in (challenge.get("open_windows") or []) if window[1] <= received <= window[2]
    ]
    single_window = len(covering) == 1 and covering[0][0] == challenge.get("id")
    unbranded_cue = bool(UNBRANDED_CODE_CUE.search(text))
    unbranded_ok = bool(
        challenge.get("open_windows") is not None
        and challenge["kind"] in {"code", "auto"}
        and single_window
        and unbranded_cue
        and not UNBRANDED_NOT_CODE.search(text)
        and not fallback_code
        and len(codes) == 1
        and re.fullmatch(r"[A-Za-z0-9]{4,10}", next(iter(codes)))
        and any(ch.isdigit() for ch in next(iter(codes)))
    )
    if unbranded_sender and not unbranded_ok:
        return reject("unbranded_ambiguous" if unbranded_cue and not single_window else "sender")
    # A shared Greenhouse sender identifies the platform, not the employer.
    # Require the employer's whole name in the inline mail before considering
    # its code. Never borrow a sibling employer's newest message. The only
    # exception is an outside-sender unbranded code that passed the sole-window
    # rule; an in-rule shared-sender mail still needs the employer's name.
    if (
        challenge.get("ats") == "greenhouse"
        and challenge["kind"] in {"code", "auto"}
        and not (unbranded_sender and unbranded_ok)
    ):
        name = re.sub(r"[^a-z0-9]+", " ", challenge.get("employer_name", "").casefold()).strip()
        name = re.sub(r"\s+(?:inc|llc|ltd|corp|corporation|limited)$", "", name).strip()
        body = " " + re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip() + " "
        if not name or " " + name + " " not in body:
            return reject("employer_identity")
    valid_links = set()
    rejected_origins = set()
    for link in links:
        try:
            link_origin = origin(link)
            if urlsplit(link).scheme == "https" and link_origin in rule["link_origins"]:
                valid_links.add(link)
            elif (
                urlsplit(link).scheme == "https"
                and link in cued
                and tracker_allowed(link_origin, rule["link_origins"])
            ):
                # The employer's own button behind iCIMS's click tracker. Its anchor text carries the cue; footer
                # links behind the same tracker stay rejected even when the subject is the cue.
                valid_links.add(link)
            else:
                rejected_origins.add(link_origin)
        except ValueError:
            continue
    if diagnostics is not None and rejected_origins:
        diagnostics["rejected_link_origins"] = sorted(rejected_origins)[:20]
    kind = challenge["kind"]
    if identity_only:
        return (
            {"kind": "link", "value": next(iter(valid_links))}
            if len(valid_links) == 1
            else reject("token_format")
        )
    if kind in {"code", "auto"} and len(codes) == 1:
        return {"kind": "code", "value": next(iter(codes))}
    if kind in {"link", "auto", "password_reset"} and len(valid_links) == 1 and (reset or not codes):
        return {"kind": "link", "value": next(iter(valid_links))}
    return reject("ambiguous_token" if len(codes) > 1 or len(valid_links) > 1 else "token_format")


def latest_employer_code(matches, challenge):
    """Newest uniquely timed Greenhouse OTP, after every identity check passed.

    Generic senders, links and tied timestamps retain the ambiguous-match rule.
    Gmail internalDate is receiving metadata, never an employer-supplied Date.
    """
    if (
        len(matches) < 2
        or challenge.get("ats") != "greenhouse"
        or not challenge.get("employer_name")
        or any(token.get("kind") != "code" for _, token, _, _ in matches)
    ):
        return matches
    dated = sorted(matches, key=lambda item: int(item[2]["internalDate"]), reverse=True)
    if int(dated[0][2]["internalDate"]) == int(dated[1][2]["internalDate"]):
        return matches
    return dated[:1]


class MailService:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.vault = Vault(config)
        self.transport = None  # An injected transport is used only by owned fixtures.
        self.locks = {}
        self.poll_lock = asyncio.Lock()
        self.message_cache = {}  # Immutable messages; secrets stay in memory, never in traces.
        self.poll_diagnostics = {}  # Counts and origins only; never message text or tokens.
        if config.gmail_address and config.gmail_app_password:
            email = config.gmail_address.strip().lower()
            with db.exclusive() as s:
                box = s.scalar(select(Mailbox).where(Mailbox.email == email))
                value = {"provider": "gmail_imap", "address": email}
                if not box:
                    s.add(Mailbox(email=email, credentials=self.vault.seal(value)))
                elif self.vault.open(box.credentials).get("provider") == "gmail_imap":
                    box.credentials, box.state, box.error = self.vault.seal(value), "connected", ""

    def client(self):
        return httpx.AsyncClient(
            transport=self.transport, timeout=12, follow_redirects=False, trust_env=False
        )

    @property
    def callback_url(self):
        return self.config.base_url.rstrip("/") + "/oauth/gmail/callback"

    def summary(self):
        with self.db.session() as s:
            boxes = [
                {k: v for k, v in record(m).items() if k != "credentials"} | {"provider": self.provider(m)}
                for m in s.scalars(select(Mailbox))
            ]
            rules = [record(r) for r in s.scalars(select(MailRule))]
            challenges = [
                {k: v for k, v in record(c).items() if k not in {"payload", "message_id"}}
                for c in s.scalars(select(MailChallenge).order_by(MailChallenge.created_at.desc()).limit(50))
            ]
        return {
            "configured": bool(self.config.google_client_id and self.config.google_client_secret),
            "redirect_uri": self.callback_url,
            "mailboxes": boxes,
            "rules": rules,
            "challenges": challenges,
        }

    def begin_oauth(self):
        if not self.config.google_client_id or not self.config.google_client_secret:
            raise ValueError("Configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET on the server first")
        state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        with self.db.exclusive() as s:
            for old in s.scalars(select(MailOAuth).where(MailOAuth.expires < time.time())):
                s.delete(old)
            s.add(
                MailOAuth(
                    state_hash=digest(state),
                    browser_hash=digest(browser),
                    verifier=self.vault.seal(verifier),
                    expires=time.time() + 600,
                )
            )
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": self.config.google_client_id,
                "redirect_uri": self.callback_url,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent select_account",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        ), browser

    async def finish_oauth(self, state, browser, code):
        with self.db.exclusive() as s:
            pending = s.get(MailOAuth, digest(state))
            if (
                not pending
                or pending.expires < time.time()
                or not browser
                or not hmac.compare_digest(pending.browser_hash, digest(browser))
            ):
                raise ValueError("Gmail authorization expired or belongs to another browser. Start again.")
            verifier = self.vault.open(pending.verifier)
            s.delete(pending)  # Single-use even when token exchange fails.
        async with self.client() as client:
            r = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": self.config.google_client_id,
                    "client_secret": self.config.google_client_secret,
                    "redirect_uri": self.callback_url,
                    "code_verifier": verifier,
                },
            )
            if r.status_code != 200:
                raise ValueError("Google could not complete authorization. Reconnect Gmail.")
            token = r.json()
            if (
                SCOPE not in token.get("scope", "").split()
                or not token.get("access_token")
                or not token.get("refresh_token")
            ):
                raise ValueError("Read-only Gmail access and offline consent are required. Reconnect Gmail.")
            p = await client.get(
                API_URL + "/profile", headers={"Authorization": "Bearer " + token["access_token"]}
            )
            if p.status_code != 200:
                raise ValueError("Gmail API is unavailable. Enable it in your Google Cloud project.")
            email = p.json()["emailAddress"].lower()
        token["expires_at"] = time.time() + token.get("expires_in", 3600)
        with self.db.exclusive() as s:
            box = s.scalar(select(Mailbox).where(Mailbox.email == email))
            if box:
                box.credentials, box.state, box.error = self.vault.seal(token), "connected", ""
            else:
                s.add(Mailbox(email=email, credentials=self.vault.seal(token)))
        return email

    def provider(self, box):
        """'gmail_imap' (app password) or 'gmail_oauth' (Google sign-in); never the credentials."""
        try:
            return self.vault.open(box.credentials).get("provider") or "gmail_oauth"
        except Exception:
            return "unknown"

    async def connect_imap(self, address, app_password):
        """Connect a Gmail inbox with an app password entered in the app.

        The login is verified before anything is saved; the password is sealed in the vault like
        OAuth tokens and is never returned. GMAIL_ADDRESS/GMAIL_APP_PASSWORD in .env keep working."""
        from .gmail_imap import request

        email = address.strip().lower()
        password = "".join(app_password.split())
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter the Gmail address you apply with")
        if not re.fullmatch(r"[A-Za-z]{16}", password):
            raise ValueError(
                "A Google app password is 16 letters (spaces are fine). Your normal password won't work."
            )
        try:
            await asyncio.to_thread(request, email, password, "/profile")
        except MailAuthenticationError:
            raise ValueError(
                "Gmail rejected this app password. Check it, and that 2-Step Verification and IMAP are on."
            ) from None
        except Exception as exc:
            raise ValueError(
                f"Could not reach Gmail ({type(exc).__name__}). Try again in a minute."
            ) from None
        sealed = self.vault.seal({"provider": "gmail_imap", "address": email, "app_password": password})
        with self.db.exclusive() as s:
            box = s.scalar(select(Mailbox).where(Mailbox.email == email))
            if box:
                box.credentials, box.state, box.error = sealed, "connected", ""
            else:
                box = Mailbox(email=email, credentials=sealed)
                s.add(box)
            s.flush()
            return {k: v for k, v in record(box).items() if k != "credentials"} | {"provider": "gmail_imap"}

    async def request(self, mailbox_id, path, params=None):
        async with self.locks.setdefault(mailbox_id, asyncio.Lock()):
            with self.db.session() as s:
                box = s.get(Mailbox, mailbox_id)
                if not box or box.state != "connected":
                    raise ValueError("Reconnect the Gmail mailbox")
                stored = box.credentials
                token = self.vault.open(stored)
            key = (
                (mailbox_id, path)
                if re.fullmatch(r"/messages/[^/]+", path) and (params or {}).get("format") == "full"
                else None
            )
            if key and key in self.message_cache:
                cached_at, cached = self.message_cache[key]
                if time.monotonic() - cached_at < 180:
                    return cached

            def cache(result):
                if key:
                    self.message_cache[key] = (time.monotonic(), result)
                    while len(self.message_cache) > 200:
                        self.message_cache.pop(next(iter(self.message_cache)))
                return result

            if token.get("provider") == "gmail_imap":
                from .gmail_imap import request

                # An app password entered in Setup is sealed with the mailbox; otherwise .env supplies it.
                password = token.get("app_password") or (
                    self.config.gmail_app_password
                    if token["address"] == self.config.gmail_address.strip().lower()
                    else ""
                )
                if not password:
                    raise ValueError("Restore GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env")
                where = "in Setup → Email" if token.get("app_password") else "in .env"
                try:
                    result = await asyncio.to_thread(
                        request,
                        token["address"],
                        "".join(password.split()),
                        path,
                        params,
                    )
                except MailAuthenticationError:
                    self.connection_error(
                        mailbox_id,
                        "reconnect_required",
                        f"Gmail login failed; check the app password {where}",
                    )
                    raise MailAuthenticationError(
                        f"Gmail login failed; check the app password {where}"
                    ) from None
                except MailMessageError as exc:
                    self.quarantine(mailbox_id, path.removeprefix("/messages/"), exc.reason)
                    raise
                except MailTransportError:
                    self.connection_error(mailbox_id, "connected", "Gmail connection interrupted; retrying")
                    raise
                except MailCursorUpgrade:
                    # An obsolete saved cursor, not a failure: the caller restarts its search.
                    # (It is a MailOperationError, so the handler below would swallow it, and the
                    # receipt poller then retried the same dead cursor on every pass.)
                    raise
                except (MailOperationError, ValueError, TypeError):
                    # A failed search/fetch is not evidence that the credentials were rejected.
                    self.connection_error(mailbox_id, "connected", "Mailbox operation failed; retry later")
                    raise MailOperationError("Mailbox operation failed; retry later") from None
                with self.db.exclusive() as s:
                    if box := s.get(Mailbox, mailbox_id):
                        box.checked_at, box.error = now(), ""
                return cache(result)
            async with self.client() as client:
                if token.get("expires_at", 0) < time.time() + 60:
                    r = await client.post(
                        TOKEN_URL,
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": token["refresh_token"],
                            "client_id": self.config.google_client_id,
                            "client_secret": self.config.google_client_secret,
                        },
                    )
                    if r.status_code != 200:
                        if r.status_code in {400, 401}:
                            self.connection_error(
                                mailbox_id,
                                "reconnect_required",
                                "Google authorization expired or was revoked",
                            )
                            raise MailAuthenticationError("Gmail token refresh failed")
                        raise MailTransportError("Gmail token refresh is temporarily unavailable")
                    token.update(r.json())
                    token["expires_at"] = time.time() + token.get("expires_in", 3600)
                    with self.db.exclusive() as s:
                        box = s.get(Mailbox, mailbox_id)
                        if not box or box.credentials != stored:
                            raise ValueError("Gmail connection changed; retry the operation")
                        box.credentials = self.vault.seal(token)
                r = await client.get(
                    API_URL + path,
                    params=params,
                    headers={"Authorization": "Bearer " + token["access_token"]},
                )
                if r.status_code == 401:
                    self.connection_error(
                        mailbox_id, "reconnect_required", "Gmail permission or API access needs attention"
                    )
                    raise MailAuthenticationError("Gmail authorization needs attention")
                if r.status_code == 404 and key:
                    self.quarantine(mailbox_id, path.removeprefix("/messages/"), "unavailable_message")
                    raise MailMessageError("unavailable_message")
                if r.status_code in {429, 500, 502, 503, 504}:
                    raise MailTransportError("Gmail is temporarily unavailable; retry later")
                if r.status_code == 403:
                    try:
                        reasons = {item.get("reason") for item in r.json().get("error", {}).get("errors", [])}
                    except (ValueError, TypeError, AttributeError):
                        reasons = set()
                    if reasons & {"insufficientPermissions", "accessNotConfigured", "forbidden"}:
                        self.connection_error(
                            mailbox_id, "reconnect_required", "Gmail permission or API access needs attention"
                        )
                        raise MailAuthenticationError("Gmail permissions need attention")
                    raise MailOperationError("Gmail request is temporarily blocked; retry later")
                if r.status_code != 200:
                    raise MailOperationError("Gmail request failed; retry later")
                with self.db.exclusive() as s:
                    box = s.get(Mailbox, mailbox_id)
                    if box:
                        box.checked_at, box.error = now(), ""
                return cache(r.json())

    def quarantine(self, mailbox_id, message_id, reason):
        """A bad message cannot disconnect a mailbox or repeatedly monopolize polling."""
        self.db.set_setting(
            "mail_quarantine:" + digest(mailbox_id + ":" + message_id),
            {"reason": reason, "at": now(), "until": time.time() + 86400},
        )

    async def read_message(self, mailbox_id, message_id):
        quarantined = self.db.get_setting("mail_quarantine:" + digest(mailbox_id + ":" + message_id), {})
        if quarantined.get("until", 0) > time.time():
            return None
        try:
            return await self.request(mailbox_id, "/messages/" + message_id, {"format": "full"})
        except MailMessageError:
            return None
        except (TypeError, RecursionError):
            self.quarantine(mailbox_id, message_id, "invalid_message")
            return None

    def connection_error(self, mailbox_id, state, error):
        with self.db.exclusive() as s:
            if box := s.get(Mailbox, mailbox_id):
                box.state, box.error = state, error

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
                select(MailRule).where(
                    MailRule.employer_origin == origin(url), MailRule.enabled.is_(True)
                )
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
            row.expires = self.window_deadline(
                session.get(Run, row.run_id),
                max(row.expires, row.since + self.config.mail_durable_wait_seconds),
            )
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
                "mailbox_id": box, "sender_domains": KNOWN_SENDERS[job.ats],
                "employer_origin": origin(job.url), "link_origins": [origin(job.url)],
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
            session, rule, recovery.get("source_run_id"),
            kind=recovery.get("mail_request_kind", "auto"), current=time.time()
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
            c = MailChallenge(
                run_id=run_id,
                rule_id=rule["id"],
                mailbox_id=box.id,
                recipient=recipient.lower(),
                kind=kind,
                since=request_since,
                expires=self.window_deadline(run, current + self.config.mail_wait_seconds),
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
        from .agent_settings import require

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

    async def poll_once(self):
        from .agent_settings import enabled

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
                pending = [
                    record(c)
                    for c in s.scalars(select(MailChallenge).where(MailChallenge.state == "pending"))
                ]
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
                    )
                    c["employer_senders"] = bool(job and job.ats in EMPLOYER_SENDER_FAMILIES)
                    c["ats"] = job.ats if job else ""
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
                    matches, rejections, rejected_origins = [], {}, set()
                    if c["kind"] in {"code", "auto"} and len(c["open_windows"]) == 1:
                        # Unbranded codes name neither employer nor domain, so the sender
                        # filter above cannot find them. Search this recipient's window
                        # for verification wording; extraction still requires the full
                        # unbranded rule. Only when this is the sole open request.
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
                    scan_seconds = time.monotonic() - scan_started
                    counts = (
                        len(messages),
                        len(matches),
                        tuple(sorted(rejections.items())),
                        tuple(sorted(rejected_origins)[:20]),
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
                                and token["kind"] == "link"
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
                                from .outbox import enqueue

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

    async def poll(self):
        last_receipts = last_drafts = 0
        receipt_task = draft_task = None
        try:
            while True:
                try:
                    await self.poll_once()
                    from .agent_settings import enabled

                    if (
                        enabled(self.db, "email")
                        and time.time() - last_receipts > 60
                        and (receipt_task is None or receipt_task.done())
                    ):
                        from .receipts import check_receipts

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
                        from .receipts import reconcile_recent_drafts

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

    async def disconnect(self, mailbox_id):
        async with self.locks.setdefault(mailbox_id, asyncio.Lock()):
            with self.db.exclusive() as s:
                box = s.get(Mailbox, mailbox_id)
                if not box:
                    raise ValueError("Mailbox not found")
                token = self.vault.open(box.credentials)
                s.delete(box)
                for rule in s.scalars(select(MailRule).where(MailRule.mailbox_id == mailbox_id)):
                    s.delete(rule)
                for c in s.scalars(
                    select(MailChallenge).where(
                        MailChallenge.mailbox_id == mailbox_id, MailChallenge.state.in_(LIVE)
                    )
                ):
                    c.state, c.payload, c.reason = "cancelled", "", "Mailbox disconnected"
            revoked = False
            if token.get("provider") == "gmail_imap":
                return {
                    "disconnected": True,
                    "revoked": False,
                    "message": "Disconnected. Delete the app password in your Google account if you no longer use it."
                    if token.get("app_password")
                    else "Remove GMAIL_APP_PASSWORD from .env and revoke it in your Google account to keep it disconnected.",
                }
            try:
                async with self.client() as client:
                    r = await client.post(
                        "https://oauth2.googleapis.com/revoke", data={"token": token["refresh_token"]}
                    )
                    revoked = r.status_code == 200
            except httpx.HTTPError:
                pass
            return {"disconnected": True, "revoked": revoked}
