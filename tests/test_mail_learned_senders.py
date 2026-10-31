"""Senders an employer's rule is missing (2026-10-31).

AARP's career site is Eightfold under a label of "custom": its rule listed aarp.org, the mail came from
"AARP Careers" <confirm@eightfold.ai> and was refused as `sender` on every poll. TikTok's rule learned mail.bytedance.net
while ByteDance's own rule (15 of 15 code windows expired) never did. Neither may loosen the tenant gate: a platform's sender
is learned only from mail that names this employer and no other open window's, and a sibling's sender is shared only when it
carries this employer's own name. Synthetic data only."""

import base64
import time

import httpx
import pytest

from jobpilot.db import Job, MailChallenge, MailRule, Run, now
from jobpilot.mail import RuleInput
from support_mail import seed

SITE = "https://apply.acme.test"


LINK = "https://apply.acme.test/verify?t=abc123def456"


def mail(sender, display, body="Please confirm your email: " + LINK, subject="Verify your email", message_id="m1", to="alex@example.test"):
    domain = sender.rpartition("@")[2]
    return {
        "id": message_id,
        "internalDate": str(int(time.time() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": f"{display} <{sender}>"},
                {"name": "To", "value": f"Alex <{to}>"},
                {"name": "Subject", "value": subject},
                {"name": "Authentication-Results", "value": f"mx.google.com; dkim=pass; dmarc=pass header.from={domain}"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
        },
    }


def serve(service, *messages):
    def route(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": m["id"]} for m in messages]})
        return httpx.Response(200, json=next(m for m in messages if request.url.path.endswith("/" + m["id"])))

    service.mail.transport = httpx.MockTransport(route)


async def employer(service, company="Acme", url=SITE + "/careers/job/1", ats="custom", run_id="a" * 32, job_id="b" * 32, domains=("acme.test",)):
    box, run = seed(service, employer=SITE, run_id=run_id) if run_id == "a" * 32 else (None, None)
    with service.db.exclusive() as s:
        s.add(Job(id=job_id, identity=job_id, url=url, ats=ats, company=company, title="Analyst", status="running"))
        if run is None:
            s.add(Run(id=run_id, job_id=job_id, state="running", started_at=now(), packet={"profile": {"email": "alex@example.test"}}))
    if box is None:
        return None
    return await service.mail.save_rule(RuleInput(mailbox_id=box["id"], employer_origin=SITE, sender_domains=list(domains)))


async def test_a_platform_sender_naming_the_employer_joins_its_rule(service):
    rule = await employer(service)
    c = service.mail.begin("a" * 32, rule, "alex@example.test", "auto")
    serve(service, mail("confirm@eightfold.ai", "Acme Careers"))
    await service.mail.poll_once()
    with service.db.session() as s:
        assert s.get(MailChallenge, c["id"]).state == "matched"
        assert {"acme.test", "eightfold.ai"} <= set(s.get(MailRule, rule["id"]).sender_domains)
    assert service.mail.claim(c["id"]) == {"kind": "link", "value": LINK}


async def test_a_platform_sender_that_names_another_employer_is_not_learned(service):
    rule = await employer(service)
    c = service.mail.begin("a" * 32, rule, "alex@example.test", "auto")
    serve(service, mail("confirm@eightfold.ai", "Other Corp Careers"))
    await service.mail.poll_once()
    with service.db.session() as s:
        assert s.get(MailChallenge, c["id"]).state == "pending"
        assert s.get(MailRule, rule["id"]).sender_domains == ["acme.test"]


async def test_a_platform_sender_naming_this_and_another_open_employer_is_not_learned(service):
    rule = await employer(service)
    c = service.mail.begin("a" * 32, rule, "alex@example.test", "auto")
    await employer(service, company="Other Corp", url="https://jobs.othercorp.test/j/2", run_id="r2", job_id="j2")
    other = await service.mail.save_rule(RuleInput(mailbox_id=rule["mailbox_id"], employer_origin="https://jobs.othercorp.test", sender_domains=["othercorp.test"]))
    service.mail.begin("r2", other, "alex@example.test", "auto")
    serve(service, mail("confirm@eightfold.ai", "Acme and Other Corp Careers"))
    await service.mail.poll_once()
    with service.db.session() as s:
        assert s.get(MailChallenge, c["id"]).state == "pending" and s.get(MailRule, rule["id"]).sender_domains == ["acme.test"]


@pytest.mark.parametrize("sender", ["x@myworkday.com", "x@greenhouse.io", "x@icims.com", "x@oraclecloud.com"])
async def test_platforms_with_attribution_rules_of_their_own_are_never_learned_this_way(service, sender):
    rule = await employer(service)
    service.mail.begin("a" * 32, rule, "alex@example.test", "auto")
    serve(service, mail(sender, "Acme Careers"))
    await service.mail.poll_once()
    with service.db.session() as s:
        assert s.get(MailRule, rule["id"]).sender_domains == ["acme.test"]


async def test_a_sibling_brands_own_domain_is_shared_by_name_only(service):
    box, _ = seed(service)
    mail_service = service.mail
    await mail_service.save_rule(RuleInput(mailbox_id=box["id"], employer_origin="https://lifeatbrandx.test", sender_domains=["mail.bytedance.test"]))
    await mail_service.save_rule(RuleInput(mailbox_id=box["id"], employer_origin="https://lifeatbrandx2.test", sender_domains=["brandx2.test", "myworkday.com", "tiktok.test"]))
    parent = await mail_service.save_rule(RuleInput(mailbox_id=box["id"], employer_origin="https://jobs.bytedance.test", sender_domains=["bytedance.test"]))
    rule = await mail_service.ensure_rule({"url": "https://jobs.bytedance.test/en/position/1/detail", "ats": "custom"}, "alex@example.test")
    assert rule["id"] == parent["id"]
    assert rule["sender_domains"] == ["bytedance.test", "mail.bytedance.test"]  # the sibling's domain carries this employer's name
    # Neither a platform sender nor a domain with another name is shared.
    assert "myworkday.com" not in rule["sender_domains"] and "tiktok.test" not in rule["sender_domains"]
    with service.db.session() as s:
        assert s.get(MailRule, parent["id"]).sender_domains == ["bytedance.test", "mail.bytedance.test"]
    other = await mail_service.save_rule(RuleInput(mailbox_id=box["id"], employer_origin="https://careers.unrelated.test", sender_domains=["unrelated.test"]))
    assert mail_service.sibling_senders(other) == []  # nothing carries its name
