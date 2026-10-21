"""AccountFlow.handle: one pass over the current account page."""

from urllib.parse import urlsplit
import re
from ..mail import origin
from .dayforce import DAYFORCE_ID_ACCOUNT
from .dayforce import dayforce_id_page
from .dayforce import dayforce_id_password_ok
from .errors import AccountActionError
from .pages import ACCOUNT_EXISTS
from .pages import PASSWORD_MAXIMUM
from .pages import REQUIRED_INPUT
from .pages import login_identifier
from .pages import registration_state
from .tenants import account_tenant


class FlowHandleMixin:
    async def handle(self):
        from ..agent_settings import enabled

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
        creating = len(passwords) > 1 or any(f.get("autocomplete") == "new-password" for f in passwords)
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
            from ..adapters.base import BAD_CREDENTIALS

            refusal = next((x for x in errors if BAD_CREDENTIALS.search(x)), "")
            if not saved or ((scope, False) in self.attempts and refusal and not page_refused):
                return await self.recover_sign_in(
                    scope, refusal or "No saved login for this employer account"
                )
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
            from ..adapters.base import BAD_CREDENTIALS

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
        reuse = creating and saved and 8 <= len(saved.get("password", "")) <= (min(maxima) if maxima else 256)
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
                            "The account form did not keep the credentials; nothing was submitted",
                            "control_failure",
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
        if not buttons and not any(
            f["type"] == "file" and e.form.frames[f["id"]] == frame for f in obs["fields"]
        ):
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
            from ..forms import FINAL

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
