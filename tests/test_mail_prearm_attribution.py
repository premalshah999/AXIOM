"""A neutral sender's code that predates the moment a challenge was armed must name this employer (synthetic data only)."""

import time
from datetime import UTC, datetime

from jobpilot.mail import extract_message
from support_mail import message

OTHER = "no-reply@mailer.unrelated.test"
INSIDE = "no-reply@careers.acme.test"
RULE = {"sender_domains": ["careers.acme.test"], "link_origins": ["https://careers.acme.test"]}


def mail(body, *, sender=OTHER, received, subject=None):
    msg = message(body, received=received)
    headers = msg["payload"]["headers"]
    domain = sender.rpartition("@")[2]
    headers[0]["value"] = "Verify <" + sender + ">"
    headers[1]["value"] = "Alex <alex@example.test>"
    headers[2]["value"] = f"mx.google.com; dkim=pass; dmarc=pass header.from={domain}"
    if subject:
        headers.append({"name": "Subject", "value": subject})
    return msg


def armed_challenge(now, *, look_back=30, peers=None):
    """B's challenge armed at `now` whose window reaches `look_back` seconds earlier; A's window is gone."""
    return {
        "id": "ch-b",
        "recipient": "alex@example.test",
        "since": now - look_back,
        "created_at": datetime.fromtimestamp(now, UTC).isoformat(),
        "expires": now + 60,
        "kind": "code",
        "ats": "custom",
        "tenant_tokens": ["acme"],
        "peer_tokens": peers or [],
        "employer_senders": False,
        "open_windows": [("ch-b", now - look_back, now + 60, "code")],
    }


def run(msg, ch):
    diag = {}
    return extract_message(msg, RULE, ch, diagnostics=diag), diag.get("rejection")


def test_cancelled_siblings_unbranded_code_before_arming_is_rejected():
    now = time.time()
    ch = armed_challenge(now)
    # Application A's unbranded code from a neutral sender, 10 s before B armed; A's challenge is cancelled.
    result, why = run(mail("Your verification code is 483921", received=now - 10), ch)
    assert result is None and why == "unbranded_before_request"


def test_same_code_after_arming_is_still_the_sole_windows():
    now = time.time()
    ch = armed_challenge(now)
    result, why = run(mail("Your verification code is 483921", received=now + 2), ch)
    assert result == {"kind": "code", "value": "483921"}, why


def test_fast_code_before_arming_from_this_employers_rule_is_accepted():
    now = time.time()
    ch = armed_challenge(now)
    result, why = run(mail("Your verification code is 483921", sender=INSIDE, received=now - 2), ch)
    assert result == {"kind": "code", "value": "483921"}, why


def test_fast_unbranded_code_before_arming_naming_this_employer_is_accepted():
    now = time.time()
    ch = armed_challenge(now)
    msg = mail("Acme Labs: your verification code is 483921", received=now - 2)
    result, why = run(msg, ch)
    assert result == {"kind": "code", "value": "483921"}, why


def test_before_arming_mail_naming_this_and_a_peer_employer_is_rejected():
    now = time.time()
    ch = armed_challenge(now, peers=[["globex"]])
    msg = mail("Acme Labs and Globex: your verification code is 483921", received=now - 2)
    assert run(msg, ch)[0] is None


def test_prior_run_code_is_still_rejected_by_the_window():
    now = time.time()
    ch = armed_challenge(now, look_back=30)
    result, why = run(mail("Your verification code is 483921", sender=INSIDE, received=now - 300), ch)
    assert (result, why) == (None, "request_window")
