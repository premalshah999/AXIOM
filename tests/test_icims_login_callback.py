"""Emory (2026-10-26): after Central accepted the password, iCIMS's callback /jobs/login?universalLogin=1&code=...
rendered only the site navigation and two runs stopped. REA: that page without its job context is empty in any
browser; the signed-in session continues from the job's own Apply."""

from unittest.mock import AsyncMock

from support_icims import adapter

CALLBACK = "https://careers-acme.icims.com/jobs/login?loginOnly=1&redirect=job&universalLogin=1&jobid=42&code=x"


def empty(url):
    return {"url": url, "fields": [], "controls": [{"id": "n", "label": "Careers", "href": "/"}], "errors": [],
            "headings": [], "text": "Careers"}


async def test_an_empty_login_callback_returns_to_the_job_once(service):
    value = adapter(service, empty(CALLBACK))
    value.attempts[("step_render", "/jobs/login")] = 3
    value.e.return_to_job = AsyncMock()
    assert await value.on_unknown(empty(CALLBACK)) is None
    value.e.return_to_job.assert_awaited_once()
