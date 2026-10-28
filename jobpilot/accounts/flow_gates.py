"""AccountFlow steps around the sign-in gate: policy acknowledgement, embedded applications, recovery."""

from urllib.parse import urlsplit
import re
from ..mail import origin
from .errors import AccountActionError
from .tenants import SF_HOSTS


class FlowGatesMixin:
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

    async def open_create_account_form(self, obs):
        """PeopleAdmin (NC State's jobs.ncsu.edu/user/new, 2026-10-26) shows "Create an Account" with its whole form
        hidden behind a "Create account" link, and the navigator asked the owner about the challenge question instead
        of opening it. A registration page with no visible password, whose one create control is that link, opens
        it once; an employer known to hold this email's account is signed in to instead."""
        e = self.e
        if ("gate", "create_form") in self.attempts or any(f["type"] == "password" for f in obs["fields"]):
            return False
        if not any(
            re.fullmatch(r"create (?:an? |your )?account", str(h).strip(), re.I) for h in obs.get("headings", [])
        ):
            return False
        creates = [b for b in obs["controls"] if re.fullmatch(r"\s*create (?:an? |your )?account\s*", b["label"], re.I)]
        url = getattr(e.form.page, "url", "") or ""
        if len(creates) != 1 or getattr(self.store, "known", lambda _url: False)(url):
            return False
        self.attempts.add(("gate", "create_form"))
        e.emit("account", "Opened the employer's account registration form")
        await e.form.click(creates[0]["id"], auth_control=True)
        settle = getattr(e.form, "settle", None)
        if settle:
            await settle(timeout=4)
        e.page = e.form.page
        return True

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
        from ..forms import FINAL

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

    @staticmethod
    def signed_out(obs, url):
        """A SuccessFactors page that offers Sign In and no Sign Out: the account action signed nobody in."""
        if not (urlsplit(url).hostname or "").lower().endswith(SF_HOSTS):
            return False
        labels = [str(b.get("label", "")).strip() for b in obs.get("controls", [])]
        return any(re.fullmatch(r"(?:sign|log) ?in", x, re.I) for x in labels) and not any(
            re.fullmatch(r"(?:sign|log) ?out", x, re.I) for x in labels
        )

    def reset_unanswered(self, url, recorded):
        """This tenant never answered a password reset its page confirmed: the record says so, or its confirmed reset
        is older than ten minutes. A reset the page never confirmed proves nothing (adapters.base.reset_proven)."""
        if recorded not in {"password_unknown", "reset_requested"}:
            return False
        import time

        from ..adapters.base import UNANSWERED_RESET_SECONDS, reset_proven

        record = getattr(self.store, "record", lambda url: {})(url)
        if not reset_proven(record):
            return False
        if recorded == "password_unknown":
            return True
        return time.time() - float(record.get("reset_requested_at") or 0) > UNANSWERED_RESET_SECONDS

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

            from ..db import Job, Run

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
        from ..adapters import for_ats

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
        e.emit(
            "account", "The employer refused the saved login of an existing account; resetting its password"
        )
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
        from ..browser import PAGE_MESSAGES
        from ..captcha import DETECT
        from ..captcha_observation import challenge_frame

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
