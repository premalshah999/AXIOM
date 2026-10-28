"""Hershey's (2026-10-27): the career site is careers.thehersheycompany.com, its SuccessFactors tenant is company=Hersheys,
and the tenant mails its codes and password resets from hersheys.com. No identity token named that domain, so the
sender search never listed the mail and every reset "did not arrive in time". The tenant's company id is now one of
the employer's names. Synthetic employer and addresses only."""

import base64
import time

import httpx

from jobpilot.db import Job, MailChallenge
from jobpilot.mail import RuleInput
from jobpilot.mail.senders import KNOWN_SENDERS, sender_allowed, tenant_tokens
from support_mail import seed

SITE = "https://careers.theacmefoodscompany.test"
JOB = SITE + "/job/Dallas-Data-Scientist-TX-75201/1432153000"


def test_the_tenant_company_id_names_the_employers_own_sender():
    tokens = tenant_tokens(JOB, "careers.theacmefoodscompany.test", "Acmefoods")
    assert "acmefoods" in tokens
    assert sender_allowed("acmefoods.test", KNOWN_SENDERS["successfactors"], tokens, True)
    # Without the tenant id the branded site alone names a different label.
    assert not sender_allowed("acmefoods.test", KNOWN_SENDERS["successfactors"], tenant_tokens(JOB, ""), True)
    # SAP's environment marker is not part of the employer's name; look-alikes still never pass.
    assert "acmefoods" in tenant_tokens(JOB, "", "AcmefoodsP2")
    assert not sender_allowed("acmefoods-careers.test", KNOWN_SENDERS["successfactors"], tokens, True)


def reset_mail(received):
    body = "Reset your password: https://career4.successfactors.com/career?company=Acmefoods&career_ns=reset&t=fx1"
    return {
        "id": "reset-1",
        "internalDate": str(int(received * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Acme Foods Careers <noreply@acmefoods.test>"},
                {"name": "To", "value": "Alex <alex@example.test>"},
                {"name": "Subject", "value": "Password reset request"},
                {
                    "name": "Authentication-Results",
                    "value": "mx.google.com; dkim=pass; dmarc=pass header.from=acmefoods.test",
                },
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
        },
    }


async def test_a_reset_from_the_tenants_own_domain_is_searched_and_matched(service):
    box, run = seed(service, employer=SITE)
    rule = await service.mail.save_rule(
        RuleInput(mailbox_id=box["id"], employer_origin=SITE, sender_domains=KNOWN_SENDERS["successfactors"])
    )
    with service.db.session() as session:
        session.add(Job(id=run["job_id"], identity="synthetic-sf", url=JOB,
                        company="careers.theacmefoodscompany.test", ats="successfactors"))
    service.db.set_setting("sf_tenant:" + SITE, {"origin": "https://career4.successfactors.com", "company": "Acmefoods"})
    challenge = service.mail.begin(run["id"], rule, "alex@example.test", "password_reset")
    mail = reset_mail(time.time())
    queries = []

    def route(request):
        if request.url.path.endswith("/messages"):
            queries.append(request.url.params["q"])
            return httpx.Response(200, json={"messages": [{"id": mail["id"]}]})
        return httpx.Response(200, json=mail)

    service.mail.transport = httpx.MockTransport(route)
    await service.mail.poll_once()
    assert any("from:acmefoods" in q for q in queries)
    with service.db.session() as session:
        row = session.get(MailChallenge, challenge["id"])
        assert row.state == "matched", row.reason
        assert service.mail.vault.open(row.payload)["value"].startswith("https://career4.successfactors.com/career?")
