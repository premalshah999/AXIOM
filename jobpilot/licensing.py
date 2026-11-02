"""The owner's license key: one key per Mac, signed offline, checked offline.

How it works (docs/LICENSING.md):

- This Mac has a short code derived from its hardware UUID (hashed, so the UUID itself is never shared). The person sends it to
  the owner; the owner signs a key for that code and an expiry date with an Ed25519 private key only the owner holds
  (scripts/axiom_license.py); the app checks the signature with the public key bundled in the build.
- No network is used anywhere in this module. Nothing is sent, nothing is fetched.
- It is OFF unless the build says otherwise (`license_build.json` next to this file, written by desktop/build.sh when
  AXIOM_LICENSE=on). Off means every function here lets everything through and the screen never appears.
- An expired or missing key stops NEW applications from starting (`require_license`, called by queue admission and by the claim
  of a queued run). It never touches an application that is already running, a continuation of one, or the person's data.
- The latest moment this Mac has seen is remembered, so setting the clock back does not bring an expired key back to life.

What it cannot do, said plainly: it cannot revoke a key (nothing is checked online, so a key works until its date), and it cannot
stop someone who edits the Python or deletes the build file. It keeps honest copies honest.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

KEY_PREFIX = "AXIOM1-"
# Signed bytes are domain-separated, so a signature made for anything else can never pass as a key.
SIGNED_PREFIX = b"axiom-license-v1\n"
MAC_SALT = b"axiom-license-mac-v1:"
BUILD_FILE = Path(__file__).with_name("license_build.json")
# Re-read the key and the clock at most this often: the claim of a queued run asks every second.
CACHE_SECONDS = 20
# The latest-seen moment is written at most this often (the expiry is a whole day; ten minutes is far finer).
SEEN_WRITE_SECONDS = 600
# More than this behind the latest moment seen is called out on the screen as a clock that was set back.
CLOCK_NOTE_AFTER = timedelta(days=1)
_MAC_CHARS = re.compile(r"^[A-Z2-7]{12}$")


class LicenseError(ValueError):
    """Every message is shown to the person as it is: plain words, no codes."""


class KeyExpired(LicenseError):
    """A genuine key for this Mac whose date has passed (`key` is what it said)."""

    def __init__(self, message, key):
        super().__init__(message)
        self.key = key


# --- the Mac's code -------------------------------------------------------------------------------------------------------

_uuid_cache: str | None = None


def hardware_uuid() -> str:
    """The Mac's hardware UUID, from the system (ioreg: local, no network). Elsewhere (development) the machine id."""
    global _uuid_cache
    if _uuid_cache:
        return _uuid_cache
    found = ""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=10, check=False, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            ).stdout
            if match := re.search(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]{16,})"', out):
                found = match.group(1)
        except (OSError, subprocess.SubprocessError):
            found = ""
    else:
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                found = Path(path).read_text().strip()
            except OSError:
                continue
            if found:
                break
    if not found:
        raise LicenseError("AXIOM could not read this Mac's identity, so it cannot make a code for it.")
    _uuid_cache = found
    return found


def mac_code_for(uuid: str) -> str:
    """`ABCD-EFGH-JKLM`: 60 bits of a SHA-256 of the UUID, so the UUID cannot be read back from it."""
    digest = hashlib.sha256(MAC_SALT + uuid.strip().lower().encode()).digest()
    letters = base64.b32encode(digest).decode()[:12]
    return "-".join(letters[i : i + 4] for i in range(0, 12, 4))


def normalize_mac_code(text: str) -> str:
    """What a person typed or pasted, as the code: any case, with or without dashes and spaces."""
    letters = re.sub(r"[\s-]+", "", text or "").upper()
    if not _MAC_CHARS.match(letters):
        raise LicenseError("That is not a Mac code. It looks like ABCD-EFGH-JKLM (12 letters and numbers).")
    return "-".join(letters[i : i + 4] for i in range(0, 12, 4))


def machine_code() -> str:
    return mac_code_for(hardware_uuid())


# --- keys -----------------------------------------------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class Key:
    mac: str
    expires: date
    issued: date
    name: str = ""


def make_key(private: Ed25519PrivateKey, mac: str, expires: date, issued: date, name: str = "") -> str:
    """Signs a key for one Mac code. Only the owner's command calls this; the app itself never signs anything."""
    payload = {
        "v": 1,
        "mac": normalize_mac_code(mac),
        "exp": expires.isoformat(),
        "iss": issued.isoformat(),
        "name": name.strip()[:60],
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return KEY_PREFIX + _b64(body) + "." + _b64(private.sign(SIGNED_PREFIX + body))


def read_key(text: str, public_key: Ed25519PublicKey) -> Key:
    """The key's contents once its signature is proved. Raises LicenseError in plain words; knows nothing about this Mac or
    today's date (check_key does)."""
    token = re.sub(r"\s+", "", text or "")
    if not token.startswith(KEY_PREFIX) or token.count(".") != 1:
        raise LicenseError("That doesn't look like an AXIOM key. Paste the whole key, starting with AXIOM1-.")
    body_text, _, signature_text = token[len(KEY_PREFIX) :].partition(".")
    try:
        body, signature = _unb64(body_text), _unb64(signature_text)
    except (binascii.Error, ValueError):
        raise LicenseError("That doesn't look like an AXIOM key. It may have been cut short when it was copied.") from None
    try:
        public_key.verify(signature, SIGNED_PREFIX + body)
    except InvalidSignature:
        raise LicenseError(
            "This key is not genuine. It was not signed by AXIOM's owner, or it was changed after it was signed."
        ) from None
    try:
        payload = json.loads(body)
        if payload["v"] != 1:
            raise LicenseError("This key is from a newer AXIOM. Update the app, then paste it again.")
        return Key(
            mac=normalize_mac_code(payload["mac"]),
            expires=date.fromisoformat(payload["exp"]),
            issued=date.fromisoformat(payload["iss"]),
            name=str(payload.get("name", ""))[:60],
        )
    except LicenseError:
        raise
    except (ValueError, KeyError, TypeError):
        raise LicenseError("That key is signed but unreadable. Ask for a new one.") from None


def nice_date(day: date) -> str:
    return f"{day:%B} {day.day}, {day.year}"


def check_key(text: str, public_key: Ed25519PublicKey, mac: str, today: date) -> Key:
    """Signature first (a forged key can claim nothing), then the Mac, then the date."""
    key = read_key(text, public_key)
    if key.mac != mac:
        raise LicenseError(
            f"This key was made for a different Mac (code {key.mac}). This Mac's code is {mac}. "
            "Send this Mac's code to whoever gave you the key, and ask for one made for it."
        )
    if key.expires < today:
        raise KeyExpired(f"This key ran out on {nice_date(key.expires)}. Ask for a new one.", key)
    return key


# --- the build setting ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Build:
    required: bool = False
    public_key: str = ""  # Base64 of the 32 raw bytes.


def load_build() -> Build:
    """Whether this build requires a key. The file beside this module is the build setting: absent means off, so a build made
    without AXIOM_LICENSE=on and every checkout behave exactly as before. AXIOM_LICENSE_BUILD names a file to use instead, for
    development only, and is ignored when the build brought its own."""
    path = BUILD_FILE
    if not path.exists() and os.environ.get("AXIOM_LICENSE_BUILD"):
        path = Path(os.environ["AXIOM_LICENSE_BUILD"])
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return Build()
    if not isinstance(data, dict) or data.get("required") is not True:
        return Build()
    return Build(True, str(data.get("public_key", "")))


def public_key_of(text: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(text))


# --- this installation's license ------------------------------------------------------------------------------------------


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.replace(temporary, path)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Licensing:
    """The license of one installation (one data folder). `status()` is cheap enough to ask every second."""

    def __init__(self, data_dir, clock=None):
        self.folder = Path(data_dir) / "license"
        self.clock = clock or _utcnow
        self._lock = threading.Lock()
        self._cached: tuple[datetime, dict] | None = None

    # -- files
    @property
    def key_path(self) -> Path:
        return self.folder / "key.txt"

    @property
    def seen_path(self) -> Path:
        return self.folder / "seen.json"

    def _seen(self) -> dict:
        try:
            data = json.loads(self.seen_path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _remember(self, now: datetime, *, activated: bool | None = None) -> None:
        """Keep the latest moment this Mac has seen, and whether a valid key was ever accepted here."""
        seen = self._seen()
        latest = _parse_moment(seen.get("latest"))
        was_activated = bool(seen.get("activated"))
        want_activated = was_activated or bool(activated)
        stale = latest is None or (now - latest).total_seconds() >= SEEN_WRITE_SECONDS
        if not stale and want_activated == was_activated:
            return
        newest = max(now, latest) if latest else now
        try:
            _write_private(self.seen_path, json.dumps({"latest": newest.isoformat(), "activated": want_activated}))
        except OSError:
            pass  # A read-only disk must not stop the app; the clock memory is best effort.

    # -- the answer
    def status(self) -> dict:
        now = self.clock()
        with self._lock:
            if self._cached and (now - self._cached[0]).total_seconds() < CACHE_SECONDS:
                return self._cached[1]
            result = self._compute(now)
            self._cached = (now, result)
            return result

    def _compute(self, wall: datetime) -> dict:
        build = load_build()
        if not build.required:
            return {
                "required": False, "state": "off", "starts_allowed": True, "blocking": False, "activated": False,
                "code": "", "reason": "", "expires": None, "days_left": None, "name": "", "clock_note": "",
            }
        seen = self._seen()
        latest = _parse_moment(seen.get("latest"))
        activated = bool(seen.get("activated"))
        # The clock can only ever move forward as far as this license is concerned.
        now = max(wall, latest) if latest else wall
        note = ""
        if latest and wall < latest - CLOCK_NOTE_AFTER:
            note = (
                f"This Mac's clock is behind the last date AXIOM saw ({nice_date(latest.astimezone().date())}). "
                "Keys are counted from that later date until the clock catches up."
            )
        result = {
            "required": True, "state": "none", "starts_allowed": False, "blocking": False, "activated": activated,
            "code": "", "reason": "", "expires": None, "days_left": None, "name": "", "clock_note": note,
        }
        try:
            code = machine_code()
        except LicenseError as exc:
            result.update(state="none", reason=str(exc), blocking=True)
            return result
        result["code"] = code
        today = now.astimezone().date()
        try:
            text = self.key_path.read_text()
        except OSError:
            text = ""
        if not text.strip():
            result.update(
                state="none", blocking=not activated,
                reason="AXIOM needs a key to start new applications. Send this Mac's code to the person who gave you AXIOM.",
            )
        else:
            try:
                key = check_key(text, public_key_of(build.public_key), code, today)
            except KeyExpired as exc:
                result.update(
                    state="expired", reason=str(exc), expires=exc.key.expires.isoformat(), name=exc.key.name,
                    days_left=(exc.key.expires - today).days,
                )
            except LicenseError as exc:
                result.update(state="invalid", reason=str(exc), blocking=not activated)
            else:
                result.update(
                    state="valid", starts_allowed=True, reason="", expires=key.expires.isoformat(),
                    days_left=(key.expires - today).days, name=key.name, activated=True,
                )
                self._remember(now, activated=True)
                return result
        self._remember(now)
        return result

    def install(self, text: str) -> dict:
        """Check a pasted key against this Mac and today, then keep it. A refused key is not kept and changes nothing."""
        build = load_build()
        if not build.required:
            raise LicenseError("This copy of AXIOM does not ask for a key.")
        wall = self.clock()
        seen = self._seen()
        latest = _parse_moment(seen.get("latest"))
        now = max(wall, latest) if latest else wall
        code = machine_code()
        check_key(text, public_key_of(build.public_key), code, now.astimezone().date())
        _write_private(self.key_path, re.sub(r"\s+", "", text) + "\n")
        self._remember(max(now, wall), activated=True)
        with self._lock:
            self._cached = None
        return self.status()

    def allows_new(self) -> tuple[bool, str]:
        """(may a new application start, why not in plain words)."""
        status = self.status()
        if status["starts_allowed"]:
            return True, ""
        if status["state"] == "expired":
            return False, (
                "Your AXIOM key has run out, so new applications are paused. Ones already started finish, and your data is "
                "untouched. Enter a new key in AXIOM to carry on."
            )
        return False, "AXIOM needs a valid key to start new applications. Enter one in AXIOM to carry on."


def _parse_moment(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


_instances: dict[Path, Licensing] = {}
_instances_lock = threading.Lock()


def licensing(data_dir) -> Licensing:
    """The one `Licensing` of a data folder in this process."""
    path = Path(data_dir).resolve()
    with _instances_lock:
        if path not in _instances:
            _instances[path] = Licensing(path)
        return _instances[path]


def require_license(data_dir, *, continuing: bool = False) -> None:
    """The gate for NEW applications. `continuing` is an application already started (a retry of it, an answered question, a
    matched email, the check of an earlier submission): it is never stopped by a key, only a fresh start is. Deferred, so what
    asked keeps its place and starts once a key is entered (the same way Stop holds it)."""
    if continuing:
        return
    allowed, why = licensing(data_dir).allows_new()
    if not allowed:
        from .admission import QueueBlocked, temporary_time

        raise QueueBlocked(why, outcome="deferred", code="license", not_before=temporary_time())
