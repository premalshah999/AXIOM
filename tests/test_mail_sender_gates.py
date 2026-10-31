"""Sender and tenant gates (2026-10-31).

Textron's Taleo mail comes from hrtextron.<tld> ("Set up a user name and password at Textron"): the employer's name with one
word before it, which the exact-label rule refused. Loosening a sender rule must not let one employer's mail through
another's window: ServiceTitan's window saw Accenture's Workday reset (same shared sender), and a code in it, unlike a
link, has no origin to be refused on. Synthetic data only."""

import base64
import time

import pytest

from jobpilot.mail import extract_message
from jobpilot.mail.senders import decorated_label, decorated_senders, sender_allowed, tenant_tokens


def msg(sender, body, subject="Hello", received=None):
    domain = sender.rpartition("@")[2]
    return {
        "id": "m1",
        "internalDate": str(int((received or time.time()) * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": f"Recruiting <{sender}>"},
                {"name": "To", "value": "Alex <alex@example.test>"},
                {"name": "Subject", "value": subject},
                {"name": "Authentication-Results", "value": f"mx.google.com; dkim=pass; dmarc=pass header.from={domain}"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
        },
    }


def challenge(kind, tokens, **extra):
    return {"recipient": "alex@example.test", "since": time.time() - 60, "expires": time.time() + 600, "kind": kind,
            "tenant_tokens": tokens, **extra}


@pytest.mark.parametrize(
    "domain,allowed",
    [
        ("hrtextron.example", True),  # the employer's name with one word before it
        ("textronhr.example", True),  # or after it
        ("textron.example", True),  # exact: as before
        ("mail.hrtextron.example", True),  # a subdomain of it
        ("hrtextron-careers.example", False),  # a hyphen separates words: look-alikes never pass
        ("nottextron.example", False),
        ("hrtextronhrmail.example", False),  # one word each side at most
        ("hrtex.example", False),
    ],
)
def test_an_employer_domain_may_carry_one_word_around_its_name(domain, allowed):
    assert sender_allowed(domain, ["taleo.net"], ["textron"], employer_senders=True) is allowed


def test_only_platforms_whose_mail_comes_from_employer_domains_get_this():
    assert not sender_allowed("hrtextron.example", ["myworkday.com"], ["textron"], employer_senders=False)


def test_short_names_are_never_decorated():
    assert not decorated_label("hrea", ["ea"]) and not sender_allowed("hrea.example", ["x.test"], ["ea"], True)
    assert decorated_senders(["ea", "abc"]) == []  # no search terms for names under five letters


def test_the_search_lists_the_decorated_domains():
    terms = decorated_senders(tenant_tokens("https://textron.taleo.net/careersection/x", "Textron"))
    assert "hrtextron" in terms and "textronhr" in terms and "careerstextron" in terms


def test_a_taleo_reset_link_from_the_decorated_domain_matches_only_on_the_employers_origin():
    rule = {"sender_domains": ["taleo.net"], "link_origins": ["https://textron.taleo.net"], "employer_origin": "https://textron.taleo.net"}
    c = challenge("password_reset", ["textron"], employer_senders=True, ats="taleo")
    own = msg("recruiting-noreply@hrtextron.example", "Reset your password: https://textron.taleo.net/careersection/reset?t=abc123def", "Reset your password")
    assert extract_message(own, rule, c) == {"kind": "link", "value": "https://textron.taleo.net/careersection/reset?t=abc123def"}
    elsewhere = msg("recruiting-noreply@hrtextron.example", "Reset your password: https://evil.example/reset?t=abc123def", "Reset your password")
    assert extract_message(elsewhere, rule, c) is None


WORKDAY_RULE = {"sender_domains": ["myworkday.com", "otp.workday.com"], "link_origins": ["https://servicetitan.wd1.myworkdayjobs.com"],
                "employer_origin": "https://servicetitan.wd1.myworkdayjobs.com"}
TITAN = ["servicetitan"]


def test_another_tenants_reset_code_never_reaches_this_tenants_window():
    """A shared sender's code names no origin; the tenant gate that links skip must apply to it."""
    other = msg("accenture@myworkday.com", "Your verification code: 483921", "Reset your password")
    assert extract_message(other, WORKDAY_RULE, challenge("password_reset", TITAN, ats="workday")) is None
    own = msg("servicetitan@myworkday.com", "Your verification code: 483921", "Reset your password")
    assert extract_message(own, WORKDAY_RULE, challenge("password_reset", TITAN, ats="workday")) == {"kind": "code", "value": "483921"}
    generic = msg("noreply@myworkday.com", "Your verification code: 483921", "Reset your password")
    assert extract_message(generic, WORKDAY_RULE, challenge("password_reset", TITAN, ats="workday")) == {"kind": "code", "value": "483921"}


@pytest.mark.parametrize("kind", ["link", "password_reset"])
def test_another_tenants_workday_reset_link_is_refused_on_its_origin(kind):
    link = "https://accenture.wd103.myworkdayjobs.com/en-US/Accenture/passwordreset/abc123def456"
    other = msg("accenture@myworkday.com", f"Reset your password: {link}", "Reset your password for your candidate account")
    assert extract_message(other, WORKDAY_RULE, challenge(kind, TITAN, ats="workday")) is None
    mine = "https://servicetitan.wd1.myworkdayjobs.com/en-US/ServiceTitan/passwordreset/abc123def456"
    own = msg("servicetitan@myworkday.com", f"Reset your password: {mine}", "Reset your password for your candidate account")
    assert extract_message(own, WORKDAY_RULE, challenge("password_reset", TITAN, ats="workday")) == {"kind": "link", "value": mine}
