"""AccountFlow steps for Dayforce guest applications and Dayforce ID."""

from urllib.parse import urlsplit
import asyncio
import re
import secrets
import time
from .dayforce import DAYFORCE_ID_ACCOUNT
from .dayforce import DAYFORCE_ID_OUTCOME_SECONDS
from .dayforce import DAYFORCE_ID_SIGNALS
from .dayforce import dayforce_id_outcome
from .dayforce import dayforce_id_page
from .errors import AccountActionError
from .pages import GUEST
from .pages import SECURITY_ANSWER
from .pages import SECURITY_QUESTION
from .pages import registration_state


class FlowDayforceMixin:
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

        notice = " ".join(
            [str(d.get("text", "")) for d in obs.get("dialogs", [])] + [obs.get("text", "")[:2000]]
        )
        if (go := single(r"continue")) and re.search(
            r"it looks like you have an existing account", notice, re.I
        ):
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
                (
                    o
                    for o in (group or {}).get("options", [])
                    if re.search(r"do not want to use", str(o), re.I)
                ),
                None,
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
                e.emit(
                    "account", "Dayforce ID already has this account; signing in instead of registering again"
                )
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
        if (
            page == "sign_in"
            and ("dayforce_id", "signin") in self.attempts
            and re.search(r"incorrect email address or password", obs.get("text", ""), re.I)
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
        from ..captcha_observation import challenge_frame

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
            limit = (
                DAYFORCE_ID_OUTCOME_SECONDS if info.get("captcha") else min(3, DAYFORCE_ID_OUTCOME_SECONDS)
            )
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
                "messages": [str(x)[:160] for x in [*info.get("validation", []), *info.get("toasts", [])]][
                    :4
                ],
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
                "reason": f"Dayforce ID {step.replace('_', ' ')} refused the form: "
                + (messages or "fields invalid"),
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
        from ..widgets import close_picker, placeholder_option

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
        # PeopleAdmin (NC State's jobs.ncsu.edu/user/new, 2026-10-26) asks its "Challenge Question" on the registration
        # form itself, beside the new password: those answers are typed here and the account's Create sends them.
        combined = bool(frame) and any(
            f["type"] == "password" and e.form.frames.get(f["id"]) == frame for f in fields
        )
        if combined:
            pairs = [(q, a) for q, a in pairs if not str(a.get("value") or "").strip()]
        # Only an identity/account page: an application's own questions never reach this shortcut.
        if (
            not pairs
            or ("security_questions", frame.url) in self.attempts
            or not (
                combined or re.search(r"account|regist|sign.?up|identity|security", urlsplit(frame.url).path, re.I)
            )
        ):
            return False
        if not combined:
            self.attempts.add(("security_questions", frame.url))
        chosen, answers = set(), {}
        for question, answer in pairs:
            if question["type"] == "select" and question.get("options"):
                options = [o for o in map(str, question["options"]) if o.strip() and not placeholder_option(o)]
            else:
                observed = await e.form.inspect_control_options(question)
                await close_picker(e.form.locator(question["id"]), frame)
                options = [
                    o["text"]
                    for o in observed.get("options", [])
                    if o.get("text") and not o.get("disabled") and not placeholder_option(o["text"])
                ]
            pick = next((o for o in options if o not in chosen), None)
            if pick is None:
                raise AccountActionError(
                    "The security question picker offered no unused question", "control_failure"
                )
            chosen.add(pick)
            await e.form.fill(question, pick)
            value = "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(10))
            el = await e.form.locator(answer["id"]).element_handle()
            self.secret_fields.append(el)
            await el.fill(value)
            await el.evaluate("e=>e.blur()")
            answers[pick] = value
        if not self.store.remember(frame.url, create=combined, security_answers=answers):
            # The answers must outlive the run; never submit questions nobody could answer again.
            raise AccountActionError("Security answers have no account record to be saved with", "account")
        if combined:
            e.emit("account", "Answered the account's challenge question; the answer is saved with the account")
            return False  # The registration's own Create sends them with the credentials.
        nexts = [
            b
            for b in obs["controls"]
            if e.form.frames.get(b["id"]) == frame
            and re.fullmatch(r"\s*(?:next|continue|save(?: and continue)?)\s*", b["label"], re.I)
        ]
        if len(nexts) != 1:
            raise AccountActionError(
                "The security questions page has no single Next action", "control_failure"
            )
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
            {
                "path": path[:120],
                "headings": headings,
                "errors": [str(x)[:160] for x in after.get("errors", [])][:4],
            },
        )
        saved = self.store.get(frame.url)
        if saved and registration_state(True, path, headings, 0) == "verification_pending":
            self.store.save(frame.url, saved, "verification_pending")
        return True
