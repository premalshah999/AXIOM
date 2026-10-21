import asyncio
import os
import re
from io import BytesIO
from pathlib import Path

from playwright.async_api import async_playwright

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from jobpilot.api import create_app
from jobpilot.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch):
    # browser-use can load the checkout's dotenv into os.environ on import.
    # Fixture configurations must never inherit production identities or keys.
    names = set()
    for name, field in Settings.model_fields.items():
        names.add(name.casefold())
        alias = field.validation_alias
        for candidate in getattr(alias, "choices", [alias]):
            if isinstance(candidate, str):
                names.add(candidate.casefold())
    names.discard("chromium_path")
    for name in list(os.environ):
        if name.casefold() in names:
            monkeypatch.delenv(name)


@pytest.fixture
def config(tmp_path):
    return Settings(
        _env_file=None,
        database_url="",
        dbos_database_url="",
        gmail_address="",
        gmail_app_password="",
        telegram_bot_token="",
        telegram_user_id="",
        telegram_enabled=True,
        serper_api_key="",
        mimo_api_key="",
        llm_provider="mimo",
        deepseek_api_key="",
        google_client_id="",
        google_client_secret="",
        browser_cdp_url="",
        twocaptcha_api_key="",
        application_password="",
        icims_login="",
        account_email="",
        haiku_api_key="",
        data_dir=tmp_path,
        app_token="test-access-token",
        enable_workers=False,
        answer_quality_enabled=False,
        browser_vision_recovery=False,
        failure_reports_enabled=False,
        allow_private_urls=True,
        chromium_path=os.getenv("CHROMIUM_PATH", ""),
        snapshot_cache_seconds=0,
    ).prepare()


@pytest.fixture
def app(config):
    return create_app(config)


@pytest.fixture
def service(app):
    return app.state.service


@pytest.fixture
def client(app, config):
    # These API fixtures have workers disabled. The async ATS harness owns its mailbox
    # poller; starting lifespan here would share an asyncio lock with another thread.
    c = TestClient(app, headers={"Authorization": "Bearer " + config.app_token})
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def resume_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 40 700 Td (Alex Example - Python engineer with API and database experience) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def prepared(client, service, resume_pdf):
    r = client.post(
        "/api/resumes",
        data={"name": "Backend", "roles": "Python, Backend"},
        files={"file": ("resume.pdf", resume_pdf, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    resume = r.json()
    client.put(
        "/api/profile",
        json={
            "name": "Alex Example",
            "email": "alex@example.test",
            "facts": {"first_name": "Alex", "last_name": "Example"},
        },
    )
    service.config.mimo_api_key = "unit-test-only"
    return resume


class SharedChromium:
    """One headless Chromium for the whole run; every test gets its own context and page from it.

    Launching a browser for each test was 761 of the suite's 1,351 seconds (56%, baseline of 2026-10-20). A browser
    that died in one test is launched again for the next, so one crash cannot fail the rest of the run."""

    def __init__(self):
        self.playwright = None
        self.browser = None

    async def launch(self):
        if self.browser is not None and self.browser.is_connected():
            return self.browser
        if self.playwright is None:
            self.playwright = await async_playwright().start()
        path = os.getenv("CHROMIUM_PATH", "") or self.playwright.chromium.executable_path
        if not Path(path).exists():
            pytest.skip("Install Chromium: playwright install chromium")
        self.browser = await self.playwright.chromium.launch(
            executable_path=path, headless=True, args=["--no-sandbox"]
        )
        return self.browser

    async def close(self):
        for closer in (self.browser and self.browser.close, self.playwright and self.playwright.stop):
            if closer:
                try:
                    await asyncio.wait_for(closer(), 15)
                except Exception:
                    pass


# Browser tests share one event loop (and with it the one browser connection). Everything else keeps a loop per test.
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def shared_chromium():
    holder = SharedChromium()
    yield holder
    await holder.close()


@pytest_asyncio.fixture(loop_scope="session")
async def page(shared_chromium):
    browser = await shared_chromium.launch()
    context = await browser.new_context()
    page = await context.new_page()
    started = asyncio.all_tasks()
    try:
        yield page
    finally:
        try:
            await asyncio.wait_for(context.close(), 15)
        except Exception:
            pass  # The browser went away during the test; the next test launches it again.
        # A per-test loop used to cancel whatever a test left running when it closed; keep that isolation.
        current = asyncio.current_task()
        leftovers = [task for task in asyncio.all_tasks() if task not in started and task is not current]
        for task in leftovers:
            task.cancel()
        await asyncio.gather(*leftovers, return_exceptions=True)


# ----- markers --------------------------------------------------------------------------------------------------
# unit         the default: no browser, database or service wiring
# integration  needs the database, the Service or the API (the service, client, app and prepared fixtures)
# browser      drives a real Chromium; the fast CI gate is `-m "not browser"`
# regression   pins a failure that happened: its module docstring cites the incident and its date
# They are applied here from what a test uses, so a new test is classified without anyone remembering a decorator.
BROWSER_FIXTURES = {"page", "twin_chrome"}
INTEGRATION_FIXTURES = {"service", "client", "app", "prepared"}
# "Qualcomm (Eightfold) 2026-10-14: ..." or "(Dayforce 10-16)": every regression test here names its incident and date.
INCIDENT = re.compile(r"\b20\d\d-\d\d-\d\d\b|\b1[0-2]-[0-3]\d\b")


def uses_session_loop(item):
    """A test whose fixtures live in the session event loop must run in it too."""
    info = getattr(item, "_fixtureinfo", None)
    definitions = [d for found in getattr(info, "name2fixturedefs", {}).values() for d in found]
    return any(getattr(d.func, "_loop_scope", None) == "session" for d in definitions)


def pytest_collection_modifyitems(items):
    for item in items:
        names = set(item.fixturenames)
        browser = bool(names & BROWSER_FIXTURES) or item.get_closest_marker("browser") is not None
        integration = bool(names & INTEGRATION_FIXTURES) or item.get_closest_marker("integration") is not None
        docs = " ".join(filter(None, [getattr(item.module, "__doc__", ""), getattr(item.function, "__doc__", "")]))
        if browser:
            item.add_marker(pytest.mark.browser)
        if integration:
            item.add_marker(pytest.mark.integration)
        if not (browser or integration):
            item.add_marker(pytest.mark.unit)
        if INCIDENT.search(docs) and not item.get_closest_marker("regression"):
            item.add_marker(pytest.mark.regression)
        if asyncio.iscoroutinefunction(item.function) and uses_session_loop(item):
            item.add_marker(pytest.mark.asyncio(loop_scope="session"), append=False)
