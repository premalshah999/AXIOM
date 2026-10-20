import time

import pytest

from jobpilot.mail import extract_message, latest_employer_code
from support_mail import message
from support_mail import configured, provider


@pytest.mark.parametrize(
    "body,accepted",
    [
        ("Your application with Acme Labs. Security code: 123456", True),
        ("Your application with Other Labs. Security code: 123456", False),
        ("Your application with Acme Labswest. Security code: 123456", False),
        ("Security code: 123456", False),
    ],
)
def test_shared_sender_code_requires_whole_employer_name(body, accepted):
    msg = message(body)
    challenge = {
        "recipient": "alex@example.test",
        "since": time.time() - 30,
        "expires": time.time() + 30,
        "kind": "code",
        "ats": "greenhouse",
        "employer_name": "Acme Labs, Inc.",
    }
    diagnostics = {}
    result = extract_message(
        msg,
        {"sender_domains": ["notify.example.test"], "link_origins": []},
        challenge,
        diagnostics=diagnostics,
    )
    assert bool(result) == accepted
    if not accepted:
        assert diagnostics["rejection"] == "employer_identity"


def test_newest_code_uses_receiving_time_and_never_order_or_links():
    older = ("older", {"kind": "code", "value": "fixture-old"}, {"internalDate": "1000"}, None)
    newer = ("newer", {"kind": "code", "value": "fixture-new"}, {"internalDate": "2000"}, None)
    challenge = {"ats": "greenhouse", "employer_name": "Acme Labs"}
    assert latest_employer_code([older, newer], challenge) == [newer]
    assert latest_employer_code([newer, older], challenge) == [newer]
    assert len(latest_employer_code([older, newer], {"ats": "custom"})) == 2
    tied = ("tied", older[1], newer[2], None)
    assert len(latest_employer_code([newer, tied], challenge)) == 2
    link = ("link", {"kind": "link", "value": "https://example.test/reset"}, newer[2], None)
    assert len(latest_employer_code([older, link], challenge)) == 2


async def test_mail_poll_matches_newest_this_employer_and_consumes_only_that_message(service):
    from jobpilot.db import Job, MailChallenge, MailConsumption
    from sqlalchemy import select

    _, run, rule = await configured(service)
    with service.db.session() as session:
        session.add(
            Job(
                id=run["job_id"],
                identity="synthetic-gh",
                url="https://boards.greenhouse.io/acme/jobs/123",
                company="Acme Labs",
                ats="greenhouse",
            )
        )
    challenge = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    current = time.time()
    messages = [
        message("Acme Labs Security code: 111111", message_id="old", received=current - 0.5),
        message("Acme Labs Security code: 222222", message_id="new", received=current),
        message("Other Labs Security code: 333333", message_id="other", received=current + 0.1),
    ]
    service.mail.transport = provider(messages)
    await service.mail.poll_once()
    with service.db.session() as session:
        row = session.get(MailChallenge, challenge["id"])
        assert row.state == "matched" and row.message_id == "new"
        assert service.mail.vault.open(row.payload)["value"] == "222222"
        consumed = list(session.scalars(select(MailConsumption)))
        assert len(consumed) == 1 and consumed[0].key.endswith(":new")


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("https://job-boards.greenhouse.io/acme/jobs/123", True),
        ("https://boards.greenhouse.io/acme/jobs/123", True),
        ("https://job-boards.greenhouse.io/embed/job_app?for=acme&token=123", True),
        ("https://job-boards.greenhouse.io/other/jobs/123", False),
        ("https://job-boards.greenhouse.io/acme/jobs/124", False),
        ("https://job-boards.greenhouse.io/acme/jobs/123/confirmation", False),
        ("https://job-boards.greenhouse.io.evil.test/acme/jobs/123", False),
        ("http://job-boards.greenhouse.io/acme/jobs/123", False),
    ],
)
def test_greenhouse_code_alias_permission_never_authorizes_other_jobs_or_email_links(url, allowed):
    from types import SimpleNamespace
    from jobpilot.email_browser import EmailBrowser

    engine = SimpleNamespace(
        job={"ats": "greenhouse", "url": "https://boards.greenhouse.io/acme/jobs/123"},
        armed=True,
        service=SimpleNamespace(mail=SimpleNamespace(rule_for=lambda url: None)),
    )
    browser = EmailBrowser(engine)
    browser.rule = {"link_origins": ["https://boards.greenhouse.io"]}
    assert browser.code_origin_allowed(url, {"text": "To submit your application, enter the code"}) == allowed
    assert browser.rule["link_origins"] == ["https://boards.greenhouse.io"]
