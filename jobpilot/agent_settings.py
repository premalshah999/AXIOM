"""Persisted helper controls. Secrets and provider endpoints remain server configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Provider = Literal["mimo", "deepseek", "haiku", "openai", "openrouter"]


class Helpers(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answers: bool = True
    accounts: bool = True
    email: bool = True
    captcha: bool = True
    navigator: bool = True
    discovery: bool = True


class Models(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: Provider
    navigator: Provider


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Helpers
    models: Models


class AgentDisabled(RuntimeError):
    pass


KEY_NAMES = {
    "deepseek": "DEEPSEEK_API_KEY",
    "haiku": "HAIKU_API_KEY",
    "mimo": "MIMO_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
PROVIDER_NAMES = tuple(KEY_NAMES)


def missing_key_message(provider):
    """What a newcomer does about a model provider without a key: name the setting and where it goes."""
    name = KEY_NAMES.get(provider, f"{provider}_API_KEY".upper())
    return (
        f"No API key for the {provider} model provider. Paste it in Settings > Connections (no restart needed), "
        f"or choose a provider you have a key for there. ({name} in .env also works.)"
    )


def current(db, config):
    default = config.llm_provider if config.llm_provider in PROVIDER_NAMES else "mimo"
    value = db.get_setting("agent_settings", {}) or {}
    return AgentSettings.model_validate(
        {
            "enabled": value.get("enabled", {}),
            "models": value.get("models", {"answers": default, "navigator": default}),
        }
    )


def enabled(db, helper):
    value = db.get_setting("agent_settings", {}) or {}
    return value.get("enabled", {}).get(helper, True) is True


def require(db, helper):
    if not enabled(db, helper):
        raise AgentDisabled(f"The {helper} agent is disabled in System settings")


def for_role(db, config, role):
    """Use an immutable per-call copy; concurrent workers never alter global config."""
    provider = getattr(current(db, config).models, role)
    return config.model_copy(update={"llm_provider": provider})


def summary(db, config):
    return {
        **current(db, config).model_dump(),
        "providers": {
            name: {
                "available": bool(getattr(config, name + "_api_key")),
                "model": getattr(config, name + "_model"),
            }
            for name in PROVIDER_NAMES
        },
    }


def save(db, config, value):
    data = AgentSettings.model_validate(value)
    prior = current(db, config)
    for role in ("answers", "navigator"):
        provider = getattr(data.models, role)
        if provider != getattr(prior.models, role) and not getattr(config, provider + "_api_key"):
            raise ValueError(missing_key_message(provider))
    db.set_setting("agent_settings", data.model_dump())
    return summary(db, config)
