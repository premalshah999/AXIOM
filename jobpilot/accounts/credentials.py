"""Accounts: which credentials a run may use; the dashboard summary."""

from urllib.parse import urlsplit
import hashlib
from ..db import Setting
from ..inbox import host_matches
from .store import AccountStore
from .tenants import AUTH_HOSTS
from .tenants import registrable


class Accounts:
    """Adapter-facing view of the encrypted store: one shared login, one record per employer origin.

    States: created_locally (form submitted), authenticated, reset_requested, password_reset, locked."""

    def __init__(self, service, profile, job_url=""):
        from ..email_alias import base_address

        self.service, self.config, self.profile = service, service.config, profile
        configured = (self.config.account_email or "").strip().lower()
        own = (profile.email or "").strip().lower()
        # A platform's plus-address of the configured login (iCIMS: local+icims@gmail.com, see email_alias) is that
        # platform's login. With ACCOUNT_EMAIL set to the base address, iCIMS kept signing in to the old Central
        # identity whose reset nobody could finish (0 of 19 eligible, 2026-10-18), while the form used the alias.
        aliased = bool(
            configured and own and own != configured and base_address(own) == base_address(configured)
        )
        self.email = own if aliased else (configured or own)
        self.store = AccountStore(service, self.email, job_url) if self.email else None

    @property
    def ready(self):
        from ..agent_settings import enabled

        return bool(self.store) and enabled(self.service.db, "accounts")

    @property
    def may_create(self):
        return self.ready and bool(self.profile.allow_account_creation)

    def saved(self, url):
        return self.store.get(url) if self.store else None

    def record(self, url):
        return self.store.record(url) if self.store else {}

    def credentials(self, url):
        """The employer's saved login, else the shared one (same email + password everywhere)."""
        from ..agent_settings import require

        require(self.service.db, "accounts")
        saved = self.saved(url)
        if saved and self.record(url).get("state") in {"password_reset", "authenticated"}:
            # A password this app set by reset (or proved by signing in) beats a configured one that the
            # employer rejected (iCIMS 2026-10-17: the configured Central password was stale).
            return saved
        configured = self.icims_credentials(url)
        if configured:
            return configured
        return saved or self.store.shared()

    def icims_credentials(self, url):
        """The applicant's explicit working login, scoped to HTTPS iCIMS hosts."""
        return self.store.icims_credentials(url) if self.ready else None

    def alternative_credentials(self, url):
        """Try a newly configured password once before resetting an existing account."""
        if self.icims_credentials(url):
            return None  # The explicit working login is already the primary candidate.
        configured = self.config.application_password
        saved = self.saved(url)
        if configured and saved and saved["password"] != configured:
            return {"email": self.email, "password": configured}
        return None

    def credential_fingerprint(self, url, credentials=None):
        """Opaque identity for a rejected credential version; never log its value."""
        value = credentials or self.credentials(url)
        return hashlib.sha256((value["email"] + "\0" + value["password"]).encode()).hexdigest()

    def mark(self, url, state, credentials=None, **extra):
        self.store.save(url, credentials or self.credentials(url), state, **extra)

    def password_allowed(self, ats, frame_url, job_url):
        """Type the shared password only on the employer's site or its ATS's own auth hosts."""
        frame = (urlsplit(frame_url).hostname or "").lower()
        job = (urlsplit(job_url).hostname or "").lower()
        if urlsplit(frame_url).scheme != "https" and not self.config.allow_private_urls:
            return False
        if frame == job or registrable(frame) == registrable(job):
            return True
        return host_matches(frame, AUTH_HOSTS.get(ats, []))


def summary(service):
    """Employer account records for the dashboard; never credentials."""
    from sqlalchemy import select

    with service.db.session() as s:
        rows = [
            {
                "id": r.key,
                "origin": r.value.get("origin", ""),
                "tenant": r.value.get("tenant", ""),
                "state": r.value.get("state", ""),
                "updated_at": r.value.get("updated_at", ""),
                "reset_requested_at": r.value.get("reset_requested_at"),
            }
            for r in s.scalars(select(Setting).where(Setting.key.like("employer_account:%")))
        ]
    return {"accounts": sorted(rows, key=lambda r: r["origin"])}
