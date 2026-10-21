"""AccountStore: employer account records in the settings table."""

from urllib.parse import urlencode
from urllib.parse import urlsplit
import hashlib
import re
import secrets
from ..db import Setting
from ..db import now
from ..mail import origin
from .errors import AccountActionError
from .pages import CONFIRMED_ACCOUNT
from .tenants import SF_HOSTS
from .tenants import account_tenant
from .tenants import sf_company


class AccountStore:
    def __init__(self, service, email, job_url=""):
        self.service, self.email = service, email.strip().lower()
        self.job_url = job_url

    def alias_key(self):
        """The SuccessFactors tenant serving this job's career site (a public fact, never a credential)."""
        try:
            employer = (urlsplit(self.job_url).hostname or "").lower()
        except ValueError:
            return ""
        return "sf_tenant:" + origin(self.job_url) if employer and not employer.endswith(SF_HOSTS) else ""

    def observe(self, url):
        """Learn the career host and company= that serve this job from any SuccessFactors page that names them."""
        tenant, key = sf_company(url or ""), self.alias_key()
        if tenant and key:
            value = {"origin": tenant[0], "company": tenant[1]}
            if self.service.db.get_setting(key) != value:
                self.service.db.set_setting(key, value)

    def canonical(self, url):
        """One SuccessFactors account per career host and company=. Westinghouse signed in on pages with and without
        company= and the employer's own career site can show the login (2026-10-16/17); each must find the same
        record, so such pages are keyed by the tenant this job's site was seen to use."""
        key = self.alias_key()
        if not key or sf_company(url):
            return url
        try:
            sf_page = (urlsplit(url).hostname or "").lower().endswith(SF_HOSTS)
        except ValueError:
            return url
        if not sf_page and origin(url) != origin(self.job_url):
            return url
        alias = self.service.db.get_setting(key) or {}
        if not alias.get("company") or (sf_page and origin(url) != alias.get("origin")):
            return url
        return alias["origin"] + "/career?" + urlencode({"company": alias["company"]})

    def legacy_key(self, url):
        """Before 2026-10-18 a SuccessFactors page without company= saved the login under the employer's site
        (Capgemini, Bunge and Farmers each have both records)."""
        canonical = self.canonical(url)
        if not sf_company(canonical) or not self.job_url or origin(self.job_url) == origin(canonical):
            return None
        identity = origin(canonical) + ":" + self.email + ":employer:" + origin(self.job_url)
        return "employer_account:" + hashlib.sha256(identity.encode()).hexdigest()

    def record(self, url):
        """The tenant's account record (sealed values), the legacy employer-keyed one when no tenant record exists."""
        if account_tenant(self.canonical(url), self.job_url) == "unresolved_shared_tenant":
            return {}
        row = self.service.db.get_setting(self.key(url), {})
        if not row and (legacy := self.legacy_key(url)):
            row = self.service.db.get_setting(legacy, {})
        return row

    def known(self, url):
        """The employer is known to hold an account for this email on this tenant."""
        record = self.record(url)
        return bool(record.get("confirmed_at") or record.get("state") in CONFIRMED_ACCOUNT)

    def key(self, url):
        url = self.canonical(url)
        tenant = account_tenant(url, self.job_url)
        account_origin = origin(url)
        if account_origin == "https://login.icims.com" and tenant.startswith("employer:"):
            # The employer's classic profile and its Central login are the
            # same account. Preserve that employer's saved credentials.
            account_origin, tenant = tenant.removeprefix("employer:"), ""
        identity = account_origin + ":" + self.email + (":" + tenant if tenant else "")
        return "employer_account:" + hashlib.sha256(identity.encode()).hexdigest()

    def shared(self, *, max_length=None):
        key = "application_credentials:" + hashlib.sha256(self.email.encode()).hexdigest()
        configured = self.service.config.application_password
        with self.service.db.exclusive() as s:
            row = s.get(Setting, key)
            if row:
                value = self.service.mail.vault.open(row.value["credentials"])
                if configured and value.get("password") != configured:
                    # APPLICATION_PASSWORD/ACCOUNT_PASSWORD changed: new accounts and resets use it.
                    # Each employer keeps its own saved password until its reset succeeds.
                    value = {"email": self.email, "password": configured}
                    row.value = {
                        **row.value,
                        "previous_credentials": row.value["credentials"],
                        "credentials": self.service.mail.vault.seal(value),
                    }
                elif not configured and max_length is not None and 12 <= max_length < len(value["password"]):
                    # An automatically generated default must fit the observed
                    # creation rule. Retain the previous encrypted default and
                    # every employer's independently saved login.
                    previous = row.value["credentials"]
                    value = {"email": self.email, "password": self.generated(min(max_length, 16))}
                    row.value = {
                        **row.value,
                        "previous_credentials": previous,
                        "credentials": self.service.mail.vault.seal(value),
                    }
                return value
            password = configured or self.generated(min(max_length or 16, 16))
            value = {"email": self.email, "password": password}
            s.add(Setting(key=key, value={"credentials": self.service.mail.vault.seal(value)}))
            return value

    @staticmethod
    def generated(length):
        if length < 12:
            raise AccountActionError(
                "Account password maximum is too short for the shared password policy", "account"
            )
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        while True:
            password = "Ap9!" + "".join(secrets.choice(alphabet) for _ in range(length - 4))
            if not re.search(r"(.)\1{2,}", password):  # Dayforce ID refuses a character three times in a row
                return password

    def get(self, url):
        row = self.record(url)  # Never reuses ambiguous shared-origin legacy credentials.
        return self.service.mail.vault.open(row["credentials"]) if row.get("credentials") else None

    def icims_credentials(self, url):
        """The applicant's working login is used only on HTTPS iCIMS hosts."""
        try:
            location = urlsplit(url)
            host = location.hostname or ""
            trusted = (
                location.scheme == "https"
                and not location.username
                and not location.password
                and location.port in {None, 443}
                and (host == "icims.com" or host.endswith(".icims.com"))
            )
        except ValueError:
            return None
        from ..email_alias import base_address

        if base_address(self.email) != self.email:
            return (
                None  # The configured login belongs to the base identity, never to a platform plus-address.
            )
        password = getattr(self.service.config, "icims_login", "")
        return {"email": self.email, "password": password} if trusted and password else None

    def state(self, url):
        """The recorded account state (created_locally, authenticated, ...), never credentials."""
        return self.record(url).get("state")

    def remember(self, url, **secrets_by_name):
        """Seal extra account secrets (security answers) into this account's existing record."""
        record = self.record(url)
        if not record:
            return False
        sealed = {name: self.service.mail.vault.seal(value) for name, value in secrets_by_name.items()}
        self.service.db.set_setting(self.key(url), {**record, **sealed, "updated_at": now()})
        return True

    def save(self, url, credentials, state, *, previous=None, **extra):
        url = self.canonical(url)
        if account_tenant(url, self.job_url) == "unresolved_shared_tenant":
            raise AccountActionError("Shared ATS account tenant is not observed", "observation_failure")
        self.observe(url)
        current = dict(self.record(url))
        if state in CONFIRMED_ACCOUNT:
            current.pop("previous_credentials", None)  # The employer settled which login is current.
        # Proof that the account exists outlives later states: AMETEK's and Westinghouse's records said "signing_in"
        # after a failed retry, and the next pass created a second account (2026-10-16..18).
        confirmed = current.get("confirmed_at") or (
            now() if state in CONFIRMED_ACCOUNT or current.get("state") in CONFIRMED_ACCOUNT else None
        )
        self.service.db.set_setting(
            self.key(url),
            {
                **current,
                "origin": origin(url),
                "tenant": account_tenant(url, self.job_url),
                "credentials": self.service.mail.vault.seal(credentials),
                "state": state,
                "updated_at": now(),
                **({"confirmed_at": confirmed} if confirmed else {}),
                # The first replaced login is the one to restore; a retried Create must not overwrite it.
                **(
                    {"previous_credentials": self.service.mail.vault.seal(previous)}
                    if previous and "previous_credentials" not in current
                    else {}
                ),
                **extra,
            },
        )

    def previous(self, url):
        """The login a not-yet-confirmed Create replaced, to restore when the employer refuses that Create."""
        sealed = self.record(url).get("previous_credentials")
        return self.service.mail.vault.open(sealed) if sealed else None
