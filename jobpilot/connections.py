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


def apply(config):
    """Lay the saved values over the environment's, remembering the environment's for status and clearing."""
    env = _ENV.setdefault(id(config), {name: str(getattr(config, name, "") or "") for name in FIELDS})
    saved = stored(config)
    for name in FIELDS:
        value = saved.get(name, env.get(name, ""))
        if name == "llm_provider" and value not in PROVIDERS:
            value = env.get(name) or "deepseek"
        object.__setattr__(config, name, value)
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
    return apply(config)


def status(config):
    """What the Connections page shows: per field whether it is set and where from; secrets are masked."""
    saved = stored(config)
    env = _ENV.get(id(config), {})
    fields = []
    for name, (group, label, secret, help_text) in FIELDS.items():
        value = str(getattr(config, name, "") or "")
        source = "app" if name in saved else "env" if env.get(name) else ""
        shown = ("••••" + value[-4:] if len(value) > 8 else "••••") if secret and value else value
        fields.append({"name": name, "group": group, "label": label, "secret": secret, "help": help_text,
                       "set": bool(value), "source": source, "value": shown})
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
