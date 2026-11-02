"""Accounts: which credentials a run may use; the dashboard summary."""

import re
from urllib.parse import urlsplit
import hashlib
from ..ats import platform_host
from ..db import Setting
from ..inbox import host_matches
from .store import AccountStore
from .tenants import AUTH_HOSTS
from .tenants import outside_sign_in
from .tenants import registrable


class Accounts:
    """Adapter-facing view of the encrypted store: one shared login, one record per employer origin.

    States: created_locally (form submitted), authenticated, reset_requested, password_reset, locked."""

    def __init__(self, service, profile, job_url=""):
        from ..email_alias import same_mailbox

        self.service, self.config, self.profile = service, service.config, profile
        configured = (self.config.account_email or "").strip().lower()
        own = (profile.email or "").strip().lower()
        # A platform's plus-address of the configured login (iCIMS: local+icims@gmail.com, see email_alias) is that
        # platform's login. With ACCOUNT_EMAIL set to the base address, iCIMS kept signing in to the old Central
        # identity whose reset nobody could finish (0 of 19 eligible, 2026-10-18), while the form used the alias.
        aliased = bool(
            configured and own and own != configured and same_mailbox(own, configured)
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
        if temporary := self.store.temporary(url):
            return temporary  # The employer's emailed temporary password: the very next sign-in uses it.
        saved = self.saved(url)
        if (
            saved
            and self.record(url).get("state") == "created_locally"
            and (urlsplit(self.store.job_url).hostname or "").endswith(".icims.com")
        ):
            # Atlassian's newly created employer profile has its own password; the configured iCIMS login
            # may belong to another employer. Try the saved credential through the ordinary rejection flow.
            return saved
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
        return password_host_allowed(ats, frame_url, job_url, allow_private=self.config.allow_private_urls)


def password_host_allowed(ats, frame_url, job_url, *, allow_private=False):
    """The employer's site or `ats`'s own auth hosts. Never an identity provider or an assessment vendor, even one on the job's own domain
    (an Apple posting's idmsa.apple.com), and never over plain HTTP."""
    frame = (urlsplit(frame_url).hostname or "").lower()
    job = (urlsplit(job_url).hostname or "").lower()
    if urlsplit(frame_url).scheme != "https" and not allow_private:
        return False
    if not frame or outside_sign_in(frame):
        return False
    if frame == job or registrable(frame) == registrable(job):
        return True
    name = registrable(frame).split(".")[0]
    if len(name) >= 4 and name == registrable(job).split(".")[0]:
        # The employer's own name under another domain: Amazon's amazon.jobs signs candidates in on
        # atozpublic.idprism-auth.amazon.com, where its reset code was refused as "an unexpected website" (2026-10-27).
        return True
    if host_matches(job, AUTH_HOSTS.get(ats, [])) and employer_domain_of_tenant(job, frame):
        return True
    return host_matches(frame, AUTH_HOSTS.get(ats, []))


def account_host_allowed(ats, frame_url, own_urls, *, company="", allow_private=False):
    """Whether the account flow may type a saved password here: an allow-rule, because a list of what to refuse never ends. The account
    flow runs on any site a page led to, so this asks more than `password_host_allowed` does of an adapter on its own platform: the
    employer's site by any address this job was given, a platform the registry names (a career site hands off to any of them), or a
    host carrying the employer's own name. `own_urls`: the addresses this job was given or opened at, never ones it walked to."""
    frame = (urlsplit(frame_url).hostname or "").lower()
    own = [str(url) for url in own_urls or () if url]
    if any(password_host_allowed(ats, frame_url, url, allow_private=allow_private) for url in own):
        return True
    if urlsplit(frame_url).scheme != "https" and not allow_private:
        return False
    if not frame or outside_sign_in(frame):
        return False
    if platform_host(frame) or any(host_matches(frame, hosts) for hosts in AUTH_HOSTS.values()):
        return True
    hosts = [(urlsplit(url).hostname or "").lower() for url in own]
    return employer_named_host(frame, company) or any(platform_host(job) and employer_domain_of_tenant(job, frame) for job in hosts)


# What an employer's own career site adds to its name: acmecareers.com, careers-acme.com, workatacme.com.
CAREER_WORDS = re.compile(r"careers?|jobs?|talent|recruit(?:ing|ment)?|hiring|apply|workat|joinus|join")


def employer_named_host(frame_host, company):
    """The host's own domain is the employer's name, alone or with a career site's words: a job board's link led on to the employer. A word
    in common is not its name ("Hire" is not hireright.com)."""
    from ..knowledge import employer_variants

    label = re.sub(r"[^a-z0-9]", "", registrable(frame_host).split(".")[0].lower())
    names = {re.sub(r"[^a-z0-9]", "", name.lower()) for name in employer_variants(company)}
    return bool(label) and bool(names & {label, CAREER_WORDS.sub("", label)})


def employer_domain_of_tenant(job_host, frame_host):
    """True when an ATS tenant host names the employer whose own domain the page moved to: Deloitte's Avature tenant
    deloitteus.avature.net signs candidates in on apply.deloitte.com, where the password was refused as "an unexpected
    website" and the run never registered (2026-10-26). The domain's name must start the tenant's first label (at least
    four letters), the way nvidia.wd5.myworkdayjobs.com names nvidia.com."""
    tenant = re.sub(r"^(?:careers?|jobs?|apply)[-.]", "", job_host.split(".")[0].lower())
    name = registrable(frame_host).split(".")[0].lower()
    return len(name) >= 4 and tenant.startswith(name)


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
