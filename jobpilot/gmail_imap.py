"""Read-only Gmail adapter. The app never sends mail or marks messages read."""

import base64
import email
import hashlib
import imaplib
import re
import ssl
import threading
import time
from datetime import datetime
from email import policy

from .mail_errors import (
    MailAuthenticationError,
    MailCursorUpgrade,
    MailMessageError,
    MailOperationError,
    MailTransportError,
)


def mime_payload(message, depth=0):
    if depth > 15:
        raise MailMessageError("invalid_mime")
    result = {
        "mimeType": message.get_content_type(),
        "filename": message.get_filename() or "",
        "headers": [{"name": k, "value": str(v)} for k, v in message.items()],
    }
    if message.get_content_disposition() == "attachment" or message.get_content_type() == "message/rfc822":
        return result
    if message.is_multipart():
        result["parts"] = [mime_payload(p, depth + 1) for p in message.iter_parts()]
    elif message.get_content_type() in {"text/plain", "text/html"}:
        content = message.get_payload(decode=True) or b""
        text = content.decode(message.get_content_charset() or "utf-8", "replace")
        result["body"] = {"data": base64.urlsafe_b64encode(text.encode()).decode()}
    return result


# Logged-in connections per mailbox instead of a login per request: one per folder, each selected once. Every IMAP
# command took ~1.9 s here (2026-10-18), and re-SELECTing between Spam, Trash and All Mail on one connection made each
# search 7+ commands; a verification code waited 46 s (median) per mailbox scan. MailService serializes requests per
# mailbox; the lock also covers direct callers. A dropped connection is replaced once.
# Receipt reconciliation reads its own connections ("background" lane): one receipt request can run several commands,
# and a verification code search queued behind a receipt sweep for 248 s while its 120 s window expired (2026-10-25).
_clients = {}
_locks = {"live": threading.Lock(), "background": threading.Lock()}
# A folder refreshed this recently already reports new mail; its next search skips the NOOP (one sweep's searches).
FRESH_SECONDS = 5.0
# Spam is searched on every Nth listing (verification mail rarely lands there; each folder costs two commands).
SPAM_EVERY = 5


class _Pool:
    """One connection per folder for a mailbox, each selected read-only once."""

    def __init__(self, address, password):
        self.address, self.password, self.conns, self.listings = address, password, {}, 0

    def control(self):
        if None not in self.conns:
            self.conns[None] = _connect(self.address, self.password)
        return self.conns[None]

    def folder(self, name):
        conn = self.conns.get(name)
        if conn is None:
            conn = _connect(self.address, self.password)
            status, _ = conn.select(name, readonly=True)
            if status != "OK":
                _close(conn)
                raise MailOperationError("Could not open Gmail folder")
            conn._jp_selected, conn._jp_refreshed = name, time.monotonic()
            self.conns[name] = conn
        elif time.monotonic() - getattr(conn, "_jp_refreshed", 0) > FRESH_SECONDS:
            conn.noop()  # The open folder reports new mail without a re-SELECT.
            conn._jp_refreshed = time.monotonic()
        return conn

    def list(self):
        return self.control().list()

    def close(self):
        for conn in self.conns.values():
            _close(conn)
        self.conns.clear()


# Gmail's own words for a rejected address or app password. Any other refusal of LOGIN ("Too many simultaneous
# connections", "[UNAVAILABLE] Temporary System Problem") is transient: read as a bad password, one refusal after the
# Mac woke on battery disabled the mailbox for five hours and every email verification failed (2026-10-22).
CREDENTIALS_REJECTED = re.compile(
    r"AUTHENTICATIONFAILED|invalid credentials|application-specific password|web login required|username and password",
    re.I,
)


def _connect(address, password):
    client = imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ssl.create_default_context(), timeout=30)
    try:
        client.login(address, password)
    except imaplib.IMAP4.error as exc:
        _close(client)
        if CREDENTIALS_REJECTED.search(str(exc)):
            raise MailAuthenticationError("Gmail authentication failed; check the saved app password") from None
        raise MailTransportError("Gmail refused the login for now; retry later") from None
    return client


def login_ok(address, password):
    """Whether Gmail accepts this login right now (one short connection, no mailbox access)."""
    try:
        _close(_connect(address, password))
        return True
    except (MailAuthenticationError, MailTransportError, OSError, imaplib.IMAP4.abort):
        return False


def _close(client):
    try:
        client.logout()
    except Exception:
        pass


def request(address, password, path, params=None, lane="live"):
    key = (address, hashlib.sha256(password.encode()).hexdigest(), lane)
    with _locks[lane]:
        for attempt in range(2):
            pool = _clients.pop(key, None) or _Pool(address, password)
            try:
                result = _request(pool, address, path, params or {})
            except (imaplib.IMAP4.abort, OSError):
                pool.close()
                if not attempt:
                    continue  # Reconnect once, including an initial transient failure.
                raise MailTransportError("Gmail connection was interrupted; retry later") from None
            except imaplib.IMAP4.error:
                pool.close()
                raise MailOperationError("Gmail could not complete the mailbox operation") from None
            except MailMessageError:
                _clients[key] = pool
                raise
            except Exception:
                pool.close()
                raise
            _clients[key] = pool
            return result
    raise MailTransportError("Gmail connection was interrupted; retry later")


def _quoted(value):
    # Names come from LIST, not from callers. Preserve modified UTF-7 and escape
    # IMAP quoted-string characters; never pass a server name as command syntax.
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise MailOperationError("Invalid Gmail folder name")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _folders(client):
    cached = getattr(client, "_jp_folders", None)
    if cached:
        return cached  # LIST took ~1.3 s on every request (2026-10-14 mail_poll 20-90 s).
    status, data = client.list()
    if status != "OK":
        raise MailOperationError("Could not list Gmail folders")
    folders = ['"INBOX"']
    for item in data or []:
        if not isinstance(item, bytes):
            continue
        match = re.fullmatch(rb'\(([^)]*)\) (?:"(?:[^"\\]|\\.)*"|NIL) (.+)', item)
        if not match:
            raise MailOperationError("Invalid Gmail folder metadata")
        flags = match[1].lower().split()
        # The app never deletes mail, so Trash holds nothing it waits for.
        if b"\\noselect" in flags or b"\\trash" in flags or not {b"\\all", b"\\junk", b"\\spam"}.intersection(flags):
            continue
        try:
            name = match[2].decode("ascii")
        except UnicodeError:
            raise MailOperationError("Invalid Gmail folder name") from None
        if name.startswith('"'):
            if not re.fullmatch(r'"(?:[^"\\]|\\["\\])*"', name):
                raise MailOperationError("Invalid Gmail folder name")
            name = re.sub(r'\\(["\\])', r"\1", name[1:-1])
        folders.append(_quoted(name))
    folders = list(dict.fromkeys(folders))
    client._jp_folders = folders
    return folders


# Gmail message id -> (folder, uid) from the latest search on this process's connection, so a
# message fetch opens its folder directly instead of searching every folder again (~2.5 s each).
_where = {}
# (folder, uid) -> Gmail message id. UIDs are stable while a folder's UIDVALIDITY holds; a stale entry is caught by
# the X-GM-MSGID check on fetch.
_identity = {}


def _search_folders(client):
    """INBOX mail is always in All Mail; searching both doubled every poll. A pool searches Spam on every
    SPAM_EVERY-th listing."""
    folders = _folders(client)
    rest = [f for f in folders if f != '"INBOX"']
    if not any("all" in f.lower() for f in rest):
        return folders
    if isinstance(client, _Pool):
        client.listings += 1
        if client.listings % SPAM_EVERY != 1:
            rest = [f for f in rest if "all" in f.lower()]
    # All Mail last: it holds nearly every hit, so it stays open for the fetches that follow
    # (re-opening it costs ~3 s each time).
    return sorted(rest, key=lambda f: "all" in f.lower())


def _select(client, folder):
    """The connection to use for this folder: a pool's own folder connection, or the one client re-selected."""
    if isinstance(client, _Pool):
        return client.folder(folder)
    if getattr(client, "_jp_selected", None) == folder:
        if noop := getattr(client, "noop", None):
            noop()  # The open folder reports new mail without a ~2.5 s re-SELECT.
        return client
    status, _ = client.select(folder, readonly=True)
    if status != "OK":
        client._jp_selected = None
        raise MailOperationError("Could not open Gmail folder")
    client._jp_selected = folder
    return client


def _search(client, *criteria):
    status, data = client.uid("search", None, *criteria)
    if status != "OK":
        raise MailOperationError("Gmail search failed")
    ids = (data[0] or b"").decode("ascii").split()
    if any(not re.fullmatch(r"[1-9][0-9]*", uid) for uid in ids):
        raise MailOperationError("Invalid Gmail search result")
    return ids


def _request(client, address, path, params):
    if path == "/profile":
        # A real login: "Connect Gmail" and the connection check call this to verify the app password, and answering
        # without one accepted any 16 letters as connected (found driving the sign-up guide, 2026-10-31). The login
        # is the control connection the pool keeps, so a later mailbox request reuses it.
        client.control()
        return {"emailAddress": address}
    if path == "/messages":
        # Raw Gmail queries otherwise exclude Spam. Selected folders still
        # bound the UID space; All Mail, Spam and Trash cover Gmail's mailbox.
        query = "in:anywhere " + str(params.get("q", "newer_than:1d"))
        # Quote the IMAP search value as a string; never permit injected search commands.
        query = (
            '"' + query.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ") + '"'
        )
        token = params.get("pageToken")
        before = None
        if token:
            if re.fullmatch(r"imap-before:[1-9][0-9]*", str(token)):
                raise MailCursorUpgrade("Restart Gmail search with immutable message identities")
            match = re.fullmatch(r"gm-before:([1-9][0-9]*)", str(token))
            if not match:
                raise MailOperationError("Invalid Gmail page token")
            before = int(match[1])
        ids = set()
        for folder in _search_folders(client):
            conn = _select(client, folder)
            uids = _search(conn, "X-GM-RAW", query)
            # Polls repeat the same window every few seconds: identities already fetched need no FETCH (~2 s each).
            known = {uid: _identity[(folder, uid)] for uid in uids if (folder, uid) in _identity}
            for uid, mid in known.items():
                ids.add(mid)
                _where[mid] = (folder, uid)
            uids = [uid for uid in uids if uid not in known]
            for start in range(0, len(uids), 100):
                batch = uids[start : start + 100]
                status, data = conn.uid("fetch", ",".join(batch), "(UID X-GM-MSGID)")
                if status != "OK":
                    raise MailOperationError("Gmail message identity lookup failed")
                for item in data or []:
                    metadata = item[0] if isinstance(item, tuple) else item
                    if not isinstance(metadata, bytes):
                        continue
                    uid = re.search(rb"\bUID ([1-9][0-9]*)\b", metadata)
                    mid = re.search(rb"\bX-GM-MSGID ([1-9][0-9]*)\b", metadata)
                    if not uid or not mid or uid[1].decode() not in batch:
                        raise MailOperationError("Invalid Gmail message identity metadata")
                    ids.add(mid[1].decode())
                    _where[mid[1].decode()] = (folder, uid[1].decode())
                    _identity[(folder, uid[1].decode())] = mid[1].decode()
        if len(_where) > 5000:
            _where.clear()
        if len(_identity) > 5000:
            _identity.clear()
        ids = sorted((mid for mid in ids if before is None or int(mid) < before), key=int, reverse=True)
        limit = max(1, min(int(params.get("maxResults", 20)), 50))
        page = ids[:limit]
        result = {"messages": [{"id": "gm:" + mid} for mid in page]}
        if len(ids) > limit:
            result["nextPageToken"] = "gm-before:" + page[-1]
        return result
    match = re.fullmatch(r"/messages/gm:([1-9][0-9]*)", path)
    if not match:
        if re.fullmatch(r"/messages/[1-9][0-9]*", path):
            raise MailCursorUpgrade("Restart Gmail search with immutable message identities")
        raise MailOperationError("Invalid Gmail message ID")
    message_id = match[1]
    uid = None
    conn = client
    legacy_inbox_ids = []
    known = _where.get(message_id)
    direct = bool(known and isinstance(client, _Pool))
    if direct:
        # The latest search mapped this message here; the FETCH below verifies its X-GM-MSGID.
        conn, uid = _select(client, known[0]), known[1]
    elif known:
        conn = _select(client, known[0])
        if _search(conn, "X-GM-MSGID", message_id) == [known[1]]:
            uid = known[1]
        else:
            _where.pop(message_id, None)
    for folder in [] if uid else _folders(client):
        conn = _select(client, folder)
        uids = _search(conn, "X-GM-MSGID", message_id)
        if len(uids) > 1:
            raise MailMessageError("message_identity")
        if uids:
            uid = uids[0]
            if folder == '"INBOX"':
                legacy_inbox_ids.append(uid)
            break
    if uid is None:
        raise MailMessageError("unavailable_message")
    # The partial BODY.PEEK bounds the transfer itself, so metadata and the
    # read-only body can arrive together without downloading oversized mail.
    status, data = conn.uid(
        "fetch", uid, "(UID X-GM-MSGID INTERNALDATE RFC822.SIZE BODY.PEEK[]<0.1000001>)"
    )
    item = next((x for x in data if isinstance(x, tuple)), None)
    metadata = item[0] if item else b" ".join(x for x in data if isinstance(x, bytes))
    size = re.search(rb"RFC822.SIZE (\d+)", metadata)
    if status != "OK" or not item:
        if direct:
            _where.pop(message_id, None)
            return _request(client, address, path, params)
        raise MailMessageError("unavailable_message")
    if not size or int(size[1]) > 1_000_000:
        raise MailMessageError("oversized_message")
    fetched_uid = re.search(rb"\bUID ([1-9][0-9]*)\b", metadata)
    fetched_id = re.search(rb"\bX-GM-MSGID ([1-9][0-9]*)\b", metadata)
    if (
        not fetched_uid
        or fetched_uid[1].decode() != uid
        or not fetched_id
        or fetched_id[1].decode() != message_id
    ):
        if direct:
            _where.pop(message_id, None)  # A stale mapping: find the message by its identity instead.
            return _request(client, address, path, params)
        raise MailMessageError("message_identity")
    date_match = re.search(rb'INTERNALDATE "([^"]+)"', item[0])
    if not date_match:
        raise MailMessageError("missing_delivery_date")
    try:
        received = datetime.strptime(date_match[1].decode(), "%d-%b-%Y %H:%M:%S %z")
    except (ValueError, UnicodeError):
        raise MailMessageError("invalid_delivery_date") from None
    try:
        msg = email.message_from_bytes(item[1], policy=policy.default)
        payload = mime_payload(msg)
    except (ValueError, LookupError, TypeError, RecursionError):
        raise MailMessageError("invalid_mime") from None
    return {
        "id": "gm:" + message_id,
        "legacyInboxIds": legacy_inbox_ids,
        "internalDate": str(int(received.timestamp() * 1000)),
        "payload": payload,
    }
