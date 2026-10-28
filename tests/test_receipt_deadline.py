import asyncio
from types import SimpleNamespace

import pytest

from jobpilot.receipts import receipt_messages, trusted_sender


@pytest.mark.parametrize('stage', ['search', 'read'])
async def test_receipt_deadline_cancels_blocked_operation_and_retains_cursor(service, stage):
    cancelled = asyncio.Event()
    async def blocked():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    async def search(*args, **kwargs):
        if stage == 'search':
            await blocked()
        return {'messages': [{'id': 'first'}, {'id': 'second'}], 'nextPageToken': 'next'}
    async def read(*args, **kwargs):
        await blocked()
    mail = SimpleNamespace(db=service.db, request=search, read_message=read)
    assert await asyncio.wait_for(receipt_messages(mail, 'box', 'alex@example.test', 0, seconds=.02), 1) == []
    assert cancelled.is_set()
    saved = service.db.get_setting('receipt_mail_page:box')
    if stage == 'read':
        assert saved['remaining_ids'] == ['first', 'second']
        assert saved['next_page_token'] == 'next'
        async def successful_read(box, mid, **kwargs):
            return {'id': mid}
        mail.read_message = successful_read
        got = await receipt_messages(mail, 'box', 'alex@example.test', 0, max_messages=1)
        assert got == [{'id': 'first'}]
        assert service.db.get_setting('receipt_mail_page:box')['remaining_ids'] == ['second']
    else:
        assert 'page_token' not in saved and 'remaining_ids' not in saved


async def test_external_cancellation_is_not_a_receipt_timeout(service):
    started = asyncio.Event()
    async def search(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    mail = SimpleNamespace(db=service.db, request=search)
    task = asyncio.create_task(receipt_messages(mail, 'box', 'alex@example.test', 0))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_pinpoint_sender_is_trusted_but_lookalikes_are_not():
    job = {'company': 'Example Robotics', 'url': 'https://careers.example.test/jobs/1'}
    assert trusted_sender('pinpoint.email', job)
    assert not trusted_sender('pinpoint.email.attacker.test', job)
    assert not trusted_sender('fakepinpoint.email', job)
