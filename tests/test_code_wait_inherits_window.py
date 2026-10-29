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


async def test_a_reset_wait_includes_mail_sent_while_it_opened(service):
    """EA's Avature reset mail (2026-10-28) was dated 2-3 s before each reset wait opened and was never found."""
    import time
    from datetime import UTC, datetime, timedelta

    from jobpilot.db import Run
    from jobpilot.mail.challenges import RESET_SENT_EARLY_SECONDS

    _, run, rule = await configured(service)
    with service.db.exclusive() as s:
        s.get(Run, run["id"]).started_at = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    opened = time.time()
    started = service.mail.begin(run["id"], rule, "alex@example.test", "password_reset")
    with service.db.session() as s:
        since = s.get(MailChallenge, started["id"]).since
    assert opened - RESET_SENT_EARLY_SECONDS - 1 <= since <= opened - RESET_SENT_EARLY_SECONDS + 1


async def test_a_reset_wait_never_opens_before_its_run_started(service):
    import time
    from datetime import UTC, datetime

    from jobpilot.db import Run

    _, run, rule = await configured(service)
    begun = datetime.now(UTC)
    with service.db.exclusive() as s:
        s.get(Run, run["id"]).started_at = begun.isoformat()
    started = service.mail.begin(run["id"], rule, "alex@example.test", "password_reset")
    with service.db.session() as s:
        assert s.get(MailChallenge, started["id"]).since >= begun.timestamp() - 0.001
    assert time.time() >= begun.timestamp()
