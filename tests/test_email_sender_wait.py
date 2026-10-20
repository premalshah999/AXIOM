"""Generic verification obeys the adapter lease cap and retains commit uncertainty."""
from types import SimpleNamespace
import pytest
from jobpilot import email_browser
from jobpilot.email_browser import EmailBrowser
from jobpilot.inbox import LEASE_WAIT_CAP_SECONDS
from jobpilot.mail_errors import MailSenderBusy


@pytest.mark.parametrize("release", [None, 2])
async def test_generic_sender_wait_is_bounded_and_typed(monkeypatch, release):
    clock = [0.0]
    async def sleep(seconds):
        clock[0] += seconds
    monkeypatch.setattr(email_browser, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(email_browser, "asyncio", SimpleNamespace(sleep=sleep))
    browser = object.__new__(EmailBrowser)
    browser.rule, browser.challenge_id = {"id": "rule"}, None
    def begin(kind):
        if release is None or clock[0] < release:
            raise ValueError("Another application is verifying with this sender")
        browser.challenge_id = "owned-window"
    browser.begin = begin
    if release is None:
        with pytest.raises(MailSenderBusy) as caught:
            await browser.prepare_submission("code")
        assert caught.value.result()["recovery_cause"] == "sender_busy"
        assert caught.value.result()["mail_request_kind"] == "code"
        assert caught.value.result(armed=True)["state"] == "submission_unknown"
        assert caught.value.result(armed=True)["recovery_cause"] == ""
        assert clock[0] == LEASE_WAIT_CAP_SECONDS
    else:
        await browser.prepare_submission("code")
        assert browser.challenge_id == "owned-window"
        assert clock[0] == release


async def test_other_mail_error_is_not_relabelled_busy():
    browser = object.__new__(EmailBrowser)
    browser.rule, browser.challenge_id = {"id": "rule"}, None
    def begin(kind):
        raise ValueError("No usable sender rule")
    browser.begin = begin
    with pytest.raises(ValueError, match="No usable sender rule"):
        await browser.prepare_submission()


async def test_the_navigators_send_click_waits_for_a_shared_sender(monkeypatch):
    # Fortinet and GM Financial (Oracle, 2026-10-18): Next on the email step was refused until the navigator stalled.
    clock = [0.0]
    async def sleep(seconds):
        clock[0] += seconds
    monkeypatch.setattr(email_browser, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(email_browser, "asyncio", SimpleNamespace(sleep=sleep))
    browser = object.__new__(EmailBrowser)
    browser.engine = SimpleNamespace(job={"ats": "oracle"})
    def begin(kind):
        if clock[0] < 90:
            raise ValueError("Another application is verifying with this sender")
        return {"id": "owned-window"}
    browser.begin = begin
    assert (await browser.acquire())["id"] == "owned-window"
    assert 90 <= clock[0] < 92
