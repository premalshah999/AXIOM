"""Receiving DMARC and DKIM parent alignment must still reject unrelated signers."""
import pytest
from jobpilot.mail.senders import authenticated_sender

@pytest.mark.parametrize("signer,expected", [
    ("platform.example", True), ("tenant.platform.example", True),
    ("other.platform.example", False), ("platform.example.attacker.test", False),
])
def test_parent_alignment_requires_parent_or_exact_sender_dkim(signer, expected):
    auth = f"mx.google.com; dkim=pass header.i=@{signer}; dmarc=pass header.from=platform.example"
    assert authenticated_sender(auth, "tenant.platform.example") is expected

@pytest.mark.parametrize("auth", [
    "mx.google.com; dkim=fail header.i=@platform.example; dmarc=pass header.from=platform.example",
    "mx.google.com; dkim=pass header.i=@platform.example; dmarc=fail header.from=platform.example",
    "mx.google.com; dkim=pass header.i=@platform.example; dmarc=pass header.from=other.example",
])
def test_failed_or_unrelated_authentication_stays_rejected(auth):
    assert not authenticated_sender(auth, "tenant.platform.example")


@pytest.mark.parametrize("target,accepted", [
    ("https://tenant.platform.example/reset?token=synthetic", True),
    ("https://other.platform.example/reset?token=synthetic", False),
])
def test_parent_authenticated_reset_still_requires_tenant_link(target, accepted):
    import time
    from jobpilot.mail import extract_message
    from support_mail import message

    msg = message(f'<a href="{target}">Reset password</a>', html=True)
    headers = msg["payload"]["headers"]
    headers[0]["value"] = "noreply@tenant.platform.example"
    headers[2]["value"] = (
        "mx.google.com; dkim=pass header.i=@platform.example; "
        "dmarc=pass header.from=platform.example"
    )
    headers.append({"name": "Subject", "value": "Password activation"})
    rule = {"sender_domains": ["tenant.platform.example"],
            "link_origins": ["https://tenant.platform.example"]}
    challenge = {"recipient": "alex@example.test", "since": time.time() - 30,
                 "expires": time.time() + 30, "kind": "password_reset", "tenant_tokens": ["tenant"]}
    assert bool(extract_message(msg, rule, challenge)) is accepted
