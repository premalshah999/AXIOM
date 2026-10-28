"""Farmers', Bain's and Intuit's password resets (2026-10-27) "did not arrive in time" with no candidate on any poll,
and nothing showed whether the mail came from a domain the employer rule does not name or never came at all. A link or
reset wait whose sender search lists nothing now records the sender domains (only) of reset mail that reached the
address in its window. Those messages are never matched. Synthetic data only."""

import base64
import time

import httpx

from jobpilot.db import Event, MailChallenge
from jobpilot.mail import RuleInput
from support_mail import seed

SITE = "https://careers.acme.test"


def mail(received):
    body = "Reset your password: https://careers.acme.test/reset?t=fixture"
    return {
        "id": "outside-1",
        "internalDate": str(int(received * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Acme Careers <noreply@mailer.acme-hr.test>"},
                {"name": "To", "value": "Alex <alex@example.test>"},
                {"name": "Subject", "value": "Reset your password"},
                {"name": "Authentication-Results",
                 "value": "mx.google.com; dkim=pass; dmarc=pass header.from=mailer.acme-hr.test"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
        },
    }


async def test_reset_mail_from_an_unlisted_sender_is_named_but_never_matched(service):
    box, run = seed(service, employer=SITE)
    rule = await service.mail.save_rule(RuleInput(mailbox_id=box["id"], employer_origin=SITE, sender_domains=["acme.test"]))
    challenge = service.mail.begin(run["id"], rule, "alex@example.test", "password_reset")
    message = mail(time.time())

    def route(request):
        if request.url.path.endswith("/messages"):
            query = request.url.params["q"]
            return httpx.Response(200, json={"messages": [] if "from:" in query else [{"id": message["id"]}]})
        return httpx.Response(200, json=message)

    service.mail.transport = httpx.MockTransport(route)
    await service.mail.poll_once()
    with service.db.session() as session:
        assert session.get(MailChallenge, challenge["id"]).state == "pending"
        polls = [e.data for e in session.query(Event).filter(Event.run_id == run["id"], Event.kind == "mail_poll")]
    assert polls and polls[-1]["outside_senders"] == ["mailer.acme-hr.test"]
    assert polls[-1]["candidate_count"] == 0
