from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot.adapters.base import Adapter
from jobpilot.schemas import Answer, Profile
from support_icims import adapter, observation


async def test_schwab_phone_protocol_is_filled_before_email_submit(service, monkeypatch):
    """Schwab, run 5798715c3947414d99adecf5464d4136, 2026-10-21: phone validation precedes CAPTCHA."""
    obs = observation("https://careers-acme.icims.com/jobs/42/engineer/login", password=False)
    obs["fields"][0]["icims_name"] = "css_loginName"
    obs["fields"] += [
        {"id": "phone", "type": "text", "icims_name": "css_phoneNumber", "label": "Number", "required": False},
        {"id": "country", "type": "combobox", "html_id": "dropdown", "label": "Country Code", "required": True},
    ]
    a = adapter(service, obs)
    order = []
    a.form.resolver = SimpleNamespace(resolve=AsyncMock(return_value=[
        Answer(field_id="country", value="United States", disposition="answer"),
        Answer(field_id="phone", value="2025550100", disposition="answer"),
    ]))
    a.form.fill = AsyncMock(side_effect=lambda field, value: order.append(field["id"]))

    async def submit(*args):
        order.append("email-submit")

    monkeypatch.setattr(Adapter, "on_email_entry", submit)
    await a.on_email_entry(obs)
    assert order == ["country", "phone", "email-submit"]
    assert [f["label"] for f in a.form.resolver.resolve.await_args.args[0]] == ["Phone Country Code", "Phone Number"]


@pytest.mark.parametrize("country,state,regions,expected", [
    ("United States", "Virginia", "California or the United Kingdom", "false"),
    ("United States", "CA", "California", "true"),
    ("United Kingdom", "", "California or the United Kingdom", "true"),
    ("United Kingdom", "", "California", "false"),
    ("United States", "", "California", None),
])
async def test_vtg_residence_is_a_fact_not_privacy_consent(service, country, state, regions, expected):
    """VTG, run d499dd84 (handoff prefix), 2026-10-21: eu_resident may truthfully stay unchecked."""
    obs = observation(password=False)
    a = adapter(service, obs)
    a.form.resolver = SimpleNamespace(profile=Profile(address={"country": country, "state": state}), resolve=AsyncMock())
    field = {"id": "resident", "icims_name": "eu_resident", "type": "checkbox", "required": True,
             "label": f"If you are a resident of {regions}, please check this box."}
    answer = await a.email_agreement_answer(field)
    assert answer.disposition == ("review" if expected is None else "answer")
    if expected is not None:
        assert answer.value == expected
    a.form.resolver.resolve.assert_not_awaited()


async def test_other_checkboxes_still_use_the_resolver(service):
    """VTG, run d499dd84 (handoff prefix), 2026-10-21: accept_gdpr remains a consent-policy decision."""
    a = adapter(service, observation(password=False))
    answer = Answer(field_id="accept", value="true", disposition="answer")
    a.form.resolver = SimpleNamespace(resolve=AsyncMock(return_value=[answer]))
    field = {"id": "accept", "icims_name": "accept_gdpr", "type": "checkbox", "label": "I accept"}
    assert await a.email_agreement_answer(field) is answer
    a.form.resolver.resolve.assert_awaited_once_with([field])
