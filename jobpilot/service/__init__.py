"""`Service`: the application's coordinator, assembled from one mixin per concern.

Import `Service`, `ACTIVE`, `RETRYABLE`, `TERMINAL` and `QueueBlocked` from here; the mixins are an internal split of what
used to be one 1,600-line class.
"""

from ..admission import QueueBlocked
from .completion import CompletionMixin
from .execution import ExecutionMixin
from .migrations import MigrationsMixin
from .queueing import QueueingMixin
from .reviews import ReviewsMixin
from .states import ACTIVE, RETRYABLE, TERMINAL


class Service(QueueingMixin, ExecutionMixin, CompletionMixin, ReviewsMixin, MigrationsMixin):
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.dispatch = None
        self.notification_tasks = set()
        from ..inbox import Inbox
        from ..mail import MailService

        self.mail = MailService(db, config)
        self.inbox = Inbox(self)
        self.migrate_reviewed_answers()
        from ..grandfather import grandfather_preferences

        grandfather_preferences(db)
        self.migrate_legacy()
        self.relabel_custom_ats()
        from ..features._store import create_tables

        create_tables(db.engine)


__all__ = ["ACTIVE", "RETRYABLE", "TERMINAL", "QueueBlocked", "Service"]
