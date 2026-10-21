from unittest.mock import AsyncMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile


@pytest.mark.parametrize("attribute", ["aria-invalid", "aria-expanded"])
async def test_replaced_packet_control_is_unverified_until_rescanned(page, tmp_path, monkeypatch, attribute):
    """Prime, run f9754403943d439d85501c8065ffae5d, 2026-10-21: replaced menu must not crash verify."""
    await page.set_content('<label>Eligibility<input role="combobox" value="Yes" aria-expanded="false"></label>')
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "resume.pdf", lambda *a: None)
    obs = await form.scan()
    field = obs["fields"][0]
    form.expected[field["id"]] = "Yes"
    form.ledger[field["id"]] = {"verified": False, "field_key": list(form.field_key(field))}
    locator = form.locator(field["id"])
    read = locator.get_attribute

    async def replaced(name, **kwargs):
        if name == attribute:
            assert kwargs.get("timeout") == 2000
            raise PlaywrightTimeoutError("Control replaced")
        return await read(name, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(form, "locator", lambda _: locator)
        patch.setattr(locator, "get_attribute", replaced)
        patch.setattr(form, "_widget_snapshot", AsyncMock())
        report = await form.verify()
    assert not report["ok"]
    assert "Value not accepted: Eligibility" in report["problems"]
    assert not form.ledger[field["id"]]["verified"]
    assert (await form.verify())["ok"]
    assert form.ledger[field["id"]]["verified"]
