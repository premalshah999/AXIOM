"""Service connections entered in the dashboard (Setup > Connections) instead of .env.

The model key, Telegram bot, 2Captcha and Serper keys used to need an edit of .env and a restart, which non-technical
owners could not do (owner 2026-10-26: "all the API connections should be set up from the app and not from .env").
They are stored here, encrypted with the same key that seals mailbox and employer credentials (DATA_DIR/mail-key),
and laid over the environment whenever the settings are loaded. A value saved in the app wins over .env; clearing it
falls back to .env again. Secrets are never returned to the browser: only whether each one is set and where from.
"""

import json
import os
import re
import weakref
from zoneinfo import ZoneInfo

import httpx
from cryptography.fernet import Fernet, InvalidToken

FILE = "connections.enc"
# id(config) -> (that config, held weakly; the environment's own values), kept so that clearing a saved value falls back to .env.
# The config is held too: a freed config's id() goes to the next one, which then took the old one's empty keys for its own
# environment and lost the key it was made with (two settings tests, 2026-11-02).
_ENV = {}


def _environment(config, read=True):
    """The environment's own values for this config, read the first time when `read`; {} when they never were."""
    held = _ENV.get(id(config))
    if held is not None and held[0]() is config:
        return held[1]
    if not read:
        return {}
    key = id(config)
    values = {name: str(getattr(config, name, "") or "") for name in FIELDS}
    _ENV[key] = (weakref.ref(config, lambda ref: _ENV.pop(key) if _ENV.get(key, (None,))[0] is ref else None), values)
    return values

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
    "telegram_bot_token": ("Telegram", "Telegram bot token", True, "In Telegram, message @BotFather and send /newbot. Paste the token it gives you, then open your new bot and press Start, or it can't message you."),
    "telegram_user_id": ("Telegram", "Your Telegram user ID", False, "A number. Message @userinfobot in Telegram and it replies with yours."),
    "twocaptcha_api_key": ("Captcha", "2Captcha API key", True, "Optional, paid per solve. 2captcha.com > Dashboard."),
    "company_logos": ("Appearance", "Show company logos", False,
                      "Finds a company's icon once, in the background, and keeps a small copy on this computer: from the company's own "
                      "job page and web site first, then from Google, which sees only the company's web address, not you, the job "
                      "or the company's name. Off: letter tiles only, and nothing is fetched."),
    "serper_api_key": ("Discovery", "Serper API key", True, "Optional. Adds Google results to job discovery (serper.dev)."),
    # Everything else a new owner sets (owner 2026-10-28: "a user should be able to configure API keys, captcha keys,
    # gmail app password, everything through the web interface").
    "openrouter_model": ("Model", "OpenRouter model", False, "Model id on openrouter.ai. Default: deepseek/deepseek-v4.1-flash-1017."),
    "openrouter_provider": ("Model", "OpenRouter host", False,
                            "Which company runs the model for OpenRouter. Prices per 1M tokens: input / output."),
    "openrouter_fallback_hosts": ("Model", "Use other hosts when busy", False,
                                  "When the chosen host is rate limited, OpenRouter tries the other DeepSeek hosts above "
                                  "instead of pausing applications."),
    "vision_api_key": ("Vision", "Screenshot model key", True, "Optional. Only if your AI model cannot read images."),
    "vision_base_url": ("Vision", "Screenshot model address", False, "OpenAI-compatible base URL, e.g. https://openrouter.ai/api/v1."),
    "vision_model": ("Vision", "Screenshot model", False, "Model id at that address."),
    "account_email": ("Accounts", "Email for employer accounts", False,
                      "Used to sign up and sign in on career sites. Empty: the email in your profile."),
    "application_password": ("Accounts", "Main password", True,
                             "Optional. One password AXIOM tries first when it creates or resets an employer account. A site that "
                             "refuses it gets a random one that meets its rules, kept for that employer in Settings › Employer accounts. "
                             "Using one password on many sites is your choice, and a risk: if one employer leaks it, the others "
                             "can be tried with it."),
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
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


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


def valid_zone(value):
    """Whether this names a time zone. Every "today" reads it, and the dashboard's one poll first of all: a typo saved here left
    the whole app on an error, with no way back to this setting (2026-11-01)."""
    try:
        ZoneInfo(str(value))
    except Exception:  # Not found, a folder name ("America"), or not a key at all (ValueError).
        return False
    return bool(value)


def typed(name, value):
    """A saved string as the setting's own type, within its limits (ValueError when it is not one)."""
    if name == "llm_provider" or kind(name) in {"secret", "text"}:
        return value
    try:
        return _adapter(name).validate_python(value)
    except Exception as exc:
        # The last line of a validation error is a link to its documentation, which is what the person used to read.
        first = (exc.errors() or [{}])[0] if hasattr(exc, "errors") else {}
        bound = {k: int(v) if float(v).is_integer() else v for k, v in (first.get("ctx") or {}).items() if isinstance(v, (int, float))}
        why = (
            f"must be {bound['le']} or less" if "le" in bound
            else f"must be {bound['ge']} or more" if "ge" in bound
            else f"must be more than {bound['gt']}" if "gt" in bound
            else f"must be less than {bound['lt']}" if "lt" in bound
            else "isn't a valid value"
        )
        raise ValueError(f"{FIELDS[name][1]} {why}") from None


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
    env = _environment(config)
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
        if name == "timezone" and not valid_zone(value):
            # Saved by a build that did not check, or a typo in .env: the environment's if it is one, otherwise UTC.
            value = env[name] if valid_zone(env.get(name)) else "UTC"
        object.__setattr__(config, name, value)
    from .redaction import register_config

    register_config(config)  # The keys saved in the app are secrets too, whatever their shape.
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
        if name == "timezone" and value and not valid_zone(value):
            raise ValueError(f"Time zone: {value} isn't one. Use a name like America/New_York or Europe/London.")
        if name == "account_email" and value and not EMAIL.fullmatch(value):
            raise ValueError("Email for employer accounts: enter an address like name@example.com.")
        if name == "vision_base_url" and value and not value.startswith(("http://", "https://")):
            raise ValueError("Screenshot model address: it starts with https://, like https://openrouter.ai/api/v1.")
        if name == "application_password" and value:
            from .config import password_problems

            if problems := password_problems(value):
                raise ValueError("Main password: " + "; ".join(problems))
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
    env = _environment(config, read=False)
    fields = []
    for name, (group, label, secret, help_text) in FIELDS.items():
        if name == "headless" and getattr(config, "browser_cdp_url", ""):
            # It only reaches the throwaway Chrome used when no worker Chrome is attached. The Mac app always attaches one, which
            # shows a window whatever this says: a switch that read "on" while the browser stayed on screen.
            continue
        current = getattr(config, name, "")
        value = ("true" if current else "false") if kind(name) == "bool" else str(current if current is not None else "")
        source = "app" if name in saved else "env" if env.get(name) else ""
        # A password has no shown tail: the last characters of a password given to many sites are part of it.
        shown = ("••••" + value[-4:] if len(value) > 8 and name != "application_password" else "••••") if secret and value else value
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


# Who a refused key is refused by: "the service" gave someone new to this nothing to look for.
SERVICE_NAMES = {
    "openrouter_api_key": "OpenRouter",
    "deepseek_api_key": "DeepSeek",
    "mimo_api_key": "Xiaomi MiMo",
    "openai_api_key": "OpenAI",
    "haiku_api_key": "Anthropic",
    "telegram_bot_token": "Telegram",
    "vision_api_key": "The screenshot model",
    "serper_api_key": "Serper",
    "twocaptcha_api_key": "2Captcha",
    "typesafe_api_key": "TypeSafe",
}


async def _test_telegram(client, config, key):
    """The token, and then the step that silently breaks it: a bot may not message anyone who never pressed Start. A typing signal
    shows up nowhere lasting and fails exactly as a message would."""
    base = f"https://api.telegram.org/bot{key}"
    response = await client.get(base + "/getMe")
    if response.status_code in {401, 404}:  # 404 is Telegram's answer to something not shaped like a token.
        return {"ok": False, "message": "Telegram didn't accept this token. Copy it again from @BotFather, all of it."}
    if response.status_code != 200:
        return {"ok": False, "message": f"Telegram isn't answering properly right now (error {response.status_code}). Try again in a minute."}
    user = str(getattr(config, "telegram_user_id", "") or "")
    if not user:
        return {"ok": False, "message": "The token works. Add your Telegram user ID, or the bot has no one to message."}
    reach = await client.post(base + "/sendChatAction", json={"chat_id": user, "action": "typing"})
    if reach.status_code == 200:
        return {"ok": True, "message": "Connected. The bot can message you."}
    if reach.status_code in {400, 403}:
        return {"ok": False, "message": "The token works, but the bot can't message you yet. Open your bot in Telegram, press Start, and check your user ID."}
    return {"ok": False, "message": f"Telegram isn't answering properly right now (error {reach.status_code}). Try again in a minute."}


async def test(config, name):
    """Check one connection against its service with the stored key: {'ok': bool, 'message': str}."""
    service = SERVICE_NAMES.get(name, "The service")
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
                return await _test_telegram(client, config, key)
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
            elif name == "typesafe_api_key":
                # One tiny question, a fraction of a cent. This used to answer "Saved." with a green Works for any text at all.
                from .jev import choice_question

                base = getattr(config, "typesafe_base_url").rstrip("/")
                response = await client.post(
                    base + "/v1/systemone", headers={"Authorization": f"Bearer {key}"},
                    json={"model": getattr(config, "jev_model"), "state": "The sky is blue.",
                          "questions": {"check": choice_question("What color is the sky?", ["Blue", "Green"])}},
                )
            else:
                return {"ok": True, "message": "Saved."}
    except (httpx.HTTPError, httpx.InvalidURL):
        return {"ok": False, "message": f"Couldn't reach {service}. Check your internet connection and try again."}
    except ValueError:  # A page where a JSON answer belonged.
        return {"ok": False, "message": f"{service} isn't answering properly right now. Try again in a minute."}
    if response.status_code == 200 and name == "openrouter_api_key":
        # /key answers 200 for a key whose account has nothing left to spend, and the first application then fails at its first
        # model question. Said now, in words, where it can be fixed (a friend's first run: out of credit looked like "no jobs").
        try:
            remaining = response.json().get("data", {}).get("limit_remaining")
        except (ValueError, AttributeError):
            remaining = None
        if isinstance(remaining, (int, float)) and remaining <= 0:
            return {"ok": False, "message": "The key is right, but this OpenRouter account is out of credit. Add credit at openrouter.ai/settings/credits, then connect again."}
    if response.status_code == 200:
        return {"ok": True, "message": "Connected."}
    if response.status_code in {401, 403}:
        return {"ok": False, "message": f"{service} didn't accept this key. Copy it again from their website, all of it."}
    if response.status_code == 402:
        return {"ok": False, "message": f"The key is right, but this {service} account is out of credit. Add credit on their website, then try again."}
    return {"ok": False, "message": f"{service} isn't answering properly right now (error {response.status_code}). Try again in a minute."}
