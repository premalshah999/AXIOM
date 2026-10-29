import time

import pytest

from jobpilot.mail import extract_message
from support_mail import message


@pytest.mark.parametrize("link,auth,accepted", [
    ("https://acme.taleo.net/careersection/reset?token=abc", "pass", True),
    ("https://other.taleo.net/careersection/reset?token=abc", "pass", False),
    ("https://acme.taleo.net/careersection/reset?token=abc", "fail", False),
])
def test_password_setup_subject_keeps_reset_identity_checks(link, auth, accepted):
    msg = message(f'<a href="{link}">Continue</a>', html=True)
    headers = msg["payload"]["headers"]
    headers[0]["value"] = "Acme <recruiting@hr.acme.test>"
    headers[2]["value"] = f"mx.google.com; dmarc={auth} header.from=hr.acme.test"
    headers.append({"name": "Subject", "value": "Set up a user name and password at Acme"})
    rule = {"sender_domains": ["taleo.net"], "link_origins": ["https://acme.taleo.net"]}
    challenge = {"recipient": "alex@example.test", "since": time.time() - 30,
                 "expires": time.time() + 30, "kind": "password_reset", "ats": "taleo",
                 "employer_senders": True, "tenant_tokens": ["acme"]}
    assert bool(extract_message(msg, rule, challenge)) is accepted
