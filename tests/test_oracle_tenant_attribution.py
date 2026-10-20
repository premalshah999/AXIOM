"""Oracle tenants mail their codes under their own identity (mailbox audit, 2026-10-18), not from one shared sender:
the employer's domain, the tenant's pod address or a display name naming the employer. With several tenants'
windows open, a code is accepted only by the tenant it names, and tenants no longer wait for each other."""

import pytest

from jobpilot.mail import employer_names, oracle_attributed, oracle_pod

FORD = ("Ford Motor Company", "efds")
HONEYWELL = ("Honeywell", "ibqbjb")
GOLDMAN = ("Goldman Sachs", "hdpc")
FORTINET = ("Fortinet", "edel")
ORACLE = ("Oracle", "eeho")
UCSF = ("University of California San Francisco", "ucsf")


def test_pod_and_names():
    assert oracle_pod("https://edel.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001/job/1") == "edel"
    assert oracle_pod("https://fa-exvu-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/x") == "exvu"
    assert oracle_pod("https://careers.example.com/job/1") == ""
    assert {"ford", "fordmotor"} <= employer_names("Ford Motor Company")
    assert "ucsf" in employer_names("University of California San Francisco")


@pytest.mark.parametrize("address,display,body,owner,others", [
    ("recruitingnoreply@ford.com", "Ford Careers", "Confirm your identity", FORD, [HONEYWELL, GOLDMAN]),
    ("noreply@honeywell.com", "Human Resources", "Confirm your identity", HONEYWELL, [FORD, GOLDMAN]),
    ("edel.fa.sender@workflow.email.us-phoenix-1.ocs.oraclecloud.com", "", "Your code", FORTINET, [FORD, GOLDMAN]),
    ("gsrecruiting@oracle.com", "Goldman Sachs Recruiting", "Your verification code", GOLDMAN, [ORACLE, FORD]),
    ("noreply@oracle.com", "Oracle Talent Acquisition", "Your Oracle identity verification", ORACLE, [GOLDMAN]),
    ("ucsf-oraclecloud-notifications@ucsf.edu", "", "Confirm your identity", UCSF, [FORD]),
])
def test_each_code_goes_only_to_the_tenant_it_names(address, display, body, owner, others):
    tenants = [owner, *others]
    accepted = [
        company for company, pod in tenants
        if oracle_attributed(address, display, body, company, pod, [t for t in tenants if t[0] != company])
    ]
    assert accepted == [owner[0]]


def test_a_body_names_the_employer_only_when_no_open_peer_is_named():
    body = "Thank you for applying to Goldman Sachs. Powered by Oracle."
    assert not oracle_attributed("noreply@oracle.com", "", body, "Oracle", "eeho", [GOLDMAN])
    assert oracle_attributed("noreply@oracle.com", "", body, "Goldman Sachs", "hdpc", [ORACLE])


def test_a_ford_code_reaches_fords_window_and_not_honeywells():
    import time

    from jobpilot.mail import extract_message
    from support_mail import message

    now = time.time()
    msg = message("Confirm your identity using the one-time passcode. Your verification code is 483921", received=now)
    headers = msg["payload"]["headers"]
    headers[0]["value"] = "Ford Careers <recruitingnoreply@ford.com>"
    headers[2]["value"] = "mx.google.com; dkim=pass; dmarc=pass header.from=ford.com"
    rule = {"sender_domains": ["oraclecloud.com", "oracle.com"], "link_origins": []}

    def window(company, pod, tokens, peers):
        return {
            "id": "ch-" + pod, "recipient": "alex@example.test", "since": now - 30, "expires": now + 30,
            "kind": "code", "ats": "oracle", "employer_name": company, "tenant_tokens": tokens,
            "employer_senders": True, "open_windows": [], "oracle_pod": pod, "oracle_peers": peers,
        }

    ford = window("Ford Motor Company", "efds", ["efds", "ford", "motor", "fordmotorcompany"], [HONEYWELL])
    honeywell = window("Honeywell", "ibqbjb", ["ibqbjb", "honeywell"], [FORD])
    assert extract_message(msg, rule, ford) == {"kind": "code", "value": "483921"}
    diagnostics = {}
    assert extract_message(msg, rule, honeywell, diagnostics=diagnostics) is None
    assert diagnostics["rejection"] in {"tenant", "sender"}
