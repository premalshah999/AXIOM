"""Unbranded verification codes: accepted only as the sole open code window.

Synthetic fixtures only. Codes, addresses and domains here are fake.
"""

import base64
import time

import httpx
import pytest

from jobpilot.db import MailChallenge
from jobpilot.mail import extract_message
from support_mail import configured, message

OTHER = "no-reply@mailer.unrelated.test"
INSIDE = "no-reply@careers.acme.test"
RULE = {"sender_domains": ["careers.acme.test"], "link_origins": ["https://careers.acme.test"]}


def unbranded(body, *, sender=OTHER, received=None, dmarc="pass", recipient="alex@example.test"):
    """A mail from a domain the employer rule does not name, DMARC-aligned to itself."""
    msg = message(body, received=received)
    headers = msg["payload"]["headers"]
    domain = sender.rpartition("@")[2]
    headers[0]["value"] = "Verify <" + sender + ">"
    headers[1]["value"] = "Alex <" + recipient + ">"
    headers[2]["value"] = f"mx.google.com; dkim=pass; dmarc={dmarc} header.from={domain}"
    return msg


def window(challenge_id, since, expires, kind="code"):
    return (challenge_id, since, expires, kind)


def challenge(*, now=None, open_windows=None, kind="code", ats="custom", employer="Acme Labs"):
    now = now or time.time()
    since, expires = now - 30, now + 30
    return {
        "id": "ch-1",
        "recipient": "alex@example.test",
        "since": since,
        "expires": expires,
        "kind": kind,
        "ats": ats,
        "employer_name": employer,
        "tenant_tokens": ["acme"],
        "employer_senders": False,
        "open_windows": [window("ch-1", since, expires, kind)] if open_windows is None else open_windows,
    }


def run_extract(msg, ch, rule=RULE):
    diagnostics = {}
    result = extract_message(msg, rule, ch, diagnostics=diagnostics)
    return result, diagnostics.get("rejection")


@pytest.mark.parametrize(
    "body",
    [
        "Your verification code is 483921",
        "Enter this one-time passcode: 483921 to continue.",
        "Your security code: 483921",
        "Use this login code 483921 to sign in.",
    ],
)
def test_unbranded_code_accepted_with_single_open_window(body):
    ch = challenge()
    result, rejection = run_extract(unbranded(body, received=time.time()), ch)
    assert result == {"kind": "code", "value": "483921"}, rejection


def test_unbranded_code_accepted_when_other_window_closed_before_arrival():
    now = time.time()
    earlier = window("ch-0", now - 600, now - 300)  # closed well before this mail arrived
    ch = challenge(now=now, open_windows=[window("ch-1", now - 30, now + 30), earlier])
    result, rejection = run_extract(unbranded("Your verification code is 483921", received=now), ch)
    assert result == {"kind": "code", "value": "483921"}, rejection


def test_unbranded_code_rejected_when_two_challenges_overlap_arrival():
    now = time.time()
    ch = challenge(
        now=now,
        open_windows=[
            window("ch-1", now - 30, now + 30),
            window("ch-2", now - 20, now + 40),  # another run's open code window at arrival
        ],
    )
    result, rejection = run_extract(unbranded("Your verification code is 483921", received=now), ch)
    assert result is None
    assert rejection == "unbranded_ambiguous"


def test_unbranded_code_rejected_when_only_other_challenge_is_open():
    now = time.time()
    ch = challenge(now=now, open_windows=[window("ch-2", now - 30, now + 30)])  # ours is not listed
    result, rejection = run_extract(unbranded("Your verification code is 483921", received=now), ch)
    assert result is None
    assert rejection == "unbranded_ambiguous"


def test_unbranded_code_rejected_outside_this_request_window():
    now = time.time()
    ch = challenge(now=now)
    early = run_extract(unbranded("Your verification code is 483921", received=now - 60), ch)
    late = run_extract(unbranded("Your verification code is 483921", received=now + 60), ch)
    assert early == (None, "request_window")
    assert late == (None, "request_window")


@pytest.mark.parametrize(
    "body",
    [
        "Thanks for applying. Your reference number is 483921.",  # no verification cue
        "Promo code 483921 for 20% off your next order.",  # marketing, not verification
        "Your verification code is 483921. Use promo code 555111 for savings.",  # two codes
        "Your verification code is 48-39-21",  # not a single 4-10 character code
        "Your verification code is ABCDEF",  # no digit: never accepted as unbranded
    ],
)
def test_unbranded_code_rejected_without_clear_single_code_template(body):
    ch = challenge()
    result, rejection = run_extract(unbranded(body, received=time.time()), ch)
    assert result is None
    assert rejection in {"sender", "unbranded_ambiguous", "token_format", "ambiguous_token"}


def test_unbranded_sender_never_yields_a_link_even_with_employer_origin():
    body = "Verify your email: https://careers.acme.test/verify?token=abc123 Verification link."
    ch = challenge(kind="auto")
    result, rejection = run_extract(unbranded(body, received=time.time()), ch)
    assert result is None
    assert rejection == "sender"


def test_unbranded_sender_rejected_without_dmarc_alignment():
    ch = challenge()
    result, rejection = run_extract(
        unbranded("Your verification code is 483921", dmarc="fail", received=time.time()), ch
    )
    assert (result, rejection) == (None, "authentication")


def test_unbranded_sender_rejected_for_other_recipient():
    ch = challenge()
    msg = unbranded(
        "Your verification code is 483921", recipient="someone@example.test", received=time.time()
    )
    result, rejection = run_extract(msg, ch)
    assert (result, rejection) == (None, "recipient")


def test_unbranded_code_needs_open_window_snapshot():
    # Callers without a mailbox snapshot (identity-only learning, legacy callers) keep rejecting.
    ch = challenge()
    ch.pop("open_windows")
    result, rejection = run_extract(unbranded("Your verification code is 483921", received=time.time()), ch)
    assert (result, rejection) == (None, "sender")


def test_unbranded_code_never_for_link_challenges():
    ch = challenge(kind="link")
    result, rejection = run_extract(unbranded("Your verification code is 483921", received=time.time()), ch)
    assert (result, rejection) == (None, "sender")


def test_employer_sender_links_unaffected_by_unbranded_rule():
    # In-rule employer sender: link acceptance is unchanged and still origin-bound.
    now = time.time()
    ch = challenge(kind="link", now=now)
    ch["open_windows"] = [window("ch-1", now - 30, now + 30, "link")]
    body = "Please verify your account: https://careers.acme.test/verify?token=abc123"
    result, _ = run_extract(unbranded(body, sender=INSIDE, received=now), ch)
    assert result == {"kind": "link", "value": "https://careers.acme.test/verify?token=abc123"}
    # A foreign-origin link in the same mail is still rejected.
    foreign = "Please verify your account: https://evil.example.test/verify?token=abc123"
    assert run_extract(unbranded(foreign, sender=INSIDE, received=now), ch) == (None, "token_format")


def test_greenhouse_code_without_employer_name_still_rejected_when_window_is_shared():
    now = time.time()
    body = "Your security code is 123456"
    shared = challenge(
        now=now,
        ats="greenhouse",
        open_windows=[
            window("ch-1", now - 30, now + 30),
            window("ch-2", now - 30, now + 30),
        ],
    )
    assert run_extract(unbranded(body, sender=INSIDE, received=now), shared) == (None, "employer_identity")


def test_greenhouse_unnamed_outside_code_accepted_only_as_sole_window():
    now = time.time()
    body = "Your security code is 123456"
    sole = challenge(now=now, ats="greenhouse")
    result, rejection = run_extract(unbranded(body, sender=OTHER, received=now), sole)
    assert result == {"kind": "code", "value": "123456"}, rejection
    shared = challenge(
        now=now,
        ats="greenhouse",
        open_windows=[
            window("ch-1", now - 30, now + 30),
            window("ch-2", now - 30, now + 30),
        ],
    )
    assert run_extract(unbranded(body, sender=OTHER, received=now), shared) == (None, "unbranded_ambiguous")


def test_greenhouse_in_rule_unnamed_code_keeps_employer_identity_even_as_sole_window():
    # The shared-sender name check is not bypassed for mail from the rule's own sender.
    now = time.time()
    sole = challenge(now=now, ats="greenhouse")
    result = run_extract(unbranded("Your security code is 123456", sender=INSIDE, received=now), sole)
    assert result == (None, "employer_identity")


# ---------------------------------------------------------------------------
# Poll-level: the widened search must find unbranded mail, and only when sole.


def _body_text(m):
    data = m["payload"]["body"]["data"]
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode()


def _from(m):
    return next(h["value"] for h in m["payload"]["headers"] if h["name"] == "From")


def gmail(messages):
    """Employer query (from:) matches only the rule's domain; wording query matches body text."""

    def route(request):
        assert request.headers["authorization"] == "Bearer fake-access-secret"
        if request.url.path.endswith("/messages"):
            q = request.url.params["q"]
            assert "after:" in q and "to:alex@example.test" in q
            if "from:" in q:
                listed = [m for m in messages if "@notify.example.test" in _from(m)]
            else:
                listed = [m for m in messages if "verification code" in _body_text(m).casefold()]
            return httpx.Response(200, json={"messages": [{"id": m["id"]} for m in listed]})
        return httpx.Response(200, json=next(m for m in messages if request.url.path.endswith("/" + m["id"])))

    return httpx.MockTransport(route)


async def test_poll_finds_unbranded_code_when_sole_open_request(service):
    _, run, rule = await configured(service)
    request = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    now = time.time()
    service.mail.transport = gmail([unbranded("Your verification code is 483921", received=now)])
    await service.mail.poll_once()
    with service.db.session() as session:
        row = session.get(MailChallenge, request["id"])
        assert row.state == "matched"
        assert service.mail.vault.open(row.payload)["value"] == "483921"


async def test_poll_does_not_widen_search_with_overlapping_requests(service):
    _, run, rule = await configured(service)
    request = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    with service.db.session() as session:
        session.add(
            MailChallenge(
                run_id=run["id"],
                rule_id=rule["id"],
                mailbox_id=rule["mailbox_id"],
                recipient="alex@example.test",
                kind="code",
                since=time.time() - 30,
                expires=time.time() + 300,
                state="pending",
            )
        )
    now = time.time()
    service.mail.transport = gmail([unbranded("Your verification code is 483921", received=now)])
    await service.mail.poll_once()
    with service.db.session() as session:
        row = session.get(MailChallenge, request["id"])
        assert row.state == "pending"
        assert row.reason.startswith("Found 0 messages") or row.reason.startswith("No messages")


def in_rule(body):
    """A mail from the employer rule's own sender (DMARC-aligned)."""
    return unbranded(body, sender=INSIDE)


def test_auto_wait_takes_an_unlabeled_code_only_when_the_mail_has_no_link():
    body = "Use 482915 to continue your Acme Labs application. This verification code expires soon."
    result, rejection = run_extract(in_rule(body), challenge(kind="auto"))
    assert result and result.get("kind") == "code" and result.get("value") == "482915", rejection
    with_link = body + " Or open https://careers.acme.test/verify?t=abc"
    result, _ = run_extract(in_rule(with_link), challenge(kind="auto"))
    assert not (result and result.get("kind") == "code")
