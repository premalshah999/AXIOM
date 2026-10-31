"""The next Telegram question is chosen on the event loop every browser shares, every few seconds. It decoded every
candidate run's packet (190 open questions, nearly all already sent) and read one marker per question, and with
the discovery scans it held the host "overloaded" and new starts to one at a time (2026-10-21). Sent questions are
now skipped from one read of the markers, and a run's packet loads only for a question still to send."""

from jobpilot import telegram
from jobpilot.db import Job, Review, Run


async def test_sent_questions_are_skipped_and_the_next_one_is_delivered(service, monkeypatch):
    service.config.telegram_bot_token, service.config.telegram_user_id = "token", "42"
    with service.db.exclusive() as s:
        s.add(Job(id="job", identity="job", url="https://careers.example.test/jobs/1", title="Engineer", company="Acme",
                  ats="custom", status="ready"))
        s.add(Run(id="run", job_id="job", state="waiting_answer", mode="submit", packet={"worker_slot": None}))
        s.add(Review(id="sent", run_id="run", question="Region?", options=[], key="a", reason="",
                     created_at="2026-10-21T00:00:00"))
        s.add(Review(id="next", run_id="run", question="Start date?", options=[], key="b", reason="",
                     created_at="2026-10-21T00:00:01"))
    service.db.set_setting("telegram_question_sent:sent", {"message_id": 7, "sent_at": "2026-10-21T00:00:02"})
    delivered = []

    async def send_help(_service, run_id, payload, **_kwargs):
        delivered.append((run_id, payload["text"]))
        return {"message_id": 8}

    monkeypatch.setattr(telegram, "send_help", send_help)
    await telegram.ask_next_question(service)
    assert len(delivered) == 1 and "Start date?" in delivered[0][1]
    assert service.db.get_setting("telegram_question") == {"review_id": "next", "message_id": 8}


def test_without_worker_bots_a_questions_packet_is_not_loaded(service):
    """The scan defers packets, and accepts_run undid it to learn a slot nobody has configured (2026-10-30)."""
    from sqlalchemy import event

    service.config.telegram_bot_token, service.config.telegram_user_id = "token", "42"
    with service.db.exclusive() as s:
        s.add(Job(id="job", identity="job", url="https://careers.example.test/jobs/1", title="Engineer", company="Acme",
                  ats="custom", status="ready"))
        s.add(Run(id="run", job_id="job", state="waiting_answer", mode="submit", packet={"worker_slot": None}))
        s.add(Review(id="q", run_id="run", question="Start date?", options=[], key="a", reason="", created_at="2026-10-21T00:00:00"))

    seen = []

    def record(conn, cursor, statement, *rest):
        seen.append(statement)

    event.listen(service.db.engine, "before_cursor_execute", record)
    try:
        chosen = telegram.next_question(service, False)
    finally:
        event.remove(service.db.engine, "before_cursor_execute", record)
    assert chosen and chosen.id == "q"
    assert not [q for q in seen if "runs.packet" in q], seen
