"""Planview (2026-10-27): "The email address and phone number are already in use" sat in the iCIMS frame, outside the
scanned text, and the run reposted the email step until it gave up instead of using the returning-candidate login."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from support_icims import adapter

URL = "https://careers-acme.icims.com/jobs/42/x/login"


class Frame:
    def __init__(self, text):
        self.text = text

    def locator(self, selector):
        return SimpleNamespace(inner_text=AsyncMock(return_value=self.text))


async def test_the_message_in_the_frame_starts_the_returning_login(service):
    obs = {"url": URL, "fields": [], "controls": [], "errors": [], "headings": [], "text": "Enter Your Information"}
    value = adapter(service, obs)
    page = SimpleNamespace(url=URL, goto=AsyncMock(), frames=[
        Frame("Careers"), Frame("The email address and phone number are already in use. Please login.")])
    value.e.form.page = page
    value.e.last_dialog = None
    value.form.settle = AsyncMock()
    value.emit = lambda *a, **k: None
    assert await value.returning_candidate_login(obs) is True
    assert "loginOnly=1" in page.goto.await_args.args[0]
