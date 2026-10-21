"""iCIMS mails every employer from one sender and routes its buttons through tracking.icims.com, so one mail lease served
every iCIMS employer: one verification at a time, and 49 iCIMS runs ended waiting for the mailbox or "sender busy" in
five days (2026-10-21). The subject names the employer ("User account with EBSCO Information Services, LLC"), so mail is
attributed by name while another employer's iCIMS window is open, and different employers no longer share the lease."""

import time

import pytest

from jobpilot.db import MailRule, Run
from jobpilot.mail import extract_message
from jobpilot.mail.senders import icims_attributed, icims_names
from support_mail import configured, message

EBSCO = icims_names("EBSCO", "https://careers-ebscoind.icims.com/jobs/1/engineer/job")
SCHWAB = icims_names("career-schwab.icims.co", "https://career-schwab.icims.com/jobs/2/engineer/job")
TRACKED = "https://tracking.icims.com/f/a/7MgBK0gRINji5sihNFJM~~/AAAAAQA~/RgRfixture"


def test_names_come_from_the_company_and_the_portal_tenant():
    assert {"ebsco", "ebscoind"} <= EBSCO
    # A company recorded as the iCIMS host names nothing; the tenant label still does.
    assert SCHWAB == {"schwab"}


def test_the_subject_decides_which_employer_a_mail_belongs_to():
    subject = "User account with EBSCO Information Services, LLC"
    assert icims_attributed(subject, "", EBSCO, SCHWAB)
    assert not icims_attributed(subject, "", SCHWAB, EBSCO)
    # A mail that names neither stays unattributed while a peer is open.
    assert not icims_attributed("Verify your email", "Click below to continue", EBSCO, SCHWAB)


def verify_mail(subject):
    msg = message(f'<p>Please verify your email.</p><a href="{TRACKED}">Verify Email</a>', html=True)
    headers = msg["payload"]["headers"]
    headers[0]["value"] = "Careers <no-reply@icims.com>"
    headers[2]["value"] = "mx.google.com; dkim=pass header.d=icims.com; dmarc=pass header.from=icims.com"
    headers.append({"name": "Subject", "value": subject})
    return msg


def window(names, peers):
    return {"recipient": "alex@example.test", "since": time.time() - 30, "expires": time.time() + 30,
            "kind": "auto", "ats": "icims", "icims_names": sorted(names), "icims_peers": sorted(peers)}


def test_a_tracked_verification_reaches_only_the_employer_it_names():
    rule = {"sender_domains": ["icims.com"], "link_origins": ["https://careers-ebscoind.icims.com"]}
    msg = verify_mail("User account with EBSCO Information Services, LLC")
    assert extract_message(msg, rule, window(EBSCO, SCHWAB)) == {"kind": "link", "value": TRACKED}
    diagnostics = {}
    schwab_rule = {**rule, "link_origins": ["https://career-schwab.icims.com"]}
    assert extract_message(msg, schwab_rule, window(SCHWAB, EBSCO), diagnostics=diagnostics) is None
    assert diagnostics["rejection"] == "tenant"


@pytest.mark.parametrize(
    "kind,other_origin,blocked",
    [
        ("auto", "https://careers-ebscoind.icims.com", False),  # another employer: its own attributed window
        ("auto", "https://careers-acme.icims.com", True),  # the same employer still waits
        ("password_reset", "https://careers-ebscoind.icims.com", True),  # resets stay serialized
    ],
)
async def test_different_icims_employers_no_longer_share_one_lease(service, kind, other_origin, blocked):
    _, run, rule = await configured(service)
    with service.db.exclusive() as session:
        row = session.get(MailRule, rule["id"])
        row.sender_domains = ["icims.com"]
        row.employer_origin = "https://careers-acme.icims.com"
        row.link_origins = ["https://login.icims.com"]
        session.add(Run(id="other", job_id="other-job", state="running"))
    rule = service.mail.rule_for("https://careers-acme.icims.com")
    service.mail.begin(run["id"], rule, "alex@example.test", kind)
    other_rule = {**rule, "employer_origin": other_origin, "link_origins": ["https://login.icims.com"]}
    with service.db.session() as session:
        assert bool(service.mail.sender_blocker(session, other_rule, "other", kind)) is blocked


async def test_a_central_reset_window_closes_after_three_minutes(service):
    """Atlassian and Mass Markets (2026-10-21) each held iCIMS's one sender for 20 minutes waiting for a Central reset
    that never came; Central mails a reset within seconds when the account exists."""
    import time

    from jobpilot.mail.challenges import CENTRAL_RESET_WAIT

    _, run, rule = await configured(service)
    with service.db.exclusive() as session:
        row = session.get(MailRule, rule["id"])
        row.sender_domains = ["icims.com"]
        row.employer_origin = "https://login.icims.com"
        row.link_origins = ["https://login.icims.com"]
    rule = service.mail.rule_for("https://login.icims.com")
    reset = service.mail.begin(run["id"], rule, "alex@example.test", "password_reset")
    assert reset["expires"] - time.time() <= CENTRAL_RESET_WAIT + 2
