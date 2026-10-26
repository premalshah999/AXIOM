"""A password reset counts only when the employer's page took it. SuccessFactors' "Forgot your password?" had not
opened its page when the reset step ran, so the email was typed into the sign-in form and "Sign In" pressed with no
password; the run recorded a reset nobody requested and later runs waited on its silence (L3Harris, Farmers,
Westinghouse, Hershey's, Belden 2026-10-21..25). Its mail rule also predated SAP's government-cloud sender."""

import pytest

from jobpilot.adapters.base import RESET_SENT, reset_proven
from jobpilot.mail import RuleInput
from support_mail import seed


@pytest.mark.parametrize("text", [
    "If an account matches the information you entered, then you will receive an email with instructions on how to "
    "reset your password.",
    "We have sent you an email with a link to reset your password.",
    "Check your inbox for the reset link.",
])
def test_the_employers_reset_confirmation_is_recognized(text):
    assert RESET_SENT.search(text)


def test_a_sign_in_page_is_no_reset_confirmation():
    assert not RESET_SENT.search("Sign In. Email. Password. Forgot your password? A password is required.")
    assert not reset_proven({"reset_requested_at": 1.0}) and reset_proven({"reset_submitted_at": "2026-10-25"})


async def test_a_platform_rule_gains_the_platforms_newer_senders_but_an_owners_rule_is_kept(service):
    box, _ = seed(service, employer="https://career-acme.example.test")
    platform = await service.mail.save_rule(RuleInput(mailbox_id=box["id"], employer_origin="https://career-acme.example.test/",
                                                      sender_domains=["successfactors.com"]))
    job = {"url": "https://career-acme.example.test/careers", "ats": "successfactors"}
    upgraded = await service.mail.ensure_rule(job, "alex@example.test")
    assert upgraded["id"] == platform["id"] and "ns2cloud.com" in upgraded["sender_domains"]
    owners = await service.mail.save_rule(RuleInput(mailbox_id=box["id"], employer_origin="https://other.example.test/",
                                                    sender_domains=["hr.other.example.test"]))
    kept = await service.mail.ensure_rule({"url": "https://other.example.test/jobs", "ats": "successfactors"},
                                          "alex@example.test")
    assert kept["id"] == owners["id"] and kept["sender_domains"] == ["hr.other.example.test"]


async def test_an_employers_own_career_site_mails_from_its_own_domain(service):
    # Intuit (2026-10-26): a custom career site had no sender rule, so its password reset was never requested.
    seed(service, employer="https://jobs.acme-corp.example.test")
    rule = await service.mail.ensure_rule({"url": "https://jobs.acme-corp.example.test/login", "ats": "custom"},
                                          "alex@example.test")
    assert rule and rule["sender_domains"] == ["example.test"]
