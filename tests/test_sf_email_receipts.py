"""SuccessFactors employer mail: receipts for unproven runs and SAP's account passcode. Synthetic data only."""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from jobpilot.adapters.base import CODE_LABEL, code_label
from jobpilot.db import Job, Mailbox, Run
from jobpilot.email_browser import CODE_LABEL as BROWSER_CODE_LABEL
from jobpilot.mail import extract_message
from jobpilot.receipts import _DRAFT_COMPARED, reconcile_recent_drafts, title_matches, trusted_sender
from support_mail import EMAIL, message


def test_two_letter_employer_domain_is_trusted_only_as_the_whole_company_name():
    assert trusted_sender("xy.test", {"company": "XY", "url": "https://careers.xy.test/job/1"})
    assert not trusted_sender("xy.test", {"company": "XY Vernova", "url": "https://careers.xyvernova.test/job/1"})
    assert not trusted_sender("xy.test", {"company": "Xylem", "url": "https://careers.xylem.test/job/1"})


def test_successfactors_eu_data_center_sends_receipts_like_its_com_hosts():
    job = {"company": "SAP", "url": "https://careers.sap.com/job/1"}
    assert trusted_sender("successfactors.eu", job) and trusted_sender("successfactors.com", job)


def test_title_glued_to_requisition_in_the_subject_still_matches_exactly():
    assert title_matches("Data Engineer", "application for data engineerrequisition number 74967.")
    assert title_matches(
        "Applications Developer & Integration Specialist",
        "Application for Applications Developer & Integration\n Specialistrequisition number 77378.",
    )
    assert not title_matches("Data Engineer I", "application for data engineer iirequisition number 1.")
    assert title_matches("Requisition Analyst", "application for requisition analyst")


def test_required_marker_does_not_hide_a_passcode_field():
    assert code_label("* Passcode:") == "Passcode:"
    assert code_label("Passcode *") == "Passcode"
    assert CODE_LABEL.search(code_label("* Passcode:"))
    assert BROWSER_CODE_LABEL.fullmatch(code_label("* Passcode:"))
    assert not CODE_LABEL.search(code_label("* Postal Code Area:"))


def test_passcode_labelled_account_mail_yields_its_code_despite_footer_numbers():
    body = (
        "Dear Alex, Thank you for your interest in joining Example! To create your Example Career Site "
        "account, please use the passcode below to verify your identity: Passcode: 4829153 "
        "Please note: This passcode is valid for the next 10 minutes. Example SE, Main Street 16, 69190 Town"
    )
    challenge = {
        "recipient": "alex@example.test",
        "since": time.time() - 30,
        "expires": time.time() + 30,
        "kind": "code",
        "ats": "successfactors",
    }
    result = extract_message(
        message(body), {"sender_domains": ["notify.example.test"], "link_origins": []}, challenge
    )
    assert result == {"kind": "code", "value": "4829153"}


def unproven_run(service, *, state="waiting_browser", title="Engineer"):
    start = datetime.now(UTC) - timedelta(hours=1)
    with service.db.session() as s:
        if not s.scalar(select(Mailbox)):
            s.add(Mailbox(email=EMAIL, credentials="owned-fixture", state="connected"))
        job = Job(
            identity=f"unproven-{state}-{title}",
            url="https://owned.test/job/1",
            demo=False,
            company="Example",
            title=title,
            status=state,
        )
        s.add(job)
        s.flush()
        run = Run(
            job_id=job.id,
            state=state,
            mode="submit",
            receipt={},
            packet={"profile": {"email": EMAIL}, "match_approved": True},
            created_at=start.isoformat(),
        )
        s.add(run)
        s.flush()
        return run.id, job.id, start


def mailbox(monkeypatch, service, messages):
    fetched = []

    async def request(box, path, params):
        if path == "/messages":
            return {"messages": [{"id": m["id"]} for m in messages]}
        fetched.append(path)
        return next(m for m in messages if path == "/messages/" + m["id"])

    monkeypatch.setattr(service.mail, "request", request)
    return fetched


def ack(mid, at, body):
    msg = message(body, message_id=mid)
    msg["internalDate"] = str(int(at.timestamp() * 1000))
    msg["payload"]["headers"][1]["value"] = EMAIL
    return msg


async def test_employer_receipt_confirms_a_run_left_waiting_for_the_browser(service, monkeypatch):
    _DRAFT_COMPARED.clear()
    rid, jid, start = unproven_run(service)
    mailbox(
        monkeypatch,
        service,
        [ack("ack", start + timedelta(minutes=5), "Thank you for applying to Example and the Engineer role.")],
    )
    assert (await reconcile_recent_drafts(service.mail))["emails_confirmed"] == 1
    with service.db.session() as s:
        run = s.get(Run, rid)
        assert run.state == "confirmed" and run.receipt["email"]["sender_trusted"]
        assert run.receipt["matched_existing_application"]
        assert s.get(Job, jid).status == "confirmed"


async def test_receipt_for_another_role_confirms_nothing_and_is_not_fetched_again(service, monkeypatch):
    _DRAFT_COMPARED.clear()
    rid, _, start = unproven_run(service, title="Platform Engineer")
    fetched = mailbox(
        monkeypatch,
        service,
        [ack("other", start + timedelta(minutes=5), "Thank you for applying to Example and the Designer role.")],
    )
    assert (await reconcile_recent_drafts(service.mail))["emails_confirmed"] == 0
    assert (await reconcile_recent_drafts(service.mail))["emails_confirmed"] == 0
    assert fetched == ["/messages/other"]
    with service.db.session() as s:
        assert s.get(Run, rid).state == "waiting_browser"


SF_RESET = (
    '<p>Example Password Reset</p><a href="https://performancemanager.successfactors.eu/sf/home?company=exampleP">'
    '<img alt="Example"></a><p>To reset your password, click the link below.</p>'
    '<a href="https://career2.successfactors.eu/career?company=exampleP&career_ns=resetPassword&token=abc123">'
    "Reset password</a>"
)


def reset_challenge(ats):
    return {"recipient": "alex@example.test", "since": time.time() - 30, "expires": time.time() + 30,
            "kind": "password_reset", "ats": ats}


def test_a_successfactors_reset_mail_links_saps_career_host_and_its_reset_link_is_the_one():
    """Nestle (2026-10-24): the employer's reset mail linked career2.successfactors.eu, outside the rule's branded
    career-site origin, and every poll rejected it as token_format; the run stayed in waiting_email."""
    rule = {"sender_domains": ["notify.example.test"], "link_origins": ["https://jobs.example.test"]}
    result = extract_message(message(SF_RESET, html=True), rule, reset_challenge("successfactors"))
    assert result == {
        "kind": "link",
        "value": "https://career2.successfactors.eu/career?company=exampleP&career_ns=resetPassword&token=abc123",
    }
    # Another platform's challenge never accepts SAP's hosts.
    assert (extract_message(message(SF_RESET, html=True), rule, reset_challenge("workday")) or {}).get("kind") != "link"
