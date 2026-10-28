"""Setup › Connections (owner 2026-10-28): every key and setting a new owner needs is entered in the web interface,
typed and checked against the setting's own limits, and used without editing .env."""

import pytest

from jobpilot.connections import FIELDS, save, status


def test_numbers_and_switches_are_typed_and_applied(service):
    config = save(service.config, {"daily_application_limit": "40", "telegram_enabled": "false", "max_model_calls": "70"})
    assert config.daily_application_limit == 40 and config.telegram_enabled is False and config.max_model_calls == 70
    fields = {f["name"]: f for f in status(config)["fields"]}
    assert fields["daily_application_limit"]["kind"] == "number" and fields["daily_application_limit"]["value"] == "40"
    assert fields["telegram_enabled"]["kind"] == "bool" and fields["telegram_enabled"]["value"] == "false"
    assert fields["normal_workers"]["restart"] is True


def test_a_value_outside_the_settings_limits_is_refused(service):
    with pytest.raises(ValueError, match="Applications per day|AI calls per application"):
        save(service.config, {"max_model_calls": "not a number"})


def test_the_shared_employer_password_must_meet_common_rules(service):
    with pytest.raises(ValueError, match="Shared employer password"):
        save(service.config, {"application_password": "short"})
    config = save(service.config, {"application_password": "Strong-Passw0rd!x"})
    assert config.application_password == "Strong-Passw0rd!x"
    shown = next(f for f in status(config)["fields"] if f["name"] == "application_password")
    assert shown["secret"] and "Strong" not in shown["value"]


def test_clearing_a_value_falls_back_to_the_environment(service):
    before = service.config.daily_budget_usd
    save(service.config, {"daily_budget_usd": "3.5"})
    assert service.config.daily_budget_usd == 3.5
    assert save(service.config, {"daily_budget_usd": ""}).daily_budget_usd == before


def test_every_field_is_a_real_setting():
    from jobpilot.config import Settings

    assert set(FIELDS) <= set(Settings.model_fields)
