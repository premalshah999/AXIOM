"""Taking secrets out of text that is stored or sent: run events, Telegram messages, saved page HTML.

Two ways, used together. The exact values this installation holds (model keys, the bot token, the app token, the shared employer
password, the mailbox password) are registered when settings load and replaced wherever they appear, in whatever format a service
chose for them. The known shapes of other keys are matched too, so a key pasted into a question, or echoed in an error from a
service that rejected it, is caught without being registered (2026-11-01 review: an OpenRouter `sk-or-v1-` key was not matched by
the Telegram filter's `sk-` rule written for another provider, and nothing masked one in a stored event).

`redact` is strict: formats and registered values only, safe for any text. `redact(text, loose=True)` also masks the value after
`password:` / `token=` style labels, for log lines and diagnostics, where a label with a value is almost always a credential.
"""

import re

MARK = "[secret]"
# Shapes of keys that identify themselves.
FORMATS = re.compile(
    r"\bsk-[A-Za-z0-9_-]{16,}"  # OpenAI, Anthropic (sk-ant-), OpenRouter (sk-or-v1-), DeepSeek and kin
    r"|\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}"
    r"|\bAIza[0-9A-Za-z_-]{30,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|\b\d{8,10}:[A-Za-z0-9_-]{30,}"  # Telegram bot token
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"  # a JWT
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
    r"|(?<![A-Za-z0-9_=-])[A-Za-z0-9_-]{43}=(?![A-Za-z0-9=])"  # a Fernet key, as DATA_DIR/mail-key holds
    r"|(?i:\bBearer)\s+[A-Za-z0-9._~+/=-]{16,}"
)
# `user:password@` in an address.
USERINFO = re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")
# A label that names a credential, then its value.
LABELLED = re.compile(
    r"(?i)\b((?:x-)?api[_-]?key|authorization|passw(?:or)?d|passcode|secret|(?:access|auth|refresh|bot|app|session)[_ -]?token|token)"
    r"([\"']?\s*[:=]\s*[\"']?)(?!\[secret\])([^\s\"',;&<>]{4,})"
)
QUERY = re.compile(r"(?i)([?&](?:api[_-]?key|key|token|access_token|secret|password|code|state)=)[^&\s#\"']+")

_known = set()
_ordered = ()  # `_known`, longest first, so a value that contains another is masked whole.


def register(values):
    """Remember exact secret values to remove from now on (values shorter than 6 characters are too common to mask)."""
    global _ordered
    for value in values:
        value = str(value or "").strip()
        if len(value) >= 6:
            _known.add(value)
            # A mailbox app password is shown with spaces and typed without.
            if " " in value:
                _known.add(value.replace(" ", ""))
    _ordered = tuple(sorted(_known, key=len, reverse=True))


def known_from(config):
    """The secret-looking string settings of `config`, for register()."""
    found = []
    for name in type(config).model_fields:
        if re.search(r"key|token|passw|secret|login", name) and not re.search(r"price|model|url|enabled|rounds|timeout|policy|file", name):
            value = getattr(config, name, "")
            if isinstance(value, str) and len(value) >= 8:
                found.append(value)
    return found


def redact(text, *, loose=False, extra=()):
    """`text` with registered values, key-shaped strings and (loose) labelled credentials replaced by [secret]."""
    if not text or not isinstance(text, str):
        return text
    for value in (_ordered if not extra else sorted({*_ordered, *(str(v) for v in extra if v and len(str(v)) >= 4)}, key=len, reverse=True)):
        if value in text:
            text = text.replace(value, MARK)
    text = FORMATS.sub(MARK, text)
    if loose:
        text = USERINFO.sub(MARK, text)
        text = QUERY.sub(lambda m: m.group(1) + MARK, text)
        text = LABELLED.sub(lambda m: m.group(1) + m.group(2) + MARK, text)
    return text


def redact_data(value, depth=0):
    """The same for the strings inside a JSON-shaped value (an event's data)."""
    if isinstance(value, str):
        return redact(value)
    if depth > 4:
        return value
    if isinstance(value, dict):
        return {k: redact_data(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_data(v, depth + 1) for v in value]
    return value
