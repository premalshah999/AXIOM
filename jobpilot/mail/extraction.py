"""Reading a verification code or link out of one message, and the best code for an employer."""

from bs4 import BeautifulSoup
from email.utils import getaddresses
from urllib.parse import urlsplit
import base64
import re
from .helpers import origin
from .senders import CLICK_TRACKERS
from .senders import authenticated_sender
from .senders import icims_attributed
from .senders import oracle_attributed
from .senders import platform_link_allowed
from .senders import sender_allowed
from .senders import tenant_conflict
from .senders import tracker_allowed


CODE_CUE = re.compile(r"verification code|security code|one.time|passcode|\bpin\b|\botp\b|\bcode\b", re.I)


# Oracle Recruiting's standard one-time-code mail. Each employer's site may send it
# from its own domain (uthscsa.edu) rather than oraclecloud.com.
# Oracle Recruiting's identity-code template as tenants word it (mailbox audit, 2026-10-25): "confirm your identity
# using the one-time pass code", "Confirm your identity using this code:", "confirm your identity for your job
# application for using this code", "Your one-time verification code to confirm your identity is". Only the first was
# recognized, so codes mailed from an employer's own domain in the others (Marriott, Uber, Ford, Akamai) expired.
ORACLE_OTP = re.compile(
    r"confirm\s+your\s+identity\b.{0,60}?\busing\s+(?:the\s+|this\s+)?(?:one[- ]time\s+)?(?:pass\s?)?code\b"
    r"|\bone[- ]time\s+(?:verification\s+|pass\s?)?code\s+to\s+confirm\s+your\s+identity\b",
    re.I | re.S,
)


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


FOOTER_TEXT = re.compile(
    r"privacy|unsubscribe|terms|help|contact|faq|support|preferences|opt.?out|policy|cookie|copyright|"
    r"accessibility|legal|careers? home|view (?:in|this) (?:browser|email)",
    re.I,
)


def _origin_or_none(href):
    try:
        return origin(href)
    except ValueError:
        return None


# A verification continuation in a link's own URL (iCIMS: ".../login?step=email&hashed=...&token=...").
VERIFY_URL = re.compile(r"verif|confirm|token|activate|[?&](?:step|hashed|key|code)=|/login\b", re.I)
RESET_URL = re.compile(r"reset|password|passwd|pwd|token|activat", re.I)


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
    plain = []  # Anchors with no cue in their text or the subject ("Click here").
    anchor_text = {}

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
                    # One href can be both a logo and a button: keep every text it is shown with.
                    anchor_text[a["href"]] = (anchor_text.get(a["href"], "") + " " + a.get_text(" ", strip=True)).strip()
                    if anchor_cue:
                        cued.add(a["href"])
                    if anchor_cue or message_link_cue:
                        links.append(a["href"])
                    else:
                        plain.append(a["href"])
            # Workday also sends bare URLs inside HTML, without <a> elements.
            # Require the same nearby reset/verification cue as plain text.
            for match in re.finditer(r"https://[^\s<>\"']+", text):
                near = bool(re.search(link_cue, text[max(0, match.start() - 120) : match.end()], re.I))
                if message_link_cue or near:
                    links.append(match.group().rstrip(".,)"))
                    if near:
                        # "Use this link to sign in: https://…" carries its own cue, like an anchor's text.
                        cued.add(match.group().rstrip(".,)"))
        for child in part.get("parts", [])[:30]:
            walk(child, depth + 1)

    walk(message.get("payload", {}))
    if challenge["kind"] == "link" and not links:
        # EBSCO's iCIMS verification mail (2026-10-21) was rejected as token_format on every poll: its button had no
        # verification wording in its text or the subject. The one link on the employer's own origin whose URL is a
        # verification continuation is the link; anything ambiguous or off-origin still never opens.
        own = set()
        for href in plain:
            try:
                if urlsplit(href).scheme == "https" and origin(href) in rule["link_origins"] and VERIFY_URL.search(href):
                    own.add(href)
            except ValueError:
                continue
        if len(own) == 1:
            links.append(next(iter(own)))
        elif not own and any(
            # iCIMS behind the employer's own domain (Garmin's careers site, 2026-10-27) mails the same tracked button.
            tracker_allowed(t, rule["link_origins"], challenge.get("ats", "")) for t in CLICK_TRACKERS
        ):
            # EBSCO 2026-10-21: "User account with EBSCO Information Services, LLC" carried its button only behind
            # iCIMS's click tracker, with no wording. The one tracker link that is not a footer link is the link; the
            # visit still has to land on the employer's own origin, so a wrong one is refused there.
            tracked = {
                href
                for href in plain
                if _origin_or_none(href) in CLICK_TRACKERS
                and tracker_allowed(_origin_or_none(href), rule["link_origins"], challenge.get("ats", ""))
                and not FOOTER_TEXT.search(anchor_text.get(href, ""))
            }
            if len(tracked) == 1:
                href = next(iter(tracked))
                links.append(href)
                cued.add(href)
    if diagnostics is not None:
        diagnostics["subject"] = " ".join(values.get("subject", []))[:80]
        hosts = set()
        for href in links + plain:
            try:
                hosts.add(urlsplit(href).hostname or "")
            except ValueError:
                continue
        diagnostics["link_hosts"] = sorted(h for h in hosts if h)[:10]
    # Some employers label the OTP in the subject. It has passed the same
    # envelope, tenant, recipient and window checks as the message body.
    text = "\n".join(values.get("subject", []) + texts)
    if outside_rule and not ORACLE_OTP.search(text):
        return reject("sender")
    if (
        oracle_template
        and challenge.get("oracle_peers")
        and not oracle_attributed(
            address,
            senders[0][0],
            text,
            challenge.get("employer_name", ""),
            challenge.get("oracle_pod", ""),
            challenge["oracle_peers"],
        )
    ):
        return reject("tenant")
    if (
        challenge.get("ats") == "icims"
        and challenge.get("icims_peers")
        and not icims_attributed(
            " ".join(values.get("subject", [])),
            text,
            set(challenge.get("icims_names", [])),
            set(challenge["icims_peers"]),
        )
    ):
        # Another employer's iCIMS window is open and this mail does not name this employer: its tracked button
        # could be theirs.
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
    # Several windows open at once (a retry wave, 2026-10-27): the code is this window's when the mail names this
    # employer (a tenant token in its sender name, subject or text) and no other open window's employer.
    squashed = re.sub(r"[^a-z0-9]+", "", (" ".join(values.get("subject", []) + [senders[0][0], text])).casefold())

    def names(tokens):
        return any(len(t) >= 4 and t in squashed for t in tokens or [])

    attributed = (
        not single_window
        and names(challenge.get("tenant_tokens"))
        and not any(names(peer) for peer in challenge.get("peer_tokens") or [])
    )
    unbranded_cue = bool(UNBRANDED_CODE_CUE.search(text))
    unbranded_ok = bool(
        challenge.get("open_windows") is not None
        and challenge["kind"] in {"code", "auto"}
        and (single_window or attributed)
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
            if urlsplit(link).scheme == "https" and (
                link_origin in rule["link_origins"] or platform_link_allowed(link_origin, challenge.get("ats"))
            ):
                valid_links.add(link)
            elif (
                urlsplit(link).scheme == "https"
                and link in cued
                and tracker_allowed(link_origin, rule["link_origins"], challenge.get("ats", ""))
            ):
                # The employer's own button behind iCIMS's click tracker. Its anchor text carries the cue; footer
                # links behind the same tracker stay rejected even when the subject is the cue.
                valid_links.add(link)
            else:
                rejected_origins.add(link_origin)
        except ValueError:
            continue
    if not valid_links and message_link_cue:
        # ClearCompany's sign-in mail (Allen Institute, 2026-10-27): the subject is the cue ("Sign-in link for your
        # application at …") and its one button goes through the clrco.com short-link without cue words, so every poll
        # refused it as token_format. With the subject as the cue, the one tracker link of this platform that has text
        # and is not a footer link is the button; the visit must still land on the employer's own origin.
        tracked = {
            link
            for link in links
            if (link_origin := _origin_or_none(link)) in CLICK_TRACKERS
            and tracker_allowed(link_origin, rule["link_origins"], challenge.get("ats", ""))
            and anchor_text.get(link)
            and not FOOTER_TEXT.search(anchor_text[link])
        }
        if len(tracked) == 1:
            valid_links = tracked
            rejected_origins.discard(_origin_or_none(next(iter(tracked))))
    if diagnostics is not None and rejected_origins:
        diagnostics["rejected_link_origins"] = sorted(rejected_origins)[:20]
    if len(valid_links) > 1:
        # A reset or verification mail also links its logo and portal home: the one link whose URL is the reset or
        # verification step is the link.
        step = {link for link in valid_links if (RESET_URL if reset else VERIFY_URL).search(link)}
        if len(step) == 1:
            valid_links = step
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
    if reset and not valid_links and len(codes) == 1 and not fallback_code:
        # A reset confirmed by an emailed code ("TikTok verification code: 523632", 2026-10-28) was refused as
        # token_format on every poll: reset windows took only links. One labelled code and no link is the reset's code.
        return {"kind": "code", "value": next(iter(codes))}
    return reject("ambiguous_token" if len(codes) > 1 or len(valid_links) > 1 else "token_format")


def names_employer(message, tokens):
    """Whether a message's subject, sender name or snippet names an employer (a tenant token of 4+ letters)."""
    headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
    text = " ".join([headers.get("subject", ""), headers.get("from", "").split("<")[0], message.get("snippet", "")])
    squashed = re.sub(r"[^a-z0-9]+", "", text.casefold())
    return any(len(t) >= 4 and t in squashed for t in tokens or [])


def latest_employer_code(matches, challenge):
    """Newest uniquely timed OTP, after every identity check passed.

    Several matches are first narrowed to those naming this employer and no other open window's (ADP's Midco and ZincFive
    codes reached one address at once, 2026-10-27). Among this employer's codes the newest wins: a resent code replaces
    the earlier one. Links and tied timestamps retain the ambiguous-match rule. Gmail internalDate is receiving metadata,
    never an employer-supplied Date.
    """
    if len(matches) < 2:
        return matches
    own = [
        item
        for item in matches
        if names_employer(item[2], challenge.get("tenant_tokens"))
        and not any(names_employer(item[2], peer) for peer in challenge.get("peer_tokens") or [])
    ]
    if own:
        matches = own
    if (
        len(matches) < 2
        or not (own or (challenge.get("ats") == "greenhouse" and challenge.get("employer_name")))
        or any(token.get("kind") != "code" for _, token, _, _ in matches)
    ):
        return matches
    dated = sorted(matches, key=lambda item: int(item[2]["internalDate"]), reverse=True)
    if int(dated[0][2]["internalDate"]) == int(dated[1][2]["internalDate"]):
        return matches
    return dated[:1]
