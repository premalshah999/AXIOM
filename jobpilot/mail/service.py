"""MailService, assembled from the mixins."""

from .mailboxes import MailboxesMixin
from .transport import TransportMixin
from .rules import RulesMixin
from .challenges import ChallengesMixin
from .polling import PollingMixin
from sqlalchemy import select
import asyncio
from ..db import Mailbox
from .vault import Vault


class MailService(MailboxesMixin, TransportMixin, RulesMixin, ChallengesMixin, PollingMixin):
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.vault = Vault(config)
        self.transport = None  # An injected transport is used only by owned fixtures.
        self.locks = {}
        self.live_requests = {}  # Mailbox id -> live verification requests in flight (background work waits).
        self.poll_lock = asyncio.Lock()
        self.message_cache = {}  # Immutable messages; secrets stay in memory, never in traces.
        self.poll_diagnostics = {}  # Counts and origins only; never message text or tokens.
        if config.gmail_address and config.gmail_app_password:
            email = config.gmail_address.strip().lower()
            with db.exclusive() as s:
                box = s.scalar(select(Mailbox).where(Mailbox.email == email))
                value = {"provider": "gmail_imap", "address": email}
                if not box:
                    s.add(Mailbox(email=email, credentials=self.vault.seal(value)))
                elif self.vault.open(box.credentials).get("provider") == "gmail_imap":
                    box.credentials, box.state, box.error = self.vault.seal(value), "connected", ""
