import os
from io import BytesIO
from pathlib import Path

from playwright.async_api import async_playwright

import pytest
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


@pytest.fixture
async def page(config):
    async with async_playwright() as pw:
        path = config.chromium_path or pw.chromium.executable_path
        if not Path(path).exists():
            pytest.skip("Install Chromium: playwright install chromium")
        browser = await pw.chromium.launch(executable_path=path, headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page = await context.new_page()
        yield page
        await browser.close()
