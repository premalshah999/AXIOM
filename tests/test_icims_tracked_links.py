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
