"""Synthetic receipts; no real mailbox, browser or applicant values."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from jobpilot.db import Event, Job, Mailbox, MailChallenge, Run
from jobpilot.receipts import acknowledgement, check_receipts
from support_mail import message
from support_mail import EMAIL, receipt_seed as base_seed


def test_production_target_counts_only_distinct_new_proven_applications(service):
    from jobpilot.autopilot import PRODUCTION_TARGET, production_progress

    service.db.set_setting(
        PRODUCTION_TARGET,
        {
            "started_at": "2026-10-12T00:00:00+00:00",
            "target": 100,
            "excluded_job_ids": ["old"],
            "baseline_run_ids": ["before"],
        },
    )
    with service.db.session() as s:
        for jid in ["new", "old", "reconciled", "unknown", "unproved", "baseline"]:
            s.add(Job(id=jid, identity=jid, url=f"https://fixture.test/job/{jid}"))
        for rid, jid in [
            ("first", "new"),
            ("retry", "new"),
            ("old-run", "old"),
            ("existing", "reconciled"),
            ("unknown-run", "unknown"),
            ("no-proof", "unproved"),
            ("before", "baseline"),
        ]:
            receipt = {
                "type": "explicit_confirmation_page",
                "evidence_version": 2,
                "website_confirmed": True,
                "job_id": jid,
                "run_id": rid,
                "evidence_selector": "h1",
                "status_region": "main",
                "confirmation": "Received",
                "resume_sha256": "pinned",
            }
            if jid == "reconciled":
                receipt["reconciled_existing_submission"] = True
            if jid == "unproved":
                receipt = {}
            s.add(
                Run(
                    id=rid,
                    job_id=jid,
                    state="submission_unknown" if jid == "unknown" else "confirmed",
                    created_at="2026-10-12T01:00:00+00:00",
                    receipt=receipt,
                    packet={"resume_sha": "pinned"},
                )
            )
    result = production_progress(service.db)
    assert result["confirmed"] == 1
    assert result["remaining"] == 99 and result["state"] == "active"


@pytest.mark.parametrize(
    "legacy_state",
    [
        {"remaining_ids": ["17"]},
        {"page_token": "imap-before:17"},
    ],
)
async def test_receipt_cursor_upgrade_restarts_search_without_reusing_uids(
    service, monkeypatch, legacy_state
):
    from jobpilot.gmail_imap import _request
    from jobpilot.mail import digest
    from jobpilot.receipts import RECEIPT_SEARCH, receipt_messages
    from support_mail import FolderClient

    seed(service, 1)
    with service.db.session() as s:
        box = s.scalar(select(Mailbox))
        box_id = box.id
    since = 1_791_201_600
    query = f"after:{since // 3600 * 3600} to:{EMAIL} {RECEIPT_SEARCH}"
    service.db.set_setting("receipt_mail_page:" + box_id, {"query_id": digest(query), **legacy_state})
    client = FolderClient()

    async def request(mailbox_id, path, params, background=False):
        return _request(client, EMAIL, path, params)

    monkeypatch.setattr(service.mail, "request", request)
    messages = await receipt_messages(service.mail, box_id, EMAIL, since)
    assert {m["id"] for m in messages} == {"gm:900", "gm:901", "gm:902"}


def seed(service, count, **kwargs):
    ids = base_seed(service, count, **kwargs)
    if kwargs.get("state") == "submission_unknown":
        with service.db.session() as s:
            for rid in ids:
                s.add(
                    Event(
                        run_id=rid,
                        kind="submitting",
                        message="Synthetic commit",
                        created_at=s.get(Run, rid).created_at,
                    )
                )
    return ids


def submitted(service, ids, times):
    with service.db.session() as s:
        s.execute(delete(Event).where(Event.run_id.in_(ids), Event.kind == "submitting"))
        for rid, at in zip(ids, times, strict=True):
            s.add(Event(run_id=rid, kind="submitting", message="Synthetic submit", created_at=at.isoformat()))


def receipt(mid, at, body="Thank you for applying to Example. We received your application."):
    msg = message(body, message_id=mid)
    msg["internalDate"] = str(int(at.timestamp() * 1000))
    msg["payload"]["headers"][1]["value"] = EMAIL
    return msg


def mailbox_provider(monkeypatch, service, messages):
    async def request(box, path, params, background=False):
        if path == "/messages":
            return {"messages": [{"id": m["id"]} for m in messages]}
        return next(m for m in messages if path == "/messages/" + m["id"])

    monkeypatch.setattr(service.mail, "request", request)


async def test_pending_challenge_never_starves_receipt_search_or_matching(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(minutes=5)
    rid = seed(service, 1, state="submission_unknown", start=at)[0]
    with service.db.session() as s:
        box = s.scalar(select(Mailbox))
        s.add(
            MailChallenge(
                run_id=rid,
                mailbox_id=box.id,
                rule_id="synthetic",
                recipient=EMAIL,
                kind="code",
                since=at.timestamp(),
                expires=datetime.now(UTC).timestamp() + 600,
            )
        )
    msg = receipt(
        "ack", at + timedelta(minutes=1), "We received your application to Example for the Engineer position."
    )
    mailbox_provider(monkeypatch, service, [msg])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 1
    with service.db.session() as s:
        assert s.get(Run, rid).state == "confirmed"
        assert s.scalar(select(MailChallenge)).state == "pending"


async def test_unsafe_equal_counts_in_overlapping_windows_confirm_nothing(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 2, state="submission_unknown", start=at)
    submitted(service, ids, [at, at + timedelta(minutes=5)])
    msgs = [receipt("one", at + timedelta(minutes=10)), receipt("two", at + timedelta(minutes=11))]
    mailbox_provider(monkeypatch, service, msgs)
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0


@pytest.mark.parametrize(
    "body",
    [
        "Thank you for applying to Example. We received your application.",
        "We received your application to Example for the Engineer position.",
    ],
)
async def test_employer_display_aliases_do_not_hide_competing_jobs(service, monkeypatch, body):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 2, state="submission_unknown", start=at)
    with service.db.session() as s:
        s.get(Job, s.get(Run, ids[0]).job_id).company = "Example Inc."
    mailbox_provider(monkeypatch, service, [receipt("aliased", at + timedelta(minutes=10), body)])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    with service.db.session() as s:
        assert all(s.get(Run, rid).state == "submission_unknown" for rid in ids)


async def test_late_generic_mail_is_ambiguous_despite_nonoverlapping_assumed_windows(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(days=4)
    ids = seed(service, 2, state="submission_unknown", start=at)
    submitted(service, ids, [at, at + timedelta(days=3)])
    msgs = [
        receipt("late-one", at + timedelta(days=3, minutes=1)),
        receipt("late-two", at + timedelta(days=3, minutes=2)),
    ]
    mailbox_provider(monkeypatch, service, msgs)
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0


async def test_independently_identified_cohort_records_count_matched(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 2, state="submission_unknown", start=at)
    submitted(service, ids, [at, at + timedelta(minutes=5)])
    with service.db.session() as s:
        for rid, level in zip(ids, ["I", "II"], strict=True):
            s.get(Job, s.get(Run, rid).job_id).title = "Engineer " + level
    msgs = [
        receipt("one", at + timedelta(minutes=10), "We received your application to Example for Engineer I."),
        receipt(
            "two", at + timedelta(minutes=11), "We received your application to Example for Engineer II."
        ),
    ]
    mailbox_provider(monkeypatch, service, msgs)
    assert (await check_receipts(service.mail))["emails_confirmed"] == 2
    with service.db.session() as s:
        matched = [s.get(Run, rid) for rid in ids]
        assert all(r.state == "confirmed" and r.receipt["count_matched"] for r in matched)
        assert {r.receipt["email"]["message_id"] for r in matched} == {"one", "two"}
        assert all(not r.receipt["website_confirmed"] for r in matched)


async def test_identical_titles_are_not_greedily_assigned_to_first_job(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    seed(service, 2, state="submission_unknown", start=at)
    msg = receipt(
        "one",
        at + timedelta(minutes=10),
        "We received your application to Example for the Engineer position.",
    )
    mailbox_provider(monkeypatch, service, [msg])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0


async def test_competitor_outside_batch_still_prevents_generic_confirmation(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 21, state="submission_unknown", start=at)
    mailbox_provider(monkeypatch, service, [receipt("generic", at + timedelta(minutes=10))])
    assert (await check_receipts(service.mail, ids[0]))["emails_confirmed"] == 0


async def test_canonical_message_cannot_reuse_a_legacy_inbox_receipt(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 2, state="submission_unknown", start=at)
    with service.db.session() as s:
        old = s.get(Run, ids[0])
        old.receipt = {"email": {"message_id": "123", "sender_trusted": True}}
        old.state = "confirmed"
        s.get(Job, old.job_id).title = "Other Engineer"
    msg = receipt(
        "gm:999",
        at + timedelta(minutes=10),
        "We received your application to Example for the Engineer position.",
    )
    msg["legacyInboxIds"] = ["123"]
    mailbox_provider(monkeypatch, service, [msg])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0


@pytest.mark.parametrize("company,expected", [("Unrelated Employer", 1), ("Example Inc.", 0)])
async def test_unidentifiable_legacy_receipt_respects_employer_aliases(
    service, monkeypatch, company, expected
):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 2, state="submission_unknown", start=at)
    with service.db.session() as s:
        old = s.get(Run, ids[0])
        old.state = "confirmed"
        old.receipt = {"email": {"message_id": "123", "sender_trusted": True}}
        job = s.get(Job, old.job_id)
        job.company = company
        job.url = "https://unrelated.test/jobs/10"
    mailbox_provider(
        monkeypatch,
        service,
        [
            receipt(
                "gm:999",
                at + timedelta(minutes=10),
                "We received your application to Example for the Engineer position.",
            )
        ],
    )
    assert (await check_receipts(service.mail))["emails_confirmed"] == expected


@pytest.mark.parametrize(
    "body",
    [
        "Thank you for applying to Example as Engineer. Activate your account with this link.",
        "Thank you for applying to Example as Engineer. Reset your password.",
        "Thank you for applying to Example as Engineer. Your one-time passcode is 123456.",
        '<p>Thank you for applying to Example as Engineer.</p><a href="https://owned.test/verify?token=fixture">Continue</a>',
    ],
)
def test_secret_challenge_content_is_not_receipt_context(body):
    at = datetime.now(UTC) - timedelta(hours=1)
    run = {"created_at": at.isoformat(), "packet": {"profile": {"email": EMAIL}}}
    msg = receipt("secret", at + timedelta(minutes=10), body)
    assert acknowledgement(msg, {"company": "Example", "title": "Engineer"}, run) is None


async def test_pre_submit_receipt_does_not_confirm_unknown_run(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 1, state="submission_unknown", start=at)
    submitted(service, ids, [at + timedelta(minutes=5)])
    mailbox_provider(
        monkeypatch,
        service,
        [
            receipt(
                "early",
                at + timedelta(minutes=1),
                "We received your application to Example for the Engineer position.",
            )
        ],
    )
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0


async def test_no_receipt_does_not_change_page_confirmation_or_unknown_protection(service, monkeypatch):
    ids = seed(service, 1, state="submission_unknown")
    page = seed(service, 1, state="confirmed")[0]
    with service.db.session() as s:
        s.get(Run, page).receipt = {"website_confirmed": True}
    monkeypatch.setattr(service.mail, "request", AsyncMock(return_value={"messages": []}))
    await check_receipts(service.mail)
    with service.db.session() as s:
        assert s.get(Run, ids[0]).state == "submission_unknown"
        assert s.get(Run, page).state == "confirmed" and s.get(Run, page).receipt["website_confirmed"]


async def test_confirmed_job_followup_cannot_confirm_an_overlapping_unknown(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 2, state="submission_unknown", start=at)
    submitted(service, ids, [at, at + timedelta(minutes=5)])
    with service.db.session() as s:
        first = s.get(Run, ids[0])
        first.state = "confirmed"
        first.receipt = {"email_confirmed": True, "email": {"message_id": "original", "sender_trusted": True}}
    mailbox_provider(monkeypatch, service, [receipt("followup", at + timedelta(minutes=10))])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    with service.db.session() as s:
        assert s.get(Run, ids[1]).state == "submission_unknown"


async def test_triangular_perfect_matching_does_not_manufacture_second_job_evidence(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 2, state="submission_unknown", start=at)
    submitted(service, ids, [at, at + timedelta(minutes=5)])
    mailbox_provider(
        monkeypatch,
        service,
        [receipt("early", at + timedelta(minutes=1)), receipt("late", at + timedelta(minutes=6))],
    )
    result = await check_receipts(service.mail)
    assert result["emails_confirmed"] == 0
    with service.db.session() as s:
        assert all(s.get(Run, rid).state == "submission_unknown" for rid in ids)


async def test_archived_canonical_receipt_keeps_legacy_delivery_claim(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 2, state="submission_unknown", start=at)
    msg = receipt(
        "gm:999",
        at + timedelta(minutes=10),
        "We received your application to Example for the Engineer position.",
    )
    with service.db.session() as s:
        old = s.get(Run, ids[0])
        old.state = "confirmed"
        old.receipt = {
            "email": {
                "message_id": "123",
                "sender_trusted": True,
                "received_at": (at + timedelta(minutes=10)).isoformat(timespec="milliseconds"),
            }
        }
        s.get(Job, old.job_id).title = "Other Engineer"
    # Exact saved receipt timestamps use datetime.isoformat's default precision.
    msg_time = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, UTC).isoformat()
    with service.db.session() as s:
        old = s.get(Run, ids[0])
        old.receipt = {"email": {**old.receipt["email"], "received_at": msg_time}}
    mailbox_provider(monkeypatch, service, [msg])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0


async def test_job_url_disambiguates_identical_titles(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 2, state="submission_unknown", start=at)
    with service.db.session() as s:
        url = s.get(Job, s.get(Run, ids[1]).job_id).url
    msg = receipt(
        "identified",
        at + timedelta(minutes=10),
        f"We received your application to Example for the Engineer position. {url}",
    )
    mailbox_provider(monkeypatch, service, [msg])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 1
    with service.db.session() as s:
        assert s.get(Run, ids[0]).state == "submission_unknown"
        assert s.get(Run, ids[1]).receipt["email"]["attribution_basis"] == "job_url"


async def test_pending_challenge_bounded_pages_resume_on_next_poll(service, monkeypatch):
    from jobpilot.receipts import receipt_messages

    at = datetime.now(UTC) - timedelta(hours=1)
    rid = seed(service, 1, state="submission_unknown", start=at)[0]
    with service.db.session() as s:
        box = s.scalar(select(Mailbox))
        bid = box.id
        s.add(
            MailChallenge(
                run_id=rid,
                mailbox_id=bid,
                rule_id="synthetic",
                recipient=EMAIL,
                kind="code",
                since=at.timestamp(),
                expires=datetime.now(UTC).timestamp() + 600,
            )
        )
    calls = []

    async def request(box, path, params, background=False):
        calls.append((path, params))
        if path == "/messages":
            offset = int(params.get("pageToken", "0"))
            return {
                "messages": [{"id": str(i)} for i in range(offset, offset + 2)],
                "nextPageToken": str(offset + 2),
            }
        return receipt(path.removeprefix("/messages/"), at + timedelta(minutes=5))

    monkeypatch.setattr(service.mail, "request", request)
    first = await receipt_messages(service.mail, bid, EMAIL, at.timestamp(), max_pages=1, max_messages=1)
    second = await receipt_messages(service.mail, bid, EMAIL, at.timestamp(), max_pages=1, max_messages=1)
    assert [m["id"] for m in first + second] == ["0", "1"]
    assert sum(path == "/messages" for path, _ in calls) == 1
    third = await receipt_messages(service.mail, bid, EMAIL, at.timestamp(), max_pages=1, max_messages=1)
    assert third[0]["id"] == "2"
    assert calls[-2][1]["pageToken"] == "2"


async def test_unknown_without_recorded_commit_is_never_confirmed_by_draft_time(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    rid = base_seed(service, 1, state="submission_unknown", start=at)[0]
    msg = receipt(
        "not-current-submit",
        at + timedelta(minutes=5),
        "We received your application to Example for the Engineer position.",
    )
    mailbox_provider(monkeypatch, service, [msg])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    with service.db.session() as s:
        assert s.get(Run, rid).state == "submission_unknown"


async def test_incomplete_archived_legacy_claim_blocks_unprovable_canonical_reuse(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 2, state="submission_unknown", start=at)
    with service.db.session() as s:
        old = s.get(Run, ids[0])
        old.state = "confirmed"
        old.receipt = {"email": {"message_id": "123", "sender_trusted": True}}
        s.get(Job, old.job_id).title = "Other Engineer"
    msg = receipt(
        "gm:999",
        at + timedelta(minutes=10),
        "We received your application to Example for the Engineer position.",
    )
    mailbox_provider(monkeypatch, service, [msg])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0


async def test_approved_reconciliation_also_requires_uncertain_submit_event(service, monkeypatch):
    from jobpilot.receipts import reconcile_approved_drafts

    at = datetime.now(UTC) - timedelta(hours=1)
    rid = base_seed(service, 1, state="submission_unknown", start=at)[0]
    with service.db.session() as s:
        run = s.get(Run, rid)
        run.mode = "submit"
        run.packet = {**run.packet, "match_approved": True}
        jid = run.job_id
    msg = receipt(
        "not-current-submit",
        at + timedelta(minutes=5),
        "We received your application to Example for the Engineer position.",
    )
    mailbox_provider(monkeypatch, service, [msg])
    assert (await reconcile_approved_drafts(service.mail, [jid]))["emails_confirmed"] == 0
    with service.db.session() as s:
        assert s.get(Run, rid).state == "submission_unknown"


@pytest.mark.parametrize(
    "body",
    [
        "Thank you for your interest in the Engineer position at Example. This email confirms our receipt of your application.",
        "Hello. We have received your resume for the Engineer role at Example.",
    ],
)
def test_employer_receipt_wording_is_recognized(body):
    at = datetime.now(UTC) - timedelta(hours=1)
    run = {"created_at": at.isoformat(), "packet": {"profile": {"email": EMAIL}}}
    msg = receipt("custom-wording", at + timedelta(minutes=10), body)
    assert acknowledgement(msg, {"company": "Example", "title": "Engineer"}, run) is not None


def test_one_message_is_parsed_once_across_candidate_runs(monkeypatch):
    """The poller compares each message with every run at that employer; parsing it once per pair on the
    shared event loop starved the application workers (2026-10-15)."""
    import jobpilot.receipts as receipts

    calls = []
    real = receipts.BeautifulSoup

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(receipts, "BeautifulSoup", counting)
    at = datetime.now(UTC) - timedelta(hours=1)
    msg = receipt(
        "parse-once",
        at + timedelta(minutes=10),
        "We have received your resume for the Engineer role at Example.",
    )
    runs = [{"created_at": at.isoformat(), "packet": {"profile": {"email": EMAIL}}} for _ in range(3)]
    results = [acknowledgement(msg, {"company": "Example", "title": "Engineer"}, run) for run in runs]
    assert all(r is not None for r in results) and results[0] == results[1] == results[2]
    assert len(calls) == 1


async def test_a_same_title_posting_that_never_committed_does_not_make_the_receipt_ambiguous(service, monkeypatch):
    """Lyft had two postings titled "Data Scientist"; only one was ever submitted, and its acknowledgement waited as
    ambiguous with the never-submitted draft (2026-10-25)."""
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 2, state="submission_unknown", start=at)
    with service.db.session() as s:
        draft = s.get(Run, ids[1])
        draft.state, draft.packet = "waiting_browser", {**draft.packet, "match_approved": True}
        s.execute(delete(Event).where(Event.run_id == ids[1], Event.kind == "submitting"))
    msg = receipt("only-one", at + timedelta(minutes=2), "We received your application to Example for the Engineer position.")
    mailbox_provider(monkeypatch, service, [msg])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 1
    with service.db.session() as s:
        assert s.get(Run, ids[0]).state == "confirmed" and s.get(Run, ids[1]).state == "waiting_browser"


async def test_the_acknowledgement_goes_to_the_run_that_submitted_not_a_later_draft(service, monkeypatch):
    """Lyft's acknowledgement was credited to the job's newest run, a later retry draft that could not take it, so the
    run that submitted stayed unconfirmed (2026-10-25)."""
    at = datetime.now(UTC) - timedelta(hours=1)
    (rid,) = seed(service, 1, state="submission_unknown", start=at)
    with service.db.session() as s:
        submitted_run = s.get(Run, rid)
        s.add(Run(id="later-draft", job_id=submitted_run.job_id, state="waiting_browser",
                  packet={"profile": {"email": EMAIL}, "match_approved": True},
                  created_at=(at + timedelta(minutes=1)).isoformat()))
    msg = receipt("ack", at + timedelta(minutes=5), "We received your application to Example for the Engineer position.")
    mailbox_provider(monkeypatch, service, [msg])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 1
    with service.db.session() as s:
        assert s.get(Run, rid).state == "confirmed" and s.get(Run, "later-draft").state == "waiting_browser"
