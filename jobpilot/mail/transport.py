"""Authenticated requests to the mail provider, with refresh, quarantine and message reading."""

import asyncio
import re
import time
from ..db import Mailbox
from ..db import now
from ..mail_errors import MailAuthenticationError
from ..mail_errors import MailCursorUpgrade
from ..mail_errors import MailMessageError
from ..mail_errors import MailOperationError
from ..mail_errors import MailTransportError
from .constants import API_URL
from .constants import TOKEN_URL
from .helpers import digest


# How long background mailbox work waits for live verification requests before it proceeds anyway. Workers poll
# codes almost continuously, so 30 s here let the receipt poller read about one message per 30 s: Lyft's two
# acknowledgements sat unread for hours (2026-10-25). Background reads have their own connections; the search they
# page is narrowed to acknowledgement wording (receipts.RECEIPT_SEARCH).
LIVE_PRECEDENCE_SECONDS = 3


class TransportMixin:
    async def request(self, mailbox_id, path, params=None, *, background=False):
        """background: receipt reconciliation. It reads over its own connections and waits while a live verification
        request for this mailbox is in flight, so a code's search never queues behind it."""
        live = self.live_requests
        if background:
            waited = time.monotonic()
            while live.get(mailbox_id) and time.monotonic() - waited < LIVE_PRECEDENCE_SECONDS:
                await asyncio.sleep(0.1)
            lock = self.locks.setdefault((mailbox_id, "background"), asyncio.Lock())
        else:
            lock = self.locks.setdefault(mailbox_id, asyncio.Lock())
            live[mailbox_id] = live.get(mailbox_id, 0) + 1
        try:
            return await self._request(lock, mailbox_id, path, params, "background" if background else "live")
        finally:
            if not background:
                live[mailbox_id] -= 1

    async def _request(self, lock, mailbox_id, path, params, lane):
        async with lock:
            with self.db.session() as s:
                box = s.get(Mailbox, mailbox_id)
                if not box or box.state != "connected":
                    raise ValueError("Reconnect the Gmail mailbox")
                stored = box.credentials
                token = self.vault.open(stored)
            key = (
                (mailbox_id, path)
                if re.fullmatch(r"/messages/[^/]+", path) and (params or {}).get("format") == "full"
                else None
            )
            if key and key in self.message_cache:
                cached_at, cached = self.message_cache[key]
                if time.monotonic() - cached_at < 180:
                    return cached

            def cache(result):
                if key:
                    self.message_cache[key] = (time.monotonic(), result)
                    while len(self.message_cache) > 200:
                        self.message_cache.pop(next(iter(self.message_cache)))
                return result

            if token.get("provider") == "gmail_imap":
                from ..gmail_imap import request

                # An app password entered in Setup is sealed with the mailbox; otherwise .env supplies it.
                password = token.get("app_password") or (
                    self.config.gmail_app_password
                    if token["address"] == self.config.gmail_address.strip().lower()
                    else ""
                )
                if not password:
                    raise ValueError("Restore GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env")
                where = "in Settings → Email" if token.get("app_password") else "in .env"
                try:
                    result = await asyncio.to_thread(
                        request,
                        token["address"],
                        "".join(password.split()),
                        path,
                        params,
                        lane,
                    )
                except MailAuthenticationError:
                    self.connection_error(
                        mailbox_id,
                        "reconnect_required",
                        f"Gmail login failed; check the app password {where}",
                    )
                    raise MailAuthenticationError(
                        f"Gmail login failed; check the app password {where}"
                    ) from None
                except MailMessageError as exc:
                    self.quarantine(mailbox_id, path.removeprefix("/messages/"), exc.reason)
                    raise
                except MailTransportError:
                    self.connection_error(mailbox_id, "connected", "Gmail connection interrupted; retrying")
                    raise
                except MailCursorUpgrade:
                    # An obsolete saved cursor, not a failure: the caller restarts its search.
                    # (It is a MailOperationError, so the handler below would swallow it, and the
                    # receipt poller then retried the same dead cursor on every pass.)
                    raise
                except (MailOperationError, ValueError, TypeError):
                    # A failed search/fetch is not evidence that the credentials were rejected.
                    self.connection_error(mailbox_id, "connected", "Mailbox operation failed; retry later")
                    raise MailOperationError("Mailbox operation failed; retry later") from None
                with self.db.exclusive() as s:
                    if box := s.get(Mailbox, mailbox_id):
                        box.checked_at, box.error = now(), ""
                return cache(result)
            async with self.client() as client:
                if token.get("expires_at", 0) < time.time() + 60:
                    r = await client.post(
                        TOKEN_URL,
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": token["refresh_token"],
                            "client_id": self.config.google_client_id,
                            "client_secret": self.config.google_client_secret,
                        },
                    )
                    if r.status_code != 200:
                        if r.status_code in {400, 401}:
                            self.connection_error(
                                mailbox_id,
                                "reconnect_required",
                                "Google authorization expired or was revoked",
                            )
                            raise MailAuthenticationError("Gmail token refresh failed")
                        raise MailTransportError("Gmail token refresh is temporarily unavailable")
                    token.update(r.json())
                    token["expires_at"] = time.time() + token.get("expires_in", 3600)
                    with self.db.exclusive() as s:
                        box = s.get(Mailbox, mailbox_id)
                        if not box or box.credentials != stored:
                            raise ValueError("Gmail connection changed; retry the operation")
                        box.credentials = self.vault.seal(token)
                r = await client.get(
                    API_URL + path,
                    params=params,
                    headers={"Authorization": "Bearer " + token["access_token"]},
                )
                if r.status_code == 401:
                    self.connection_error(
                        mailbox_id, "reconnect_required", "Gmail permission or API access needs attention"
                    )
                    raise MailAuthenticationError("Gmail authorization needs attention")
                if r.status_code == 404 and key:
                    self.quarantine(mailbox_id, path.removeprefix("/messages/"), "unavailable_message")
                    raise MailMessageError("unavailable_message")
                if r.status_code in {429, 500, 502, 503, 504}:
                    raise MailTransportError("Gmail is temporarily unavailable; retry later")
                if r.status_code == 403:
                    try:
                        reasons = {item.get("reason") for item in r.json().get("error", {}).get("errors", [])}
                    except (ValueError, TypeError, AttributeError):
                        reasons = set()
                    if reasons & {"insufficientPermissions", "accessNotConfigured", "forbidden"}:
                        self.connection_error(
                            mailbox_id, "reconnect_required", "Gmail permission or API access needs attention"
                        )
                        raise MailAuthenticationError("Gmail permissions need attention")
                    raise MailOperationError("Gmail request is temporarily blocked; retry later")
                if r.status_code != 200:
                    raise MailOperationError("Gmail request failed; retry later")
                with self.db.exclusive() as s:
                    box = s.get(Mailbox, mailbox_id)
                    if box:
                        box.checked_at, box.error = now(), ""
                return cache(r.json())

    def quarantine(self, mailbox_id, message_id, reason):
        """A bad message cannot disconnect a mailbox or repeatedly monopolize polling."""
        self.db.set_setting(
            "mail_quarantine:" + digest(mailbox_id + ":" + message_id),
            {"reason": reason, "at": now(), "until": time.time() + 86400},
        )

    async def read_message(self, mailbox_id, message_id, *, background=False):
        quarantined = self.db.get_setting("mail_quarantine:" + digest(mailbox_id + ":" + message_id), {})
        if quarantined.get("until", 0) > time.time():
            return None
        try:
            return await self.request(
                mailbox_id, "/messages/" + message_id, {"format": "full"}, background=background
            )
        except MailMessageError:
            return None
        except (TypeError, RecursionError):
            self.quarantine(mailbox_id, message_id, "invalid_message")
            return None
