"""A third-party sign-in widget posted into an iframe must not hold the readiness wait (Workday, 2026-11-01).

Workday's "Apply with LinkedIn" widget is a form POSTed into an iframe, so Chrome calls the request a document. The monitor
let every beacon through the telemetry filter only when it was not a document, and counted every document at any age: the
widget's POST was "in flight" for the life of the page, so each readiness wait on a Workday application ran to its deadline
(1,592 unsettled waits in a week, 8,120 s, 12 s per Workday application). The widget is not the application."""

import asyncio
import time

from jobpilot.forms import FormSession
from jobpilot.page_clock import PageClock
from support_budget import scaled

WIDGET = "https://www.linkedin.com/talentwidgets/apply-with-linkedin"
PAGE = f"""<!doctype html><title>Apply</title><label>Name<input id=n></label>
<form id=w method=post action="{WIDGET}" target=f></form><iframe name=f></iframe>
<script>document.getElementById('w').submit()</script>"""


async def hold_widget(page, release):
    async def handler(route):
        url = route.request.url
        if url == "https://ats.example.test/apply":
            return await route.fulfill(status=200, content_type="text/html", body=PAGE)
        if url.startswith(WIDGET):
            await release.wait()  # a request that never answers, as the live widget's
            return await route.abort()
        return await route.fulfill(status=200, content_type="text/html", body="")

    await page.route("**/*", handler)


async def test_a_stalled_sign_in_widget_document_does_not_hold_the_wait(page):
    release = asyncio.Event()
    await hold_widget(page, release)
    form = FormSession.__new__(FormSession)
    form.clock = PageClock()  # __init__ is skipped; settle is timed on it
    from jobpilot.forms import NetworkMonitor

    form.page, form.network = page, NetworkMonitor(page)
    await page.goto("https://ats.example.test/apply", wait_until="domcontentloaded")  # the widget's frame never finishes loading
    await page.wait_for_function("document.querySelector('iframe')")
    await asyncio.sleep(0.6)  # the widget's POST has been sent and is unanswered
    assert not form.network.inflight, "the widget's document counted as the application's own traffic"
    assert form.network.quiet(0.3)
    started = time.monotonic()
    await form.settle(timeout=6)
    assert time.monotonic() - started < scaled(2.5), "the readiness wait ran to its deadline on the widget"
    release.set()


async def test_a_page_document_of_the_application_still_holds_the_wait(page):
    """What the age rule exists for: a document request replaces the controls (iCIMS resume parsing), so it is waited for."""
    from jobpilot.forms import NetworkMonitor

    release = asyncio.Event()

    async def handler(route):
        url = route.request.url
        if url.endswith("/apply"):
            return await route.fulfill(status=200, content_type="text/html", body="<iframe name=f></iframe>")
        await release.wait()
        return await route.abort()

    await page.route("https://ats.example.test/**", handler)
    await page.goto("https://ats.example.test/apply", wait_until="domcontentloaded")
    monitor = NetworkMonitor(page)
    await page.evaluate("() => { const f = document.createElement('form'); f.method = 'post'; f.action = '/candidate'; f.target = 'f'; document.body.append(f); f.submit(); }")
    await asyncio.sleep(0.6)
    assert monitor.inflight and not monitor.quiet(0.3)
    release.set()


def test_only_widget_hosts_are_ignored_as_documents_never_a_postings_own_address():
    """BEACON_REQUEST also matches any address with "snowplow" in its path, for fetches; a document at such an address is an
    employer's own page (a role named Snowplow), so a document is ignored only on the hosts that are never the application."""
    from jobpilot.forms import NetworkMonitor

    class Target:
        def on(self, *_):
            pass

    class Request:
        def __init__(self, url):
            self.url, self.resource_type = url, "document"

    def counted(url):
        monitor = NetworkMonitor(Target())
        monitor._start(Request(url))
        return bool(monitor.inflight)

    assert not counted("https://www.linkedin.com/talentwidgets/apply-with-linkedin")
    assert not counted("https://mon-va.byteoversea.com/monitor_browser/collect/batch/")
    assert counted("https://jobs.example.test/careers/snowplow-data-engineer")
    assert counted("https://ats.example.test/jobs/1/apply")
