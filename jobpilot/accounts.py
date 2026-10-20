"""Account credentials stay outside model prompts and answer ledgers."""

import asyncio
import hashlib
import secrets
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from .db import Setting, now
from .mail import origin


SF_HOSTS = (".successfactors.com", ".successfactors.eu", ".sapsf.com")
# States that prove the employer holds an account for this email. "signing_in" and "created_locally" prove nothing.
CONFIRMED_ACCOUNT = {"authenticated", "password_reset", "exists", "locked", "verification_pending"}
# A Create refused because the email is registered (AMETEK 2026-10-18: "Account Already Exists"). Prompts such as
# "Already have an account?" on SuccessFactors' sign-in page are not refusals.
ACCOUNT_EXISTS = re.compile(
    r"account (?:already )?exists|already (?:exists|registered|in use|taken)|"
    r"(?:e-?mail|user ?name)(?: address)? (?:is )?already (?:in use|registered|taken|exists)",
    re.I,
)


def sf_company(url):
    """The SuccessFactors tenant a page names, as (career host origin, company), else None. Westinghouse's sign-in
    page carries company= and career_company= (2026-10-16); its portal pages carry neither."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not (parts.hostname or "").lower().endswith(SF_HOSTS):
        return None
    query = parse_qs(parts.query)
    for name in ("company", "career_company"):
        values = query.get(name, [])
        if len(values) == 1 and values[0]:
            return origin(url), values[0]
    return None


PASSWORD_MAXIMUM = re.compile(
    r"password (?:must |should )?(?:not be longer than|must not exceed|cannot exceed|maximum(?: length)?(?: is| of)?|"
    r"max(?:imum)? length[: ]+)\s*(\d{1,3})\s*characters",
    re.I,
)


class AccountActionError(ValueError):
    def __init__(self, message, blocker_type):
        self.blocker_type = blocker_type
        super().__init__(message)


def account_tenant(url, job_url=""):
    """Only explicit tenant identities or the originating employer site scope shared hosts."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "login.icims.com":
        # Central is a shared authentication host, not a shared employer
        # account. A different tenant's rejection/reset/signup says nothing
        # about this employer's candidate profile.
        try:
            employer = urlsplit(job_url)
            if (
                employer.scheme == "https"
                and employer.hostname
                and employer.hostname.lower() != host
                and employer.port in {None, 443}
                and not employer.username
                and not employer.password
            ):
                return "employer:" + origin(job_url)
        except ValueError:
            pass
        return "unresolved_shared_tenant"
    if host == "workforcenow.adp.com":
        for candidate in (url, job_url):
            parsed = urlsplit(candidate)
            if (parsed.hostname or "").lower() != host:
                continue
            query = {key.lower(): value for key, value in parse_qs(parsed.query).items()}
            tenant = query.get("cid", [])
            if len(tenant) == 1 and tenant[0]:
                return "cid:" + tenant[0]
        return "unresolved_shared_tenant"
    if host.endswith(SF_HOSTS):
        for candidate in (url, job_url):
            if tenant := sf_company(candidate):
                return "company:" + tenant[1]
        if job_url and origin(job_url) != origin(url):
            return "employer:" + origin(job_url)
        return "unresolved_shared_tenant"
    if host.endswith(".myworkdaysite.com"):
        for candidate in (url, job_url):
            parsed = urlsplit(candidate)
            if (parsed.hostname or "").lower() == host:
                match = re.search(r"/(?:recruiting|recruit)/([^/]+)/", parsed.path)
                if match:
                    return "recruiting:" + match[1]
        return "unresolved_shared_tenant"
    return ""


# Registration that continues after its first page (Dayforce ID: security questions, emailed code, multifactor setup).
# The account does not exist yet there; marking it authenticated sent the next pass to "Sign In" (2026-10-16).
REGISTRATION_STEP = re.compile(r"security.?questions|/verif|mfa.?setup|multi.?factor", re.I)
REGISTRATION_HEADING = re.compile(
    r"^\s*(?:security questions|enter (?:the )?verification code|verify your e-?mail|multi.?factor authentication|"
    r"set up multi.?factor)",
    re.I,
)

ACCOUNT_EXISTS_STEP = re.compile(r"/verif|mfa.?setup|multi.?factor", re.I)
ACCOUNT_EXISTS_HEADING = re.compile(
    r"^\s*(?:enter (?:the )?verification code|verify your e-?mail|multi.?factor authentication|set up multi.?factor)", re.I
)

# Dayforce ID (dfid.dayforcehcm.com) is one sign-in for every Dayforce employer. Its /globalidentity/account/login path
# serves both registration and sign-in (the returnUrl's action decides), so the title and heading name the step.
DAYFORCE_ID_HOST = "dfid.dayforcehcm.com"
DAYFORCE_ID_PAGES = {
    "create your account": "register",
    "security questions": "security_questions",
    "enter verification code": "verification",
    "multifactor authentication": "mfa",
    "sign in": "sign_in",
}
# The account exists: its security questions were saved (verification_pending), a sign-in worked (authenticated), or
# Dayforce refused to register this email again (exists). Any other record (registering, a legacy signing_in) has no
# account behind it; signing in to it stalled every Dayforce ID run from 10-07 to 10-18.
DAYFORCE_ID_ACCOUNT = {"verification_pending", "authenticated", "exists"}
DAYFORCE_ID_SPECIAL = re.compile(r"""[!\\/"#$%&'()*+,\-.:;<>?@\[\]^_`{|}~]""")
DAYFORCE_ID_OUTCOME_SECONDS = 12


def dayforce_id_page(obs):
    """The Dayforce ID step on screen: register, security_questions, verification, mfa, sign_in, or "" elsewhere."""
    parts = urlsplit(obs.get("url", ""))
    if (parts.hostname or "").lower() != DAYFORCE_ID_HOST:
        return ""
    for name in [str(obs.get("title", "")).split("|")[0], *obs.get("headings", [])]:
        if page := DAYFORCE_ID_PAGES.get(re.sub(r"\s+", " ", str(name)).strip().lower()):
            return page
    path = parts.path.lower()
    for marker, page in (
        ("/securityquestions", "security_questions"),
        ("/users/verification", "verification"),
        ("/mfasetup", "mfa"),
        ("/account/register", "register"),
    ):
        if marker in path:
            return page
    if path.rstrip("/").endswith("/account/login"):
        back = parse_qs(parts.query).get("returnUrl", [""])[0]
        return "register" if parse_qs(urlsplit(back).query).get("action") == ["register"] else "sign_in"
    return ""


def dayforce_id_password_ok(password):
    """Dayforce ID's own password rules (its registration bundle): 8+ characters, an upper and a lower case letter,
    one special character from its list ("=" and space are not on it), and no character three times in a row."""
    return bool(
        len(password) >= 8
        and re.search(r"[A-Z]", password)
        and re.search(r"[a-z]", password)
        and DAYFORCE_ID_SPECIAL.search(password)
        and not re.search(r"(.)\1{2,}", password)
    )


def dayforce_id_outcome(step, page, info):
    """What Dayforce ID's Next did (its bundle, 2026-10-18): the next step, a refusal, a notice, or nothing yet."""
    messages = " ".join([*info.get("validation", []), *info.get("toasts", [])])
    if page and page != step and page in {"security_questions", "verification", "mfa"}:
        return "advanced"
    if re.search(r"registration failed", messages, re.I):
        return "registration_failed"
    if re.search(r"existing account", " ".join(info.get("modal", [])), re.I):
        return "existing_account"
    if re.search(r"request can.t be completed", messages, re.I):
        return "request_failed"
    if info.get("invalid") or re.search(r"required|must|do not match|unique|invalid", messages, re.I):
        return "validation"
    if any(entry.get("pending") for entry in info.get("captcha", [])) or info.get("challenge"):
        return "captcha"
    return "no_response"


# What a Dayforce ID page shows after Next, never values: its validation text (where "Registration failed" appears;
# the scanner's error selectors miss it), toasts, modal, invalid inputs and the invisible reCAPTCHA's state.
DAYFORCE_ID_SIGNALS = r"""() => {
  const shown = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const text = e => (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim();
  const all = s => [...document.querySelectorAll(s)].filter(shown).map(text).filter(Boolean).map(t => t.slice(0, 300));
  const hook = window.__jpTokenCaptcha;
  return {
    validation: all('[class*=evrValidationText]').slice(0, 4),
    toasts: all('.toast.show, [role=alert]').slice(0, 4),
    modal: all('[id^="modal_"], [role=dialog], dialog[open]').slice(0, 2),
    invalid: [...document.querySelectorAll('input[aria-invalid=true]')].filter(shown).map(i => i.id || i.type).slice(0, 8),
    captcha: (hook?.entries || []).slice(-4).map(x => ({invisible: !!x.invisible, pending: !!x.pending,
      delivered: !!x.delivered, generation: x.generation || 0})),
    sequence: hook?.sequence || 0,
  };
}"""


def registration_state(creating, path, headings, password_fields):
    """The account state an account action's result proves: "verification_pending" once a registration has reached its
    code or multifactor step, "authenticated" once a sign-in or one-page registration has left the account form, else
    None (still a registration step, or the form came back)."""
    continuing = creating and (REGISTRATION_STEP.search(path) or any(REGISTRATION_HEADING.search(h) for h in headings))
    if continuing and (ACCOUNT_EXISTS_STEP.search(path) or any(ACCOUNT_EXISTS_HEADING.search(h) for h in headings)):
        return "verification_pending"
    if not password_fields and not continuing:
        return "authenticated"
    return None


# The page's own required-field check: nothing reached the server, so it says nothing about whether an account
# exists. Westinghouse (SuccessFactors) showed "A password is required." and the engine created a second account for
# an email that already had one (2026-10-16/17).
REQUIRED_INPUT = re.compile(
    r"\b(?:password|e-?mail(?: address)?|user ?name|login)\b[^.]{0,20}\bis required\b|complete all required fields",
    re.I,
)

LOGIN_IDENTIFIER = re.compile(
    # ADP Workforce Now (ZincFive 2026-10-16) asks for "Email or Mobile Number".
    r"(?:(?:enter|type)(?: your)? |your )?(?:valid )?e.?mail(?: address)?(?: or (?:mobile|phone|cell)(?: phone)?(?: number)?)?"
    r"|user ?name|login",
    re.I,
)


def login_identifier(field):
    """An account form's email or user-name input. SuccessFactors sign-in labels it "Valid Email"
    (Capgemini) or "Email Address:" (EY); left unfilled, the employer rejected the login and cleared
    the password, and the run looped on the sign-in page (2026-10-15)."""
    return field["type"] == "email" or bool(
        LOGIN_IDENTIFIER.fullmatch(re.sub(r"[\s:*✱]+$", "", field["label"]))
    )


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
        from .email_alias import base_address

        if base_address(self.email) != self.email:
            return None  # The configured login belongs to the base identity, never to a platform plus-address.
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


SECURITY_QUESTION = re.compile(
    r"^\s*(?:security|secret|challenge) question(?:\s*(?:#|no\.?)?\s*\d+)?\s*\*?\s*$", re.I
)
SECURITY_ANSWER = re.compile(r"^\s*(?:security |secret )?answer(?:\s*(?:#|no\.?)?\s*\d+)?\s*\*?\s*$", re.I)

GUEST = re.compile(
    r"apply without (?:an |a )?account|apply as (?:a )?guest|continue as (?:a )?guest|"
    r"guest (?:apply|application)|apply without signing in|continue without (?:an |a )?account",
    re.I,
)


class AccountFlow:
    """Discover ordinary account forms, including on previously unknown ATS sites."""

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

    async def apply_as_guest(self, obs):
        """Answer rules › guest first: an employer's guest application before any sign-in or sign-up.

        Dayforce (York, mPulse 2026-10-16) offers "Apply without an Account" beside Sign In; the
        navigator sometimes took the reCAPTCHA-protected account path instead. Once per run."""
        e = self.e
        rules = getattr(getattr(e.form, "resolver", None), "answer_rules", None)
        if self.guest_chosen or not rules or not rules.guest_apply_first:
            return False
        guest = [b for b in obs["controls"] if GUEST.fullmatch(b["label"].strip())]
        if not guest and await self.dayforce_guest_allowed():
            # The tenant's sitecontext allows guests (enableApplyWithoutAccount, REA map 10-18): its "Apply without
            # an Account" renders after hydration, and choosing Sign In or Create first needs a Dayforce ID.
            for _ in range(8):
                await asyncio.sleep(0.5)
                guest = [b for b in (await e.form.scan())["controls"] if GUEST.fullmatch(b["label"].strip())]
                if guest:
                    break
        if len(guest) != 1:
            return False
        self.guest_chosen = True
        await e.form.click(guest[0]["id"], auth_control=True)
        e.page = e.form.page
        await e.form.settle(timeout=4)
        e.emit("account", "Chose the employer's guest application; no account needed")
        return True

    async def dayforce_guest_allowed(self):
        """A Dayforce apply page whose tenant sitecontext (in its own __NEXT_DATA__) allows applying without an account."""
        page = self.e.form.page
        parts = urlsplit(page.url or "")
        # Only the flow-selection page (…/jobs/{id}/apply), once: the guest form under /apply/ carries the same flag.
        if (
            (parts.hostname or "").lower() != "jobs.dayforcehcm.com"
            or not parts.path.rstrip("/").endswith("/apply")
            or ("guest_wait", page.url) in self.attempts
        ):
            return False
        self.attempts.add(("guest_wait", page.url))
        try:
            data = await page.evaluate("() => document.getElementById('__NEXT_DATA__')?.textContent || ''")
        except Exception:
            return False
        return '"enableApplyWithoutAccount":true' in data

    async def dayforce_id(self, obs):
        """Dayforce ID steps the generic account flow misread (REA map and bundle, 2026-10-18).

        - Its two "It looks like you have an existing account" notices have one action, Continue. On Create Your Account
          the notice is about the employer's older career site and Continue goes on to the security questions (the old
          profile moves to the new account); on Sign In it says a new account is being linked.
        - Multifactor setup takes "I do not want to use multifactor authentication": a phone code cannot be received.
        - Sign In without a created account opens "Create one now." instead: no saved login exists behind it.
        - Create Your Account for an email that already has an account goes to Sign In; it is never registered again.
        """
        e = self.e
        page = dayforce_id_page(obs)
        if not page:
            return False
        url = obs["url"]
        exists = self.store.state(url) in DAYFORCE_ID_ACCOUNT

        def single(pattern):
            found = [b for b in obs["controls"] if re.fullmatch(pattern, b["label"].strip(), re.I)]
            return found[0] if len(found) == 1 else None

        notice = " ".join([str(d.get("text", "")) for d in obs.get("dialogs", [])] + [obs.get("text", "")[:2000]])
        if (go := single(r"continue")) and re.search(r"it looks like you have an existing account", notice, re.I):
            e.emit(
                "account",
                "Continued Dayforce ID's account-linking notice"
                if page == "sign_in"
                else "Continued Dayforce ID's existing-account notice; registering moves the older career-site profile",
            )
            await e.form.click(go["id"], auth_control=True)
            e.page = e.form.page
            await e.form.settle(timeout=4)
            return True
        if page == "mfa":
            group = next((f for f in obs["fields"] if f["type"] == "radiogroup"), None)
            opt_out = next(
                (o for o in (group or {}).get("options", []) if re.search(r"do not want to use", str(o), re.I)), None
            )
            save = single(r"save")
            if not opt_out or not save or ("mfa", url) in self.attempts:
                return False
            self.attempts.add(("mfa", url))
            await e.form.fill(group, opt_out)
            e.emit("account", "Dayforce ID multifactor setup: chose not to use multifactor authentication")
            await e.form.click(save["id"], auth_control=True)
            e.page = e.form.page
            await e.form.settle(timeout=8)
            saved = self.store.get(url)
            if saved and not exists:
                # Setup follows the emailed code, so the account exists (the code step leaves no record of its own).
                self.store.save(url, saved, "verification_pending")
            return True
        if page == "sign_in" and not exists:
            create = single(r"create one now\.?|create (?:an? )?account")
            if create and ("dayforce_id", "create") not in self.attempts:
                self.attempts.add(("dayforce_id", "create"))
                e.emit("account", "Dayforce ID has no account for this email yet; opened Create one now.")
                await e.form.click(create["id"], auth_control=True)
                e.page = e.form.page
                return True
            e.result = {
                "state": "waiting_browser",
                "reason": "Dayforce ID has no created account for this email, so signing in cannot work; "
                "its registration did not complete",
                "blocker_type": "account",
                "recoverable": False,
            }
            return True
        if page == "register" and exists:
            signin = single(r"sign in")
            if signin and ("switch", "signin") not in self.attempts:
                self.attempts.add(("switch", "signin"))
                e.emit("account", "Dayforce ID already has this account; signing in instead of registering again")
                await e.form.click(signin["id"], auth_control=True)
                e.page = e.form.page
                return True
            e.result = {
                "state": "waiting_browser",
                "reason": "Dayforce ID already has an account for this email and it is never registered twice; "
                "sign in with its password or reset it at Dayforce ID",
                "blocker_type": "account",
                "recoverable": False,
            }
            return True
        if page == "sign_in" and ("dayforce_id", "signin") in self.attempts and re.search(
            r"incorrect email address or password", obs.get("text", ""), re.I
        ):
            e.result = {
                "state": "waiting_browser",
                "reason": "Dayforce ID refused the saved password for this email; reset it with Forgot Password at "
                "Dayforce ID, then retry",
                "blocker_type": "account",
                "recoverable": False,
            }
            return True
        return False

    async def dayforce_id_result(self, step):
        """Wait for what Dayforce ID's Next did and emit it, never values. A pending invisible reCAPTCHA goes to the
        solver once: the page's own execute() takes the token through the widget's callback and continues its submit.
        Production (10-16..18) showed nothing for each Next: the page's ValidationText and toasts are outside the
        scanner's error selectors, and the reCAPTCHA state was never read."""
        from .captcha_observation import challenge_frame

        e = self.e
        started = time.monotonic()
        solver_tried = False
        while True:
            await asyncio.sleep(0.5)
            obs = await e.form.scan()
            page = dayforce_id_page(obs)
            try:
                info = await e.form.page.evaluate(DAYFORCE_ID_SIGNALS)
                info["challenge"] = bool(await challenge_frame(e.page))
            except Exception:
                info = {}
            outcome = dayforce_id_outcome(step, page, info)
            if outcome == "captcha" and not solver_tried:
                solver_tried = True
                if challenge := await e.browser_challenge():
                    e.result = challenge
                    break
                continue
            waited = time.monotonic() - started
            limit = DAYFORCE_ID_OUTCOME_SECONDS if info.get("captcha") else min(3, DAYFORCE_ID_OUTCOME_SECONDS)
            if outcome not in {"captcha", "no_response"} or waited >= limit:
                break
        e.emit(
            "account_outcome",
            f"Dayforce ID {step.replace('_', ' ')}: {outcome.replace('_', ' ')}",
            {
                "step": step,
                "outcome": outcome,
                "page": page,
                "path": urlsplit(obs.get("url", "")).path[:120],
                "messages": [str(x)[:160] for x in [*info.get("validation", []), *info.get("toasts", [])]][:4],
                "modal": [str(x)[:160] for x in info.get("modal", [])][:2],
                "invalid": info.get("invalid", [])[:8],
                "captcha": info.get("captcha", [])[:4],
                "challenge_visible": info.get("challenge", False),
            },
        )
        return outcome, obs, info

    def dayforce_id_blocker(self, step, outcome, info):
        """The run result for a Dayforce ID Next that did not advance."""
        messages = "; ".join([*info.get("validation", []), *info.get("toasts", [])])[:240]
        if outcome == "request_failed":
            return {
                "state": "waiting_browser",
                "reason": "Dayforce ID could not take the request right now: " + messages,
                "blocker_type": "account",
                "recoverable": False,
                "recovery_cause": "site_temporary",
            }
        if outcome == "validation":
            return {
                "state": "waiting_browser",
                "reason": f"Dayforce ID {step.replace('_', ' ')} refused the form: " + (messages or "fields invalid"),
                "blocker_type": "validation",
                "recoverable": True,
                "recovery_cause": "control_observation",
            }
        return {
            "state": "waiting_browser",
            "reason": f"Dayforce ID {step.replace('_', ' ')}: Next got no reCAPTCHA token, so nothing was sent "
            "and no account exists yet",
            "blocker_type": "captcha",
            "recovery_cause": "security_challenge",
        }

    async def security_questions(self, obs):
        """Dayforce ID registration (2026-10-16) continues on a page of three security questions, each a
        picker followed by an "Answer" input, which the navigator would have sent to the owner. Choose
        distinct questions, type generated answers and seal them with the account like its password."""
        from .widgets import close_picker, placeholder_option

        e = self.e
        fields = obs["fields"]
        pairs = []
        for index, field in enumerate(fields):
            if not SECURITY_QUESTION.match(field["label"]):
                continue
            answer = fields[index + 1] if index + 1 < len(fields) else None
            if field["type"] not in {"combobox", "select", "dropdown"} or not (
                answer and answer["type"] in {"text", "password"} and SECURITY_ANSWER.match(answer["label"])
            ):
                return False
            pairs.append((field, answer))
        frame = e.form.frames.get(pairs[0][0]["id"]) if pairs else None
        # Only an identity/account page: an application's own questions never reach this shortcut.
        if (
            not pairs
            or ("security_questions", frame.url) in self.attempts
            or not re.search(r"account|regist|sign.?up|identity|security", urlsplit(frame.url).path, re.I)
        ):
            return False
        self.attempts.add(("security_questions", frame.url))
        chosen, answers = set(), {}
        for question, answer in pairs:
            observed = await e.form.inspect_control_options(question)
            await close_picker(e.form.locator(question["id"]), frame)
            options = [
                o["text"]
                for o in observed.get("options", [])
                if o.get("text") and not o.get("disabled") and not placeholder_option(o["text"])
            ]
            pick = next((o for o in options if o not in chosen), None)
            if pick is None:
                raise AccountActionError("The security question picker offered no unused question", "control_failure")
            chosen.add(pick)
            await e.form.fill(question, pick)
            value = "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(10))
            el = await e.form.locator(answer["id"]).element_handle()
            self.secret_fields.append(el)
            await el.fill(value)
            await el.evaluate("e=>e.blur()")
            answers[pick] = value
        if not self.store.remember(frame.url, security_answers=answers):
            # The answers must outlive the run; never submit questions nobody could answer again.
            raise AccountActionError("Security answers have no account record to be saved with", "account")
        nexts = [
            b
            for b in obs["controls"]
            if e.form.frames.get(b["id"]) == frame
            and re.fullmatch(r"\s*(?:next|continue|save(?: and continue)?)\s*", b["label"], re.I)
        ]
        if len(nexts) != 1:
            raise AccountActionError("The security questions page has no single Next action", "control_failure")
        e.emit("account", "Answered the account's security questions; answers are saved with the account")
        dfid = dayforce_id_page(obs) == "security_questions"
        if dfid:
            # Saving them creates the Dayforce ID account and sends its emailed code: open the mail window first.
            await e.email_verification.prepare_submission()
        await e.form.click(nexts[0]["id"], auth_control=True)
        e.page = e.form.page
        if dfid:
            outcome, after, info = await self.dayforce_id_result("security_questions")
            if getattr(e, "result", None):
                return True
            if outcome == "advanced":
                saved = self.store.get(frame.url)
                if saved:
                    self.store.save(frame.url, saved, "verification_pending")
                return True
            e.result = self.dayforce_id_blocker("security_questions", outcome, info)
            return True
        await e.form.settle(timeout=8)
        if challenge := await e.browser_challenge():
            e.result = challenge
            return True
        after = await e.form.scan()
        path = urlsplit(e.form.page.url).path
        headings = [str(h)[:80] for h in after.get("headings", [])][:4]
        e.emit(
            "account_outcome",
            "Security questions: account action result",
            {"path": path[:120], "headings": headings, "errors": [str(x)[:160] for x in after.get("errors", [])][:4]},
        )
        saved = self.store.get(frame.url)
        if saved and registration_state(True, path, headings, 0) == "verification_pending":
            self.store.save(frame.url, saved, "verification_pending")
        return True

    async def acknowledge_policy(self, obs):
        """Open explicit acknowledgement links, then accept their owned modal."""
        e = self.e
        if not e.form.resolver.profile.accept_all_application_terms:
            return False
        for dialog in reversed(obs.get("dialogs", [])):
            if not re.search(r"privacy|terms|consent|data protection", dialog.get("text", ""), re.I):
                continue
            actions = [
                b
                for b in obs["controls"]
                if b["id"] in dialog.get("control_ids", [])
                and re.fullmatch(r"(?:i )?(?:accept|agree)(?: (?:and continue|all))?", b["label"], re.I)
            ]
            if len(actions) == 1:
                for f in obs["fields"]:
                    if f["id"] in dialog.get("field_ids", []) and f["type"] == "checkbox":
                        answer = e.form.resolver.local(f)
                        if answer and answer.value == "true":
                            await e.form.fill(f, "true")
                await e.form.click(actions[0]["id"], auth_control=True)
                e.page = e.form.page
                await e.form.settle(timeout=3)
                return True
        for b in obs["controls"]:
            if not re.search(
                r"(?:read|view).{0,25}(?:acknowledge|accept).{0,35}(?:privacy|terms|notice)", b["label"], re.I
            ):
                continue
            key = (origin(e.form.frames[b["id"]].url), b["label"])
            if key in self.policy_links:
                continue
            self.policy_links.add(key)
            await e.form.click(b["id"], auth_control=True)
            e.page = e.form.page
            await e.form.settle(timeout=3)
            after = await e.form.scan()
            if not any(
                re.search(r"privacy|terms|consent|data protection", d.get("text", ""), re.I)
                for d in after.get("dialogs", [])
            ):
                # SuccessFactors opens its data-privacy statement only after Country is chosen;
                # clicked first, it just flags the empty field. Retry after filling, before Create.
                self.policy_links.discard(key)
                self.policy_pending = True
                return False
            return True
        return False

    async def open_sign_in_gate(self, obs):
        """SuccessFactors' career site answers Apply with a landing page whose only action is Sign In
        (Capgemini 2026-10-16; the navigator went back instead). Open its login once per run."""
        if not re.search(r"successfactors\.(?:com|eu)/(?:career|portalcareer)", self.e.form.page.url or ""):
            return False
        # After sign-in Capgemini shows its data privacy statement as a page with Acknowledge and Go Back;
        # going back returned to the registration form, whose Apply then required the agreement.
        acknowledge = [
            b for b in obs["controls"] if re.fullmatch(r"\s*(?:i )?acknowledge\s*", b["label"], re.I)
        ]
        if (
            len(acknowledge) == 1
            and ("gate", "privacy") not in self.attempts
            and self.e.form.resolver.profile.accept_all_application_terms
            and re.search(r"privacy|personal data|data protection", obs.get("text", ""), re.I)
            and not [f for f in obs["fields"] if f["type"] not in {"checkbox"}]
        ):
            self.attempts.add(("gate", "privacy"))
            self.e.emit("account", "Acknowledged the employer's data privacy statement")
            await self.e.form.click(acknowledge[0]["id"], auth_control=True)
            self.e.page = self.e.form.page
            return True
        if ("gate", "signin") in self.attempts:
            return False
        labels = [b["label"].strip() for b in obs["controls"]]
        if any(re.fullmatch(r"sign ?out|log ?out", label, re.I) for label in labels):
            return False
        gates = [b for b in obs["controls"] if re.fullmatch(r"\s*(?:sign|log) ?in\s*", b["label"], re.I)]
        applicant = [f for f in obs["fields"] if not re.fullmatch(r"language", f["label"], re.I)]
        if not gates or applicant:
            return False
        self.attempts.add(("gate", "signin"))
        self.e.emit("account", "Opened the employer sign-in from its career landing page")
        await self.e.form.click(gates[0]["id"], auth_control=True)
        self.e.page = self.e.form.page
        return True

    async def clear(self):
        for el in self.secret_fields:
            try:
                await el.fill("", timeout=300)
            except Exception:
                pass
        self.secret_fields = []

    async def prepare_embedded_application(self, obs, passwords, frame, account_url, credentials):
        """Prepare a combined registration/application without requiring its upload first."""
        from .forms import FINAL

        form = self.e.form
        finals = [
            b
            for b in obs["controls"]
            if form.frames.get(b["id"]) == frame
            and (FINAL.search(b["label"]) or re.fullmatch(r"apply(?: now)?", b["label"], re.I))
        ]
        files = [f for f in obs["fields"] if f["type"] == "file" and form.frames.get(f["id"]) == frame]
        if len(finals) != 1 or not files:
            return False
        button = await form.locator(finals[0]["id"]).element_handle()
        # Same <form>, or (React pages without any <form>) both outside one in this frame.
        same_form = "(e,b)=>{const f=x=>x.form||x.closest('form');return f(e)===f(b);}"
        if not all([await form.locator(f["id"]).evaluate(same_form, button) for f in [*passwords, *files]]):
            return False
        # A separate registration action means this is still an account step.
        for control in obs["controls"]:
            if (
                form.frames.get(control["id"]) == frame
                and re.fullmatch(
                    r"create(?: (?:an? )?(?:account|profile))?|register|sign up|continue|save and continue|submit profile",
                    control["label"],
                    re.I,
                )
                and await form.locator(control["id"]).evaluate(same_form, button)
            ):
                return False
        for field in passwords:
            element = await form.locator(field["id"]).element_handle()
            self.secret_fields.append(element)
            await element.fill(credentials["password"])
            await element.evaluate("e=>e.blur()")
        if not all(
            [
                await form.locator(f["id"]).evaluate("e=>Boolean(e.value) && e.checkValidity()")
                for f in passwords
            ]
        ):
            raise AccountActionError(
                "Embedded account password did not satisfy the form validation", "account"
            )
        for field in passwords:
            await form.locator(field["id"]).evaluate("e=>e.dataset.jpEmbeddedAccountReady='true'")
        self.store.save(account_url, credentials, "prepared_application")
        self.e.emit("account", "Prepared embedded account fields; final application submit remains reserved")
        return True

    async def handle(self):
        from .agent_settings import enabled

        if not enabled(self.e.service.db, "accounts"):
            return False
        e = self.e
        obs = await e.form.scan()
        observe = getattr(self.store, "observe", None)
        if observe:
            observe(getattr(e.form.page, "url", ""))  # A tenant named by any page keys this job's account.
        if await self.apply_as_guest(obs):
            return True
        if not e.form.resolver.profile.allow_account_creation:
            return False
        if await self.acknowledge_policy(obs):
            return True
        if await self.security_questions(obs):
            return True
        if any(f["id"] not in e.form.frames for f in obs["fields"]):
            obs = await e.form.scan()  # The policy check re-scanned the page; its control ids replaced these.
        if await self.dayforce_id(obs):
            return True
        dfid = dayforce_id_page(obs)
        passwords = [
            f
            for f in obs["fields"]
            if f["type"] == "password" and not re.search(r"code|otp|one.time", f["label"], re.I)
        ]
        if not passwords:
            return await self.open_sign_in_gate(obs)
        frame = e.form.frames[passwords[0]["id"]]
        if any(e.form.frames[f["id"]] != frame for f in passwords):
            raise AccountActionError("Account password fields span different frames", "observation_failure")
        account_url = frame.url
        creating =len(passwords) > 1 or any(f.get("autocomplete") == "new-password" for f in passwords)
        # TikTok: "Create Account" page with one password and no new-password hint was treated as
        # sign-in, so its "Create" button made the action ambiguous and no credentials were sent.
        creating = creating or bool(
            re.search(
                r"create[-_ ]?account|register|sign[-_ ]?up|create[-_ ]?profile",
                urlsplit(account_url).path,
                re.I,
            )
            or any(
                re.fullmatch(
                    r"create (?:an? |your )?(?:account|profile)|register|sign up", str(h).strip(), re.I
                )
                for h in obs.get("headings", [])
            )
        )
        # A combined registration/application form keeps its prepared password
        # controls until final submission. They must not trigger a second login
        # or be sent through an unreserved authentication click.
        if creating and all(
            [
                await e.form.locator(f["id"]).evaluate(
                    "e=>e.dataset.jpEmbeddedAccountReady==='true' && Boolean(e.value) && e.checkValidity()"
                )
                for f in passwords
            ]
        ):
            return False
        saved = self.store.get(account_url)
        configured = self.store.icims_credentials(account_url)
        # "created_locally" means we pressed Create but the employer never confirmed the
        # account. Westinghouse/Corning SuccessFactors then "signed in" to an account that did
        # not exist, failed, and flip-flopped between Sign In and Create an account.
        state = getattr(self.store, "state", None)
        recorded = state(account_url) if state else None
        unconfirmed = recorded == "created_locally"
        canonical = getattr(self.store, "canonical", lambda url: url)(account_url)
        tenant = account_tenant(canonical, getattr(self.store, "job_url", ""))
        scope = origin(canonical) + (":" + tenant if tenant != "unresolved_shared_tenant" and tenant else "")
        known = bool(getattr(self.store, "known", lambda url: False)(account_url)) or self.applied_before()
        errors = [str(x) for x in obs.get("errors", [])]
        page_refused = any(REQUIRED_INPUT.search(x) for x in [*errors, obs.get("text", "")])
        create_links = [
            b
            for b in obs["controls"]
            if re.fullmatch(
                r"create (?:an? )?(?:account|profile)|register|sign up|new user", b["label"], re.I
            )
        ]
        if not creating and not configured and known and recorded != "authenticated":
            # Westinghouse (SuccessFactors) 2026-10-16/17 and AMETEK 10-18: the employer already holds this
            # email's account, yet a refused or unanswered sign-in led to Create, whose password then replaced the
            # working one. A known account signs in, resets a refused password by mail, and is never registered again.
            from .adapters.base import BAD_CREDENTIALS

            refusal = next((x for x in errors if BAD_CREDENTIALS.search(x)), "")
            if not saved or ((scope, False) in self.attempts and refusal and not page_refused):
                return await self.recover_sign_in(scope, refusal or "No saved login for this employer account")
            if (scope, False) in self.attempts:
                if ("retry", scope) in self.attempts:
                    e.result = {
                        "state": "waiting_browser",
                        "reason": "The employer returned its sign-in page without accepting or refusing the saved "
                        "login; the existing account was kept and no new account was created",
                        "blocker_type": "account",
                        "recoverable": False,
                    }
                    return True
                # No refusal on the returned page (AMETEK 10-18, while another run opened the same career host).
                self.attempts.add(("retry", scope))
                self.attempts.discard((scope, False))
        # SuccessFactors (EY, Capgemini 2026-10-15): a login that was never confirmed may belong to an
        # account that does not exist, and an account created earlier is refused again as existing.
        # One switch each way per run: a rejected sign-in tries Create, a refused Create tries Sign in.
        if (
            not creating
            and not configured
            and not known
            and (scope, False) in self.attempts
            and recorded != "authenticated"
            and create_links
            and ("switch", "create") not in self.attempts
            and not page_refused
        ):
            self.attempts.add(("switch", "create"))
            await e.form.click(create_links[0]["id"], auth_control=True)
            e.page = e.form.page
            return True
        exists_refused = any(ACCOUNT_EXISTS.search(x) for x in [*errors, *map(str, obs.get("headings", []))])
        if (
            creating
            and ("switch", "signin") not in self.attempts
            # Dayforce ID's form comes back whenever Next gets no reCAPTCHA token; no account exists then, and its
            # "Have an account? Sign In" led every run since 10-07 to sign in to nothing (exists: dayforce_id switches).
            and dfid != "register"
            and (
                known
                or exists_refused
                # An earlier run's combined registration and application already created this
                # account (Capgemini: Create was refused with "Account Already Exists").
                or recorded == "prepared_application"
                # Create was already pressed in this run and the create form came back. SAP sends a
                # passcode, then refuses it for an existing account and returns here without any
                # "already exists" text (2026-10-16). A form returned with its own validation errors
                # (Farmers 10-17: the privacy agreement was not opened) was not refused as existing.
                or ((scope, True) in self.attempts and not page_refused and not errors)
            )
        ):
            signin_links = [
                b
                for b in obs["controls"]
                if re.fullmatch(r"(?:please )?(?:sign|log) ?in|login", b["label"], re.I)
            ]
            if signin_links:
                self.attempts.add(("switch", "signin"))
                # Keep the employer's own login; a Create this run pressed may have replaced it.
                previous = getattr(self.store, "previous", lambda url: None)(account_url)
                login = previous or saved or self.store.shared()
                self.store.save(account_url, login, "exists" if exists_refused or known else "signing_in")
                await e.form.click(signin_links[0]["id"], auth_control=True)
                e.page = e.form.page
                return True
        if not creating and not configured and not known and (not saved or unconfirmed):
            if create_links and ("switch", "signin") not in self.attempts:
                await e.form.click(create_links[0]["id"], auth_control=True)
                e.page = e.form.page
                return True
        if tenant == "unresolved_shared_tenant":
            raise AccountActionError("Shared ATS account tenant is not observed", "observation_failure")
        signature = (scope, creating)
        if signature in self.attempts and not creating and recorded == "authenticated":
            # AMETEK (SuccessFactors) 2026-10-16: a confirmed sign-in expired while the application form
            # was being filled, and the login page returned. Sign in again, at most twice per run.
            renewals = sum(1 for item in self.attempts if item[:2] == ("renewed", scope))
            if renewals < 2:
                self.attempts.discard(signature)
                self.attempts.add(("renewed", scope, renewals))
        if (
            signature in self.attempts
            and creating
            and ("password_rules", scope) not in self.attempts
            and PASSWORD_MAXIMUM.search(obs.get("text", ""))
        ):
            # Westinghouse (SuccessFactors) 2026-10-17 printed "Password must not be longer than 18 characters"
            # only after Create refused the longer password; retry once inside the stated limits.
            self.attempts.discard(signature)
            self.attempts.add(("password_rules", scope))
        if signature in self.attempts:
            from .adapters.base import BAD_CREDENTIALS

            rejected = any(BAD_CREDENTIALS.search(error) for error in obs.get("errors", []))
            e.result = {
                "state": "waiting_browser",
                "reason": "The employer explicitly rejected the account credentials"
                if rejected
                else "Account action did not advance; inspect and repair the current form validation",
                "blocker_type": "account" if rejected else "validation",
                "recoverable": not rejected,
                "recovery_cause": "" if rejected else "control_observation",
            }
            return True
        maxima = [f.get("maxlength", -1) for f in passwords if f.get("maxlength", -1) > 0]
        if creating:
            # Some portals render the password limit as instructions but omit
            # maxlength on the input. Read only an explicit password maximum.
            maxima += [int(n) for n in PASSWORD_MAXIMUM.findall(obs.get("text", "")) if 1 <= int(n) <= 256]
        # A Create reuses this employer's saved login when it fits, so a Create the employer refuses as existing
        # leaves the working password in place (AMETEK 2026-10-18 saved a new one over it).
        reuse = (
            creating
            and saved
            and 8 <= len(saved.get("password", "")) <= (min(maxima) if maxima else 256)
        )
        creds = configured or (
            saved
            if reuse or (saved and not creating)
            else self.store.shared(max_length=min(maxima) if maxima else None)
        )
        if maxima and len(creds["password"]) > min(maxima):
            if not creating:
                raise AccountActionError(
                    "The configured account password exceeds the website's maximum length", "account"
                )
            # A new account on a site whose limit is shorter than the configured password gets its own compliant
            # password; it is saved for this employer like every site login (Westinghouse: 18 characters).
            creds = {**creds, "password": self.store.generated(max(8, min(min(maxima), 16)))}
        if dfid == "register" and not dayforce_id_password_ok(creds["password"]):
            # Dayforce ID refuses a password outside its rules with field text the scanner does not read (both password
            # inputs ended invalid at Imperial Brady, 10-18). This account gets its own compliant password, saved with it.
            creds = {**creds, "password": self.store.generated(16)}
        if creating and await self.prepare_embedded_application(obs, passwords, frame, account_url, creds):
            return False
        # Fill profile and consent fields through the ordinary evidence resolver.
        report = await e.form.fill_current()
        unresolved = [
            q
            for q in report["pending"]
            if q.get("key") != "session" and not re.fullmatch(r"user ?name|login", q["question"], re.I)
        ]
        if unresolved:
            e.result = {
                "state": "needs_review",
                "reason": "Account setup needs a confirmed profile answer",
                "reviews": unresolved,
            }
            return True
        if not report.get("ok", True):
            e.result = {
                "state": "waiting_browser",
                "reason": "Repair required account form values before submitting credentials",
                "blocker_type": "validation",
                "recoverable": True,
                "recovery_cause": "control_observation",
            }
            return True
        if getattr(self, "policy_pending", False):
            self.policy_pending = False
            if await self.acknowledge_policy(await e.form.scan()):
                await self.acknowledge_policy(await e.form.scan())  # accept the opened statement
        # A privacy acknowledgement may replace account inputs. Fill credentials
        # on the resulting controls, so the account action cannot send empty replacements.
        obs = await e.form.scan()
        for f in obs["fields"]:
            if e.form.frames[f["id"]] != frame:
                continue
            value = None
            if f["type"] == "password":
                value = creds["password"]
            elif login_identifier(f):
                username = getattr(getattr(e, "profile", None), "account_username", "")
                value = (
                    username
                    if username and f["type"] != "email" and re.fullmatch(r"user ?name", f["label"], re.I)
                    else creds["email"]
                )
            if value is not None:
                el = await e.form.locator(f["id"]).element_handle()
                if f["type"] == "password":
                    self.secret_fields.append(el)
                await el.fill(value)
                await el.evaluate("e=>e.blur()")
                if not await el.evaluate("e => Boolean(e.value)"):
                    # A script cleared it on blur: type it once more, then refuse to send an empty login.
                    await el.press_sequentially(value, delay=15)
                    if not await el.evaluate("e => Boolean(e.value)"):
                        raise AccountActionError(
                            "The account form did not keep the credentials; nothing was submitted", "control_failure"
                        )
        labels = (
            r"create(?: (?:an? )?(?:account|profile))?|register|sign up|continue|save and continue|submit profile"
            if creating
            else r"sign in|log in|login|continue"
        )
        buttons = [
            b
            for b in obs["controls"]
            if e.form.frames[b["id"]] == frame and re.fullmatch(labels, b["label"], re.I)
        ]
        if not buttons and not any(f["type"] == "file" and e.form.frames[f["id"]] == frame for f in obs["fields"]):
            # Dayforce ID (Security Benefit, Imperial Brady 2026-10-16): registration's only action is
            # "Next". The ambiguous-action refusal below left the passwords unsent on every run.
            buttons = [
                b
                for b in obs["controls"]
                if e.form.frames[b["id"]] == frame and re.fullmatch(r"\s*next\s*", b["label"], re.I)
            ]
        # A modal's explicit account action takes precedence over navigation
        # behind it. Same-frame membership alone does not establish ownership.
        dialogs = [
            d for d in obs.get("dialogs", []) if any(f["id"] in d.get("field_ids", []) for f in passwords)
        ]
        if dialogs:
            buttons = [b for b in buttons if b["id"] in dialogs[-1].get("control_ids", [])]
        primary = [
            b
            for b in buttons
            if re.fullmatch(
                r"create(?: (?:an? )?(?:account|profile))?|register|sign up"
                if creating
                else r"sign in|log in|login",
                b["label"],
                re.I,
            )
        ]
        if len(primary) == 1:
            buttons = primary
        if not buttons and creating:
            from .forms import FINAL

            finals = [
                b
                for b in obs["controls"]
                if e.form.frames[b["id"]] == frame
                and (FINAL.search(b["label"]) or re.fullmatch(r"apply(?: now)?", b["label"], re.I))
            ]
            files = [f for f in obs["fields"] if f["type"] == "file" and e.form.frames[f["id"]] == frame]
            if len(finals) == 1 and files:
                # All password and resume inputs must belong to the final
                # button's actual HTML form. Background uploads do not qualify.
                button = await e.form.locator(finals[0]["id"]).element_handle()
                members = [*passwords, *files]
                same_form = all(
                    [
                        await e.form.locator(f["id"]).evaluate(
                            "(e,b)=>Boolean(e.form||e.closest('form')) && (e.form||e.closest('form'))===b.form",
                            button,
                        )
                        for f in members
                    ]
                )
                if same_form:
                    self.store.save(account_url, creds, "prepared_application")
                    for f in passwords:
                        await e.form.locator(f["id"]).evaluate("e=>e.dataset.jpEmbeddedAccountReady='true'")
                    e.emit(
                        "account",
                        "Prepared embedded account fields; final application submit remains reserved",
                    )
                    return False
        if len(buttons) != 1:
            raise AccountActionError(
                "Account action is ambiguous; no credentials were submitted", "control_failure"
            )
        if dfid == "register":
            # Dayforce ID creates the account only when its security questions are saved: until then the record holds
            # the credentials (and later the answers) but no account.
            state = "registering"
        elif dfid and recorded in DAYFORCE_ID_ACCOUNT:
            state = recorded  # a sign-in attempt never erases that the account exists
        else:
            state = "created_locally" if creating else "signing_in"
        # A Create that replaced a saved login keeps it, so "Account Already Exists" can restore it.
        replaced = saved if creating and saved and saved.get("password") != creds["password"] else None
        if replaced:
            self.store.save(account_url, creds, state, previous=replaced)
        else:
            self.store.save(account_url, creds, state)
        self.attempts.add(signature)
        if dfid == "sign_in":
            self.attempts.add(("dayforce_id", "signin"))
        await e.email_verification.prepare_submission()
        e.emit("account", "Creating an employer account" if creating else "Signing in with the saved account")
        await e.form.click(buttons[0]["id"], auth_control=True)
        e.page = e.form.page
        if dfid == "register":
            outcome, after, info = await self.dayforce_id_result("register")
            if getattr(e, "result", None):
                return True
            if outcome == "advanced":
                if dayforce_id_page(after) in {"verification", "mfa"}:
                    self.store.save(account_url, creds, "verification_pending")
                return True
            if outcome == "existing_account":
                return await self.dayforce_id(after) or True
            if outcome == "registration_failed":
                # "Registration failed. Try again using a different email address and/or password.": Dayforce ID
                # already knows this email. Sign in from here on; registering it again can never succeed.
                self.store.save(account_url, creds, "exists")
                return await self.dayforce_id(after) or True
            e.result = self.dayforce_id_blocker("register", outcome, info)
            return True
        await e.form.settle(timeout=8)
        if challenge := await e.browser_challenge():
            e.result = challenge
            return True
        after = await e.form.scan()
        path = urlsplit(e.form.page.url).path
        headings = [str(h)[:80] for h in after.get("headings", [])][:4]
        # Every account action's visible result, never values (Dayforce ID's Next left no trace, 2026-10-16).
        e.emit(
            "account_outcome",
            "Creating: account action result" if creating else "Signing in: account action result",
            {
                "path": path[:120],
                "headings": headings,
                "errors": [str(x)[:160] for x in after.get("errors", [])][:4],
                "password_fields": sum(f["type"] == "password" for f in after["fields"]),
            },
        )
        passwords_left = sum(f["type"] == "password" for f in after["fields"])
        if creating and any(ACCOUNT_EXISTS.search(str(x)) for x in [*after.get("errors", []), *headings]):
            # AMETEK (SuccessFactors) 2026-10-18 answered Create with "Account Already Exists" and no password field;
            # that read as a finished registration and its new password replaced the working one.
            previous = getattr(self.store, "previous", lambda url: None)(account_url)
            self.store.save(account_url, previous or creds, "exists")
            e.emit("account", "The employer already has an account for this email; signing in to it instead")
            return True
        state = registration_state(creating, path, headings, passwords_left)
        if state == "authenticated" and self.signed_out(after, account_url):
            # Westinghouse 2026-10-16/17: Create landed on the job search offering only "Sign In"; nobody was signed in.
            state = None
        if state:
            # Dayforce ID creates the account when its security questions are saved; later passes sign in to it.
            self.store.save(account_url, creds, state)
        elif passwords_left and urlsplit(e.form.page.url).path == urlsplit(account_url).path:
            await self.report_unchanged(after)
        return True

    @staticmethod
    def signed_out(obs, url):
        """A SuccessFactors page that offers Sign In and no Sign Out: the account action signed nobody in."""
        if not (urlsplit(url).hostname or "").lower().endswith(SF_HOSTS):
            return False
        labels = [str(b.get("label", "")).strip() for b in obs.get("controls", [])]
        return any(re.fullmatch(r"(?:sign|log) ?in", x, re.I) for x in labels) and not any(
            re.fullmatch(r"(?:sign|log) ?out", x, re.I) for x in labels
        )

    def applied_before(self):
        """SuccessFactors takes applications only from signed-in candidates: an earlier confirmed or already-applied
        run on this employer's career site proves the account. Westinghouse confirmed on 10-16 04:06, its record later
        read "signing_in", and the next runs registered again."""
        if getattr(self, "_applied", None) is not None:
            return self._applied
        job = getattr(self.e, "job", None) or {}
        self._applied = False
        if job.get("ats") == "successfactors" and job.get("url"):
            from sqlalchemy import select

            from .db import Job, Run

            with self.e.service.db.session() as s:
                self._applied = bool(
                    s.scalar(
                        select(Run.id)
                        .join(Job, Job.id == Run.job_id)
                        .where(
                            Job.ats == "successfactors",
                            Job.url.startswith(origin(job["url"]) + "/", autoescape=True),
                            Run.state.in_(["confirmed", "already_applied"]),
                        )
                        .limit(1)
                    )
                )
        return self._applied

    async def recover_sign_in(self, scope, detail):
        """A known account whose password the employer refused goes to "Forgot your password?" through the mailbox
        (the adapter's recover()), never to Create (Westinghouse 2026-10-16/17, AMETEK 10-18)."""
        from .adapters import for_ats

        e = self.e
        if ("recover", scope) in self.attempts:
            e.result = {
                "state": "waiting_browser",
                "reason": f"Could not sign in to the employer's existing account ({detail[:160]}); "
                "no new account was created",
                "blocker_type": "account",
                "recoverable": False,
            }
            return True
        self.attempts.add(("recover", scope))
        adapter = getattr(e, "fallback_mail_adapter", None)
        if not callable(getattr(adapter, "recover", None)):
            adapter = e.fallback_mail_adapter = for_ats(e.job.get("ats", "custom"), e)
        e.emit("account", "The employer refused the saved login of an existing account; resetting its password")
        result = await adapter.recover(await e.form.scan(), detail)
        e.page = e.form.page
        if result and result.get("state"):
            e.result = result
        else:
            # The reset finished in this run: sign in afresh with the new password.
            self.attempts.discard((scope, False))
            self.attempts.discard(("retry", scope))
        return True

    async def report_unchanged(self, obs):
        """The account action left its form in place. Record why for the next fix: Dayforce ID
        (2026-10-16) left no CAPTCHA event, message or validation error behind."""
        from .browser import PAGE_MESSAGES
        from .captcha import DETECT
        from .captcha_observation import challenge_frame

        e = self.e
        detected = []
        for frame in e.page.frames:
            try:
                info = await frame.evaluate(DETECT, True)
            except Exception:
                continue
            if info:
                keys = ("kind", "invisible", "dormant", "ambiguous", "delivered")
                detected.append({k: info.get(k) for k in keys if k in info})
        try:
            hooked = await e.page.evaluate("() => !!window.__jpTokenCaptcha")
            captured = await e.page.evaluate(
                """() => (window.__jpTokenCaptcha?.entries || []).map(x => ({kind: x.kind,
                invisible: !!x.invisible, pending: !!x.pending, delivered: !!x.delivered, key: !!x.key}))"""
            )
            messages = await e.page.evaluate(PAGE_MESSAGES)
        except Exception:
            hooked, captured, messages = None, [], []
        e.emit(
            "account_unchanged",
            "The account action left its form in place",
            {
                "hook": hooked,
                "captured": captured[:6],
                "detected": detected[:6],
                "challenge_visible": bool(await challenge_frame(e.page)),
                "messages": [str(m)[:160] for m in messages][:4],
                "errors": [str(x)[:160] for x in obs.get("errors", [])][:4],
                "disabled_actions": [str(x)[:40] for x in e.form.meta.get("disabled_actions", [])][:6],
            },
        )


# ----- adapter view ----------------------------------------------------------------------------

from .inbox import host_matches  # noqa: E402

# Hosts where an ATS legitimately asks for the candidate password.
AUTH_HOSTS = {
    "workday": ["myworkdayjobs.com", "myworkdaysite.com", "myworkday.com", "workday.com"],
    "oracle": ["oraclecloud.com"],
    "icims": ["icims.com"],
    "taleo": ["taleo.net"],
    "successfactors": ["successfactors.com", "successfactors.eu", "sapsf.com", "jobs2web.com"],
    "eightfold": ["eightfold.ai"],
    "smartrecruiters": ["smartrecruiters.com"],
    "avature": ["avature.net"],
    "jobvite": ["jobvite.com"],
    "adp": ["adp.com"],
}


def registrable(host):
    parts = (host or "").lower().split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "ac", "gov"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


class Accounts:
    """Adapter-facing view of the encrypted store: one shared login, one record per employer origin.

    States: created_locally (form submitted), authenticated, reset_requested, password_reset, locked."""

    def __init__(self, service, profile, job_url=""):
        from .email_alias import base_address

        self.service, self.config, self.profile = service, service.config, profile
        configured = (self.config.account_email or "").strip().lower()
        own = (profile.email or "").strip().lower()
        # A platform's plus-address of the configured login (iCIMS: local+icims@gmail.com, see email_alias) is that
        # platform's login. With ACCOUNT_EMAIL set to the base address, iCIMS kept signing in to the old Central
        # identity whose reset nobody could finish (0 of 19 eligible, 2026-10-18), while the form used the alias.
        aliased = bool(configured and own and own != configured and base_address(own) == base_address(configured))
        self.email = own if aliased else (configured or own)
        self.store = AccountStore(service, self.email, job_url) if self.email else None

    @property
    def ready(self):
        from .agent_settings import enabled

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
        from .agent_settings import require

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
