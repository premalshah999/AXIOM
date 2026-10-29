from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot.adapters.base import Adapter
from jobpilot.db import Run


@pytest.mark.parametrize("ats", ["custom", "avature", "taleo"])
async def test_parked_reset_is_completed_before_navigation(service, ats):
    with service.db.session() as session:
        session.add(Run(id="source", job_id="job", state="waiting_email", mode="submit", packet={}))
        session.commit()
    adapter = Adapter.__new__(Adapter)
    adapter.id, adapter.service = ats, service
    adapter.job = {"id": "job", "url": "https://careers.example.test/jobs/1"}
    adapter.run_record = {"packet": {"email_wait_resume": {
        "kind": "password_reset", "source_run_id": "source", "challenge_id": "challenge"}}}
    adapter.accounts = SimpleNamespace(store=SimpleNamespace(canonical=lambda url: url))
    adapter.challenge = {"id": "challenge"}
    adapter.arm_mail = AsyncMock(return_value=True)
    adapter.complete_password_reset = AsyncMock(return_value={"state": "waiting_browser", "reason": "reset outcome"})
    assert (await adapter._drive())["reason"] == "reset outcome"
    adapter.arm_mail.assert_awaited_once_with("password_reset")
    adapter.complete_password_reset.assert_awaited_once_with(adapter.job["url"])


def test_continuation_requires_a_correlated_source_and_challenge(service):
    with service.db.session() as session:
        session.add(Run(id="other", job_id="other-job", state="waiting_email", mode="submit", packet={}))
        session.commit()
    adapter = Adapter.__new__(Adapter)
    adapter.service, adapter.job = service, {"id": "job"}
    adapter.accounts = SimpleNamespace(store=object())
    handle = {"kind": "password_reset", "source_run_id": "other", "challenge_id": "challenge"}
    adapter.run_record = {"packet": {"email_wait_resume": handle}}
    assert not adapter.reset_mail_continuation()
    handle.pop("challenge_id")
    assert not adapter.reset_mail_continuation()


async def test_continuation_stops_when_mailbox_does_not_recover_challenge(service):
    with service.db.session() as session:
        session.add(Run(id="source", job_id="job", state="waiting_email", mode="submit", packet={}))
        session.commit()
    adapter = Adapter.__new__(Adapter)
    adapter.id, adapter.service = "custom", service
    adapter.job = {"id": "job", "url": "https://careers.example.test/jobs/1"}
    adapter.run_record = {"packet": {"email_wait_resume": {
        "kind": "password_reset", "source_run_id": "source", "challenge_id": "challenge"}}}
    adapter.accounts = SimpleNamespace(store=object())
    adapter.challenge = None
    adapter.arm_mail = AsyncMock(return_value=True)
    adapter.complete_password_reset = AsyncMock()
    adapter.config = SimpleNamespace(browser_cdp_url=None)
    result = await adapter._drive()
    assert result["state"] == "waiting_browser"
    adapter.complete_password_reset.assert_not_awaited()
