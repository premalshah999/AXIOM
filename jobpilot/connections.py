"""Service connections entered in the dashboard (Setup > Connections) instead of .env.

The model key, Telegram bot, 2Captcha and Serper keys used to need an edit of .env and a restart, which non-technical
owners could not do (owner 2026-10-26: "all the API connections should be set up from the app and not from .env").
They are stored here, encrypted with the same key that seals mailbox and employer credentials (DATA_DIR/mail-key),
and laid over the environment whenever the settings are loaded. A value saved in the app wins over .env; clearing it
falls back to .env again. Secrets are never returned to the browser: only whether each one is set and where from.
"""

import json
import os

import httpx
from cryptography.fernet import Fernet, InvalidToken

FILE = "connections.enc"
# id(config) -> the environment's own values, kept so that clearing a saved value falls back to .env.
_ENV = {}

# name -> (group, label, secret, help)
FIELDS = {
    "llm_provider": ("Model", "Model provider", False, "Which AI service reads job pages and writes answers."),
    "deepseek_api_key": ("Model", "DeepSeek API key", True, "platform.deepseek.com > API keys. Recommended: lowest cost."),
    "haiku_api_key": ("Model", "Anthropic API key", True, "console.anthropic.com > API keys (Claude Haiku)."),
    "mimo_api_key": ("Model", "Xiaomi MiMo API key", True, "Optional third provider."),
    "openai_api_key": ("Model", "OpenAI API key", True, "platform.openai.com > API keys. GPT-6 Luna at its cheapest setting."),
    "openrouter_api_key": ("Model", "OpenRouter API key", True,
                           "openrouter.ai > Keys. DeepSeek V4.1 Flash 1017 served by Relace, reasoning off."),
    "typesafe_api_key": ("Model", "TypeSafe Jev key", True, "Optional. Answers multiple-choice questions first."),
    "telegram_bot_token": ("Telegram", "Telegram bot token", True, "From @BotFather in Telegram: /newbot."),
    "telegram_user_id": ("Telegram", "Your Telegram user ID", False, "A number, from @userinfobot in Telegram."),
    "twocaptcha_api_key": ("Captcha", "2Captcha API key", True, "Optional, paid per solve. 2captcha.com > Dashboard."),
    "serper_api_key": ("Discovery", "Serper API key", True, "Optional. Adds Google results to job discovery (serper.dev)."),
    # Everything else a new owner sets (owner 2026-10-28: "a user should be able to configure API keys, captcha keys,
    # gmail app password, everything through the web interface").
    "openrouter_model": ("Model", "OpenRouter model", False, "Model id on openrouter.ai. Default: deepseek/deepseek-v4.1-flash-1017."),
    "openrouter_provider": ("Model", "OpenRouter host", False,
                            "Which company runs the model for OpenRouter. Prices per 1M tokens: input / output."),
    "vision_api_key": ("Vision", "Screenshot model key", True, "Optional. Only if your AI model cannot read images."),
    "vision_base_url": ("Vision", "Screenshot model address", False, "OpenAI-compatible base URL, e.g. https://openrouter.ai/api/v1."),
    "vision_model": ("Vision", "Screenshot model", False, "Model id at that address."),
    "account_email": ("Accounts", "Email for employer accounts", False,
                      "Used to sign up and sign in on career sites. Empty: the email in your profile."),
    "application_password": ("Accounts", "Shared employer password", True,
                             "One password for every employer account the app creates. A site that refuses it gets a "
                             "stronger variant, kept in Setup › Accounts."),
    "telegram_enabled": ("Telegram", "Telegram messages", False, "Send questions and alerts to Telegram."),
    "captcha_image_rounds": ("Captcha", "Paid image rounds per puzzle", False, "More rounds solve more puzzles and cost more."),
    "captcha_timeout": ("Captcha", "Seconds to wait for a solve", False, "How long one captcha may take."),
    "daily_application_limit": ("Limits", "Applications per day", False, "The batch stops starting new ones at this number."),
    "daily_budget_usd": ("Limits", "AI spending per day (USD)", False, "Model calls stop for the day at this amount."),
    "max_model_calls": ("Limits", "AI calls per application", False, "A run that needs more stops and is repaired later."),
    "timezone": ("Limits", "Time zone", False, "For dates on forms and the daily limits, e.g. America/New_York."),
    "normal_workers": ("Workers", "Normal workers", False,
                       "Applications on known platforms running at once. Each uses a Chrome tab and about 0.8 GB of "
                       "memory: 4 suits a 16 GB computer."),
    "specialist_workers": ("Workers", "Difficult-mode workers", False,
                           "Retries of applications that failed on a normal worker, with a larger budget."),
    "custom_ats_worker": ("Workers", "Custom-site worker", False,
                          "One worker for career sites with no known platform. Off: those applications wait."),
    "headless": ("Workers", "Hide the browser", False, "Run Chrome without a window."),
}
# Settings read once at startup: saved here, used after the app restarts (the worker queues are sized then).
RESTART = {"normal_workers", "specialist_workers", "custom_ats_worker", "headless"}
# Fields the page offers as a fixed choice (owner 2026-10-28: normal 4, 6 or 8; difficult mode 1 or 2).
OPTIONS = {
    "normal_workers": ["4", "6", "8"],
    "specialist_workers": ["1", "2"],
    "openrouter_provider": ["sail-research", "deepinfra", "relace", "wafer"],
}
# How a choice reads on the page.
OPTION_LABELS = {
    "sail-research": "Sail Research · $0.10 / $0.15 (most affordable, can be slower)",
    "deepinfra": "DeepInfra · $0.10 / $0.29",
    "relace": "Relace · $0.07 / $0.50",
    "wafer": "Wafer · $0.07 / $0.50",
}
PROVIDERS = ("deepseek", "haiku", "mimo", "openai", "openrouter")


def _cipher(config):
    if getattr(config, "mail_encryption_key", ""):
        return Fernet(config.mail_encryption_key.encode())
    path = config.data_dir / "mail-key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
    return Fernet(path.read_bytes().strip())


def _adapter(name):
    from typing import Annotated

    from pydantic import TypeAdapter

    from .config import Settings

    info = Settings.model_fields[name]
    return TypeAdapter(Annotated[info.annotation, *info.metadata]) if info.metadata else TypeAdapter(info.annotation)


def kind(name):
    """How the page edits a field: secret, bool, number or text, from the setting's own type."""
    from .config import Settings

    if FIELDS[name][2]:
        return "secret"
    annotation = Settings.model_fields[name].annotation
    return "bool" if annotation is bool else "number" if annotation in (int, float) else "text"


def typed(name, value):
    """A saved string as the setting's own type, within its limits (ValueError when it is not one)."""
    if name == "llm_provider" or kind(name) in {"secret", "text"}:
        return value
    try:
        return _adapter(name).validate_python(value)
    except Exception as exc:
        raise ValueError(f"{FIELDS[name][1]}: {str(exc).splitlines()[-1].strip()[:120]}") from None


def stored(config):
    """The values saved in the app ({} when none, or when the file cannot be read)."""
    path = config.data_dir / FILE
    if not path.exists():
        return {}
    try:
        values = json.loads(_cipher(config).decrypt(path.read_bytes()))
    except (InvalidToken, ValueError, OSError):
        return {}
    return {k: str(v) for k, v in values.items() if k in FIELDS and str(v).strip()}


def apply(config, live=False):
    """Lay the saved values over the environment's, remembering the environment's for status and clearing. `live`: the
    app is running, so startup-only settings (RESTART) keep their running value until the next start."""
    env = _ENV.setdefault(id(config), {name: str(getattr(config, name, "") or "") for name in FIELDS})
    saved = stored(config)
    for name in FIELDS:
        if live and name in RESTART:
            continue
        value = saved.get(name, env.get(name, ""))
        if name == "llm_provider" and value not in PROVIDERS:
            value = env.get(name) or "deepseek"
        try:
            value = typed(name, value) if value != "" or kind(name) in {"secret", "text"} else getattr(config, name)
        except ValueError:
            value = typed(name, env[name]) if env.get(name) not in (None, "") else getattr(config, name)
        object.__setattr__(config, name, value)
    if not live and hasattr(config, "normal_workers"):
        # The total the validator computed before these values were laid over the environment.
        object.__setattr__(
            config,
            "workers",
            config.normal_workers + config.specialist_workers + int(bool(getattr(config, "custom_ats_worker", False))),
        )
    return config


def save(config, updates):
    """Merge `updates` (a blank value removes that saved value) into the encrypted file and apply it."""
    values = stored(config)
    for name, value in updates.items():
        if name not in FIELDS:
            raise ValueError(f"Unknown connection: {name}")
        value = str(value or "").strip()
        if name == "llm_provider" and value and value not in PROVIDERS:
            raise ValueError("Choose deepseek, haiku, mimo, openai or openrouter")
        if name == "telegram_user_id" and value and not value.lstrip("-").isdigit():
            raise ValueError("The Telegram user ID is a number (ask @userinfobot), not an @username")
        if value and name in OPTIONS and value not in OPTIONS[name]:
            raise ValueError(f"{FIELDS[name][1]}: choose {' or '.join(OPTIONS[name])}")
        if value:
            typed(name, value)  # Refuses a value outside the setting's type or limits before it is saved.
        if name == "application_password" and value:
            from .config import password_problems

            if problems := password_problems(value):
                raise ValueError("Shared employer password: " + "; ".join(problems))
        if value:
            values[name] = value
        else:
            values.pop(name, None)
    path = config.data_dir / FILE
    data = _cipher(config).encrypt(json.dumps(values).encode())
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return apply(config, live=True)


def status(config):
    """What the Connections page shows: per field whether it is set and where from; secrets are masked."""
    saved = stored(config)
    env = _ENV.get(id(config), {})
    fields = []
    for name, (group, label, secret, help_text) in FIELDS.items():
        current = getattr(config, name, "")
        value = ("true" if current else "false") if kind(name) == "bool" else str(current if current is not None else "")
        source = "app" if name in saved else "env" if env.get(name) else ""
        shown = ("••••" + value[-4:] if len(value) > 8 else "••••") if secret and value else value
        pending = ""
        if name in RESTART and (name in saved or env.get(name)):
            # Saved, not yet running: the next start uses it.
            want = typed(name, saved.get(name, env.get(name, "")))
            if want != current:
                pending = ("true" if want else "false") if kind(name) == "bool" else str(want)
        fields.append({"name": name, "group": group, "label": label, "secret": secret, "help": help_text,
                       "set": bool(value), "source": source, "value": shown, "kind": kind(name),
                       "restart": name in RESTART, "options": OPTIONS.get(name, []), "pending": pending,
                       "option_labels": {o: OPTION_LABELS[o] for o in OPTIONS.get(name, []) if o in OPTION_LABELS}})
    provider = str(getattr(config, "llm_provider", "deepseek"))
    return {"fields": fields, "provider": provider, "model_ready": bool(getattr(config, provider + "_api_key", ""))}


async def test(config, name):
    """Check one connection against its service with the stored key: {'ok': bool, 'message': str}."""
    key = str(getattr(config, name, "") or "")
    if not key:
        return {"ok": False, "message": "Nothing is saved for this yet."}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if name == "openrouter_api_key":
                # /key checks the key itself (its limit and usage); /models answers without one.
                base = getattr(config, "openrouter_base_url").rstrip("/")
                response = await client.get(base + "/key", headers={"Authorization": f"Bearer {key}"})
            elif name in {"deepseek_api_key", "mimo_api_key", "openai_api_key"}:
                base = getattr(config, name.replace("_api_key", "_base_url")).rstrip("/")
                response = await client.get(base + "/models", headers={"Authorization": f"Bearer {key}"})
            elif name == "haiku_api_key":
                base = getattr(config, "haiku_base_url", "https://api.anthropic.com").rstrip("/")
                response = await client.get(
                    base + "/v1/models", headers={"x-api-key": key, "anthropic-version": "2023-06-01"}
                )
            elif name == "telegram_bot_token":
                response = await client.get(f"https://api.telegram.org/bot{key}/getMe")
            elif name == "twocaptcha_api_key":
                response = await client.get("https://2captcha.com/res.php",
                                            params={"key": key, "action": "getbalance", "json": 1})
                body = response.json()
                if body.get("status") == 1:
                    return {"ok": True, "message": f"Connected. Balance: ${float(body.get('request', 0)):.2f}"}
                return {"ok": False, "message": "2Captcha refused this key."}
            elif name == "application_password":
                from .config import password_problems

                problems = password_problems(key)
                return {"ok": not problems, "message": "; ".join(problems) or "Meets common password rules."}
            elif name == "vision_api_key":
                base = (getattr(config, "vision_base_url", "") or "").rstrip("/")
                if not base:
                    return {"ok": False, "message": "Add the screenshot model address first."}
                response = await client.get(base + "/models", headers={"Authorization": f"Bearer {key}"})
            elif name == "serper_api_key":
                response = await client.post("https://google.serper.dev/search", headers={"X-API-KEY": key},
                                             json={"q": "software engineer jobs", "num": 1})
            else:
                return {"ok": True, "message": "Saved."}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Could not reach the service ({type(exc).__name__})."}
    if response.status_code == 200:
        return {"ok": True, "message": "Connected."}
    if response.status_code in {401, 403}:
        return {"ok": False, "message": "The service refused this key. Copy it again from the service's website."}
    if response.status_code == 402:
        return {"ok": False, "message": "The key works but the account has no credit. Top it up on the service's website."}
    return {"ok": False, "message": f"The service answered HTTP {response.status_code}."}
