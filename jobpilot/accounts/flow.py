"""AccountFlow, assembled from the mixins."""

from .flow_dayforce import FlowDayforceMixin
from .flow_gates import FlowGatesMixin
from .flow_handle import FlowHandleMixin
from .store import AccountStore


class AccountFlow(FlowDayforceMixin, FlowGatesMixin, FlowHandleMixin):
    def __init__(self, engine):
        self.e = engine
        self.store = AccountStore(
            engine.service,
            engine.service.config.account_email or engine.form.resolver.profile.email,
            getattr(engine, "job", {}).get("url", ""),
        )
        self.secret_fields = []
        self.attempts = set()
        self.policy_links = set()
        self.guest_chosen = False
