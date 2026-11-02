"""Taking secrets out of text that is stored or sent: run events, Telegram messages, saved page HTML.

Two ways, used together. The exact values this installation holds (model keys, the bot token, the app token, the shared employer
password, the mailbox password) are registered when settings load and replaced wherever they appear, in whatever format a service
chose for them. The known shapes of other keys are matched too, so a key pasted into a question, or echoed in an error from a
service that rejected it, is caught without being registered (2026-11-01 review: an OpenRouter `sk-or-v1-` key was not matched by
the Telegram filter's `sk-` rule written for another provider, and nothing masked one in a stored event).

`redact` is strict: formats and registered values only, safe for any text. `redact(text, loose=True)` also masks the value after
`password:` / `token=` style labels, for log lines and diagnostics, where a label with a value is almost always a credential.

A registered value that could be an ordinary word or number (`summer`, `Password`, `20261101`) is never replaced wherever it
appears: that would blank the word in a job title, in a person's name, in an answer shown back to its owner. It is masked only where
something says it is the secret: after a credential's label, and in a saved page as a whole quoted value. Nothing here touches what
is typed into a form; that text never passes through this module.
"""

import html
import io
import json
import re
import sys
from urllib.parse import quote, quote_plus, unquote, urlsplit

MARK = "[secret]"
# Shapes of keys that identify themselves.
FORMATS = re.compile(
    r"\bsk-[A-Za-z0-9_-]{16,}"  # OpenAI, Anthropic (sk-ant-), OpenRouter (sk-or-v1-), DeepSeek and kin
    r"|\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}"
    r"|\bAIza[0-9A-Za-z_-]{30,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    # A Telegram bot token, also where its address puts it: api.telegram.org/bot123456789:AA... has no word boundary before the digits.
    r"|(?<![0-9])\d{8,10}:[A-Za-z0-9_-]{30,}"
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
_common = set()  # Secrets that could be ordinary words: masked only where something says what they are (common_value).
_labelled_common = None  # The compiled pattern for `_common` behind a credential's label.

# Too short to mean anything: "abc" is in every page.
SHORTEST = 4
# Below this a value is common text whatever it is made of.
SHORT = 8
# A single word (`full-time` is one) is common up to here. A mailbox app password is 16 letters in one run, and no ordinary word is
# that long.
WORD_LENGTH = 16
# Several words (a job title), or digits with separators (a date, a phone number), are common up to here.
PHRASE_LENGTH = 24
ONE_WORD = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*")
WORDS = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*(?: +[^\W\d_]+(?:['-][^\W\d_]+)*)+|[\d ./-]+")
APP_PASSWORD = re.compile(r"[a-z]{4}(?: [a-z]{4}){3}")  # How Gmail shows one.
LABEL = r"(?:(?:[a-z]+[_ -])?passw(?:or)?d|passcode|pwd|secret|(?:[a-z]+[_ -])?token|api[_ -]?key|credentials?)"


def common_value(value):
    """True when a secret could also be everyday text: a short one, or one that is only words or only digits."""
    if len(value) < SHORT:
        return True
    if APP_PASSWORD.fullmatch(value):
        return False
    if ONE_WORD.fullmatch(value):
        return len(value) < WORD_LENGTH
    return len(value) < PHRASE_LENGTH and bool(WORDS.fullmatch(value))


def forms(value):
    """A value as text carries it. A page holds `&` as `&amp;`, an address as `%26`, a JSON document as `\\"`: the exact bytes of a
    password with a symbol in it never appear, and it went straight past an exact match."""
    found = {
        value,
        html.escape(value, quote=False),
        html.escape(value),
        value.replace("&", "&amp;").replace('"', "&quot;"),  # How a browser writes an attribute back out.
        quote(value, safe=""),
        quote(value),
        quote(value, safe="!'()*~"),  # As a page's own script encodes it (encodeURIComponent).
        quote_plus(value),
        json.dumps(value)[1:-1],
        json.dumps(value, ensure_ascii=False)[1:-1],
    }
    return found


def register(values):
    """Remember exact secret values to remove from now on. See common_value for those that may also be ordinary words."""
    global _ordered, _labelled_common
    before = len(_known), len(_common)
    for value in values:
        value = str(value or "").strip()
        if len(value) < SHORTEST:
            continue
        if common_value(value):
            _common.add(value)
            continue
        _known.update(forms(value))
        # A mailbox app password is shown with spaces and typed without.
        if " " in value:
            _known.update(forms(value.replace(" ", "")))
    if (len(_known), len(_common)) != before:  # Called on every credential read: sort only when something is new.
        _ordered = tuple(sorted(_known, key=len, reverse=True))
        _labelled_common = _behind_a_label(_common)


def _behind_a_label(values):
    if not values:
        return None
    choices = "|".join(re.escape(v) for v in sorted(values, key=len, reverse=True))
    return re.compile(rf"(?i)(\b{LABEL}\b[\"']?\s*(?:is\s+|[:=]\s*)[\"']?)(?:{choices})(?![\w])")


# A key in a sealed credential that names a secret (an app password, a token, an employer password), and not one that only
# describes it: an OAuth record's `token_uri` is an address and its `token_type` is the word Bearer.
CREDENTIAL_KEY = re.compile(r"pass|token|secret|key|credential|verifier", re.I)
NOT_A_CREDENTIAL = re.compile(r"ur[il]|type|expir|scope|kind|name|hint|rule", re.I)


def register_credentials(value):
    """Register the secret strings of a credential (the flat dict a mailbox or an employer account is sealed with). Nested values
    are a site's own saved session, not ours, and are left out so that the list stays as long as the accounts are."""
    if not isinstance(value, dict):
        return
    if value.get("kind") == "password":  # A temporary password an employer mailed (mail/extraction.py).
        register([value.get("value")])
    register([
        item for key, item in value.items()
        if isinstance(item, str) and len(item) <= 512 and CREDENTIAL_KEY.search(str(key)) and not NOT_A_CREDENTIAL.search(str(key))
    ])


def register_config(config):
    """Register the secrets `config` holds. Without this only key-shaped text was ever masked: a mailbox app password, the
    access token and a password typed in by the owner have no shape, and nothing called `register` (2026-11-01 review)."""
    register(known_from(config))
    for name in ("database_url", "dbos_database_url"):
        register([database_password(getattr(config, name, ""))])


def database_password(url):
    """The password in a database address: an error from the driver can quote it."""
    try:
        return unquote(urlsplit(str(url or "")).password or "")
    except ValueError:
        return ""


def known_from(config):
    """The secret-looking string settings of `config`, for register(). `icims_login`, despite its name, is a password."""
    found = []
    for name in type(config).model_fields:
        if re.search(r"key|token|passw|secret|login", name) and not re.search(r"price|model|url|enabled|rounds|timeout|policy|file", name):
            value = getattr(config, name, "")
            if isinstance(value, str) and value:
                found.append(value)
    return found


def redact(text, *, loose=False):
    """`text` with registered values, key-shaped strings and (loose) labelled credentials replaced by [secret]."""
    if not text or not isinstance(text, str):
        return text
    for value in _ordered:
        if value in text:
            text = text.replace(value, MARK)
    if _labelled_common:
        text = _labelled_common.sub(lambda m: m.group(1) + MARK, text)
    text = FORMATS.sub(MARK, text)
    if loose:
        text = USERINFO.sub(MARK, text)
        text = QUERY.sub(lambda m: m.group(1) + MARK, text)
        text = LABELLED.sub(lambda m: m.group(1) + m.group(2) + MARK, text)
    return text


def redact_page(text, extra=()):
    """A saved page, or anything else copied from a site. `extra` are secrets this run holds that may not have been registered yet.
    A short or word-like one goes only where it is a whole quoted value (`value="summer"`, `"pw":"summer"`): the words of the page stay."""
    if not text or not isinstance(text, str):
        return text
    register(extra)
    text = redact(text)
    if _common:
        choices = "|".join(re.escape(v) for f in _common for v in forms(f))
        text = re.sub(rf"(?<=[=:\s])([\"'])(?:{choices})\1", lambda m: m.group(1) + MARK + m.group(1), text)
    return text


# An event's field that holds a credential by its name, whatever the value looks like.
SECRET_FIELD = re.compile(r"(?:^|_)(?:passw(?:or)?d|passcode|pwd|secret|access_token|refresh_token|app_token|bot_token|api_key)$", re.I)


def redact_data(value, depth=0):
    """The same for the strings inside a JSON-shaped value (an event's data)."""
    if isinstance(value, str):
        return redact(value)
    if depth > 12:
        # Too deep to walk: masked as text rather than stored untouched (a nested value used to skip masking past depth 4).
        return redact(json.dumps(value, default=str)) if isinstance(value, (dict, list, tuple)) else value
    if isinstance(value, dict):
        return {
            redact(k) if isinstance(k, str) else k: (
                MARK if isinstance(v, str) and v and isinstance(k, str) and SECRET_FIELD.search(k) else redact_data(v, depth + 1)
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_data(v, depth + 1) for v in value]
    return value


class MaskedStream(io.TextIOBase):
    """A process's stdout or stderr with secrets taken out. Its logging, its tracebacks and every print pass through here on the way
    to a log file, and a log is read by whoever copies it to report a problem. Loose: a label with a value in a log is a credential."""

    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        self.stream.write(redact(text, loose=True))
        return len(text)

    def flush(self):
        self.stream.flush()

    def writable(self):
        return True

    def isatty(self):
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()

    @property
    def encoding(self):
        return self.stream.encoding

    def __getattr__(self, name):  # `buffer`, `reconfigure`: what a library may ask of the real stream.
        return getattr(self.stream, name)


def mask_this_process():
    """Before the logging is set up, in a process whose output goes to a log (`jobpilot serve`). A command someone runs by hand is left
    alone: `jobpilot token` must print the token."""
    if not isinstance(sys.stdout, MaskedStream):
        sys.stdout, sys.stderr = MaskedStream(sys.stdout), MaskedStream(sys.stderr)
