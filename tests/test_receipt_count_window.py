"""Count-in-window: an employer's title-less acknowledgements counted against its recent commits. Synthetic data."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from jobpilot.db import Event, Job, Run
from jobpilot.receipts import check_receipts, count_in_window
from support_mail import EMAIL, receipt_seed
from test_receipt_attribution import mailbox_provider, receipt

THANKS = "Thank you for applying to Example. We received your application."


def commit(service, state, at, *, email_claim="", submitting=True, started=None, title="Engineer"):
    """One run of the employer "Example", committed at `at` (a "submitting" event) unless `submitting` is false."""
    receipt_seed(service, 0)  # The mailbox.
    with service.db.session() as s:
        n = s.scalar(select(func.count(Job.id)))
        job = Job(identity=f"count-{n}", url=f"https://owned.test/count/{n}", demo=False, company="Example", title=title)
        s.add(job)
        s.flush()
        claim = {"email": {"message_id": email_claim, "sender_trusted": True}} if email_claim else {}
        run = Run(
            job_id=job.id,
            state=state,
            receipt=claim,
            packet={"profile": {"email": EMAIL}},
            created_at=(started or at - timedelta(minutes=3)).isoformat(),
        )
        s.add(run)
        s.flush()
        if submitting:
            s.add(Event(run_id=run.id, kind="submitting", message="Synthetic commit", created_at=at.isoformat()))
        return run.id


async def states(service, ids):
    with service.db.session() as s:
        return [s.get(Run, rid).state for rid in ids]


async def test_two_commits_minutes_apart_are_credited_by_two_title_less_mails_despite_old_jobs(service, monkeypatch):
    """Exelixis: two Workday applications 2 min 41 s apart, two "Thank You For Applying!" mails, three older
    committed jobs that "fit" every such mail by time (2026-10-30)."""
    at = datetime.now(UTC) - timedelta(hours=1)
    for days in (12, 6, 3):
        commit(service, "confirmed", at - timedelta(days=days))
    page = commit(service, "confirmed", at)  # Its page confirmed it; the mail is still unattached.
    unknown = commit(service, "submission_unknown", at + timedelta(seconds=161))
    mailbox_provider(
        monkeypatch,
        service,
        [receipt("first", at + timedelta(seconds=13), THANKS), receipt("second", at + timedelta(seconds=176), THANKS)],
    )
    assert (await check_receipts(service.mail))["emails_confirmed"] == 1
    with service.db.session() as s:
        row = s.get(Run, unknown)
        assert row.state == "confirmed"
        assert row.receipt["email"]["attribution_basis"] == "count_in_window"
        assert row.receipt["email"]["message_id"] == "second" and row.receipt["count_matched"]
        assert s.get(Run, page).state == "confirmed"


async def test_a_single_commit_is_credited_though_the_employer_has_older_jobs(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    for days in (9, 4):
        commit(service, "confirmed", at - timedelta(days=days))
    unknown = commit(service, "submission_unknown", at)
    # 5 s before the logged event, as Snap-on's was: grace and count together.
    mailbox_provider(monkeypatch, service, [receipt("ack", at - timedelta(seconds=5), THANKS)])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 1
    assert await states(service, [unknown]) == ["confirmed"]


async def test_fewer_mails_than_commits_credit_nothing(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "confirmed", at - timedelta(days=3))
    commit(service, "confirmed", at)
    unknown = commit(service, "submission_unknown", at + timedelta(seconds=90))
    mailbox_provider(monkeypatch, service, [receipt("only-one", at + timedelta(seconds=100), THANKS)])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    assert await states(service, [unknown]) == ["submission_unknown"]


async def test_two_unconfirmed_commits_are_never_told_apart_by_generic_mails(service, monkeypatch):
    """A mail minutes after the second commit may be the first's duplicate, so neither is credited (the old strictness)."""
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "confirmed", at - timedelta(days=3))
    ids = [commit(service, "submission_unknown", at + timedelta(seconds=s)) for s in (0, 150)]
    msgs = [receipt("one", at + timedelta(seconds=15), THANKS), receipt("two", at + timedelta(seconds=165), THANKS)]
    mailbox_provider(monkeypatch, service, msgs)
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    assert await states(service, ids) == ["submission_unknown"] * 2


async def test_an_unconfirmed_older_run_still_awaiting_its_mail_blocks_the_credit(service, monkeypatch):
    """Late generic mail 3 days after the first commit may be that first run's own (test_receipt_attribution's case)."""
    at = datetime.now(UTC) - timedelta(days=4)
    ids = [commit(service, "submission_unknown", at), commit(service, "submission_unknown", at + timedelta(days=3))]
    msgs = [receipt("late-one", at + timedelta(days=3, seconds=60), THANKS)]
    mailbox_provider(monkeypatch, service, msgs)
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    assert await states(service, ids) == ["submission_unknown"] * 2


async def test_an_unconfirmed_run_older_than_the_wait_no_longer_blocks(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "submission_unknown", at - timedelta(days=9))
    unknown = commit(service, "submission_unknown", at)
    mailbox_provider(monkeypatch, service, [receipt("ack", at + timedelta(seconds=20), THANKS)])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 1
    assert await states(service, [unknown]) == ["confirmed"]


async def test_a_mail_long_after_the_commit_is_outside_the_window(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "confirmed", at - timedelta(days=3))
    unknown = commit(service, "submission_unknown", at)
    mailbox_provider(monkeypatch, service, [receipt("later", at + timedelta(minutes=9), THANKS)])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    assert await states(service, [unknown]) == ["submission_unknown"]


@pytest.mark.parametrize(
    "body",
    [
        "You've started your job application! Finish your application to Example.",
        "Thank you for applying to Example. Please verify your email address: the verification code is 123456.",
        "Thank you for applying to Example. To reset your password use the link below.",
        "Thank you for applying to Example. Unfortunately we are not moving forward with your application.",
    ],
)
async def test_unfinished_challenge_and_decline_mail_never_counts(service, monkeypatch, body):
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "confirmed", at - timedelta(days=3))
    unknown = commit(service, "submission_unknown", at)
    mailbox_provider(monkeypatch, service, [receipt("not-an-ack", at + timedelta(seconds=10), body)])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    assert await states(service, [unknown]) == ["submission_unknown"]


async def test_a_started_notice_does_not_make_up_the_count(service, monkeypatch):
    """Two commits, one real acknowledgement and one "started" notice: the count is one short."""
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "confirmed", at - timedelta(days=3))
    ids = [commit(service, "submission_unknown", at + timedelta(seconds=s)) for s in (0, 60)]
    msgs = [
        receipt("real", at + timedelta(seconds=15), THANKS),
        receipt("started", at + timedelta(seconds=40), "You've started your job application! Example is waiting."),
    ]
    mailbox_provider(monkeypatch, service, msgs)
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    assert await states(service, ids) == ["submission_unknown"] * 2


async def test_a_run_without_a_commit_event_is_never_credited(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "confirmed", at - timedelta(days=3))
    unknown = commit(service, "submission_unknown", at, submitting=False)
    mailbox_provider(monkeypatch, service, [receipt("ack", at + timedelta(seconds=10), THANKS)])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    assert await states(service, [unknown]) == ["submission_unknown"]


async def test_a_confirmed_run_of_unknown_commit_time_nearby_blocks_the_credit(service, monkeypatch):
    """A confirmed run with no "submitting" event may have committed inside the window and own the mail."""
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "confirmed", at - timedelta(days=3))
    commit(service, "confirmed", at, submitting=False, started=at - timedelta(minutes=20))
    unknown = commit(service, "submission_unknown", at + timedelta(minutes=1))
    mailbox_provider(monkeypatch, service, [receipt("ack", at + timedelta(minutes=1, seconds=10), THANKS)])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    assert await states(service, [unknown]) == ["submission_unknown"]


async def test_a_mail_that_already_proves_another_run_is_not_counted_again(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "confirmed", at - timedelta(days=3))
    commit(service, "confirmed", at, email_claim="ack")  # Already holds the mail.
    unknown = commit(service, "submission_unknown", at + timedelta(seconds=30))
    mailbox_provider(monkeypatch, service, [receipt("ack", at + timedelta(seconds=15), THANKS)])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    assert await states(service, [unknown]) == ["submission_unknown"]


async def test_a_run_proven_by_a_titled_mail_does_not_count_against_the_title_less_ones(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "confirmed", at - timedelta(days=3))
    proven = commit(service, "confirmed", at, email_claim="titled", title="Data Analyst")
    unknown = commit(service, "submission_unknown", at + timedelta(seconds=30))
    titled = "Thank you for applying to Example. We received your application for the Data Analyst role."
    msgs = [receipt("titled", at + timedelta(seconds=12), titled), receipt("plain", at + timedelta(seconds=44), THANKS)]
    mailbox_provider(monkeypatch, service, msgs)
    await check_receipts(service.mail)
    with service.db.session() as s:
        assert s.get(Run, unknown).state == "confirmed"
        assert s.get(Run, unknown).receipt["email"]["message_id"] == "plain"
        assert s.get(Run, proven).receipt["email"]["message_id"] == "titled"


async def test_a_mail_that_also_fits_another_employers_commit_is_not_counted(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "confirmed", at - timedelta(days=3))
    unknown = commit(service, "submission_unknown", at)
    with service.db.session() as s:
        other = Job(identity="other", url="https://owned.test/other", demo=False, company="Other Corp", title="Analyst")
        s.add(other)
        s.flush()
        run = Run(job_id=other.id, state="confirmed", packet={"profile": {"email": EMAIL}},
                  created_at=(at - timedelta(minutes=3)).isoformat())
        s.add(run)
        s.flush()
        s.add(Event(run_id=run.id, kind="submitting", message="Synthetic commit", created_at=at.isoformat()))
    both = "Thank you for applying to Example and Other Corp. We received your application."
    msg = receipt("both", at + timedelta(seconds=10), both)
    # An ATS sender acknowledges for every employer it hosts.
    msg["payload"]["headers"][0]["value"] = "Careers <no-reply@us.greenhouse-mail.io>"
    msg["payload"]["headers"][2]["value"] = "mx.google.com; dkim=pass; dmarc=pass header.from=us.greenhouse-mail.io"
    mailbox_provider(monkeypatch, service, [msg])
    await check_receipts(service.mail)
    assert await states(service, [unknown]) == ["submission_unknown"]


async def test_a_run_that_holds_its_own_mail_leaves_the_other_mails_to_the_unknown_run(service, monkeypatch):
    """Matched by time alone, the proven run would take the earlier mail and leave the unknown run its neighbour's."""
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "confirmed", at - timedelta(days=3))
    commit(service, "confirmed", at, email_claim="theirs")
    unknown = commit(service, "submission_unknown", at + timedelta(seconds=30))
    msgs = [receipt("mine", at + timedelta(seconds=5), THANKS), receipt("theirs", at + timedelta(seconds=15), THANKS)]
    mailbox_provider(monkeypatch, service, msgs)
    await check_receipts(service.mail)
    with service.db.session() as s:
        assert s.get(Run, unknown).receipt["email"]["message_id"] == "mine"


async def test_a_mail_from_an_unrelated_sender_is_not_counted(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "confirmed", at - timedelta(days=3))
    unknown = commit(service, "submission_unknown", at)
    msg = receipt("elsewhere", at + timedelta(seconds=10), THANKS)
    msg["payload"]["headers"][0]["value"] = "News <no-reply@news.unrelated.test>"
    msg["payload"]["headers"][2]["value"] = "mx.google.com; dkim=pass; dmarc=pass header.from=news.unrelated.test"
    mailbox_provider(monkeypatch, service, [msg])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    assert await states(service, [unknown]) == ["submission_unknown"]


async def test_a_mail_naming_another_job_of_the_employer_is_not_title_less(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    commit(service, "confirmed", at - timedelta(days=3), title="Data Analyst")
    unknown = commit(service, "submission_unknown", at, title="Engineer")
    named = "Thank you for applying to Example. We received your application for the Data Analyst role."
    mailbox_provider(monkeypatch, service, [receipt("names-the-old-job", at + timedelta(seconds=10), named)])
    await check_receipts(service.mail)  # The title rules may attach it to the old job; never to the new run.
    assert await states(service, [unknown]) == ["submission_unknown"]


def test_count_in_window_pairs_each_run_with_a_mail_of_its_own_window():
    # (id, committed, earliest acceptable, unproven); (id, received)
    runs = [("a", 1000, 880, False), ("b", 1161, 1041, True)]
    assert count_in_window(runs, [("m1", 1013), ("m2", 1176)]) == {"a": "m1", "b": "m2"}
    assert count_in_window(runs, [("m2", 1176)]) == {}
    # A surplus mail inside both windows still leaves every run a mail.
    assert set(count_in_window(runs, [("m1", 1013), ("x", 1100), ("m2", 1176)])) == {"a", "b"}
    # Clusters are judged separately: the far run needs a mail of its own, the near pair does not lose theirs.
    far = runs + [("c", 100_000, 99_880, False)]
    assert count_in_window(far, [("m1", 1013), ("m2", 1176)]) == {"a": "m1", "b": "m2"}
    assert count_in_window(far, [("m1", 1013), ("m2", 1176), ("m3", 100_020)]) == {"a": "m1", "b": "m2", "c": "m3"}
    # One mail inside two windows cannot stand for two commits.
    assert count_in_window([("a", 1000, 880, False), ("b", 1100, 980, True)], [("m", 1090)]) == {}


def test_count_in_window_refuses_two_unproven_runs_in_one_cluster():
    runs = [("a", 1000, 880, True), ("b", 1100, 980, True)]
    assert count_in_window(runs, [("m1", 1010), ("m2", 1110)]) == {}
    # Once one is proven the other is credited.
    assert count_in_window([("a", 1000, 880, False), ("b", 1100, 980, True)], [("m1", 1010), ("m2", 1110)]) == {
        "a": "m1",
        "b": "m2",
    }


def test_count_in_window_never_uses_a_mail_before_the_runs_own_start():
    assert count_in_window([("a", 1000, 990, True)], [("early", 985)]) == {}
    assert count_in_window([("a", 1000, 990, True)], [("ok", 995)]) == {"a": "ok"}
