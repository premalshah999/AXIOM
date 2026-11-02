"""A link or reset mail from a sender nobody listed, on an empty database (2026-11-01).

The owner's install has 1,131 learned rules; a new install has none. A career site's own sender is derived from its name, so
a reset that comes from another domain (a mailer the employer hires) was only named in the run's diagnostics and never
matched, on every platform but Workday. The exact origin of an employer's own host in an authenticated mail addressed to the
applicant, inside a request window, is the employer's proof; a host many employers share proves nothing. Synthetic data only."""

import base64
import time

import httpx
import pytest

from jobpilot.db import Event, Job, MailChallenge, MailRule
from jobpilot.mail import RuleInput
from jobpilot.mail.polling import employer_only_host
from support_mail import seed

ME = "alex@example.test"


def mail(origin, *, kind="password_reset", sender="noreply@mailer.acme-hr.test", auth="dmarc=pass", message_id="m1", path=None):
    domain = sender.rpartition("@")[2]
    reset = kind == "password_reset"
    subject = "Reset your password" if reset else "Verify your email"
    link = origin + (path or ("/password/reset?t=abc123def456" if reset else "/verify?t=abc123def456"))
    body = f"To {subject.lower()}, open this link: {link}"
    return {
        "id": message_id,
        "internalDate": str(int(time.time() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": f"Acme Careers <{sender}>"},
                {"name": "To", "value": f"Alex <{ME}>"},
                {"name": "Subject", "value": subject},
                {"name": "Authentication-Results", "value": f"mx.google.com; dkim=pass; {auth} header.from={domain}"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
        },
    }


def serve(service, message, queries):
    def route(request):
        if request.url.path.endswith("/messages"):
            queries.append(request.url.params["q"])
            return httpx.Response(200, json={"messages": [{"id": message["id"]}]})
        return httpx.Response(200, json=message)

    service.mail.transport = httpx.MockTransport(route)


async def window(service, *, ats, origin, company="Acme", kind="password_reset", domains=("acme.test",)):
    """An empty database but for this one employer's job, its rule and its open request."""
    box, run = seed(service, employer=origin)
    with service.db.exclusive() as s:
        s.add(Job(id="b" * 32, identity="b" * 32, url=origin + "/careers/job/1", ats=ats, company=company, title="Analyst", status="running"))
    rule = await service.mail.save_rule(RuleInput(mailbox_id=box["id"], employer_origin=origin, sender_domains=list(domains)))
    return rule, service.mail.begin(run["id"], rule, ME, kind)


async def polled(service, rule, challenge):
    await service.mail.poll_once()
    with service.db.session() as s:
        return s.get(MailChallenge, challenge["id"]).state, set(s.get(MailRule, rule["id"]).sender_domains)


@pytest.mark.parametrize("kind", ["link", "password_reset"])
@pytest.mark.parametrize(
    "ats,origin",
    [
        ("custom", "https://careers.acme.test"),
        ("eightfold", "https://acme.eightfold.ai"),
        ("taleo", "https://acme.taleo.net"),
        ("avature", "https://acme.avature.net"),
    ],
)
async def test_a_mail_linking_to_the_employers_own_origin_is_matched_from_a_sender_nobody_listed(service, ats, origin, kind):
    rule, challenge = await window(service, ats=ats, origin=origin, kind=kind)
    queries = []
    serve(service, mail(origin, kind=kind), queries)
    state, domains = await polled(service, rule, challenge)
    assert state == "matched", queries
    assert "mailer.acme-hr.test" in domains  # learned: the next window lists it
    path = "/password/reset?t=abc123def456" if kind == "password_reset" else "/verify?t=abc123def456"
    assert service.mail.claim(challenge["id"]) == {"kind": "link", "value": origin + path}
    with service.db.session() as s:
        assert [e for e in s.query(Event).filter(Event.kind == "mail_sender_learned")]


async def test_the_search_asks_for_reset_wording_beside_the_listed_senders(service):
    rule, challenge = await window(service, ats="custom", origin="https://careers.acme.test")
    queries = []
    serve(service, mail("https://careers.acme.test"), queries)
    await service.mail.poll_once()
    assert any("from:acme.test" in q for q in queries), queries
    assert any("subject:reset" in q and "subject:password" in q and "subject:activation" in q for q in queries), queries


async def test_a_busy_mailbox_of_worded_mail_never_fails_the_window(service):
    """The wording is searched beside the sender search: past its page it reads the newest, and the window stays open."""
    rule, challenge = await window(service, ats="custom", origin="https://careers.acme.test")
    message = mail("https://careers.acme.test")

    def route(request):
        if request.url.path.endswith("/messages"):
            worded = "subject:" in request.url.params["q"]
            return httpx.Response(200, json={"messages": [{"id": "m1"}] if worded else [], **({"nextPageToken": "more"} if worded else {})})
        return httpx.Response(200, json=message)

    service.mail.transport = httpx.MockTransport(route)
    state, domains = await polled(service, rule, challenge)
    assert state == "matched" and "mailer.acme-hr.test" in domains


async def test_a_tracker_or_shared_host_link_proves_nothing(service):
    """A career site that is iCIMS underneath: the mail's valid link can be iCIMS's tracker, once its sender is known. It is
    not this employer's origin, so it proves nothing about a sender nobody listed."""
    rule, challenge = await window(service, ats="custom", origin="https://careers.acme.test", kind="link", domains=("acme.test", "icims.com"))
    serve(service, mail("https://tracking.icims.com", kind="link"), [])
    state, domains = await polled(service, rule, challenge)
    assert state == "pending" and "mailer.acme-hr.test" not in domains


async def test_a_public_mail_domain_is_never_learned(service):
    rule, challenge = await window(service, ats="custom", origin="https://careers.acme.test")
    serve(service, mail("https://careers.acme.test", sender="someone@gmail.com"), [])
    state, domains = await polled(service, rule, challenge)
    assert state == "pending" and "gmail.com" not in domains


async def test_a_link_to_a_posting_on_the_site_is_no_proof(service):
    """A job alert from another domain, worded "confirm", linking to the employer's own posting."""
    rule, challenge = await window(service, ats="custom", origin="https://careers.acme.test", kind="link")
    serve(service, mail("https://careers.acme.test", kind="link", path="/jobs/1234"), [])
    state, domains = await polled(service, rule, challenge)
    assert state == "pending" and "mailer.acme-hr.test" not in domains


async def test_a_host_that_turns_out_to_serve_another_employer_proves_nothing(service):
    origin = "https://portal.example-ats.test"
    rule, challenge = await window(service, ats="custom", origin=origin)
    with service.db.exclusive() as s:
        s.add(Job(id="c" * 32, identity="c" * 32, url=origin + "/other/1", ats="custom", company="Globex", title="Clerk", status="discovered"))
    serve(service, mail(origin), [])
    state, domains = await polled(service, rule, challenge)
    assert state == "pending" and "mailer.acme-hr.test" not in domains


async def test_another_posting_of_the_same_employer_does_not_stop_it(service):
    origin = "https://careers.acme.test"
    rule, challenge = await window(service, ats="custom", origin=origin)
    with service.db.exclusive() as s:
        s.add(Job(id="c" * 32, identity="c" * 32, url=origin + "/jobs/2", ats="custom", company="ACME", title="Clerk", status="discovered"))
    serve(service, mail(origin), [])
    assert (await polled(service, rule, challenge))[0] == "matched"


async def test_a_link_to_another_origin_is_not_taken_whoever_sent_it(service):
    rule, challenge = await window(service, ats="custom", origin="https://careers.acme.test")
    serve(service, mail("https://careers.other.test"), [])
    state, domains = await polled(service, rule, challenge)
    assert state == "pending" and "mailer.acme-hr.test" not in domains


async def test_a_mail_the_receiving_server_did_not_authenticate_is_not_taken(service):
    rule, challenge = await window(service, ats="custom", origin="https://careers.acme.test")
    serve(service, mail("https://careers.acme.test", auth="dmarc=fail"), [])
    state, domains = await polled(service, rule, challenge)
    assert state == "pending" and "mailer.acme-hr.test" not in domains


async def test_a_code_window_never_learns_a_sender_from_a_link(service):
    rule, challenge = await window(service, ats="custom", origin="https://careers.acme.test", kind="code")
    serve(service, mail("https://careers.acme.test"), [])
    state, domains = await polled(service, rule, challenge)
    assert "mailer.acme-hr.test" not in domains


@pytest.mark.parametrize(
    "ats,url,expected",
    [
        ("workday", "https://acme.wd5.myworkdayjobs.com/en-US/External/job/A_R1", True),
        ("eightfold", "https://acme.eightfold.ai/careers/job/1", True),
        ("taleo", "https://acme.taleo.net/careersection/ext/jobdetail.ftl?job=1", True),
        ("avature", "https://acme.avature.net/careers/JobDetail/A/1", True),
        ("custom", "https://careers.acme.test/jobs/1", True),
        # Hosts many employers share: a link to one of them says nothing about whose mail it is.
        ("lever", "https://jobs.lever.co/acme/1b2c3d4e-0000-4000-8000-000000000001", False),
        ("greenhouse", "https://job-boards.greenhouse.io/acme/jobs/1", False),
        ("ashby", "https://jobs.ashbyhq.com/acme/2c3d4e5f-0000-4000-8000-000000000001", False),
        ("workable", "https://apply.workable.com/acme/j/ABCDE12345/", False),
        ("smartrecruiters", "https://jobs.smartrecruiters.com/Acme/1", False),
        ("successfactors", "https://career4.successfactors.com/career?company=AcmeP&career_job_req_id=1", False),
        ("adp", "https://myjobs.adp.com/acmecareers/cx/job-details?reqId=1", False),
        ("dayforce", "https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL/jobs/1", False),
        ("icims", "https://careers-acme.icims.com/jobs/1/a/job", False),
        ("oracle", "https://ecqq.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1", False),
        # A "custom" site that is really a platform's host is not the employer's own.
        ("custom", "https://jobs.lever.co/acme/1b2c3d4e-0000-4000-8000-000000000001", False),
        ("custom", "", False),
        ("custom", "ftp://careers.acme.test/jobs/1", False),
        # An employer's own host is its own whichever platform runs behind it.
        ("avature", "https://jobs.acme.test/en_US/careers/JobDetail/Analyst/1", True),
        ("eightfold", "https://apply.careers.acme.test/careers/job/1", True),
        # The platform's own front door: many employers on one host.
        ("eightfold", "https://app.eightfold.ai/careers/job/687?domain=acme.test", False),
        ("taleo", "https://www.taleo.net/careersection/ext/jobdetail.ftl?job=1", False),
        # A short link or a shared portal is nobody's own.
        ("custom", "https://go.vansh.nyc/AB", False),
        ("custom", "https://recruiting.paylocity.com/Recruiting/Jobs/Details/1", False),
    ],
)
def test_only_a_host_one_employer_owns_can_prove_a_sender(ats, url, expected):
    assert employer_only_host(ats, url) is expected
