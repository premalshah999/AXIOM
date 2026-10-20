"""Workday account setup uses deterministic controls and an encrypted credential store."""

import asyncio
import hashlib
import re
import time
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError

from .ats import detect
from .db import Setting
from .mail import origin
from .accounts import AccountStore


# Explicit Workday account messages. Recovery (a password reset) follows only WRONG.
WRONG = re.compile(
    r"wrong (?:email|password)|incorrect (?:email|password)|invalid (?:email|password|credentials|sign.?in)|"
    r"(?:email|password) (?:is |was )?(?:incorrect|invalid)|does(?: not|n.t) match|not recogni[sz]ed",
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
COOKIE_BUTTONS = re.compile(r"^(?:Accept Cookies|Accept All Cookies|Accept All|Accept)$", re.I)


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

    async def click(self, el):
        handle = await el.element_handle()
        await handle.evaluate(
            "e=>{e.dataset.jpAuthControl='true';const f=e.closest('form');if(f)f.dataset.jpAuthControl='true'}"
        )
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
            if overlay and await overlay.count() == 1 and await overlay.is_visible():
                clicked_overlay = await overlay.element_handle()
                await clicked_overlay.evaluate("e=>e.dataset.jpAuthControl='true'")
                target = clicked_overlay
            else:
                target = handle
            try:
                await target.click(timeout=5000)
            except PlaywrightError:
                # A cookie notice or backdrop intercepts the pointer: clear it, then retry once.
                await self.dismiss_cookies()
                try:
                    await target.click(timeout=4000)
                except PlaywrightError:
                    # Dispatch on this exact element, never at screen coordinates.
                    await target.evaluate("e=>e.click()")
        finally:
            try:
                await handle.evaluate(
                    "e=>{delete e.dataset.jpAuthControl;const f=e.closest('form');if(f)delete f.dataset.jpAuthControl}"
                )
                if clicked_overlay:
                    await clicked_overlay.evaluate("e=>delete e.dataset.jpAuthControl")
            except Exception:
                pass

    async def dismiss_cookies(self):
        page = self.engine.page
        for button in await page.get_by_role("button", name=COOKIE_BUTTONS).all():
            try:
                if await button.is_visible():
                    await button.click(timeout=3000)
                    return True
            except PlaywrightError:
                continue
        from .forms import COOKIE_JS

        for frame in page.frames[:3]:
            try:
                if await frame.evaluate(COOKIE_JS):
                    return True
            except PlaywrightError:
                continue
        return False

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

        def seen(response):
            try:
                if response.request.method in {"POST", "PUT"} and origin(response.url) == home:
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
        ready = (
            await uploads.count() > 0
            or await page.locator('[data-automation-id="legalNameSection_firstName"]').count() > 0
            or (await advance.count() == 1 and await advance.is_visible())
            or at_review
        )
        return ready and not await page.locator('input[type="password"]:visible').count()

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

        e, page = self.engine, self.engine.page
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
            token = await mail.wait(cid)
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
        from .email_browser import VerificationGuard

        e, page = self.engine, self.engine.page
        mail = e.service.mail
        rule = e.email_verification.rule
        if self.recovery_attempted or not rule:
            raise ValueError("Workday account recovery requires a connected mailbox for this employer")
        saved = e.db.get_setting(self.account_key(), {})
        pending_since = saved.get("reset_requested_at", 0)
        resume_reset = 0 < time.time() - pending_since < 900
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
            e.email_verification.close()
            e.email_verification.challenge_id = None
        challenge = mail.begin(
            e.run_id,
            rule,
            e.form.resolver.profile.email,
            "password_reset",
            pending_since if resume_reset else None,
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
                token = await mail.wait(challenge["id"])
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
                "button", name=re.compile(r"^(Reset Password|Submit|Change Password)$", re.I)
            )
            # A click_filter overlay repeats the button's label; the button itself is the target.
            visible = [
                b
                for b in await button.all()
                if await b.is_visible() and await b.get_attribute("data-automation-id") != "click_filter"
            ]
            if len(visible) != 1:
                raise ValueError("Workday password reset confirmation is ambiguous")
            await self.click(visible[0])
            for _ in range(60):
                await asyncio.sleep(0.25)
                body = await page.locator("body").inner_text()
                if re.search(
                    r"password (?:has been|was) (?:successfully )?(?:changed|reset)|"
                    r"password (?:reset|change) (?:was )?successful|"
                    r"password (?:successfully (?:changed|reset)|(?:changed|reset) successfully)",
                    body,
                    re.I,
                ):
                    self.save_credentials(credentials, "password_reset", reset_requested_at=0)
                    mail.outcome(challenge["id"], "verified", "Employer accepted password reset")
                    e.emit("account", "Employer password recovered and saved securely")
                    self.attempted = False
                    return
            raise ValueError("Workday did not confirm the password reset")
        except Exception as exc:
            mail.outcome(challenge["id"], "failed", "Employer account recovery did not complete")
            if isinstance(exc, ValueError) and "did not arrive" in str(exc):
                raise
            raise ValueError(
                "Workday account recovery did not complete; see account and email status"
            ) from None
        finally:
            await self.clear()
            if guard:
                await guard.close()
            e.verification_origins = None
            # Never expose the one-time reset URL to the agent or screenshot ledger.
            if not getattr(e, "result", None):
                await page.goto(e.job["url"], wait_until="domcontentloaded", timeout=25000)

    async def run(self):
        e, page = self.engine, self.engine.page
        if detect(e.job["url"]).id != "workday" or origin(page.url) != origin(e.job["url"]):
            raise ValueError("Workday account origin does not match this employer")
        if not e.form.resolver.profile.allow_account_creation:
            return False
        self.watch_responses()
        saved = e.db.get_setting(self.account_key(), {})
        if 0 < time.time() - saved.get("reset_requested_at", 0) < 900 and not await self.form_ready():
            await self.recover()
        unexplained = 0
        for _ in range(60):
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
            for name in ["Apply", "Continue Application", method, "Sign in with email"]:
                button = page.get_by_role("button", name=name, exact=True)
                if await button.count() == 1 and await button.is_visible():
                    await self.click(button)
                    await asyncio.sleep(0.4)
            password = page.locator('input[data-automation-id="password"]')
            if await password.count() and await password.is_visible():
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
                    if action == "sign_in" and WRONG.search(error):
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
                create = page.locator('[data-automation-id="createAccountLink"]')
                if (
                    self.account_state
                    in {"authenticated", "password_reset", "activated", "verification_pending", "exists"}
                    and await page.locator('[data-automation-id="verifyPassword"]').count()
                ):
                    await self.click(page.locator('[data-automation-id="signInLink"]'))
                    await page.locator('[data-automation-id="verifyPassword"]').wait_for(
                        state="detached", timeout=8000
                    )
                if (not stored or self.account_state == "created_locally") and await create.count():
                    await self.click(create)
                    await page.locator('[data-automation-id="verifyPassword"]').wait_for(timeout=8000)
                creating = await page.locator('[data-automation-id="verifyPassword"]').count() > 0
                creds = stored or self.credentials(create=True)
                email = page.locator('[data-automation-id="email"]')
                if await email.count() != 1:
                    email = page.get_by_label("Email Address", exact=False)
                await self.enter(email, creds["email"])
                for field in [password, page.locator('[data-automation-id="verifyPassword"]')]:
                    if await field.count():
                        self.secrets.append(await field.element_handle())
                        await self.enter(field, creds["password"])
                if e.form.resolver.profile.accept_all_application_terms:
                    for checkbox in await page.locator('input[type="checkbox"]:visible').all():
                        await checkbox.check()
                if creating:
                    self.created_at = time.time() - 5
                    await e.email_verification.prepare_submission()
                button = page.locator(
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
                await self.click(button)
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
            await asyncio.sleep(0.5)
        raise ValueError("Workday did not reach an application form within the account setup window")
