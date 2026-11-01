"""What came back after an application. 3,000 confirmed applications and nothing measured what followed them (review of 2026-11-01):
the rules below read declines, interview requests and assessments from the mail the app already reads. Synthetic mail only."""

import base64
import re
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from jobpilot import replies
from jobpilot.db import Job, Mailbox, Run
from jobpilot.replies import SEARCH, check_replies, kind_of, read_replies, reply_of, summary

DECLINES = [
    "After careful consideration, we have decided to move forward with other candidates.",
    "Unfortunately, we will not be moving forward with your application at this time.",
    "We regret to inform you that your application was not selected.",
    "We won't be moving forward with your candidacy.",
    "You have not been selected for this position.",
    "Your application is no longer under consideration.",
    "We have decided not to proceed with your application.",
    "We are pursuing other candidates whose experience is a closer match.",
    "The position has been filled.",
    "Your application was unsuccessful on this occasion.",
    "We’ve decided to pursue other applicants.",
    "We are unable to offer you this role.",
]
INTERVIEWS = [
    "We would like to schedule a call to learn more about your background.",
    "We'd love to chat about the role next week.",
    "We are inviting you to a phone screen with our recruiter.",
    "Please share your availability for a 30 minute conversation.",
    "You have been selected for an interview.",
    "Use this link to book your interview with the team.",
    "We'd like to move you forward to the next round.",
]
ASSESSMENTS = [
    "Please complete the online assessment within five days.",
    "We invite you to take a HackerRank test for this role.",
    "As a next step, complete a video interview on HireVue.",
    "Your Codility challenge is ready; please complete the coding challenge by Friday.",
]
# What acknowledgements, footers and newsletters really say. None of these is an answer.
NOT_REPLIES = [
    "Thank you for applying. We received your application and will review it shortly.",
    "If your qualifications match, a recruiter will contact you to schedule an interview.",
    "A recruiter will reach out to schedule an interview.",
    "Should your background be a fit, we would like to speak with you.",
    "If you are not selected, we will keep your resume on file.",
    "If we decide not to move forward, we will let you know by email.",
    "Unfortunately, due to the volume of applications, we can't respond to everyone individually.",
    "Candidates selected for an interview will be contacted within two weeks.",
    "Only those selected for an interview will be contacted.",
    "We would like to invite you to join our talent community.",
    "We'd love to hear your feedback in a short survey.",
    "Our process usually includes a phone screen and two interviews.",
    "How to ace your interview: five tips for candidates.",
    "Our team reviews every application and we will be in touch about next steps.",
    "Applicants may be asked to complete an assessment.",
]


@pytest.mark.parametrize("text", DECLINES)
def test_a_decline_is_read(text):
    assert kind_of("Your application", text) == "declined"


@pytest.mark.parametrize("text", INTERVIEWS)
def test_an_interview_request_is_read(text):
    assert kind_of("Your application", text) == "interview"


@pytest.mark.parametrize("text", ASSESSMENTS)
def test_an_assessment_is_read(text):
    assert kind_of("Next step", text) == "assessment"


@pytest.mark.parametrize("text", NOT_REPLIES)
def test_an_acknowledgement_or_a_condition_is_not_a_reply(text):
    assert kind_of("Thank you for your application", text) is None


def test_a_subject_can_say_it_but_a_tip_cannot():
    assert kind_of("Interview invitation: Software Engineer", "Hello") == "interview"
    assert kind_of("Re: Interview with ExampleSoft", "See you then") == "interview"
    assert kind_of("Interview tips for candidates", "Hello") is None


def test_a_decline_wins_over_a_friendly_close():
    assert kind_of("Update", "We have decided to move forward with other candidates. We'd love to connect in the future.") == "declined"


def test_a_decline_in_an_unpunctuated_html_mail_survives_its_footer():
    assert kind_of("Update", "We will not be moving forward with your application If you have questions contact us") == "declined"


def test_may_the_month_is_not_a_condition():
    assert kind_of("Update", "We would like to schedule a call on May 5 with the team.") == "interview"


@pytest.mark.parametrize("text", DECLINES + INTERVIEWS + ASSESSMENTS)
def test_the_mailbox_search_asks_for_every_phrase(text):
    """Gmail matches words: a phrase the search doesn't name is never listed, so the rule would never see it."""
    terms = [t.strip('"') for t in re.findall(r'"[^"]+"|[\w]+', SEARCH.split(") -")[0]) if t != "OR"]
    plain = text.casefold().replace("’", "'")
    assert any(re.search(rf"\b{re.escape(term)}\b", plain) for term in terms), text


def test_the_search_is_ascii():
    assert SEARCH.isascii()  # imaplib sends a command as ASCII (test_gmail_imap_pool)


SENT = datetime(2026, 10, 10, 12, tzinfo=UTC)
AFTER = (SENT + timedelta(days=3)).timestamp()
BEFORE = (SENT - timedelta(days=3)).timestamp()


def mail(body, *, sender="jobs@examplesoft.test", subject="Your application", at=AFTER, message_id="m1", dmarc="pass", url=""):
    domain = sender.rpartition("@")[2]
    html = f'<p>{body}</p><a href="{url}">Posting</a>' if url else body
    return {
        "id": message_id,
        "internalDate": str(int(at * 1000)),
        "payload": {
            "mimeType": "text/html" if url else "text/plain",
            "headers": [
                {"name": "From", "value": f"Careers <{sender}>"},
                {"name": "Subject", "value": subject},
                {"name": "Authentication-Results", "value": f"mx.google.com; dkim=pass; dmarc={dmarc} header.from={domain}"},
            ],
            "body": {"data": base64.urlsafe_b64encode(html.encode()).decode().rstrip("=")},
        },
    }


def job(job_id="j1", company="ExampleSoft", title="Software Engineer", url="https://boards.example.test/examplesoft/1", at=SENT):
    return {"id": job_id, "company": company, "title": title, "url": url, "ats": "greenhouse", "at": at.timestamp()}


DECLINE = "We have decided to move forward with other candidates for the role at ExampleSoft."


def test_an_employers_answer_counts_for_the_job_applied_to():
    assert reply_of(mail(DECLINE), [job()]) == {"job": "j1", "kind": "declined", "at": int(AFTER)}


def test_an_ats_address_counts_when_the_message_names_the_employer():
    assert reply_of(mail(DECLINE, sender="no-reply@us.greenhouse-mail.io"), [job()])["job"] == "j1"


def test_a_sender_failing_dmarc_is_not_an_employer():
    assert reply_of(mail(DECLINE, dmarc="fail"), [job()]) is None


def test_a_stranger_naming_the_employer_is_not_the_employer():
    assert reply_of(mail(DECLINE, sender="news@careernews.test"), [job()]) is None


def test_an_ats_message_about_another_employer_is_not_this_job():
    other = "We have decided to move forward with other candidates for the role at Northwind."
    assert reply_of(mail(other, sender="no-reply@us.greenhouse-mail.io"), [job()]) is None


def test_mail_from_before_the_application_answered_something_else():
    assert reply_of(mail(DECLINE, at=BEFORE), [job()]) is None


def test_a_verification_mail_is_not_a_reply():
    assert reply_of(mail("Your verification code is 482913. We'd like to schedule a call. ExampleSoft"), [job()]) is None


def test_a_message_naming_the_role_finds_that_job_among_the_employers():
    jobs = [job("j1", title="Data Analyst", at=SENT + timedelta(hours=1)), job("j2", title="Software Engineer")]
    text = "We have decided to move forward with other candidates for Software Engineer at ExampleSoft."
    assert reply_of(mail(text), jobs)["job"] == "j2"


def test_a_message_naming_no_role_goes_to_the_latest_application_before_it():
    later = job("j3", title="Platform Engineer", at=SENT + timedelta(days=5))
    jobs = [job("j1", title="Data Analyst"), job("j2", title="QA Engineer", at=SENT + timedelta(hours=1)), later]
    assert reply_of(mail(DECLINE), jobs)["job"] == "j2"


def test_one_message_is_one_record_and_no_mail_text_is_kept():
    found = read_replies([mail(DECLINE, subject="Private subject")], [job()])
    (record,) = found.values()
    assert set(record) == {"job", "kind", "at"}
    assert "Private" not in str(found) and "ExampleSoft" not in str(found)


def seed(service, *, state="confirmed", at=SENT, company="ExampleSoft", title="Software Engineer", identity="one", mailbox=True):
    with service.db.session() as s:
        if mailbox and not s.scalar(select(Mailbox)):
            s.add(Mailbox(email="alex@example.test", credentials="fixture", state="connected"))
        row = Job(identity=identity, url=f"https://boards.example.test/{identity}", demo=False, company=company, title=title, status=state)
        s.add(row)
        s.flush()
        s.add(Run(job_id=row.id, state=state, receipt={}, packet={}, created_at=at.isoformat()))
        s.flush()
        return row.id


def serve(monkeypatch, service, pages):
    """A mailbox of `pages` lists of messages: every search is recorded, and pages are served through the real reader."""
    queries = []
    byid = {m["id"]: m for page in pages for m in page}

    async def request(box, path, params, background=False):
        queries.append(params)
        token = params.get("pageToken")
        index = int(token) if token else 0
        result = {"messages": [{"id": m["id"]} for m in pages[index]] if pages else []}
        if index + 1 < len(pages):
            result["nextPageToken"] = str(index + 1)
        return result

    async def read_message(box, message_id, background=False):
        return byid[message_id]

    monkeypatch.setattr(service.mail, "request", request)
    monkeypatch.setattr(service.mail, "read_message", read_message)
    return queries


async def test_a_pass_records_the_answer_and_summary_counts_the_job_once(service, monkeypatch):
    job_id = seed(service)
    pages = [[mail(DECLINE, message_id="m1"), mail(DECLINE, message_id="m2")]]
    serve(monkeypatch, service, pages)
    await check_replies(service.mail)
    assert summary(service.db) == {
        "connected": True, "complete": True, "replies": 1, "declined": 1, "assessment": 0, "interview": 0, "job_ids": [job_id],
    }  # fmt: skip


async def test_the_search_covers_the_whole_mailbox_from_the_first_application(service, monkeypatch):
    seed(service)
    queries = serve(monkeypatch, service, [[]])
    await check_replies(service.mail)
    query = queries[0]["q"]
    assert SEARCH in query and "to:" not in query
    assert int(query.split("after:")[1].split()[0]) == int(SENT.timestamp()) // 3600 * 3600


async def test_a_long_sweep_resumes_where_it_stopped_and_only_then_reads_new_mail(service, monkeypatch):
    seed(service)
    monkeypatch.setattr(replies, "MAX_MESSAGES", 2)
    pages = [[mail(DECLINE, message_id=f"a{i}") for i in range(2)], [mail(DECLINE, message_id=f"b{i}") for i in range(2)]]
    queries = serve(monkeypatch, service, pages)
    await check_replies(service.mail)
    assert summary(service.db)["complete"] is False
    assert summary(service.db)["replies"] == 1
    await check_replies(service.mail)
    assert summary(service.db)["complete"] is True
    assert len({q["q"] for q in queries}) == 1, "the search must not change while a sweep is under way"
    # Caught up: no search until the next look is due, then one day back, not the whole history.
    count = len(queries)
    await check_replies(service.mail)
    assert len(queries) == count
    saved = service.db.get_setting(replies.KEY, {})
    service.db.set_setting(replies.KEY, {**saved, "checked_at": time.time() - replies.CAUGHT_UP_SECONDS - 1})
    await check_replies(service.mail)
    assert int(queries[-1]["q"].split("after:")[1].split()[0]) > time.time() - replies.OVERLAP_SECONDS * 2


async def test_a_message_read_twice_is_counted_once(service, monkeypatch):
    seed(service)
    serve(monkeypatch, service, [[mail(DECLINE, message_id="m1")]])
    await check_replies(service.mail)
    saved = service.db.get_setting(replies.KEY, {})
    service.db.set_setting(replies.KEY, {**saved, "checked_at": 0})
    await check_replies(service.mail)
    assert len(service.db.get_setting(replies.KEY, {})["found"]) == 1


async def test_a_job_no_longer_confirmed_is_not_counted(service, monkeypatch):
    job_id = seed(service)
    serve(monkeypatch, service, [[mail(DECLINE)]])
    await check_replies(service.mail)
    with service.db.session() as s:
        s.scalar(select(Run).where(Run.job_id == job_id)).state = "cancelled"
    assert summary(service.db)["replies"] == 0


def test_with_no_mailbox_the_figure_says_so(service):
    seed(service, mailbox=False)
    result = summary(service.db)
    assert result["connected"] is False and result["replies"] == 0


async def test_the_reader_never_writes_to_the_mailbox(service, monkeypatch):
    """Only searches and reads: nothing is marked, moved or deleted, and no code is consumed."""
    seed(service)
    calls = []

    async def request(box, path, params, background=False):
        calls.append(("request", path, background))
        return {"messages": [{"id": "m1"}]}

    async def read_message(box, message_id, background=False):
        calls.append(("read", message_id, background))
        return mail(DECLINE, message_id=message_id)

    monkeypatch.setattr(service.mail, "request", request)
    monkeypatch.setattr(service.mail, "read_message", read_message)
    await check_replies(service.mail)
    assert calls == [("request", "/messages", True), ("read", "m1", True)]


async def test_the_mail_loop_runs_the_reader_and_a_failure_is_counted_not_lost(service, monkeypatch):
    """A reader nothing starts would leave the figure at zero for ever, which reads as nobody having answered."""
    import asyncio

    called = asyncio.Event()

    async def boom(mail):
        called.set()
        raise RuntimeError("synthetic")

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(service.mail, "heal_mailbox", nothing)
    monkeypatch.setattr(service.mail, "poll_once", nothing)
    monkeypatch.setattr("jobpilot.receipts.check_receipts", nothing)
    monkeypatch.setattr("jobpilot.receipts.reconcile_recent_drafts", nothing)
    monkeypatch.setattr(replies, "check_replies", boom)
    task = asyncio.create_task(service.mail.poll())
    try:
        await asyncio.wait_for(called.wait(), 5)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert service.mail.failure_counts["replies"]["count"] == 1
