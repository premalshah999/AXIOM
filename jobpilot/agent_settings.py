"""Persisted helper controls. Secrets and provider endpoints remain server configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Provider = Literal["mimo", "deepseek", "haiku"]


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


def current(db, config):
    default = config.llm_provider if config.llm_provider in {"mimo", "deepseek", "haiku"} else "mimo"
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
            for name in ("mimo", "deepseek", "haiku")
        },
    }


def save(db, config, value):
    data = AgentSettings.model_validate(value)
    prior = current(db, config)
    for role in ("answers", "navigator"):
        provider = getattr(data.models, role)
        if provider != getattr(prior.models, role) and not getattr(config, provider + "_api_key"):
            raise ValueError(f"Configure the {provider} API key before selecting it")
    db.set_setting("agent_settings", data.model_dump())
    return summary(db, config)
