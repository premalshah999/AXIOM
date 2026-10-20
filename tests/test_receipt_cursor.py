import pytest

from jobpilot.db import Mailbox
from jobpilot.mail_errors import MailCursorUpgrade, MailOperationError


def make_box(service):
    service.config.gmail_address = "alex@example.test"
    service.config.gmail_app_password = "fixture-app-password"
    with service.db.exclusive() as s:
        box = Mailbox(
            email="alex@example.test",
            credentials=service.mail.vault.seal({"provider": "gmail_imap", "address": "alex@example.test"}),
        )
        s.add(box)
        s.flush()
        return box.id


async def test_an_obsolete_cursor_reaches_the_receipt_reader_instead_of_a_generic_failure(
    service, monkeypatch
):
    # MailCursorUpgrade is a MailOperationError; the generic handler used to swallow it, so the
    # receipt poller retried the same dead cursor for ever and never matched a receipt email.
    box_id = make_box(service)

    def stale(*args, **kwargs):
        raise MailCursorUpgrade("obsolete provider cursor")

    monkeypatch.setattr("jobpilot.gmail_imap.request", stale)
    with pytest.raises(MailCursorUpgrade):
        await service.mail.request(box_id, "/messages/17", {"format": "full"})


async def test_other_mailbox_operation_errors_stay_generic(service, monkeypatch):
    box_id = make_box(service)

    def broken(*args, **kwargs):
        raise MailOperationError("internal detail that must not leak")

    monkeypatch.setattr("jobpilot.gmail_imap.request", broken)
    with pytest.raises(MailOperationError) as caught:
        await service.mail.request(box_id, "/messages", {"q": "x"})
    assert not isinstance(caught.value, MailCursorUpgrade)
    assert "internal detail" not in str(caught.value)
