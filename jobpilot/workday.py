"""Workday account setup uses deterministic controls and an encrypted credential store."""

import asyncio
import hashlib
import re
import time
from urllib.parse import unquote, urlsplit, urlunsplit

from playwright.async_api import Error as PlaywrightError

from .ats import detect
from .db import Setting, now
from .mail import origin
from .mail_errors import MailPending
from .accounts import AccountStore

# A not-found page closes a posting only after this many distinct runs saw it at the posting URL.
POSTING_GONE_CONFIRMATIONS = 2
POSTING_MISSING_REASON = "The employer reports that this posting page does not exist; availability or the posting URL must be checked"
ROUTE_MISSING_REASON = "The application step is not found; the next attempt opens the posting from its start"
CLOSED_REASON = "The posting is closed or no longer available"


WORKDAY_LOCALE = re.compile(r"^/([a-z]{2}-[A-Z]{2})/")


def english_posting_url(url):
    """A Workday posting in its en-US locale. RTX's fr-CA posting (2026-10-18) rendered every step in French,
    and 30 page states in a row were unrecognized; the same posting serves every locale."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if not host.endswith((".myworkdayjobs.com", ".myworkdaysite.com")):
        return url
    match = WORKDAY_LOCALE.match(parts.path)
    if not match or match.group(1).startswith("en-"):
        return url
    return urlunsplit(parts._replace(path="/en-US/" + parts.path[match.end():]))


def planned_maintenance_until(url, text, at=None):
    """Only Workday's observed maintenance page can defer the whole platform."""
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    page = urlsplit(url)
    # Workday's own maintenance page. AAA SoCal and USC (2026-10-17) redirected to /maintenance-page?d=5&s=1&e=1
    # with wording the exact sentence check missed, and were parked as an "account origin" mismatch instead of
    # waiting for Workday. Any unavailability/maintenance wording there counts; an all-clear page does not.
    if (page.scheme != "https" or page.hostname != "community.workday.com"
            or page.path.rstrip("/") != "/maintenance-page"
            or not re.search(r"unavailable|maintenance|be back|downtime|down for", text, re.I)):
        return None
    at = at or datetime.now(UTC)
    dates = re.findall(
        r"([A-Z][a-z]+ \d{1,2}, \d{4}) at (\d{1,2}:\d{2})\s*(AM|PM)\s*(EDT|EST|UTC|GMT)", text
    )
    if dates:
        date, clock, period, zone = dates[-1]
        try:
            end = datetime.strptime(f"{date} {clock} {period}", "%B %d, %Y %I:%M %p").replace(
                tzinfo=ZoneInfo("America/New_York") if zone in {"EST", "EDT"} else UTC
            ).astimezone(UTC)
            if at < end <= at + timedelta(days=1):
                return end.isoformat()
        except ValueError:
            pass
    return (at + timedelta(hours=1)).isoformat()


def same_posting_page(page_url, job_url):
    """True only for the employer's posting page itself, never an /apply step beneath it."""
    page, job = urlsplit(page_url or ""), urlsplit(job_url or "")
    if page.scheme != "https" or job.scheme != "https" or origin(page_url) != origin(job_url):
        return False

    def path(parts):
        return unquote(parts.path).rstrip("/").casefold()

    return bool(page.path) and path(page) == path(job)


WORKDAY_POSTING = re.compile(
    r"https://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)/job/[^?#]*_([A-Za-z0-9]+)(?:-\d+)?/?$"
)


async def tenant_search_lists(job_url):
    """Whether the employer's own Workday search still lists this requisition: True, False, or None when unknown.

    Cisco, Visa, Applied Materials and AIG (2026-10-18) answered "page does not exist" while a static new-grad list kept
    the link alive; their own searches returned nothing for the requisition, yet the jobs waited as failures."""
    import httpx

    m = WORKDAY_POSTING.match(job_url or "")
    if not m:
        return None
    tenant, pod, site, requisition = m.groups()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Safari/537.36",
    }
    try:
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            r = await client.post(
                f"https://{tenant}.{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
                json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": requisition},
            )
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None
    paths = [str(p.get("externalPath", "")) for p in data.get("jobPostings") or []]
    if any(path.rstrip("/").split("_")[-1].split("-")[0] == requisition for path in paths):
        return True
    return False if data.get("total") == 0 else None


def missing_posting_result(db, job, run_id, page_url, listed=None):
    """Classify a not-found page. One run never closes a posting; two distinct runs at the posting URL do, and so does
    one run when the employer's own search no longer lists the requisition (`listed` False)."""
    if not same_posting_page(page_url, job["url"]):
        return {
            "state": "waiting_browser",
            "reason": ROUTE_MISSING_REASON,
            "recovery_cause": "route_unavailable",
        }
    if listed is False:
        return {"state": "closed", "reason": CLOSED_REASON}
    key = "posting_gone:" + job["id"]
    seen = list((db.get_setting(key, {}) or {}).get("run_ids", []))
    if run_id not in seen:
        seen.append(run_id)
        db.set_setting(key, {"run_ids": seen[-10:], "at": now()})
    if len(set(seen)) >= POSTING_GONE_CONFIRMATIONS:
        return {"state": "closed", "reason": CLOSED_REASON}
    return {
        "state": "waiting_browser",
        "reason": POSTING_MISSING_REASON,
        "recovery_cause": "posting_unavailable",
    }


async def _emit_unexplained(e, page, iteration):
    """Record the page the account loop could not read, so the next fix has its shape."""
    from .widgets import automation_ids

    try:
        title = (await page.title())[:60]
    except Exception:
        title = ""
    e.emit(
        "unexplained_state",
        "Workday page state was not recognized",
        {
            "path": urlsplit(page.url).path,
            "automation_ids": await automation_ids(page, 12),
            "title": title,
            "iteration": iteration,
        },
    )


async def _requisition_submitted(page, job_url):
    """Return the My Applications row text showing a submitted status for THIS requisition.

    Matched on the requisition number in the posting URL (e.g. ..._332049), so another
    application listed on the same dashboard never confirms this job."""
    match = re.search(r"_([0-9]{4,})/?$", urlsplit(job_url).path)
    if not match:
        return ""
    rows = await page.evaluate(
        r"""(req) => [...new Set([...document.querySelectorAll('tr,[role=row],li')]
          .filter(r => r.getClientRects().length && !r.querySelector('input,textarea,select'))
          .map(r => r.innerText.replace(/\s+/g, ' ').trim())
          .filter(t => t.length < 400 && new RegExp('(?:^|[^0-9])' + req + '(?:[^0-9]|$)').test(t)
            && /application (?:received|submitted)|under consideration/i.test(t)))]""",
        match[1],
    )
    return rows[0] if len(rows) == 1 else ""


# Recover only an explicit credential rejection or mandatory password reset.
WRONG = re.compile(
    r"wrong (?:email|password)|incorrect (?:email|password)|invalid (?:email|password|credentials|sign.?in)|"
    r"(?:email|password) (?:is |was )?(?:incorrect|invalid)|does(?: not|n.t) match|not recogni[sz]ed",
    re.I,
)
RESET_REQUIRED = re.compile(
    r"(?:administrator|system|account).{0,80}(?:requires?|requiring).{0,40}(?:password.{0,20}reset|reset.{0,20}password)|"
    r"(?:must|need to|are required to).{0,30}(?:reset|change).{0,20}(?:your )?password|"
    r"password (?:has )?expired|password reset (?:is )?required",
    re.I,
)
UNVERIFIED = re.compile(
    r"not (?:been |yet )?verified|verify your (?:email|account)|activate your account|"
    r"(?:verification|activation) (?:email|link)|check your email",
    re.I,
)
EXISTS = re.compile(
    r"already (?:exists|in use|registered)|account (?:with this email )?already|sign in instead", re.I
)
LOCKED = re.compile(r"account (?:is |has been )?locked|too many (?:failed )?(?:attempts|tries)", re.I)
ACKNOWLEDGED = re.compile(
    r"(?:email|link) (?:has been |was )?sent|check your email|sent you an email|we.ve sent|"
    r"if an account exists",
    re.I,
)


class WorkdayAuth:
    def __init__(self, engine):
        self.engine = engine
        self.secrets = []
        self.attempted = False
        self.account_state = None
        self.recovery_attempted = False
        self.last_action = None  # "create" or "sign_in": what the last auth click asked for.
        self.activated = False
        self.created_at = None
        self.responses = []
        self.watching = False
        self.page_recoveries = 0

    def account_key(self):
        e = self.engine
        return (
            "employer_account:"
            + hashlib.sha256(
                (origin(e.job["url"]) + ":" + e.form.resolver.profile.email.lower()).encode()
            ).hexdigest()
        )

    def credentials(self, create=False):
        e = self.engine
        key = self.account_key()
        # Generate outside the employer transaction: shared() has its own lock.
        shared = self.shared_credentials() if create else None
        with e.db.exclusive() as s:
            row = s.get(Setting, key)
            if row:
                self.account_state = row.value.get("state")
                return e.service.mail.vault.open(row.value["credentials"])
            if not create:
                return None
            value = shared
            s.add(
                Setting(
                    key=key,
                    value={
                        "origin": origin(e.job["url"]),
                        "credentials": e.service.mail.vault.seal(value),
                        "state": "created_locally",
                    },
                )
            )
            self.account_state = "created_locally"
            return value

    def shared_credentials(self):
        return AccountStore(self.engine.service, self.engine.form.resolver.profile.email).shared()

    async def clear(self):
        for field in self.secrets:
            try:
                await field.fill("", timeout=300)
            except Exception:
                pass
        self.secrets = []

    async def click(self, el, *, auth_submit=False):
        handle = await el.element_handle()

        async def authorize(control, paired_auth=False):
            accepted = await control.evaluate(
                r"""(e,auth) => {
                  const label=(e.innerText || e.value || e.getAttribute('aria-label') || '').trim();
                  const final=/\bsubmit\b|send (?:my )?application|complete (?:my )?application|finish application|\babsenden\b|bewerbung (?:abschicken|einreichen)|envoyer ma candidature|\bsoumettre\b/i.test(label);
                  const allowed=auth && /^submit$/i.test(label);
                  if(final && !allowed)return false;
                  e.dataset.jpAuthControl='true';e.dataset.jpControlLabel=label;
                  e.dataset.jpFinalAuth=String(allowed);
                  const f=e.closest('form');if(f)f.__jpAllowedControl=e;
                  return true;
                }""",
                auth_submit or paired_auth,
            )
            if not accepted:
                raise ValueError("Workday account control became an application submission")

        await authorize(handle)
        clicked_overlay = None
        try:
            # Workday renders an accessible click_filter over some submit buttons.
            # Click the visible control that a user actually reaches.
            label = (await handle.inner_text()).strip()
            # Snap calls this overlay "Submit" even when its paired button says
            # "Sign In". Associate it with the exact button, not a global label.
            overlay = None
            automation_id = await handle.get_attribute("data-automation-id") or ""
            if label in {
                "Create Account",
                "Sign In",
                "Reset Password",
                "Submit",
            } and not automation_id.endswith("Link"):
                overlay = el.locator("..").locator('[data-automation-id="click_filter"]')
                if await overlay.count() != 1:
                    overlay = self.engine.page.locator(
                        f'[data-automation-id="click_filter"][aria-label="{label}"]'
                    )
                    candidates = [
                        candidate
                        for candidate in await overlay.all()
                        if await candidate.evaluate(
                            "(e,b)=>!!b.closest('form') && e.closest('form')===b.closest('form')", handle
                        )
                    ]
                    overlay = candidates[0] if len(candidates) == 1 else None
            if overlay and await overlay.count() == 1 and await overlay.is_visible():
                clicked_overlay = await overlay.element_handle()
                await authorize(
                    clicked_overlay,
                    paired_auth=automation_id
                    in {"signInSubmitButton", "createAccountSubmitButton", "resetPasswordButton"},
                )
                target = clicked_overlay
            else:
                target = handle
            try:
                await target.click(timeout=5000)
            except PlaywrightError:
                # A cookie notice or backdrop intercepts the pointer: clear it, then retry once.
                await self.dismiss_cookies()
                await target.click(timeout=4000)
        finally:
            try:
                await handle.evaluate(
                    "e=>{delete e.dataset.jpAuthControl;delete e.dataset.jpControlLabel;delete e.dataset.jpFinalAuth;const f=e.closest('form');if(f?.__jpAllowedControl===e)delete f.__jpAllowedControl}"
                )
                if clicked_overlay:
                    await clicked_overlay.evaluate(
                        "e=>{delete e.dataset.jpAuthControl;delete e.dataset.jpControlLabel;delete e.dataset.jpFinalAuth;const f=e.closest('form');if(f?.__jpAllowedControl===e)delete f.__jpAllowedControl}"
                    )
            except Exception:
                pass

    async def dismiss_cookies(self):
        from .privacy import dismiss_cookies

        return await dismiss_cookies(
            self.engine.page,
            authorized=getattr(self.engine.profile, "accept_all_application_terms", False),
        )

    async def enter(self, field, value):
        # Live Workday click filters depend on keyboard-driven validation. fill()
        # updates the visible value but can leave the auth action inert.
        await field.fill("")
        await field.press_sequentially(value, delay=10)
        await field.press("Tab")

    def watch_responses(self):
        """Record the status of Workday's own auth requests (path only; no query, no body)."""
        if self.watching:
            return
        self.watching = True
        home = origin(self.engine.job["url"])

        async def seen(response):
            try:
                if response.request.method in {"POST", "PUT"} and origin(response.url) == home:
                    if re.search(r"/jobapplications(?:/|$)", urlsplit(response.url).path):
                        if response.status >= 400:
                            category = "unclassified"
                            try:
                                body = str(await response.json())[:32000]
                                for name, pattern in [
                                    ("session", r"csrf|session.{0,30}(?:expired|invalid)|not authenticated"),
                                    (
                                        "draft_state",
                                        r"application.{0,30}(?:not found|already|invalid state)|job.{0,30}(?:closed|expired)",
                                    ),
                                    ("validation", r"required|validation|invalid field|missing field"),
                                    ("server", r"internal server|unexpected error|service unavailable"),
                                ]:
                                    if re.search(pattern, body, re.I):
                                        category = name
                                        break
                            except Exception:
                                pass
                            self.engine.emit(
                                "site_response",
                                "Workday rejected a draft request",
                                {"status": response.status, "category": category},
                            )
                        return  # Draft requests are not authentication responses.
                    path = re.sub(r"[A-Za-z0-9_-]{24,}", "…", urlsplit(response.url).path)[-120:]
                    self.responses.append((response.status, path))
                    del self.responses[:-10]
            except Exception:
                pass

        self.engine.page.on("response", seen)

    def report(self, step):
        """Emit the redacted response status of the last auth request (diagnostics, no secrets)."""
        status, path = self.responses[-1] if self.responses else (None, "")
        self.engine.emit("account_response", f"Workday {step} response", {"status": status, "path": path})
        return status

    async def auth_error(self):
        page = self.engine.page
        texts = []
        for alert in await page.locator('[data-automation-id="errorMessage"],[role=alert]').all():
            try:
                if await alert.is_visible():
                    text = (await alert.inner_text()).strip()
                    # Workday announces successful navigation through role=alert too.
                    # The job title behind a login dialog is not an account error.
                    if not re.search(r"\bpage is loaded[.!]?$", text, re.I):
                        texts.append(text)
            except PlaywrightError:
                continue
        return " ".join(t for t in texts if t)[:400]

    async def form_ready(self):
        page = self.engine.page
        if await page.locator(
            '[data-automation-id="signInSubmitButton"]:visible,'
            '[data-automation-id="createAccountSubmitButton"]:visible,'
            '[data-automation-id="resetPasswordButton"]:visible'
        ).count():
            return False
        uploads = page.locator('input[type="file"]')
        advance = page.get_by_role("button", name="Save and Continue", exact=True)
        review = page.locator('[data-automation-id="progressBarActiveStep"]')
        final_submit = page.get_by_role("button", name="Submit", exact=True)
        at_review = (
            await review.count() == 1
            and "review" in (await review.inner_text()).lower()
            and await final_submit.count() == 1
            and await final_submit.is_visible()
        )
        # Newer Workday tenants expose ordinary input ids and a Next footer,
        # rather than legalNameSection_firstName / Save and Continue. The
        # exact application-stage heading distinguishes this from job details.
        stage = page.get_by_role(
            "heading",
            name=re.compile(
                r"^(?:My Information|My Experience|Application Questions|Voluntary Disclosures|Self Identify)$",
                re.I,
            ),
        )
        next_step = page.get_by_role("button", name="Next", exact=True)
        new_layout = (
            await stage.count() > 0
            and await next_step.count() == 1
            and await next_step.is_visible()
            and await page.locator(
                "input:visible:not([type=password]):not([type=hidden]),textarea:visible,select:visible,"
                "[role=combobox]:visible,button[aria-haspopup=listbox]:visible"
            ).count()
            > 0
            and not await page.locator('input[autocomplete="one-time-code"]:visible').count()
        )
        ready = (
            await uploads.count() > 0
            or await page.locator('[data-automation-id="legalNameSection_firstName"]').count() > 0
            or (await advance.count() == 1 and await advance.is_visible())
            or at_review
            or new_layout
        )
        return ready and not await page.locator('input[type="password"]:visible').count()

    async def recover_error_page(self):
        """Recover Workday's explicit refresh page before a final submit."""
        if getattr(self.engine, "armed", False):
            return False
        page = self.engine.page
        body = await page.locator("body").inner_text(timeout=3000)
        panel = page.locator('[data-automation-id="errorHeading"]:visible')
        page_error = bool(await panel.count()) and bool(
            re.search(r"Error Code:\s*VPS\|", await panel.inner_text(), re.I)
        )
        if not page_error and not (
            re.search(r"something went wrong", body, re.I)
            and re.search(r"please refresh the page.*try again|error code:\s*VPS\|", body, re.I)
        ):
            return False
        if self.page_recoveries >= 2:
            raise ValueError("Workday server error persisted after refresh and reopening this job")
        self.engine.emit(
            "browser_recovery",
            "Recovering Workday's explicit server-error page",
            {
                "strategy": "refresh" if self.page_recoveries == 0 else "reopen_job",
            },
        )
        if self.page_recoveries == 0:
            await page.reload(wait_until="domcontentloaded", timeout=20000)
        else:
            await page.goto(self.engine.job["url"], wait_until="domcontentloaded", timeout=20000)
        self.page_recoveries += 1
        self.last_action = None
        await self.engine.form.settle(timeout=3)
        return True

    async def settle_after_auth(self, password, timeout=15, previous=""):
        """Wait for the real outcome: the application form, an explicit error, or a verification
        message. A password field that disappears for a moment is not progress."""
        page = self.engine.page
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            if await self.form_ready():
                return "ready"
            if (error := await self.auth_error()) and error != previous:
                return "error:" + error  # A new message, not the one shown before this click.
            visible = await password.count() and await password.is_visible()
            if not visible:
                body = await page.locator("body").inner_text()
                if UNVERIFIED.search(body):
                    return "verify"
        return "timeout"

    async def activate(self):
        """Open the account activation link from the trusted mailbox (once per run)."""
        from .email_browser import VerificationGuard
        from .agent_settings import require

        e, page = self.engine, self.engine.page
        require(e.db, "accounts")
        mail = e.service.mail
        rule = getattr(e.email_verification, "rule", None)
        if self.activated or not rule:
            raise ValueError("Workday needs the account activation email; connect Gmail for this employer")
        self.activated = True
        cid = getattr(e.email_verification, "challenge_id", None)
        if not cid:
            c = mail.begin(
                e.run_id, rule, e.form.resolver.profile.email, "link", self.created_at or time.time() - 300
            )
            cid = c["id"]
        e.emit("account", "Waiting for the Workday account activation email")
        guard = None
        try:
            token = await mail.wait(cid, max_wait_seconds=3)
            if token["kind"] != "link":
                raise ValueError("The activation email did not contain a link")
            e.verification_origins = set(rule["link_origins"])
            guard = await VerificationGuard(page, e.verification_origins, e.config.allow_private_urls).start()
            await page.goto(token["value"], wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(1)
            if guard.blocked:
                raise ValueError("The activation link redirected outside the employer")
            mail.outcome(cid, "verified", "Employer account activated")
            self.save_credentials(self.credentials() or self.shared_credentials(), "activated")
            e.emit("account", "Workday account activated")
        except MailPending:
            if e.email_verification:
                e.email_verification.challenge_id = cid
                e.email_verification.deferred = True
            raise
        except ValueError:
            mail.outcome(cid, "failed", "Account activation did not complete")
            raise
        except Exception:
            mail.outcome(cid, "failed", "Account activation did not complete")
            raise ValueError("Workday account activation did not complete") from None
        finally:
            if guard:
                await guard.close()
            e.verification_origins = None
            if hasattr(e.email_verification, "challenge_id"):
                e.email_verification.challenge_id = None
        await page.goto(e.job["url"], wait_until="domcontentloaded", timeout=25000)

    async def reverify(self):
        """An account an earlier run left unverified: request a fresh email, then follow it."""
        page = self.engine.page
        resend = page.get_by_role(
            "button",
            name=re.compile(r"resend|send (?:the )?(?:verification|email) again|verify (?:my )?email", re.I),
        )
        visible = [b for b in await resend.all() if await b.is_visible()]
        if len(visible) == 1:
            e = self.engine
            rule = getattr(e.email_verification, "rule", None)
            if not rule:
                raise ValueError(
                    "Workday needs the account activation email; connect Gmail for this employer"
                )
            self.created_at = time.time() - 5
            if getattr(e.email_verification, "challenge_id", None):
                e.email_verification.close()
            c = e.service.mail.begin(e.run_id, rule, e.form.resolver.profile.email, "link", self.created_at)
            e.email_verification.challenge_id = c["id"]
            await self.click(visible[0])
            e.emit("account", "Requested a new Workday activation email")
            await self.activate()
            return
        # Workday's reset email also proves the address; it sets the shared password too.
        self.activated = True
        await self.recover()

    def save_credentials(self, value, state, **extra):
        with self.engine.db.exclusive() as s:
            row = s.get(Setting, self.account_key())
            saved = {
                "origin": origin(self.engine.job["url"]),
                "credentials": self.engine.service.mail.vault.seal(value),
                "state": state,
                **extra,
            }
            if row:
                row.value = {**row.value, **saved}
            else:
                s.add(Setting(key=self.account_key(), value=saved))
        self.account_state = state

    async def recover(self):
        """Recover this employer account through its authenticated reset email."""
        from .agent_settings import require
        from .email_browser import VerificationGuard

        e, page = self.engine, self.engine.page
        require(e.db, "accounts")
        mail = e.service.mail
        rule = e.email_verification.rule
        if self.recovery_attempted or not rule:
            raise ValueError("Workday account recovery requires a connected mailbox for this employer")
        saved = e.db.get_setting(self.account_key(), {})
        pending_since = saved.get("reset_requested_at", 0)
        resume = e.run_record["packet"].get("email_wait_resume", {})
        resumed_request = resume.get("challenge_id") if resume.get("kind") == "password_reset" else None
        resume_reset = bool(resumed_request) or (
            saved.get("state") == "reset_requested"
            and 0 < time.time() - pending_since < e.config.mail_durable_wait_seconds
        )
        self.recovery_attempted = True
        if not resume_reset:
            forgot = page.get_by_text("Forgot your password?", exact=True)
            if await forgot.count() != 1:
                raise ValueError("Workday password recovery control is unavailable")
            await self.click(forgot)
            reset_button = page.locator('[data-automation-id="resetPasswordButton"]')
            await reset_button.wait_for(state="visible", timeout=10000)
            # Explicit reset-email step inside the Reset Password dialog (modal precedence: the
            # sign-in form behind it has its own email field).
            dialog = page.locator(
                '[role="dialog"]:visible,[data-automation-id="popUpDialog"]:visible'
            ).filter(has=reset_button)
            scope = dialog.last if await dialog.count() else page
            email = scope.locator('input[data-automation-id="email"]')
            if await email.count() != 1:
                raise ValueError("Workday password recovery email field is ambiguous")
            await email.wait_for(state="visible", timeout=10000)
            await self.enter(email, e.form.resolver.profile.email)
        # Account creation and password reset must not reuse the same mail
        # challenge: a reset message has different matching rules.
        if getattr(e.email_verification, "challenge_id", None):
            if e.email_verification.challenge_id != resumed_request:
                e.email_verification.close()
            e.email_verification.challenge_id = None
        challenge = mail.begin(
            e.run_id,
            rule,
            e.form.resolver.profile.email,
            "password_reset",
            pending_since if resume_reset else None,
            resume_challenge_id=resumed_request,
        )
        credentials = self.shared_credentials()
        previous_state = saved.get("state")
        # Keep the working password until the website confirms its replacement.
        self.save_credentials(
            self.credentials() or credentials,
            "reset_requested",
            reset_requested_at=pending_since if resume_reset else time.time(),
        )
        guard = None
        e.emit("account", "Recovering the employer account through the connected mailbox")
        try:
            acknowledged = resume_reset
            if resume_reset:
                e.emit("account", "Resuming the previously requested password reset email")
            else:
                self.responses.clear()
                await self.click(reset_button)
                if hasattr(e, "browser_challenge") and (challenge_result := await e.browser_challenge()):
                    e.result = challenge_result
                    raise ValueError("Account recovery security challenge was not accepted")
                await asyncio.sleep(0.5)
                self.report("password reset")
                acknowledged = bool(ACKNOWLEDGED.search(await page.locator("body").inner_text())) or any(
                    200 <= status < 300 for status, _ in self.responses[-2:]
                )
                e.emit("account", "Employer password reset requested", {"acknowledged": acknowledged})
            try:
                token = await mail.wait(challenge["id"], max_wait_seconds=3)
            except MailPending:
                raise
            except ValueError:
                # Nothing was delivered: the reset is not spent. The cooldown applies only when the
                # website acknowledged sending one (a later email would then supersede this one).
                extra = {} if acknowledged else {"reset_requested_at": 0}
                self.save_credentials(
                    self.credentials() or credentials, previous_state or "created_locally", **extra
                )
                if previous_state not in {
                    "authenticated",
                    "password_reset",
                    "activated",
                    "verification_pending",
                    "exists",
                    "locked",
                } and not getattr(self, "creation_after_reset", False):
                    # A local credential record is not proof of a remote account.
                    # Workday intentionally acknowledges resets for nonexistent emails.
                    self.creation_after_reset = True
                    self.last_action = None
                    self.save_credentials(credentials, "created_locally", reset_requested_at=0)
                    mail.outcome(challenge["id"], "failed", "No reset email for an unconfirmed account")
                    e.emit("account", "No existing account confirmed; trying account creation once")
                    return
                raise ValueError("The Workday password reset email did not arrive") from None
            e.emit("account", "Employer password reset email received")
            self.save_credentials(self.credentials() or credentials, "reset_email_received")
            if token["kind"] != "link":
                raise ValueError("Workday recovery did not provide a password reset link")
            e.verification_origins = set(rule["link_origins"])
            guard = await VerificationGuard(page, e.verification_origins, e.config.allow_private_urls).start()
            await page.goto(token["value"], wait_until="domcontentloaded", timeout=25000)
            fields = page.locator('input[type="password"]:visible')
            await fields.first.wait_for(timeout=20000)
            await fields.nth(1).wait_for(timeout=10000)
            e.emit("account", "Employer password reset form opened")
            if guard.blocked or origin(page.url) not in e.verification_origins or await fields.count() != 2:
                raise ValueError("Workday password reset form could not be verified")
            for field in await fields.all():
                self.secrets.append(await field.element_handle())
                await self.enter(field, credentials["password"])
            button = page.get_by_role(
                "button",
                name=re.compile(r"^(Reset Password|Submit|Change Password)$", re.I),
                include_hidden=True,
            )
            # A click_filter overlay repeats the button's label; the button itself is the target.
            # The paired native button can be aria-hidden while visually present.
            # click() then operates its observed overlay with the auth guard set.
            visible = [
                b
                for b in await button.all()
                if await b.is_visible() and await b.get_attribute("data-automation-id") != "click_filter"
            ]
            if len(visible) != 1:
                raise ValueError("Workday password reset confirmation is ambiguous")
            await self.click(visible[0], auth_submit=True)
            for _ in range(60):
                await asyncio.sleep(0.25)
                body = await page.locator("body").inner_text()
                if re.search(
                    r"password (?:has been|was) (?:successfully )?(?:changed|reset)|"
                    r"password (?:reset|change) (?:was )?successful|"
                    r"password (?:successfully (?:changed|reset)|(?:changed|reset) successfully)|"
                    r"password (?:was |has been )?(?:successfully )?updated|"
                    r"new password (?:has been |was )?(?:saved|set)|you can now sign in",
                    body,
                    re.I,
                ):
                    self.save_credentials(credentials, "password_reset", reset_requested_at=0)
                    mail.outcome(challenge["id"], "verified", "Employer accepted password reset")
                    e.emit("account", "Employer password recovered and saved securely")
                    self.attempted = False
                    return
            if (
                not await page.locator('input[type="password"]:visible').count()
                or await page.locator(
                    '[data-automation-id="signInSubmitButton"]:visible,[data-automation-id="signInLink"]:visible'
                ).count()
            ):
                # The reset form closed without a recognized message. Keep the new
                # password: retaining the old one made every later sign-in fail and
                # request another reset (34 resets requested, 16 recovered).
                self.save_credentials(credentials, "password_reset", reset_requested_at=0)
                mail.outcome(
                    challenge["id"], "verified", "Reset form closed after submitting the new password"
                )
                e.emit("account", "Employer reset form closed; new password saved for sign-in")
                self.attempted = False
                return
            raise ValueError("Workday did not confirm the password reset")
        except MailPending:
            if e.email_verification:
                e.email_verification.challenge_id = challenge["id"]
                e.email_verification.deferred = True
            raise
        except Exception as exc:
            mail.outcome(challenge["id"], "failed", "Employer account recovery did not complete")
            if isinstance(exc, ValueError) and "did not arrive" in str(exc):
                raise
            import traceback

            own = [f for f in traceback.extract_tb(exc.__traceback__) if "/jobpilot/" in f.filename]
            e.emit(
                "account_error",
                "Workday reset did not reach its accepted postcondition",
                {
                    "error_type": type(exc).__name__,
                    "function": own[-1].name if own else "",
                    "line": own[-1].lineno if own else 0,
                },
            )
            raise ValueError(
                "Workday account recovery did not complete; see account and email status"
            ) from None
        finally:
            await self.clear()
            if guard:
                await guard.close()
            e.verification_origins = None
            # Never expose the one-time reset URL to the agent or screenshot ledger.
            if not getattr(e, "result", None) and not getattr(e.email_verification, "deferred", False):
                await page.goto(e.job["url"], wait_until="domcontentloaded", timeout=25000)

    async def posting_already_applied(self):
        """The posting page of a submitted application says "You applied for this job on <date>" with View
        Application and no Apply. State Street 2026-10-17: a second job record for the same requisition spent its
        whole account-setup window on that page."""
        e, page = self.engine, self.engine.page
        if not same_posting_page(page.url, e.job["url"]):
            return None
        from .adapters.base import ALREADY

        try:
            found = ALREADY.search(await page.locator("body").inner_text(timeout=2000))
        except PlaywrightError:
            return None
        if not found:
            return None
        return {
            "state": "already_applied",
            "reason": "The employer says you already applied to this job",
            "receipt": {"type": "employer_already_applied", "url": page.url, "evidence": found[0][:200]},
        }

    async def defer_maintenance(self):
        e, page = self.engine, self.engine.page
        if urlsplit(page.url).hostname != "community.workday.com":
            return False
        until = planned_maintenance_until(page.url, await page.locator("body").inner_text(timeout=3000))
        if not until:
            return False
        e.db.set_setting("ats_outage:workday", {"until": until, "reason": "Workday planned maintenance"})
        e.result = {"state": "waiting_browser", "reason": "Workday planned maintenance until " + until,
                    "recovery_cause": "site_temporary"}
        return True

    async def advance_apply_method(self):
        """Workday's method controls may be anchors as well as buttons."""
        e, page = self.engine, self.engine.page
        preferred = getattr(e, "workday_method", "Autofill with Resume")
        methods = {
            "Autofill with Resume": "autofillWithResume",
            "Apply Manually": "applyManually",
            "Use My Last Application": "useMyLastApplication",
        }
        # Prefer the engine's chosen method, then a blank manual application.
        # Never choose an unrelated link merely because a dialog is present.
        candidates = [methods.get(preferred, "autofillWithResume"), "applyManually"]
        for identity in dict.fromkeys(candidates):
            control = page.locator(f'[data-automation-id="{identity}"]:visible')
            if await control.count() == 1 and await control.is_enabled():
                await self.click(control)
                e.emit("apply_method", "Opened the observed Workday application method", {"method": identity})
                return True
        return False

    async def choose_email_sign_in(self):
        """Tenants that also offer Google/LinkedIn show the email sign-in form only after this choice (RTX)."""
        control = self.engine.page.locator('[data-automation-id="SignInWithEmailButton"]:visible')
        if await control.count() != 1:
            return False
        await self.click(control)
        return True

    async def run(self):
        e, page = self.engine, self.engine.page
        if await self.defer_maintenance():
            return False
        own = detect(e.job["url"]).id == "workday" and origin(page.url) == origin(e.job["url"])
        # The employer's own career site may hand the application to its Workday tenant (Morgan Stanley's Eightfold
        # job page -> ms.wd5.myworkdayjobs.com, 2026-10-19). That observed handoff origin is this employer's tenant.
        handed = getattr(e, "handoff_origin", None) == origin(page.url) and detect(page.url).id == "workday"
        if not (own or handed):
            raise ValueError("Workday account origin does not match this employer")
        if await self.form_ready():
            e.emit("account", "Resumed the existing employer application form")
            return True
        if not e.form.resolver.profile.allow_account_creation:
            return False
        self.watch_responses()
        saved = e.db.get_setting(self.account_key(), {})
        if 0 < time.time() - saved.get("reset_requested_at", 0) < 900 and not await self.form_ready():
            await self.recover()
        unexplained = 0
        blank = 0
        # Iterations that recognised no state (fell through to the bottom of the loop).
        unrecognized = 0  # consecutive
        unrecognized_total = 0
        route_reset_done = False
        for _ in range(60):
            if await self.defer_maintenance():
                return False
            if await self.recover_error_page():
                continue
            if result := await self.posting_already_applied():
                e.result = result
                e.emit("account", "The employer reports an existing application for this posting")
                return False
            # A reopened saved route can leave Workday's app unrendered: blank, or only the
            # header/footer shell with a spinner. Reload once, then drop this site's stored
            # data (a fresh browser renders the same page) and reopen the posting itself.
            from .widgets import page_is_stalled_shell, reset_origin_data

            if await page_is_stalled_shell(page):
                blank += 1
                if blank == 6:
                    e.emit("page_blank", "Workday rendered no application; reloading the page")
                    await _emit_unexplained(e, page, blank)
                    await page.reload(wait_until="domcontentloaded", timeout=20000)
                elif blank == 12:
                    reset = await reset_origin_data(page, e.job["url"])
                    await _emit_unexplained(e, page, blank)
                    e.emit(
                        "page_blank",
                        "Workday is still not rendering; cleared this site's stored data and reopening the posting",
                        {"reset": reset},
                    )
                    await page.goto(e.job["url"], wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(700)
                continue
            blank = 0
            recognized = False
            if proof := await e.form.proof():
                if proof.get("existing_application_notice"):
                    e.result = {
                        "state": "confirmed",
                        "reason": "Employer confirms an existing application for this job; nothing resubmitted",
                        "receipt": {**proof, "reconciled_existing_submission": True},
                    }
                    return True
            # A tab left on Workday's My Applications dashboard after an earlier submit
            # ("Application Submitted" modal, this requisition shown as Application Received).
            if submitted := await _requisition_submitted(page, e.job["url"]):
                e.result = {
                    "state": "confirmed",
                    "reason": "Employer confirms an existing application for this job; nothing resubmitted",
                    "receipt": {
                        "url": page.url,
                        "confirmation": submitted[:300],
                        "title": await page.title(),
                        "type": "explicit_confirmation_page",
                        "evidence_version": 2,
                        **e.form.evidence_context(),
                        "evidence_selector": "workday_my_applications_requisition_row",
                        "status_region": "job_application_status",
                        "reconciled_existing_submission": True,
                    },
                }
                return True
            if re.search(
                r"the page you are looking for (?:does not|doesn't) exist",
                await page.locator("body").inner_text(),
                re.I,
            ):
                if not await page.locator("input:visible,textarea:visible").count():
                    e.result = missing_posting_result(
                        e.db, e.job, e.run_id, page.url, listed=await tenant_search_lists(e.job["url"])
                    )
                    return False
            if hasattr(e, "browser_challenge") and (challenge := await e.browser_challenge()):
                e.result = challenge
                return False
            if re.search(
                r"Workday is currently unavailable|experiencing a service interruption",
                await page.locator("body").inner_text(),
                re.I,
            ):
                raise ValueError(
                    "Workday is temporarily unavailable due to a service interruption. No application was submitted."
                )
            if origin(page.url) != origin(e.job["url"]):
                raise ValueError("Workday changed login origin")
            # Workday's cookie banner (legalNotice) in any locale: RTX's fr-CA banner read "Accepter les cookies".
            cookie = page.locator('[data-automation-id="legalNoticeAcceptButton"]:visible')
            if await cookie.count() != 1:
                cookie = page.get_by_role("button", name="Accept Cookies", exact=True)
            if await cookie.count() and await cookie.is_visible():
                await self.click(cookie)
            # A desktop run can resume inside the Forgot Password dialog left by
            # a previous attempt. Re-enter sign-in before touching background
            # Apply controls; no reset email is requested just to restore the UI.
            reset = page.locator('[data-automation-id="resetPasswordButton"]:visible')
            if await reset.count() and not await page.locator('input[type="password"]:visible').count():
                sign_in = page.locator('[data-automation-id="signInLink"]:visible')
                if await sign_in.count() != 1:
                    raise ValueError("Workday's open password-reset dialog has no unique sign-in control")
                await self.click(sign_in)
                await page.locator('input[data-automation-id="password"]:visible').wait_for(timeout=8000)
                continue
            method = getattr(e, "workday_method", "Autofill with Resume")
            if not await page.locator("input[type=password]:visible").count():
                if await self.advance_apply_method():
                    await e.form.settle(timeout=2, quiet=0.15)
                    continue
                # A dedicated code page after sign-in/sign-up has no password field;
                # its handler used to sit inside the password branch and never ran.
                if e.email_verification.state(await e.form.scan())[0] == "code":
                    recognized = True
                    await e.email_verification.handle()
                    self.last_action = None
                    continue
                # Posting pages often render Apply as a link, not a role=button.
                automation_apply = page.locator(
                    '[data-automation-id="adventureButton"]:visible,[data-automation-id="applyButton"]:visible,'
                    '[data-automation-id="jobPostingApplyButton"]:visible'
                )
                named_apply = page.get_by_role("button", name="Apply", exact=True)
                if await automation_apply.count() == 1 and not await named_apply.count():
                    await self.click(automation_apply)
                    recognized = True
                    await asyncio.sleep(0.4)
                if await self.choose_email_sign_in():
                    recognized = True
                    await asyncio.sleep(0.4)
                for name in ["Apply", "Continue Application", method, "Sign in with email"]:
                    button = page.get_by_role("button", name=name, exact=True)
                    if await button.count() == 1 and await button.is_visible():
                        recognized = True
                        await self.click(button)
                        await asyncio.sleep(0.4)
                        if await page.locator("input[type=password]:visible").count():
                            break  # A sign-in modal owns the step; do not click the job behind it.
            dialogs = page.locator(
                "[role=dialog]:visible,[role=alertdialog]:visible,[aria-modal=true]:visible,[data-automation-id=popUpDialog]:visible,[data-automation-id=wd-Popup]:visible"
            )
            scope = dialogs.last if await dialogs.count() else page
            password = scope.locator('input[data-automation-id="password"]:visible')
            if await password.count() and await password.is_visible():
                recognized = True
                from .agent_settings import require

                require(e.db, "accounts")
                stored = self.credentials()
                error = await self.auth_error()
                if getattr(e, "imported_session", False) and self.account_state not in {
                    "authenticated",
                    "password_reset",
                }:
                    await self.recover()
                    continue
                if self.last_action:
                    # The previous click came back to the account form: read why before acting.
                    action, self.last_action = self.last_action, None
                    if LOCKED.search(error):
                        self.save_credentials(stored or self.shared_credentials(), "locked")
                        raise ValueError(
                            "Workday locked this account after failed sign-ins; sign in manually once"
                        )
                    if action == "create" and EXISTS.search(error):
                        self.created_at = None  # A rejected create did not send activation mail.
                        if getattr(e.email_verification, "challenge_id", None):
                            e.email_verification.close()
                        self.save_credentials(stored or self.shared_credentials(), "exists")
                        link = page.locator('[data-automation-id="signInLink"]')
                        if await link.count():
                            await self.click(link)
                            await asyncio.sleep(0.5)
                        continue
                    if UNVERIFIED.search(error) and not self.activated:
                        if self.created_at is None:
                            # Created by an earlier run: its activation email is old. Ask again.
                            await self.reverify()
                        else:
                            await self.activate()
                        continue
                    if RESET_REQUIRED.search(error) or (action == "sign_in" and WRONG.search(error)):
                        if not self.recovery_attempted:
                            await self.recover()
                        else:
                            raise ValueError("Workday rejected the recovered password")
                        continue
                    if action == "create" and not error:
                        # Account created; Workday returned to Sign In (often pending activation).
                        self.save_credentials(stored or self.shared_credentials(), "verification_pending")
                    elif error:
                        # A message none of the rules explain: never resubmit the same credentials.
                        raise ValueError(f"Workday rejected the {action.replace('_', ' ')}: {error[:200]}")
                    else:
                        unexplained += 1
                        if unexplained > 1:
                            raise ValueError(
                                "Workday returned to sign-in without an error message; "
                                "no password reset was requested"
                            )
                create = scope.locator('[data-automation-id="createAccountLink"]:visible')
                if (
                    self.account_state
                    in {"authenticated", "password_reset", "activated", "verification_pending", "exists"}
                    and await scope.locator('[data-automation-id="verifyPassword"]:visible').count()
                ):
                    await self.click(scope.locator('[data-automation-id="signInLink"]:visible'))
                    await scope.locator('[data-automation-id="verifyPassword"]').wait_for(
                        state="hidden", timeout=8000
                    )
                if (not stored or self.account_state == "created_locally") and await create.count():
                    await self.click(create)
                    await scope.locator('[data-automation-id="verifyPassword"]:visible').wait_for(
                        timeout=8000
                    )
                creating = await scope.locator('[data-automation-id="verifyPassword"]:visible').count() > 0
                creds = stored or self.credentials(create=True)
                email = scope.locator('[data-automation-id="email"]:visible')
                if await email.count() != 1:
                    email = scope.get_by_label("Email Address", exact=False).filter(visible=True)
                await self.enter(email, creds["email"])
                for field in [password, scope.locator('[data-automation-id="verifyPassword"]:visible')]:
                    if await field.count():
                        self.secrets.append(await field.element_handle())
                        await self.enter(field, creds["password"])
                if e.form.resolver.profile.accept_all_application_terms:
                    for checkbox in await page.locator('input[type="checkbox"]:visible').all():
                        await e.form.set_checked(checkbox, True)
                if creating:
                    self.created_at = time.time() - 5
                    await e.email_verification.prepare_submission(kind="link")
                button = scope.locator(
                    '[data-automation-id="createAccountSubmitButton"]'
                    if creating
                    else '[data-automation-id="signInSubmitButton"]'
                )
                if await button.count() != 1:
                    raise ValueError("Workday authentication control was not unique")
                self.attempted = True
                self.last_action = "create" if creating else "sign_in"
                previous_error = await self.auth_error()
                e.emit(
                    "account",
                    "Creating an employer account"
                    if creating
                    else "Signing in with the saved employer account",
                )
                await self.click(button, auth_submit=True)
                if hasattr(e, "browser_challenge") and (challenge := await e.browser_challenge()):
                    e.result = challenge
                    return False
                outcome = await self.settle_after_auth(password, previous=previous_error)
                self.report("create account" if creating else "sign in")
                await self.clear()
                observation = await e.form.scan()
                pending = e.email_verification.state(observation)[0]
                if (
                    outcome == "verify"
                    and pending != "code"
                    and not self.activated
                    and getattr(e.email_verification, "rule", None)
                ):
                    # Activation link: open it, then sign in (the account form returns).
                    if not creating and self.created_at is None:
                        # Sign-in does not send a new activation message. Request a fresh
                        # verification/reset instead of waiting on a window with no request.
                        await self.reverify()
                    else:
                        await self.activate()
                    self.last_action = None
                elif pending and not (await password.count() and await password.is_visible()):
                    # A dedicated verification step only; while the sign-in form is still shown,
                    # the next iteration reads its message first (unverified -> activation).
                    await e.email_verification.handle()
                    self.last_action = None
                if outcome == "ready":
                    self.last_action = None
            # A saved draft can reopen on any section. Resume uploads and legal-name
            # fields may then be absent, even though the application form is ready.
            if await self.form_ready():
                if value := self.credentials():
                    self.save_credentials(value, "authenticated")
                e.emit("account", "Employer application form opened")
                return True
            if recognized:
                unrecognized = 0
            else:
                unrecognized += 1
                unrecognized_total += 1
                if unrecognized_total % 20 == 0:
                    await _emit_unexplained(e, page, unrecognized_total)
                # Stuck on a route other than the posting itself: reopen the plain posting once.
                if (
                    unrecognized >= 30
                    and not route_reset_done
                    and urlsplit(page.url).path != urlsplit(e.job["url"]).path
                ):
                    route_reset_done = True
                    unrecognized = 0
                    try:
                        await page.goto(e.job["url"], wait_until="domcontentloaded", timeout=30000)
                    except PlaywrightError:
                        pass
                    e.emit(
                        "route_reset",
                        "Reopened the plain Workday posting after 30 unrecognized page states",
                        {"path": urlsplit(e.job["url"]).path},
                    )
            await asyncio.sleep(0.5)
        await _emit_unexplained(e, page, unrecognized_total)
        from .widgets import automation_ids

        posting = {
            "jobPostingHeader",
            "jobPostingDescription",
            "jobPostingPage",
            "adventureButton",
            "applyButton",
            "jobPostingApplyButton",
            "progressBar",
        }
        ids = set(await automation_ids(page, 80))
        rendered = "utilityButtonSignIn" in ids or any(i.startswith("navigationItem") for i in ids)
        if same_posting_page(page.url, e.job["url"]) and rendered and not ids & posting:
            # Only Workday's header shell at the posting URL for the whole window (RTX
            # "Private_Posting_No_TMP" 2026-10-14): the requisition is not publicly viewable.
            e.result = missing_posting_result(
                e.db, e.job, e.run_id, page.url, listed=await tenant_search_lists(e.job["url"])
            )
            return False
        raise ValueError("Workday did not reach an application form within the account setup window")
