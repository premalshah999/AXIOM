"""iCIMS wraps the buttons of its account mail in its own click tracker. Schwab's "Reset your password" button went
through clicks.icims.com and was rejected as token_format on every poll (2026-10-18/19), so the reset never finished
and its wait held iCIMS's one mail sender for every other job. The button's own text decides; footer links behind the
same tracker stay rejected, and the link visit may add only that tracker hop to the rule's origins."""

import time
from types import SimpleNamespace

from jobpilot.adapters.base import Adapter
from jobpilot.mail import extract_message
from support_mail import message

TRACKED = "https://clicks.icims.com/f/a/7MgBK0gRINji5sihNFJM~~/AAAAAQA~/RgRfixture"
FOOTER = "https://clicks.icims.com/f/a/footer~~/AAAAAQA~/RgRprivacy"
ICIMS_RULE = {"sender_domains": ["icims.com"], "link_origins": ["https://login.icims.com"]}


def reset_mail(html):
    msg = message(html, html=True)
    headers = msg["payload"]["headers"]
    headers[0]["value"] = "Charles Schwab <no-reply@icims.com>"
    headers[2]["value"] = "mx.google.com; dkim=pass header.d=icims.com; dmarc=pass header.from=icims.com"
    headers.append({"name": "Subject", "value": "Reset your password"})
    return msg


def challenge():
    return {"recipient": "alex@example.test", "since": time.time() - 30, "expires": time.time() + 30,
            "kind": "password_reset"}


def test_the_reset_button_behind_icims_tracker_is_the_reset_link():
    msg = reset_mail(
        "<p>We received a request to change your password.</p>"
        f'<a href="{TRACKED}">Reset your password</a> <a href="{FOOTER}">Privacy Policy</a>'
    )
    assert extract_message(msg, ICIMS_RULE, challenge()) == {"kind": "link", "value": TRACKED}


def test_a_tracked_link_needs_its_own_cue():
    diagnostics = {}
    assert extract_message(reset_mail(f'<a href="{FOOTER}">Privacy Policy</a>'), ICIMS_RULE, challenge(),
                           diagnostics=diagnostics) is None
    assert diagnostics["rejected_link_origins"] == ["https://clicks.icims.com"]


def test_the_tracker_is_trusted_only_for_icims_rules():
    rule = {"sender_domains": ["icims.com"], "link_origins": ["https://acme.wd5.myworkdayjobs.com"]}
    assert extract_message(reset_mail(f'<a href="{TRACKED}">Reset your password</a>'), rule, challenge()) is None


async def test_the_link_visit_adds_only_the_tracker_hop():
    seen = {}

    class Page:
        async def goto(self, url, **kwargs):
            seen["origins"] = set(engine.verification_origins)

        async def evaluate(self, *args):
            return None

    async def settle(timeout):
        return None

    engine = SimpleNamespace(verification_origins=None, form=SimpleNamespace(page=Page(), settle=settle))
    adapter = Adapter.__new__(Adapter)
    adapter.e, adapter.challenge = engine, {"rule": ICIMS_RULE}
    await adapter.open_link(TRACKED)
    assert seen["origins"] == {"https://login.icims.com", "https://clicks.icims.com"}
    assert engine.verification_origins is None
    await adapter.open_link("https://login.icims.com/u/reset-password?ticket=fixture")
    assert seen["origins"] == {"https://login.icims.com"}


TENANT = "https://careers-acme.icims.com"
TENANT_RULE = {"sender_domains": ["icims.com"], "link_origins": [TENANT]}


def verify_mail(html, subject="Complete your application"):
    msg = reset_mail(html)
    for header in msg["payload"]["headers"]:
        if header["name"] == "Subject":
            header["value"] = subject
    return msg


def link_challenge():
    return {**challenge(), "kind": "link"}


def test_an_uncued_button_is_the_link_when_it_alone_continues_on_the_employer_origin():
    """EBSCO 2026-10-21: the iCIMS verification button said "Click here" under a plain subject, so the mail was
    rejected as token_format on every poll and its wait held iCIMS's one mail sender."""
    link = TENANT + "/jobs/2112/software-dev-engineer-i/login?step=email&hashed=-625886742&token=abc123"
    msg = verify_mail(f'<p>Hello</p><a href="{link}">Click here</a> <a href="{TENANT}/jobs/intro">Careers home</a>')
    assert extract_message(msg, TENANT_RULE, link_challenge()) == {"kind": "link", "value": link}


def test_uncued_links_stay_rejected_when_ambiguous_or_off_origin():
    one = TENANT + "/jobs/1/a/login?step=email&hashed=1"
    two = TENANT + "/jobs/2/b/login?step=email&hashed=2"
    diagnostics = {}
    assert extract_message(verify_mail(f'<a href="{one}">Here</a><a href="{two}">Here</a>'), TENANT_RULE,
                           link_challenge(), diagnostics=diagnostics) is None
    assert diagnostics["subject"] == "Complete your application" and diagnostics["link_hosts"] == ["careers-acme.icims.com"]
    off = "https://evil.example/login?step=email&hashed=1"
    assert extract_message(verify_mail(f'<a href="{off}">Here</a>'), TENANT_RULE, link_challenge()) is None
    # A code wait never takes an uncued link.
    assert extract_message(verify_mail(f'<a href="{one}">Here</a>'), TENANT_RULE, {**challenge(), "kind": "code"}) is None


TRACKED_ACCOUNT = "https://tracking.icims.com/f/a/acct~~/AAAAAQA~/RgRverifyme"
TRACKED_FOOTER = "https://tracking.icims.com/f/a/footer~~/AAAAAQA~/RgRprivacy"


def test_the_one_tracked_non_footer_button_is_the_account_link():
    """EBSCO 2026-10-21: "User account with EBSCO Information Services, LLC" linked only through tracking.icims.com,
    with no verification wording; it was rejected as token_format on every poll."""
    msg = verify_mail(f'<a href="{TRACKED_ACCOUNT}">Click here</a> <a href="{TRACKED_FOOTER}">Privacy Policy</a>',
                      subject="User account with Acme Information Services, LLC")
    assert extract_message(msg, TENANT_RULE, link_challenge()) == {"kind": "link", "value": TRACKED_ACCOUNT}


def test_two_tracked_buttons_or_a_non_icims_rule_stay_rejected():
    two = verify_mail(f'<a href="{TRACKED_ACCOUNT}">Here</a> <a href="{TRACKED_ACCOUNT}2">There</a>')
    assert extract_message(two, TENANT_RULE, link_challenge()) is None
    workday = {"sender_domains": ["icims.com"], "link_origins": ["https://acme.wd5.myworkdayjobs.com"]}
    assert extract_message(verify_mail(f'<a href="{TRACKED_ACCOUNT}">Here</a>'), workday, link_challenge()) is None


def test_icims_on_the_employers_own_domain_trusts_its_tracked_button():
    # Garmin 2026-10-27: careers.garmin.com is iCIMS; "User account with Garmin International, Inc." carried only a
    # tracked button and was refused as token_format while the job read as a custom site.
    rule = {"sender_domains": ["acme-corp.example.test", "icims.com"], "link_origins": ["https://careers.acme-corp.example.test"]}
    mail = reset_mail(f'<p>Welcome.</p><a href="{TRACKED}">Click here</a> <a href="{FOOTER}">Privacy Policy</a>')
    assert extract_message(mail, rule, {**challenge(), "kind": "link", "ats": "icims"}) == {"kind": "link", "value": TRACKED}
    assert extract_message(mail, rule, {**challenge(), "kind": "link", "ats": "custom"}) is None
