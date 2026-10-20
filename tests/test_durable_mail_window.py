import time

import pytest

from jobpilot.db import MailChallenge
from jobpilot.mail_errors import MailPending
from support_mail import configured


@pytest.mark.parametrize(
    "domain,accepted",
    [
        ("workflow.email.us-phoenix-1.ocs.oraclecloud.com", True),
        ("workflow.email.uk-london-1.ocs.oraclecloud.com", True),
        ("workflow.mail.us2.oraclecloud.com", True),
        ("other-employer.oraclecloud.com", False),
        ("workflow.email.other-employer.ocs.oraclecloud.com", False),
        ("workflow.email.us-phoenix-1.ocs.oraclecloud.com.evil.test", False),
    ],
)
def test_oracle_regional_transport_is_not_an_employer_and_keeps_envelope_guards(domain, accepted):
    from jobpilot.mail import extract_message
    from support_mail import message

    msg = message()
    headers = msg["payload"]["headers"]
    headers[0]["value"] = "Oracle <pod.fa.sender@" + domain + ">"
    headers[2]["value"] = "mx.google.com; dmarc=pass header.from=" + domain
    challenge = {
        "recipient": "alex@example.test",
        "since": time.time() - 30,
        "expires": time.time() + 30,
        "kind": "code",
        "tenant_tokens": ["acme"],
    }
    rule = {"sender_domains": ["oraclecloud.com"], "link_origins": []}
    assert bool(extract_message(msg, rule, challenge)) is accepted
    if accepted:
        headers[1]["value"] = "other@example.test"
        assert extract_message(msg, rule, challenge) is None
        headers[1]["value"] = "alex@example.test"
        headers[2]["value"] = "mx.google.com; dmarc=fail header.from=" + domain
        assert extract_message(msg, rule, challenge) is None


async def test_persisted_wait_extends_delivery_without_moving_request_or_renewing(service):
    _, run, rule = await configured(service)
    request = service.mail.begin(run["id"], rule, "alex@example.test", "password_reset")
    with pytest.raises(MailPending) as pending:
        await service.inbox.wait(request, max_wait_seconds=0)
    first = pending.value.challenge
    assert first["since"] == request["since"]
    assert first["expires"] == request["since"] + service.config.mail_durable_wait_seconds
    second = service.mail.persist_wait(request["id"])
    assert second["expires"] == first["expires"]
    assert second["request_id"] == request["id"]
    assert second["generation"] == first["generation"]


async def test_cancelled_or_expired_request_cannot_be_resurrected(service):
    _, run, rule = await configured(service)
    request = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    with service.db.session() as session:
        session.get(MailChallenge, request["id"]).expires = time.time() - 1
    with pytest.raises(ValueError, match="expired"):
        service.mail.persist_wait(request["id"])


async def test_direct_mail_wait_uses_same_persisted_window(service):
    _, run, rule = await configured(service)
    request = service.mail.begin(run["id"], rule, "alex@example.test", "link")
    with pytest.raises(MailPending) as pending:
        await service.mail.wait(request["id"], max_wait_seconds=0)
    assert pending.value.challenge["expires"] == request["since"] + service.config.mail_durable_wait_seconds


@pytest.mark.parametrize("same_request", [True, False])
async def test_private_helper_cleanup_preserves_only_request_parked_by_other_ats_helper(
    service, same_request
):
    from types import SimpleNamespace
    from jobpilot.email_browser import EmailBrowser

    _, run, rule = await configured(service)
    request = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    helper = EmailBrowser.__new__(EmailBrowser)
    helper.mail, helper.challenge_id, helper.deferred = service.mail, request["id"], False
    helper.engine = SimpleNamespace(
        result={
            "waiting_kind": "email",
            "wait_handle": {"challenge_id": request["id"] if same_request else "different-request"},
        }
    )
    helper.close()
    with service.db.session() as session:
        row = session.get(MailChallenge, request["id"])
        assert row.state == ("pending" if same_request else "cancelled")


@pytest.mark.parametrize("same_handle", [False, True])
async def test_terminal_code_request_clears_only_its_own_continuation(service, same_handle):
    from jobpilot.db import Run

    _, run, rule = await configured(service)
    request = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    with service.db.session() as s:
        owner = s.get(Run, run["id"])
        owner.packet = {
            **owner.packet,
            "email_wait_resume": {"challenge_id": request["id"] if same_handle else "another-request"},
            "kept": "checkpoint",
        }
        s.get(MailChallenge, request["id"]).state = "consumed"
    service.mail.outcome(request["id"], "failed", "Code rejected")
    with service.db.session() as s:
        packet = s.get(Run, run["id"]).packet
        assert ("email_wait_resume" not in packet) == same_handle
        assert packet["kept"] == "checkpoint"
    if same_handle:
        fresh = service.mail.begin(run["id"], rule, "alex@example.test", "code")
        assert fresh["id"] != request["id"]
        assert fresh["since"] >= request["since"]


@pytest.mark.parametrize(
    "kind,origin,rejected",
    [
        (
            "link",
            "https://acme.wd5.myworkdayjobs.com",
            False,
        ),  # origin-guarded link: sender name is not decisive
        ("code", "https://acme.wd5.myworkdayjobs.com", True),  # codes have no origin guard: stay strict
    ],
)
def test_workday_tenant_name_only_decides_unguarded_mail(kind, origin, rejected):
    from jobpilot.mail import extract_message
    from support_mail import message

    msg = message("Verify your account: https://acme.wd5.myworkdayjobs.com/en-US/External/activate/abc123")
    headers = msg["payload"]["headers"]
    headers[0]["value"] = "Workday <someothername@myworkday.com>"
    headers[2]["value"] = "mx.google.com; dmarc=pass header.from=myworkday.com"
    challenge = {
        "recipient": "alex@example.test",
        "since": time.time() - 30,
        "expires": time.time() + 30,
        "kind": kind,
        "tenant_tokens": ["acme"],
    }
    rule = {"sender_domains": ["myworkday.com"], "link_origins": [origin]}
    diagnostics = {}
    extract_message(msg, rule, challenge, diagnostics=diagnostics)
    assert (diagnostics.get("rejection") == "tenant") is rejected


def _oracle_message(domain, body, auth="mx.google.com; dkim=pass header.d={d}; dmarc=pass header.from={d}"):
    from support_mail import message

    msg = message(body)
    headers = msg["payload"]["headers"]
    headers[0]["value"] = f"UT Health <donotreply@{domain}>"
    headers[2]["value"] = auth.format(d=domain)
    return msg


@pytest.mark.parametrize(
    "domain,body,auth,accepted",
    [
        (
            "uthscsa.edu",
            "Hello. You must confirm your identity using the one-time pass code : 4F7K2Q9X Note : expires.",
            None,
            True,
        ),
        ("anything.test", "You must confirm your identity using the one-time passcode: 4F7K2Q9X", None, True),
        ("uthscsa.edu", "Your verification code is 4F7K2Q9X", None, False),  # not the Oracle template
        (
            "uthscsa.edu",
            "You must confirm your identity using the one-time pass code : 4F7K2Q9X",
            "mx.google.com; dkim=fail header.d=uthscsa.edu; dmarc=fail header.from=uthscsa.edu",
            False,
        ),  # unauthenticated
    ],
)
def test_oracle_code_from_an_employer_domain_is_accepted_only_for_its_template(domain, body, auth, accepted):
    from jobpilot.mail import extract_message

    msg = _oracle_message(domain, body, *([auth] if auth else []))
    challenge = {
        "recipient": "alex@example.test",
        "since": time.time() - 30,
        "expires": time.time() + 30,
        "kind": "code",
        "ats": "oracle",
        "tenant_tokens": ["uthealth", "houston"],
    }
    rule = {"sender_domains": ["oraclecloud.com", "oracle.com"], "link_origins": []}
    assert bool(extract_message(msg, rule, challenge)) is accepted


def test_oracle_template_is_not_trusted_for_other_ats():
    from jobpilot.mail import extract_message

    msg = _oracle_message(
        "uthscsa.edu", "You must confirm your identity using the one-time pass code : 4F7K2Q9X"
    )
    challenge = {
        "recipient": "alex@example.test",
        "since": time.time() - 30,
        "expires": time.time() + 30,
        "kind": "code",
        "ats": "workday",
        "tenant_tokens": ["acme"],
    }
    assert extract_message(msg, {"sender_domains": ["myworkday.com"], "link_origins": []}, challenge) is None
