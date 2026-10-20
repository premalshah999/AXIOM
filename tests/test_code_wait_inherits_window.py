"""Starbucks (Eightfold) 2026-10-14: the code mail landed between a cancelled wait and its replacement."""

from jobpilot.db import MailChallenge
from support_mail import configured


async def test_replacing_code_wait_inherits_the_cancelled_window(service):
    _, run, rule = await configured(service)
    first = service.mail.begin(run["id"], rule, "alex@example.test", "auto")
    with service.db.exclusive() as s:
        c = s.get(MailChallenge, first["id"])
        c.since -= 9  # the general wait opened 9 s before the code wait
        c.state, c.reason = "cancelled", "No email was needed"
        opened = c.since
    second = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    with service.db.session() as s:
        assert s.get(MailChallenge, second["id"]).since == opened


async def test_a_fresh_code_wait_without_a_cancelled_one_starts_now(service):
    _, run, rule = await configured(service)
    started = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    with service.db.session() as s:
        c = s.get(MailChallenge, started["id"])
        assert c.since >= c.created_at.timestamp() - 1 if hasattr(c.created_at, "timestamp") else True
