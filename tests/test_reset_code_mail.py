"""TikTok careers (2026-10-28): the password reset is confirmed by an emailed code ("TikTok verification code: 523632")
typed into six react-code-input boxes. The boxes read maxlength 6,6,6,6,6,1 (every box but the last takes a pasted
code), so the page was never seen as a code step; the run waited for a reset link, and every poll refused the code mail
as token_format because reset windows took only links. Synthetic data only."""

import base64
import time

import httpx

from jobpilot.adapters.base import Adapter
from jobpilot.db import MailChallenge
from jobpilot.mail import RuleInput, extract_message
from support_mail import seed

RULE = {"sender_domains": ["acme.test"], "link_origins": ["https://careers.acme.test"]}


def mail(body, subject="Acme verification code: 523632", received=None):
    return {
        "id": "reset-code-1",
        "internalDate": str(int((received or time.time()) * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Acme Careers <noreply@acme.test>"},
                {"name": "To", "value": "Alex <alex@example.test>"},
                {"name": "Subject", "value": subject},
                {"name": "Authentication-Results", "value": "mx.google.com; dkim=pass; dmarc=pass header.from=acme.test"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
        },
    }


def reset_window():
    return {"recipient": "alex@example.test", "since": time.time() - 30, "expires": time.time() + 30,
            "kind": "password_reset"}


def test_a_reset_window_takes_a_lone_emailed_code():
    msg = mail("Your verification code is 523632. It expires in 10 minutes.")
    assert extract_message(msg, RULE, reset_window()) == {"kind": "code", "value": "523632"}


def test_a_reset_link_still_wins_and_two_codes_are_never_guessed():
    link = "https://careers.acme.test/reset-password?token=fixture"
    both = mail(f"Reset your password: {link} Verification code: 523632", subject="Reset your password")
    assert extract_message(both, RULE, reset_window()) == {"kind": "link", "value": link}
    two = mail("Verification code: 523632. Old code: 111111", subject="Your codes")
    assert extract_message(two, RULE, reset_window()) is None


async def test_the_poll_hands_a_reset_window_its_code(service):
    site = "https://careers.acme.test"
    box, run = seed(service, employer=site)
    rule = await service.mail.save_rule(RuleInput(mailbox_id=box["id"], employer_origin=site, sender_domains=["acme.test"]))
    challenge = service.mail.begin(run["id"], rule, "alex@example.test", "password_reset")
    message = mail("Your verification code is 523632.")

    def route(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": message["id"]}]})
        return httpx.Response(200, json=message)

    service.mail.transport = httpx.MockTransport(route)
    await service.mail.poll_once()
    with service.db.session() as session:
        row = session.get(MailChallenge, challenge["id"])
        assert row.state == "matched", row.reason
    assert service.mail.vault.open(row.payload) == {"kind": "code", "value": "523632"}


def boxes(maxlengths, kind="number"):
    return [{"id": f"d{i}", "type": kind, "label": "Enter the code in the email sent to you", "maxlength": n}
            for i, n in enumerate(maxlengths)]


def code_fields(fields, text="Verify Email Account\\nEnter the code in the email sent to alex@example.test"):
    adapter = Adapter.__new__(Adapter)
    return adapter.code_fields({"fields": fields, "text": text})


def test_react_code_input_boxes_are_a_code_step():
    assert len(code_fields(boxes([6, 6, 6, 6, 6, 1]))) == 6
    assert len(code_fields(boxes([1, 1, 1, 1, 1, 1]))) == 6  # the ordinary split code still is


def test_other_long_inputs_are_not_code_boxes():
    assert code_fields(boxes([6, 6, 6, 6, 6, 6])) == []  # no last single-character box
    assert code_fields(boxes([6, 6, 6, 6, 6, 1]), text="Tell us about yourself") == []  # no code instruction
    mixed = boxes([6, 6, 6, 6, 6]) + boxes([1], kind="text")
    assert code_fields(mixed) == []  # one widget's boxes share a type
