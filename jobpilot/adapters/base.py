"""Deterministic application driver: observe -> classify step -> act -> verify, with progress limits.

The model is used only to answer questions the profile/knowledge base cannot. Navigation, accounts,
email verification and submission are code paths with explicit success checks."""

import asyncio
import re
from ..db import now
import time
from collections import Counter
from enum import StrEnum
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError

from ..service import Service
from ..accounts import Accounts
from ..captcha_observation import ARM_JS
from ..forms import AUTH, FINAL
from ..privacy import acknowledge_privacy_notice, dismiss_cookies
from ..mail_errors import MailPending, code_inline_wait

APPLY = re.compile(
    r"^(?:apply|apply now|apply here|apply for (?:this )?(?:job|position|role|opportunity)|apply online|"
    r"apply for this job online|start (?:your |my )?application|apply to (?:this )?(?:job|position)|"
    # XPO's SuccessFactors site (2026-10-21): "Apply now »" was no way forward and the guided agent took the run.
    r"i'?m interested|begin (?:your )?application|easy apply|apply on company (?:site|website)|apply today)"
    r"(?:\s*[>›→»])?$",
    re.I,
)
NOT_APPLY = re.compile(r"linkedin|indeed|seek\b|glassdoor|google|facebook|refer|share|save job|alert", re.I)
NEXT = re.compile(
    r"^(?:next|continue|save (?:and|&) continue|save (?:and|&) next|next step|proceed|continue to (?:next step|"
    r"application|apply)|review(?: (?:my )?application)?|go to review|next page|start|get started|continue application)"
    r"(?:\s*[>›→»])?$",
    re.I,
)
CONFIRM_TEXT = re.compile(
    r"thank you for (?:applying|your (?:job )?application|your interest|submitting)|"
    r"application (?:has been |was )?(?:successfully )?(?:submitted|received|complete)|"
    r"we(?:'ve| have) received your application|successfully (?:applied|submitted)|"
    r"your application (?:is|has been) (?:complete|submitted|on its way|received)|"
    r"you(?:'ve| have) (?:successfully )?applied|application submitted|"
    r"congratulations.{0,60}(?:submitted|applied)|thank you for sending us your online job submission|"
    r"your application (?:has been|was) sent|thanks? (?:you )?for (?:applying|your application)",
    re.I,
)
# Explicit statements only. "Already applied? Sign in" footers and drafts are not applications.
ALREADY = re.compile(
    r"you already applied (?:for|to) this (?:job|position)(?:[.!]|$)|"
    r"you(?:'ve| have) already applied|you have previously applied|already submitted an application|"
    r"application for this (?:job|position|role) (?:has )?already been submitted|"
    # Workday posting page after an application (HPE 2026-10-15): "You applied for this job on October 15, 2026."
    r"you applied for this (?:job|position|role) on\b",
    re.I,
)
CLOSED = re.compile(
    r"no longer (?:accepting (?:applications)?|available|active|open)|position has been filled|"
    r"job (?:posting |ad |listing |opening )?(?:has (?:been )?)?(?:closed|expired|removed)|this (?:job|position|requisition) (?:is )?"
    r"(?:closed|unavailable|no longer)|job not found|the page you are looking for (?:does not|doesn't) exist",
    re.I,
)
CHECK_EMAIL = re.compile(
    r"check your (?:email|inbox)|(?:verification|confirmation|activation) (?:email|link) (?:has been |was )?sent|"
    r"we(?:'ve| have)? (?:just )?sent (?:you )?(?:an? )?(?:email|link|verification email)|verify your (?:email|account)|"
    r"activate your account|confirm your email|account (?:needs to be|must be) verified",
    re.I,
)
CODE_TEXT = re.compile(
    r"verification code|security code|one.time (?:code|passcode|password|pin)|enter (?:the |your )?(?:\d.digit )?"
    r"(?:code|pin)|confirm your identity|we(?:'ve)? sent (?:a |you a )?(?:\d.digit )?(?:code|pin)|passcode",
    re.I,
)
STRONG_CODE_LABEL = re.compile(
    r"verif|security|authenticat|confirmation|one.time|e.?mail|\botp\b|passcode|validation|access code", re.I
)


def dedicated_code_step(fields, codes):
    """A code step is its own screen or modal: a digit-box group, or at most two other inputs.

    John Deere's (Eightfold) one-page application has a code-like field among name, email,
    phone and resume fields; treating it as an email-code step parked 10 runs on a mail
    that was never sent (2026-10-13)."""
    if len(codes) >= 4:
        return True
    if any(STRONG_CODE_LABEL.search(str(f.get("label", ""))) for f in codes):
        return True  # An explicit verification-code label keeps the mixed-step safety review.
    ids = {id(f) for f in codes}
    others = [
        f
        for f in fields
        if id(f) not in ids and f.get("type") not in {"hidden", "checkbox", "radio", "button"}
    ]
    return len(others) <= 2


def code_label(label):
    """A label without required-field markers: SAP's passcode field is "* Passcode:"."""
    return re.sub(r"^[\s*]+|[\s*]+$", "", str(label or ""))


CODE_LABEL = re.compile(
    r"^(?:(?:enter|type)(?: the| your)? )?(?:(?:email|verification|security|authentication|confirmation|one.time|"
    r"access)(?: verification)? )?(?:code|passcode|otp|pin(?: code)?)(?: \d+)?[: ]*$|^digit \d+$|^pin code \d$",
    re.I,
)
SMS = re.compile(
    r"(?:sent|texted).{0,60}(?:your (?:phone|mobile)|sms|text message)|(?:sms|text message) (?:code|verification)|"
    r"authenticator app|passkey|security key",
    re.I,
)
CREATE_LINK = re.compile(
    r"^(?:create (?:an? )?(?:account|profile)|sign up|register|new user|join(?: now)?|new candidate|"
    r"don'?t have an account\??.{0,20}|create account)$",
    re.I,
)
SIGNIN_LINK = re.compile(
    r"^(?:sign in|log ?in|already have an account\??.{0,20}|returning (?:user|candidate)|existing (?:user|candidate))$",
    re.I,
)
FORGOT = re.compile(
    r"forgot (?:your )?password|reset (?:your )?password|can'?t sign in|trouble signing in", re.I
)
CONSENT_MENUS = {"select", "combobox", "dropdown", "radio"}
CONSENT_LABEL = re.compile(r"acknowledg|agree|consent|accept|privacy|terms", re.I)
AUTH_BUTTON = re.compile(
    r"^(?:sign in|log ?in|create (?:an? )?account|register|sign up|submit|continue|next|verify|confirm|"
    r"send|reset(?: password)?|save|create profile|set password|change password)$",
    re.I,
)
BAD_CREDENTIALS = re.compile(
    r"invalid (?:user ?name|e-?mail|credentials|login|password|sign.?in)|incorrect (?:e-?mail|password|user|credentials)|"
    r"wrong (?:e-?mail|password|user ?name)|(?:e-?mail|password|credentials|user ?name) (?:is |are |was )?(?:incorrect|invalid|not valid)|"
    r"does(?: not|n'?t) match|unable to sign in|sign.?in failed|no account|account (?:was )?not found|not registered|"
    r"couldn'?t find (?:an |your )?account|does not correspond to a registered user|"
    # SAP SuccessFactors: "We couldn't authenticate you. Check your username and password."
    r"couldn'?t authenticate|could not authenticate|check your (?:user ?name|e-?mail)(?: and| or) password|"
    r"authenticat\w* (?:failed|unsuccessful)",
    # Taleo uses this wording for a username with no account, before offering New User.
    re.I,
)
NOT_FOUND = re.compile(
    r"no account|account (?:was )?not found|not registered|couldn'?t find (?:an |your )?account|"
    r"does not correspond to a registered user|(?:user|account) (?:does not|doesn'?t) exist|no user (?:found|with)",
    re.I,
)
EXISTS = re.compile(
    r"already (?:exists|registered|in use|associated|taken|have an account)|account (?:already )?exists|"
    r"account with this e-?mail|e-?mail (?:address )?(?:is )?already|user ?name (?:is )?(?:already|taken|not available)",
    re.I,
)
LOCKED = re.compile(
    r"account (?:is |has been )?locked|too many (?:failed )?(?:attempts|tries)|temporarily (?:locked|disabled)",
    re.I,
)
PASSWORD_RULES = re.compile(
    r"password (?:must|should|needs to|requirements)|at least \d+ characters|must contain|not meet.{0,40}requirements",
    re.I,
)
NOT_APPLICATION = re.compile(
    r"search|keyword|filter|sort by|language|cookie|subscribe|job alert|newsletter", re.I
)
# A job search's radius picker beside its postal-code box. XPO's job page (2026-10-21) showed only "Search by Postal
# Code" and "Distance", was read as a form with no way forward, and the guided agent took every run.
SEARCH_RADIUS = re.compile(r"\s*(?:distance|radius|within)\s*:?\s*", re.I)


# A reset whose email has not come within this time is unanswered: the employer mails within seconds when it can.
UNANSWERED_RESET_SECONDS = 600
PASSWORD_UNKNOWN = (
    "This employer has an account for your email but refuses the saved password, and its password reset email never "
    "arrives. Reset the password in your own browser, then save it under Setup > Accounts"
)


# Account states meaning "an account exists here": sign in rather than create.
KNOWN_ACCOUNT = {
    "authenticated",
    "verification_pending",
    "reset_requested",
    "password_reset",
    "exists",
    "locked",
    "credentials_rejected",
    "password_unknown",
}


class Step(StrEnum):
    JOB = "job_details"
    METHOD = "apply_method"
    EMAIL_ENTRY = "email_entry"
    SIGN_IN = "sign_in"
    CREATE_ACCOUNT = "create_account"
    VERIFY_LINK = "verify_link"
    EMAIL_CODE = "email_code"
    RESET_PASSWORD = "reset_password"
    CONSENT = "consent"
    FORM = "form"
    REVIEW = "review"
    CONFIRMATION = "confirmation"
    ALREADY_APPLIED = "already_applied"
    CLOSED = "closed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class Adapter:
    id = "custom"
    name = "Career site"
    max_steps = 45
    # Labels that mean "submit the whole application" on this ATS.
    final = FINAL
    next_ids: tuple[str, ...] = ()
    back_ids: tuple[str, ...] = ()
    apply_ids: tuple[str, ...] = ()
    # Element ids of the email step's own submit, whose label varies by employer.
    email_submit_ids: tuple[str, ...] = ()

    def __init__(self, engine, ats_id=None, name=None):
        if ats_id:
            self.id = ats_id
        if name:
            self.name = name
        self.e = engine
        self.service, self.config = engine.service, engine.config
        self.job, self.run_record = engine.job, engine.run_record
        self.accounts = Accounts(self.service, engine.profile, self.job["url"])
        self.inbox = self.service.inbox
        self.signatures = Counter()
        self.attempts = Counter()
        self.step_no = 0
        self.challenge_since = time.time() - 90
        self.challenge = None  # The open mail request window (MailService challenge).
        self.committed = False
        self.before_commit = None
        self.commit_pages = set()  # Signatures of pages whose final control was clicked.
        self.post_commit_steps = 0
        self.apply_url = None
        self.last_step = None

    # ----- plumbing -----------------------------------------------------------------------------
    @property
    def form(self):
        return self.e.form

    @property
    def page(self):
        return self.e.form.page

    def emit(self, kind, message, data=None):
        self.e.emit(kind, message, data)

    def site_lock(self, obs):
        """A result when the page says the site has locked this applicant out for a while, else None."""
        return None

    def defer(self, reason, reviews=None, state="needs_review", *, blocker_type=None):
        reviews = reviews or []
        if (
            state == "needs_review"
            and self.config.browser_cdp_url
            and reviews
            and all(r.get("key") in {"session", "manual"} for r in reviews)
        ):
            # With the desktop Chrome, a browser-fixable blocker keeps its tab open for you.
            return {
                "state": "waiting_browser",
                "reason": reason[:900] + ". The browser tab is retained for recovery.",
                "reviews": reviews,
            }
        result = {"state": state, "reason": reason[:1000], "reviews": reviews}
        if blocker_type:
            result["blocker_type"] = blocker_type
        if state == "waiting_browser" and not reviews and blocker_type not in {"account", "email_wait"}:
            result["recovery_cause"] = "control_observation"
        return result

    @staticmethod
    def prepare_url(url):
        return url

    async def dismiss_overlays(self):
        await dismiss_cookies(self.page, authorized=self.form.resolver.profile.accept_all_application_terms)

    def handoff(self, url):
        """Career sites hand the application to the real ATS (Phenom -> Workday, site -> Taleo)."""
        from ..ats import detect
        from . import REGISTRY

        current = detect(url).id
        if current != self.id and current in REGISTRY and not self.committed:
            return current
        return None

    def signature(self, step, obs):
        from ..browser_contracts import fingerprint

        return step.value + ":" + fingerprint(obs)

    def app_fields(self, obs):
        return [
            f
            for f in obs["fields"]
            if f["type"] == "file"
            or (
                not NOT_APPLICATION.search(f["label"])
                and not SEARCH_RADIUS.fullmatch(f["label"])
                and f["label"] != "Unlabelled field"
            )
        ]

    def buttons(self, obs, pattern, ids=()):
        hits = [
            b
            for b in obs["controls"]
            if (b["automation_id"] and b["automation_id"] in ids)
            or pattern.search(b["label"])
            or (b["aria_label"] and pattern.search(b["aria_label"]))
        ]
        return hits

    def find_next(self, obs):
        # A job-board listing's "Next page" (with "Previous page" and 1/2/3) pages
        # through other postings; it is not this application's Next (Veryable).
        labels = [c.get("label", "").strip().lower() for c in obs.get("controls", [])]
        pagination = "previous page" in labels or sum(label.isdigit() for label in labels) >= 2
        hits = [
            b
            for b in self.buttons(obs, NEXT, self.next_ids)
            if not self.final.search(b["label"])
            and not re.search(r"^back|previous|cancel|save (?:as )?draft", b["label"], re.I)
            and not (pagination and b["label"].strip().lower() == "next page")
        ]
        if not hits:
            return None
        rank = ["save and continue", "save & continue", "continue", "next", "review"]

        def score(b):
            label = b["label"].lower()
            return (
                0 if b["automation_id"] in self.next_ids else 1,
                next((i for i, r in enumerate(rank) if label.startswith(r)), len(rank)),
            )

        best = sorted(hits, key=score)
        top = [b for b in best if score(b) == score(best[0])]
        return top[-1]  # bottom navigation when duplicated top/bottom

    def find_final(self, obs):
        hits = [
            b
            for b in obs["controls"]
            if self.final.search(b["label"])
            and not b.get("stepper")  # Dayforce's progress step "Submit" is not the button (2026-10-16)
            and not re.search(
                r"code|search|feedback|question|draft|resume only|cancel|request|privacy|cookie",
                b["label"],
                re.I,
            )
        ]
        labels = {b["label"].strip().lower() for b in hits}
        if not hits:
            return None
        if len(labels) == 1:
            return hits[-1]
        return None

    async def observed_final(self, obs):
        if final := self.find_final(obs):
            return final
        observe = getattr(self.form, "application_submit_controls", None)
        prepared = await observe() if observe else []
        return prepared[0] if len(prepared) == 1 else None

    def find_apply(self, obs):
        hits = [
            b
            for b in obs["controls"]
            if (
                b["automation_id"] in self.apply_ids
                or APPLY.search(b["label"])
                or APPLY.search(b["aria_label"])
            )
            and not NOT_APPLY.search(b["label"] + " " + b["href"])
        ]
        if not hits:
            return None
        preferred = [b for b in hits if b["automation_id"] in self.apply_ids]
        return (preferred or hits)[0]

    def code_fields(self, obs):
        from ..email_browser import autocomplete_code_field

        codes = [
            f
            for f in obs["fields"]
            if f["type"] in {"text", "tel", "number", "password"}
            and (
                CODE_LABEL.search(code_label(f["label"]))
                or autocomplete_code_field(f, obs["text"])
            )
        ]
        if not codes and CODE_TEXT.search(obs["text"]):
            digits = [
                f for f in obs["fields"] if f["maxlength"] == 1 and f["type"] in {"text", "tel", "number"}
            ]
            if 4 <= len(digits) <= 10:
                codes = digits
        return codes

    # ----- classification -----------------------------------------------------------------------
    def classify(self, obs):
        dialog = self.dialog_view(obs)
        if dialog is not None:
            # An open dialog (reset password, sign in, terms) is the step, not the page behind it.
            return self.classify_page(dialog)
        return self.classify_page(obs)

    @staticmethod
    def dialog_view(obs):
        """The observation restricted to the top-most open modal dialog, if it holds controls."""
        dialogs = [d for d in obs.get("dialogs", []) if d.get("field_ids") or d.get("control_ids")]
        if not dialogs:
            return None
        top = dialogs[-1]
        fields = [f for f in obs["fields"] if f["id"] in top["field_ids"]]
        controls = [c for c in obs["controls"] if c["id"] in top["control_ids"]]
        if not fields and not [
            c for c in controls if not re.fullmatch(r"close|x|×|cancel", c["label"], re.I)
        ]:
            return None
        return {
            **obs,
            "fields": fields,
            "controls": controls,
            "text": top.get("text", ""),
            "headings": [top["title"]] if top.get("title") else [],
            "automation": top.get("automation", []),
            "dialog": True,
        }

    def classify_page(self, obs):
        text = obs["text"]
        fields = self.app_fields(obs)
        passwords = [f for f in obs["fields"] if f["type"] == "password"]
        meaningful = [f for f in fields if f["type"] != "checkbox"]
        # A job page saying "thank you for your interest" still has an Apply button.
        if (
            CONFIRM_TEXT.search(text)
            and len(meaningful) <= 1
            and not passwords
            and (self.committed or not self.find_apply(obs))
        ):
            return Step.CONFIRMATION
        if ALREADY.search(text) and not passwords and len(meaningful) <= 1 and not self.find_apply(obs):
            return Step.ALREADY_APPLIED
        codes = self.code_fields(obs)
        if codes and (self.committed or dedicated_code_step(obs["fields"], codes)):
            return Step.BLOCKED if SMS.search(text) else Step.EMAIL_CODE
        if (
            not passwords
            and FORGOT.search(text + " " + " ".join(obs.get("headings", [])))
            and [
                f
                for f in meaningful
                if f["type"] == "email" or re.search(r"e-?mail|user ?name", f["label"], re.I)
            ]
            and self.auth_button(obs, "reset")
        ):
            return Step.RESET_PASSWORD
        if passwords:
            # Some real sign-up forms ask once and retain a Sign in link in the header.
            create_path = re.search(
                r"/(?:create[-_]account|sign[-_]?up|register)(?:/|$)", urlsplit(obs.get("url", "")).path, re.I
            )
            create_control = any(
                re.fullmatch(r"create(?: (?:an? )?(?:account|profile))?|sign up|register", b["label"], re.I)
                and not b.get("href")
                for b in obs["controls"]
            )
            new_password = any(f.get("autocomplete") == "new-password" for f in passwords)
            return (
                Step.CREATE_ACCOUNT
                if len(passwords) >= 2 or create_path or (create_control and new_password)
                else Step.SIGN_IN
            )
        if CHECK_EMAIL.search(text) and len(meaningful) <= 1 and not self.find_apply(obs):
            return Step.VERIFY_LINK
        if not meaningful and CLOSED.search(text) and not self.find_apply(obs):
            return Step.CLOSED
        emails = [f for f in meaningful if f["type"] == "email" or re.search(r"e-?mail", f["label"], re.I)]
        if (
            emails
            and len(meaningful) == len(emails)
            and (self.find_next(obs) or self.buttons(obs, AUTH_BUTTON))
        ):
            return Step.EMAIL_ENTRY
        if meaningful or any(f["type"] == "file" for f in fields):
            return Step.FORM
        if self.find_final(obs) and not self.find_apply(obs):
            return Step.REVIEW
        if self.find_apply(obs):
            return Step.JOB
        if [
            b
            for b in obs["controls"]
            if re.search(r"^(?:i )?(?:accept|agree)(?: and continue)?$", b["label"], re.I)
        ]:
            return Step.CONSENT
        if self.find_next(obs):
            return Step.FORM
        return Step.UNKNOWN

    # ----- main loop ----------------------------------------------------------------------------
    async def drive(self):
        suspended_email = False
        try:
            return await self._drive()
        except MailPending:
            # BrowserEngine records the durable wait only after this exception
            # reaches it. Its result is still empty during our finally block;
            # cancelling here destroys the request before later mail can match.
            suspended_email = True
            raise
        finally:
            if not suspended_email:
                self.release_mail()

    async def _drive(self):
        for _ in range(self.max_steps):
            recover = getattr(self.e, "recover_workday_error", None)
            if recover and await recover():
                continue
            if getattr(self.e, "result", None):
                return self.e.result
            await self.form.settle(timeout=10)
            await self.dismiss_overlays()
            if await self.captcha_open():
                # An open challenge (e.g. hCaptcha after an email step) is solved before anything
                # else; the scanner never shows CAPTCHA frames as fields or controls.
                if challenge := await self.solve_captcha():
                    return challenge
            obs = await self.form.scan()
            from ..browser_contracts import missing_posting_notice

            if not self.committed and missing_posting_notice(obs, self.job["url"]):
                return await self.on_closed(obs)
            shell_recovery = getattr(self.e, "recover_oracle_job_shell", None)
            if shell_recovery and await shell_recovery(obs):
                continue
            if await self.e.refresh_expired_entry(obs):
                continue
            moved = self.handoff(obs["url"])
            if moved:
                return {"handoff": moved}
            # Modal precedence: an open dialog is the current step; act only on its controls.
            obs = self.dialog_view(obs) or obs
            step = self.classify_page(obs)
            if step != Step.EMAIL_ENTRY:
                self.pending_auth_transition_source = None
            if self.committed and step not in {Step.CONFIRMATION, Step.ALREADY_APPLIED}:
                self.post_commit_steps += 1
                if self.post_commit_steps > 4:
                    return {
                        "state": "submission_unknown",
                        "reason": "Submitted, but no confirmation page appeared",
                    }
            sig = self.signature(step, obs)
            self.signatures[sig] = (
                self.signatures[sig] + 1 if sig == getattr(self, "last_signature", None) else 1
            )
            self.last_signature = sig
            self.step_no += 1
            await self.e.trace(self.step_no, step.value, obs)
            if self.signatures[sig] > 3:
                return self.defer(
                    f"No progress on the {step.value.replace('_', ' ')} step: " + "; ".join(obs["errors"][:3])
                )
            if step != self.last_step:
                self.emit(
                    "step", f"{self.name}: {step.value.replace('_', ' ')}", {"url": self.e.redact(obs["url"])}
                )
            self.last_step = step
            result = await getattr(self, "on_" + step.value)(obs)
            if isinstance(result, dict):
                return result
        return self.defer("The application has more steps than the configured limit")

    # ----- step handlers ------------------------------------------------------------------------
    async def on_job_details(self, obs):
        button = self.find_apply(obs)
        if not button:
            return {"fallback": True, "reason": "No apply control found"}
        self.attempts["apply"] += 1
        if self.attempts["apply"] > 3:
            return self.defer("The apply button did not open an application")
        try:
            await self.click(button)
        except ValueError as exc:
            if "stale" not in str(exc).lower():
                raise
            # Oracle re-renders its job page after the first scan (Verisk, Dallas County 2026-10-23: "Control is
            # stale" 7 s in, then a retry after backoff). The Apply control is found again and clicked once.
            button = self.find_apply(await self.form.scan())
            if not button:
                return {"fallback": True, "reason": "No apply control found"}
            await self.click(button)
        self.apply_url = self.page.url

    async def on_apply_method(self, obs):
        return {"fallback": True, "reason": "Unknown application method chooser"}

    async def on_consent(self, obs):
        buttons = [b for b in obs["controls"] if re.search(r"^(?:i )?(?:accept|agree)", b["label"], re.I)]
        for f in obs["fields"]:
            if f["type"] == "checkbox" and f["value"] != "true":
                await self.form.fill(f, "true")
        if not buttons:
            return {"fallback": True}
        await self.click(buttons[-1])

    def email_submit(self, obs):
        """The email step's own submit: Next, a platform's known submit id (its label varies by employer: iCIMS's
        "Next" or Schwab's "I Acknowledge the Privacy Notice"), else a generic account button."""
        known = [b for b in obs["controls"] if b.get("html_id") and b["html_id"] in self.email_submit_ids]
        return self.find_next(obs) or (known[0] if known else None) or next(iter(self.buttons(obs, AUTH_BUTTON)), None)

    async def email_agreement_answer(self, field):
        return (await self.form.resolver.resolve([field]))[0]

    async def on_email_entry(self, obs):
        if not self.accounts.email:
            return self.defer("Set ACCOUNT_EMAIL (or your profile email) so I can identify you to employers")
        self.attempts["email_entry"] += 1
        # Microsoft careers: "We couldn't find an account with that username" -> sign up.
        shown = " ".join(str(e) for e in obs.get("errors", []))
        if (
            self.attempts["email_entry"] > 1
            and NOT_FOUND.search(shown)
            and self.accounts.may_create
            and self.attempts["create"] == 0
            and await self.switch(obs, CREATE_LINK)
        ):
            return None
        if self.attempts["email_entry"] > 2:
            return self.defer("The employer did not accept the email address step")
        seen = set()

        async def agree(fields):
            """Tick each consent box the resolver accepts; return a deferral for a required one it cannot."""
            for f in fields:
                if f["id"] in seen:
                    continue
                if f["type"] != "checkbox":
                    if (
                        f.get("required")
                        and f.get("options")
                        and not f.get("value")
                        and f["type"] in CONSENT_MENUS
                        and CONSENT_LABEL.search(f["label"])
                    ):
                        # A consent asked as a menu (Atlassian's "By selecting continue ... you acknowledge"). Other
                        # menus on the step, such as Schwab's phone country code, are not agreements.
                        seen.add(f["id"])
                        answer = await self.email_agreement_answer(f)
                        if answer.disposition != "answer":
                            return self.defer(
                                "A required agreement on the email step needs your approval", [self.review_for(f)]
                            )
                        try:
                            await self.form.fill(f, answer.value)
                        except (PlaywrightError, ValueError):
                            return self.defer(
                                "A required agreement on the email step could not be selected", [self.review_for(f)]
                            )
                    continue
                if f["value"] == "true":
                    continue
                seen.add(f["id"])
                answer = await self.email_agreement_answer(f)
                if answer.disposition == "answer" and answer.value == "true":
                    await self.form.fill(f, "true")
                elif answer.disposition == "answer" and answer.value == "false":
                    continue  # A conditional box that does not apply stays unchecked.
                elif f["required"]:
                    return self.defer(
                        "A required agreement on the email step needs your approval", [self.review_for(f)]
                    )
            return None

        for f in obs["fields"]:
            if f["type"] == "email" or re.search(r"e-?mail", f["label"], re.I):
                await self.form.fill(f, self.accounts.email)
        if deferred := await agree(obs["fields"]):
            return deferred
        if not await self.arm_mail():
            return self.lease_busy()
        # Accepting Oracle's legal dialog recreates the entry controls.
        target = None
        for _ in range(20):
            obs = await self.form.scan()
            # Iridium (iCIMS) 2026-10-21: ticking the privacy-notice box reveals an "I accept" box; Next stays
            # disabled until it is ticked, and the step fell back to the guided flow without it.
            if deferred := await agree(obs["fields"]):
                return deferred
            target = self.email_submit(obs)
            if target:
                break
            await asyncio.sleep(0.2)
        if not target:
            return {"fallback": True}
        # A fast redirect can reach its account page before CAPTCHA polling
        # starts. Retain only this action's source and armed-request identity.
        self.auth_transition_source = (
            {"url": obs["url"], "stage": Step.EMAIL_ENTRY, "challenge_id": self.challenge["id"]}
            if self.challenge
            and self.challenge.get("id")
            and obs["url"] == self.page.url
            and self.classify_page(obs) == Step.EMAIL_ENTRY
            else None
        )
        try:
            await self.click(target, auth=True)
            if challenge := await self.after_auth_click():
                return challenge
            # Some providers mount after the immediate half-second poll.
            # Keep this request-bound source for the next loop's solver.
            self.pending_auth_transition_source = self.auth_transition_source
        finally:
            self.auth_transition_source = None
        await self.after_auth_dialogs()

    async def after_auth_dialogs(self):
        """Terms/privacy modals that must be accepted before continuing (Oracle, iCIMS)."""
        await self.form.settle(timeout=4)
        obs = await self.form.scan()
        agree = [
            b
            for b in obs["controls"]
            if re.fullmatch(r"(?:i )?(?:agree|accept)(?: and continue)?", b["label"], re.I)
        ]
        if agree and len(self.app_fields(obs)) <= 2:
            await self.click(agree[-1], auth=True)

    async def on_sign_in(self, obs):
        return await self.authenticate(obs, "sign_in")

    async def on_create_account(self, obs):
        return await self.authenticate(obs, "create")

    async def on_verify_link(self, obs):
        if not self.inbox.configured:
            return self.defer(
                "This employer emailed a verification link. Connect Gmail (GMAIL_APP_PASSWORD)."
            )
        if not self.mail_rule_known:
            return await self.discover_verification()
        self.attempts["verify_link"] += 1
        if self.attempts["verify_link"] > 2:
            return self.defer("The employer kept asking for email verification")
        token = await self.wait_email("link")
        if not token:
            return self.defer(self.mail_problem("verification link"))
        if token["kind"] != "link":
            self.finish_mail("failed", "Expected a verification link, received a code")
            return self.defer("The employer sent a code where the page expected a link")
        await self.open_link(token["value"])
        self.finish_mail("verified", "Website accepted the verification link")
        self.accounts.mark(self.page.url, "authenticated")
        self.emit("email_verified", "Employer email verification completed")
        await self.form.settle(timeout=8)
        obs = await self.form.scan()
        if self.classify(obs) in {Step.CONFIRMATION, Step.UNKNOWN, Step.VERIFY_LINK} or not obs["fields"]:
            await self.return_to_application()

    async def on_email_code(self, obs):
        if not self.inbox.configured:
            return self.defer("This employer emailed a one-time code. Connect Gmail (GMAIL_APP_PASSWORD).")
        if not self.mail_rule_known:
            return await self.discover_verification()
        self.attempts["email_code"] += 1
        if self.attempts["email_code"] > 2:
            return self.defer("The verification code was not accepted")
        # A persisted browser session can reopen an old PIN page without sending
        # another message. A new wait window alone cannot cause delivery, and
        # MailPending would park before the fallback resend below ever executes.
        # Keep correlated continuations intact; only request a fresh code when
        # this owner has no live request and the page offers exactly one resend.
        resuming = getattr(self, "run_record", {}).get("packet", {}).get("email_wait_resume")
        if not resuming and not self.inbox.live(self.challenge) and not self.attempts["entry_resend"]:
            resend = [
                b
                for b in obs["controls"]
                if not b.get("disabled")
                and re.search(r"resend|send (?:a )?new code|send again", b["label"], re.I)
            ]
            if len(resend) == 1:
                if not await self.arm_mail("code"):
                    return self.lease_busy()
                self.attempts["entry_resend"] += 1
                await self.click(resend[0], auth=True)
                self.emit("email_requested", "Requested a fresh code for the reopened verification page")
        token = await self.wait_email("code")
        if not token:
            resend = [
                b
                for b in obs["controls"]
                if re.search(r"resend|send (?:a )?new code|send again", b["label"], re.I)
            ]
            if resend and self.attempts["email_code"] == 1:
                self.release_mail()
                if not await self.arm_mail("code"):
                    return self.lease_busy()
                await self.click(resend[0], auth=True)
                token = await self.wait_email("code")
            if not token:
                return self.defer(self.mail_problem("verification code"))
        if token["kind"] != "code":
            await self.open_link(token["value"])
            self.finish_mail("verified", "Website accepted the verification link")
            return None
        obs = await self.form.scan()
        fields = self.code_fields(obs)
        code = token["value"]
        if not fields:
            self.finish_mail("failed", "Code field disappeared")
            return self.defer("A code arrived but the page no longer shows a code field")
        if len(fields) > 1 and len(fields) != len(code):
            self.finish_mail("failed", "Code length mismatch")
            return self.defer("The code length does not match the website")
        for i, f in enumerate(fields):
            frame_url = self.form.frames[f["id"]].url
            landing = urlsplit(getattr(self.e, "landing_url", "") or "")
            # The job link's own redirect target is the employer's site (Dell, Oracle 2026-10-23: six runs refused
            # their code on enterpriseplatform.dell.com and Oracle locked verification).
            landed = (
                landing.scheme == "https"
                and urlsplit(frame_url).scheme == "https"  # never a code over plain HTTP
                and bool(landing.hostname)
                and (urlsplit(frame_url).hostname or "").lower() == landing.hostname.lower()
            )
            if not landed and not self.accounts.password_allowed(self.id, frame_url, self.job["url"]):
                self.finish_mail("failed", "Code field on an unexpected website")
                return self.defer("The code field is on an unexpected website; not entering the code")
            await self.form.locator(f["id"]).fill(code if len(fields) == 1 else code[i], timeout=4000)
        await asyncio.sleep(0.3)
        obs = await self.form.scan()
        if self.code_fields(obs):
            confirm = [
                b
                for b in obs["controls"]
                if re.search(r"^(?:verify|confirm|continue|next|submit|sign in|log in)", b["label"], re.I)
            ]
            if confirm:
                await self.click(confirm[-1], auth=True)
        await self.form.settle(timeout=6)
        after = await self.form.scan()
        # A rejected or expired PIN often clears/remounts its inputs. That is
        # a changed signature, not successful authentication. Wait for the
        # dedicated code step to actually disappear before recording success.
        for _ in range(12):
            if await self.code_accepted(after):
                break
            if re.search(
                r"(?:code|pin).{0,40}(?:incorrect|invalid|expired)|(?:incorrect|invalid|expired).{0,40}(?:code|pin)",
                " ".join(after.get("errors", [])),
                re.I,
            ):
                break
            await asyncio.sleep(0.25)
            after = await self.form.scan()
        if not await self.code_accepted(after):
            self.finish_mail("failed", "Website did not accept the code")
            return None  # The loop sees the same step again and retries or defers.
        self.finish_mail("verified", "Website accepted the verification code")
        store = getattr(self.accounts, "store", None)
        if store and store.state(self.page.url) in {"created_locally", "signing_in"}:
            # SAP's passcode completes account creation (2026-10-16): later runs must sign in,
            # not press Create again for an account that now exists.
            self.accounts.mark(self.page.url, "authenticated")
        self.emit("email_verified", "Entered the emailed verification code")

    async def code_accepted(self, obs):
        """A remount/loading screen is not evidence that a one-use code worked."""
        if obs.get("busy") or self.code_fields(obs):
            return False
        if any(f["type"] == "password" for f in obs["fields"]):
            return False
        explicit = re.search(
            r"email (?:address )?(?:has been |is )?verified|verification (?:complete|successful)|"
            r"successfully (?:verified|signed in)|you are (?:now )?signed in",
            obs.get("text", ""),
            re.I,
        )
        if explicit:
            return True
        step = self.classify(obs)
        return (step in {Step.FORM, Step.REVIEW} and bool(self.app_fields(obs))) or (
            step == Step.CONFIRMATION and bool(await self.form.proof())
        )

    async def on_reset_password(self, obs):
        """A reset dialog/page that asks for the account email (e.g. after Forgot password)."""
        return await self.request_reset(obs, "The password reset was requested from this page")

    async def on_form(self, obs):
        from ..browser_contracts import semantic_state

        report = await self.fill_page()
        if isinstance(report, dict) and report.get("state"):
            return report
        obs = await self.form.scan()
        # authenticate() hands over application forms that embed account fields; those classify as account steps
        # (iCIMS's candidate profile: password boxes beside the resume and name fields, saved by Submit Profile).
        # Only those two steps extend what this handler owns: a page that became an emailed-code step, a review or a
        # confirmation while it was filled has its own handler in the main loop, not a Next click.
        embedded = {self.classify(obs)} & {Step.CREATE_ACCOUNT, Step.SIGN_IN}
        nxt = self.find_next(obs)
        final = await self.observed_final(obs)
        if not nxt and final:
            if await self.form.intermediate_submit(final["id"], obs):
                await self.click(final, step=True)
                return None
            return await self.on_review(obs, report)
        if not nxt:
            return {"fallback": True, "reason": "No way forward from this page"}
        # Review each step while its controls are still editable. Reviewing only
        # on a read-only final page turned every correction into Back navigation
        # through the whole wizard (Blackstone, BCBS-MA, NVIDIA).
        gate = getattr(self.e, "quality_gate", None)
        if gate and (quality_blocker := await gate(current_page=True)):
            return quality_blocker
        if gate:
            obs = await self.form.scan()
            nxt = self.find_next(obs)
            if not nxt:
                return {"fallback": True, "reason": "No way forward from this page"}
        page = semantic_state(obs)["page"]
        if challenge := await self.solve_captcha():
            return challenge
        # The website callback may replace the form with authentication or a
        # different step. Use that current page rather than the pre-CAPTCHA ID.
        obs = await self.form.scan()
        step = self.classify(obs)
        # The iCIMS profile was refused here on every pass (ICE, iPipeline 2026-10-19): it classifies as account
        # creation, so Submit Profile was never pressed and the third pass reported a failed sign-in.
        if step != Step.FORM and step not in embedded:
            return None
        if semantic_state(obs)["page"] != page:
            return None  # The callback submitted this page; the next one is filled before anything is clicked.
        nxt = self.find_next(obs)
        if not nxt:
            if await self.observed_final(obs):
                return await self.on_review(obs, report)
            return {"fallback": True, "reason": "No way forward after CAPTCHA acceptance"}
        before = self.signature(step, obs)
        await self.click(nxt, step=True)
        after = await self.form.scan()
        if self.signature(self.classify(after), after) == before:
            # Server-side validation: answer what it flagged, then try once more.
            if after["errors"] or any(f["error"] for f in after["fields"]):
                repaired = await self.repair(after)
                if isinstance(repaired, dict):
                    return repaired

    async def revisit_history(self, blocker):
        """Follow observed Back controls to the exact question, not a guessed page name."""
        from ..answers import normalize
        from ..browser_contracts import fingerprint

        if self.committed or getattr(self.e, "armed", False):
            return False
        pending = ((blocker or {}).get("report") or {}).get("pending", [])
        targets = []
        for item in pending:
            if item.get("repair_action") != "revisit_history_row" or not item.get("question"):
                continue
            scope = item.get("scope") or {}
            key = "quality_revisit:" + repr((item["question"], sorted(scope.items())))
            if not self.attempts[key]:
                targets.append((key, item, scope))
        if not targets:
            return False
        seen = set()
        for step in range(7):
            obs = await self.form.scan()
            matched = []
            for key, item, scope in targets:
                fields = [f for f in obs.get("fields", [])
                          if normalize(f["label"]) == normalize(item["question"])
                          and (not item.get("type") or f.get("type") == item["type"])
                          and all(normalize(str(f.get(k, ""))) == normalize(str(v))
                                  for k, v in scope.items() if v not in (None, ""))]
                if len(fields) == 1:
                    matched.append(key)
            if matched:
                for key in matched:
                    self.attempts[key] += 1
                self.emit("quality_repair", "Reopened the observed question owning the supported correction",
                          {"fields": len(matched), "back_steps": step})
                return True
            signature = fingerprint(obs)
            if signature in seen or step == 6:
                break
            seen.add(signature)
            back = [b for b in obs.get("controls", []) if not b.get("disabled") and
                    (b.get("automation_id") in self.back_ids or
                     re.fullmatch(r"back|previous(?: step)?", b.get("label", ""), re.I))]
            if len(back) != 1:
                break
            await self.click(back[0])
            await self.form.settle(timeout=4)
        # A failed traversal is not a reason to walk the same pages forever.
        for key, _, _ in targets:
            self.attempts[key] += 1
        return False

    async def on_review(self, obs, report=None):
        if self.committed and self.signature(Step.REVIEW, await self.form.scan()) in self.commit_pages:
            # The submitted page is still showing: never click its Submit a second time.
            return {
                "state": "submission_unknown",
                "reason": "Submission attempted; the page did not change and it was not submitted again",
            }
        if report is None:
            report = await self.form.verify()
        if report.get("auth_required"):
            return self.defer("Employer sign-in must complete before application submission")
        problems = [
            p
            for p in report["problems"]
            if not p.startswith("Resume attachment") or self.form.resume_fields()
        ]
        if problems:
            filled = await self.fill_page()
            if isinstance(filled, dict) and filled.get("state"):
                return filled
            report = await self.form.verify()
            problems = [p for p in report["problems"] if not p.startswith("Resume attachment")]
            if problems:
                return self.defer(
                    "; ".join(problems[:6]),
                    report.get("pending"),
                    state="waiting_browser",
                    blocker_type="validation",
                )
        if (self.form.resume_fields() or self.form.resume_seen) and not self.form.upload_verified:
            # Checked for the whole application, not only the page that had the upload.
            return self.defer("Resume attachment was not accepted")
        if not self.form.upload_verified or not report.get("verified_fields"):
            # Same rule as BrowserEngine.finish: never start a final submit without an
            # attached resume and verified answers. Microsoft's apply page reported
            # zero verified fields and no resume, and the adapter still clicked Submit.
            return {
                "state": "waiting_browser",
                "reason": "Final submit needs an attached resume and verified answers; observing the application again",
                "blocker_type": "validation",
                "recovery_cause": "control_observation",
                "recoverable": True,
            }
        if self.run_record["mode"] == "dry_run":
            return {
                "state": "dry_run_passed",
                "reason": f"Reached the final {self.name} submit step with verified answers; nothing submitted",
                "report": report,
            }
        obs = await self.form.scan()
        final = await self.observed_final(obs)
        if not final:
            return self.defer("Final submit control was not unique")
        return await self.commit(final)

    async def on_confirmation(self, obs):
        if self.committed:
            receipt = await self.form.proof()
            if receipt and receipt != self.before_commit:
                receipt["resume_sha256"] = self.run_record["packet"]["resume_sha"]
                return {"state": "confirmed", "reason": "Website confirmation captured", "receipt": receipt}
            return None  # Wait for a confirmation that this submission produced.
        # A confirmation-looking page this run did not produce is never recorded as an application.
        return self.defer(
            "The employer shows a confirmation, but this run did not submit; check whether you already applied",
            [
                {
                    "question": "Confirm whether you already applied to this job",
                    "options": [],
                    "key": "manual",
                    "reason": "Confirmation without a submission",
                }
            ],
        )

    async def on_already_applied(self, obs):
        if self.committed:
            return await self.on_confirmation(obs)
        return {
            "state": "already_applied",
            "reason": "The employer says you already applied to this job",
            "receipt": {
                "type": "employer_already_applied",
                "url": self.page.url,
                "evidence": (ALREADY.search(obs["text"]) or [""])[0][:200],
            },
        }

    async def on_closed(self, obs):
        return {"state": "closed", "reason": "The posting is closed or no longer available"}

    async def on_blocked(self, obs):
        return self.defer(
            "This employer requires SMS/passkey verification. Run `jobpilot login URL` once to save a session.",
            [
                {
                    "question": "Sign in to this employer and import the browser session",
                    "options": [],
                    "key": "session",
                    "reason": "SMS or passkey verification",
                }
            ],
        )

    async def on_unknown(self, obs):
        return {"fallback": True, "reason": "Unrecognized page"}

    # ----- actions ------------------------------------------------------------------------------
    def is_final_control(self, button):
        return bool(self.final.search(button["label"]))

    async def click(self, button, auth=False, step=True):
        """Adapters click controls they classified themselves; the final submit goes through commit()."""
        if (
            self.is_final_control(button)
            and not auth
            and not self.committed
            and self.last_step
            not in {
                Step.EMAIL_CODE,
                Step.CONSENT,
                Step.JOB,
                Step.METHOD,
            }
            and not await self.form.intermediate_submit(button["id"], await self.form.scan())
        ):
            raise ValueError("Refusing to click a final submit control outside the commit step")
        await self.dismiss_overlays()
        before = set(self.page.context.pages)
        result = await self.form.click(button["id"], auth_control=auth, step_control=step or auth)
        if set(self.page.context.pages) - before:
            self.emit("new_tab", "The application opened in a new tab")
        return result

    async def fill_page(self):
        report = await self.form.fill_current(accept_prefilled=True)
        if report["pending"]:
            facts = [q for q in report["pending"] if Service.review_kind(q) == "missing_fact"]
            if facts:
                # A control repair cannot make an absent fact appear. Surface
                # the actual questions once rather than spending another retry
                # on a form that still cannot be completed autonomously.
                if await self.e.ask(facts):
                    report = await self.form.fill_current(accept_prefilled=True)
                else:
                    return self.defer("Required application facts are missing", facts)
            control_errors = [q for q in report["pending"] if Service.review_kind(q) != "missing_fact"]
            if control_errors:
                report = await self.form.recover_controls(report)
                if report["ok"]:
                    return report
                control_errors = [
                    q for q in report.get("pending", []) if Service.review_kind(q) != "missing_fact"
                ]
            if control_errors:
                return {
                    "state": "waiting_browser",
                    "recoverable": True,
                    "blocker_type": "control_failure",
                    "recovery_cause": "control_observation",
                    "reason": "Known answers need browser recovery: "
                    + "; ".join(q["question"] for q in control_errors),
                }
            asked = await self.e.ask(report["pending"])
            if asked:
                report = await self.form.fill_current(accept_prefilled=True)
            if report["pending"]:
                return self.defer("Required answers need your review", report["pending"])
        if not report["ok"]:
            # One more pass catches fields revealed or re-rendered by the previous answers.
            report = await self.form.fill_current(accept_prefilled=True)
            if not report["ok"]:
                report = await self.form.recover_controls(report)
            if report["pending"]:
                return self.defer("Required answers need your review", report["pending"])
            fatal = [p for p in report["problems"] if not p.startswith("Resume attachment")]
            if fatal:
                report = await self.form.recover_controls(report)
                if report["ok"]:
                    return report
                return self.defer(
                    "; ".join(report["problems"][:6]), state="waiting_browser", blocker_type="validation"
                )
        return report

    async def repair(self, obs):
        self.attempts["repair:" + urlsplit(obs["url"]).path] += 1
        if self.attempts["repair:" + urlsplit(obs["url"]).path] > 2:
            return self.defer("The employer rejected the page: " + "; ".join(obs["errors"][:4]))
        flagged = [f for f in obs["fields"] if f["error"] or not f["valid"]]
        for f in flagged:
            self.form.expected.pop(f["id"], None)
        report = await self.fill_page()
        if isinstance(report, dict) and report.get("state"):
            return report
        obs = await self.form.scan()
        nxt = self.find_next(obs)
        if nxt:
            await self.click(nxt, step=True)
        return None

    async def solve_captcha(self):
        """None when the page is clear; a waiting_browser result when a check stays unsolved."""
        return await self.e.solve_captcha(adapter=self)

    async def captcha_open(self):
        from ..captcha_observation import challenge_frame, checkbox_frame

        try:
            return bool(await challenge_frame(self.page) or await checkbox_frame(self.page))
        except Exception:
            return False

    async def after_auth_click(self):
        """Security checks that appear right after an email/sign-in/create click."""
        await asyncio.sleep(0.5)
        if await self.captcha_open():
            return await self.solve_captcha()
        return None

    async def commit(self, final):
        if not self.committed and getattr(self.form, "referral_route", lambda: False)():
            return {
                "fallback": True,
                "reason": "Referral form cannot submit this job application; restore the application entry",
            }
        signup = getattr(self.form, "signup_form", None)
        if not self.committed and signup and await signup(final["id"]) is True:
            return {
                "fallback": True,
                "reason": "This is a talent-community signup, not the job application; open the job's Apply",
            }
        if quality_blocker := await self.e.quality_gate():
            if await self.revisit_history(quality_blocker):
                return None  # Walked back to the step that owns the flagged answer; the loop refills it.
            return quality_blocker
        # The reviewer may have remounted a repaired input; use a fresh final ID.
        final = await self.observed_final(await self.form.scan())
        if not final and any(FINAL.search(x) for x in self.form.meta.get("disabled_actions", [])):
            # Dayforce (mPulse 2026-10-16): Submit stays disabled until "I agree to the Candidate
            # Acknowledgement" is ticked; the box is not marked required, so verify() passed.
            filled = await self.fill_page()
            if isinstance(filled, dict) and filled.get("state"):
                return filled
            final = await self.observed_final(await self.form.scan())
        if not final:
            return {
                "state": "waiting_browser",
                "reason": "Review changed the final control; observing the draft again",
                "recovery_cause": "control_observation",
            }
        if challenge := await self.solve_captcha():
            return challenge  # Unsolved check before anything was submitted.
        from ..widgets import ensure_uncovered

        try:
            await ensure_uncovered(self.form.locator(final["id"]))
        except ValueError:
            return {
                "state": "waiting_browser",
                "reason": "A dialog covers the final application control; resolve it before submission",
                "blocker_type": "control_failure",
                "recovery_cause": "control_observation",
                "recoverable": True,
            }
        self.before_commit = await self.form.proof()
        page = await self.form.scan()
        self.commit_pages.add(self.signature(Step.REVIEW, page))
        if not self.committed and (refused := await self.e.try_reserve_commit()):
            return refused
        self.e.armed = True
        self.committed = True
        for frame in self.page.frames:
            try:
                await frame.evaluate(ARM_JS)
            except Exception:
                pass
        self.emit("submitting", "Commit step started; automatic retries are disabled")
        try:
            await self.form.click(final["id"], step_control=True)
        except ValueError as exc:
            # JPMorgan Oracle 2026-10-14: the captcha callback re-rendered the page and the final
            # control id went stale. That refusal happens before any click, so re-observe the one
            # final control and click it once.
            if "stale" not in str(exc).lower():
                raise
            fresh = await self.observed_final(await self.form.scan())
            if not fresh:
                raise
            await self.form.click(fresh["id"], step_control=True)
        # Dayforce's "Congratulations, your application has been submitted!" arrives after its
        # reCAPTCHA and server work, past 20 s (Interactive Brokers, York 2026-10-16).
        for _ in range(120 if self.job.get("ats") == "dayforce" else 40):
            await asyncio.sleep(0.5)
            receipt = await self.form.proof()
            if receipt and receipt != self.before_commit:
                receipt["resume_sha256"] = self.run_record["packet"]["resume_sha"]
                return {"state": "confirmed", "reason": "Website confirmation captured", "receipt": receipt}
            if rejection := (await self.validation_rejection() or await self.form.rejection()):
                if rejection.get("category") == "validation":
                    attempts = getattr(self, "validation_attempts", 0)
                    repair = await self.form.repair_validation(rejection) if attempts < 2 else None
                    if repair and repair["ok"] and repair["changed"]:
                        try:
                            self.service.authorize_validation_retry(self.e.run_id, rejection)
                        except ValueError as exc:
                            self.e.armed = self.committed = False
                            return {
                                "state": "waiting_browser",
                                "reason": str(exc),
                                "receipt": rejection,
                                "recovery_cause": "validation_controls",
                            }
                        self.validation_attempts = attempts + 1
                        next_final = await self.observed_final(await self.form.scan())
                        if next_final:
                            return await self.commit(next_final)
                    self.e.armed = False
                    self.committed = False
                    self.e.validation_refusal = rejection
                    return {
                        "state": "waiting_browser",
                        "reason": "Employer requested field corrections; automatic repair could not complete them",
                        "blocker_type": "validation",
                        "recovery_cause": "validation_controls",
                        "receipt": rejection,
                        "reviews": (repair or {}).get("pending", []),
                    }
                if rejection.get("category") == "employer_limit":
                    self.e.armed = self.committed = False
                    return await self.e.employer_limit_result(rejection)
                if rejection.get("category") == "security":
                    # Lever: "There was an error verifying your application. Please try again."
                    # Nothing was accepted; a fresh form (restart_rejected_form) may try again.
                    self.e.armed = self.committed = False
                    return {
                        "state": "waiting_browser",
                        "reason": "The website's security check refused the submission and asked to try again; "
                        "nothing was accepted",
                        "receipt": rejection,
                        "blocker_type": "validation",
                        "recovery_cause": "site_temporary",
                        "recoverable": True,
                    }
                return {
                    "state": "failed",
                    "reason": "Employer website explicitly rejected the submission",
                    "receipt": rejection,
                }
            if await self.solve_captcha():
                return {
                    "state": "submission_unknown",
                    "reason": "Submission attempted but a security check was not accepted; "
                    "automatic resubmission is disabled",
                }
            obs = await self.form.scan()
            step = self.classify(obs)
            verifier = getattr(self.e, "email_verification", None)
            if step == Step.EMAIL_CODE or (verifier is not None and verifier.state(obs)[0]):
                # Post-submit verification (Greenhouse security code; Starbucks/Eightfold "Check your
                # email" modal, shown with "Missing or Invalid OTP" before any code, 2026-10-14).
                return None
            if (
                step in {Step.FORM, Step.REVIEW}
                and self.app_fields(obs)
                and not obs["errors"]
                and self.signature(Step.REVIEW, obs) not in self.commit_pages
            ):
                # A genuinely new page after the commit (survey, EEO). Continue the loop.
                return None
            if obs["errors"] and step in {Step.FORM, Step.REVIEW}:
                break
        # An authenticated acknowledgement email can still confirm it later (receipts.py).
        return {
            "state": "submission_unknown",
            "reason": "Submission attempted, but no explicit receipt was captured",
        }

    async def validation_rejection(self):
        return None

    async def open_link(self, url):
        # Navigations during the link visit may only reach the employer rule's origins.
        rule = (self.challenge or {}).get("rule") or {}
        allowed = set(rule.get("link_origins", []))
        from ..mail import origin as link_origin, tracker_allowed

        try:
            if allowed and tracker_allowed(link_origin(url), allowed):
                # iCIMS's click tracker is only the first hop; it must redirect to the rule's own origin.
                allowed.add(link_origin(url))
        except ValueError:
            pass
        self.e.verification_origins = allowed or None
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
        finally:
            self.e.verification_origins = None
        await self.form.settle(timeout=8)
        # Remove tokens from the address bar before anything else observes the URL.
        try:
            await self.page.evaluate(
                "() => {const u=new URL(location.href);for(const k of [...u.searchParams.keys()])"
                "if(/token|code|secret|ticket|key/i.test(k))u.searchParams.delete(k);history.replaceState(null,'',u);}"
            )
        except Exception:
            pass

    async def return_to_application(self):
        target = self.apply_url or self.job["url"]
        await self.page.goto(target, wait_until="domcontentloaded", timeout=25000)

    # ----- email ------------------------------------------------------------------------------
    def mail_job(self, kind):
        """Identity that owns this observed email request, not a redirect allowance."""
        return self.job

    async def arm_mail(self, kind="auto"):
        """Open the request window BEFORE the action that makes the employer send mail.

        MailService serializes runs whose employers share a sender domain (the lease), so one
        run can never consume another's code."""
        self.challenge_since = None
        self.mail_request_kind = kind
        if self.inbox.live(self.challenge) or not self.inbox.configured:
            return True
        try:
            # Briefly allow a finishing sibling to release; longer waits defer before another browser start.
            # Shared-sender platforms wait for the holder's whole code step (inbox.lease_wait_cap).
            started = time.monotonic()
            self.challenge = await self.inbox.arm(
                self.e.run_id,
                self.mail_job(kind),
                self.accounts.email,
                kind,
                since=self.challenge_since,
                patience=max(self.config.mail_wait_seconds, code_inline_wait(self.id)) + 30,
            )
            if (waited := time.monotonic() - started) > 5:
                self.e.extend(waited)
        except ValueError as exc:
            # Another application holds this sender: sending now could hand our code to it.
            self.challenge = None
            self.emit("mail_wait", str(exc)[:200])
            return False
        self.challenge_since = self.challenge.get("since") if self.challenge else None
        return True  # Armed, or no sender rule for this employer (nothing can be matched).

    def lease_busy(self):
        return self.defer(
            "Another application kept the same email sender busy; nothing was sent. Requeue this job",
            state="timed_out",
        ) | {"blocker_type": "email_wait", "recovery_cause": "sender_busy",
             "mail_request_kind": getattr(self, "mail_request_kind", "auto")}

    def release_mail(self):
        # Close it even when its deadline has passed: a stale pending window must never be
        # handed back by the next begin().
        if self.challenge:
            if getattr(self.e, "result", None) and self.e.result.get("waiting_kind") == "email":
                return
            self.inbox.finish(self.challenge, "cancelled", "No email was needed")
        self.challenge = None

    def finish_mail(self, state, reason):
        if self.challenge:
            self.inbox.finish(self.challenge, state, reason)
            packet = getattr(self, "run_record", {}).get("packet", {})
            if packet.get("email_wait_resume", {}).get("challenge_id") == self.challenge["id"]:
                packet.pop("email_wait_resume", None)
        self.challenge = None

    def mail_problem(self, what):
        reason = self.inbox.reason(self.challenge) if self.challenge else ""
        if self.challenge is None and not self.mail_rule_known:
            return f"No trusted sender is known for this employer's {what}; add a rule in Settings › Email"
        return f"The {what} email did not arrive or was not unique" + (f" ({reason})" if reason else "")

    async def discover_verification(self):
        """Use the same bounded authenticated discovery as the guided controller.

        Unknown portals previously stopped before this existing capability ran.
        Discovery still requires an employer-bound link; a naked code cannot
        establish an unfamiliar sender's identity, and no secret reaches a model.
        """
        try:
            handled = await self.e.email_verification.handle()
        except ValueError as exc:
            return self.defer(str(exc), state="waiting_browser", blocker_type="email_wait")
        if not handled:
            return {"fallback": True, "reason": "Verification page requires broader observation"}
        return None

    @property
    def mail_rule_known(self):
        return bool(self.service.mail.rule_for(self.job["url"]))

    async def wait_email(self, kind):
        if not self.inbox.live(self.challenge):
            # The site may have sent the mail during navigation; the window starts before that.
            self.challenge = None
            try:
                self.challenge = await self.inbox.arm(
                    self.e.run_id, self.mail_job(kind), self.accounts.email, kind, since=self.challenge_since
                )
            except ValueError as exc:
                self.emit("mail_wait", str(exc)[:200])
                return None
        if not self.challenge:
            return None
        self.service.mail.narrow(self.challenge["id"], kind)
        self.emit("email_wait", f"Waiting for the employer's verification {kind}")
        inline = code_inline_wait(self.id) if kind == "code" else 3
        self.e.extend(max(min(self.config.mail_wait_seconds, 120), inline))
        token = await self.inbox.wait(self.challenge, max_wait_seconds=inline)
        if token:
            self.emit("email_matched", "Matched a trusted verification email", {"kind": token["kind"]})
        return token

    # ----- accounts ---------------------------------------------------------------------------
    def review_for(self, field, reason="Needs your approval"):
        from ..answers import answer_key

        return {
            "question": field["label"],
            "options": field["options"],
            "key": answer_key(field),
            "reason": reason,
        }

    def auth_url(self, obs):
        """The page that owns the password fields (the account belongs to that origin)."""
        passwords = [f for f in obs["fields"] if f["type"] == "password"]
        # The observation may be older than the page (a failed sign-in reloads it).
        return (passwords[0].get("frame_url") if passwords else None) or self.page.url

    async def authenticate(self, obs, mode):
        from ..agent_settings import require

        require(self.service.db, "accounts")
        if not self.accounts.ready:
            return self.defer("Add your email to the profile so I can identify you to employers")
        url = self.auth_url(obs)
        record = self.accounts.record(url)
        known = record.get("state") in KNOWN_ACCOUNT
        if mode == "create" and not self.accounts.may_create and not known:
            return self.defer(
                "This employer requires an account. Enable “Create and reuse employer accounts” in your profile.",
                [
                    {
                        "question": "Allow account creation for employers that require one",
                        "options": [],
                        "key": "session",
                        "reason": "Account required",
                    }
                ],
            )
        self.attempts[mode] += 1
        if self.attempts[mode] > 2 or self.attempts["sign_in"] + self.attempts["create"] > 4:
            problems = [p for p in getattr(self.form, "last_problems", []) if not p.startswith("Needs an approved answer")]
            if problems and any(f["type"] == "file" for f in obs["fields"]):
                # These account fields belong to an application form that keeps failing its own checks; that is the
                # failure. Schwab and Iridium (2026-10-21) each ended "Could not sign in" over a graduation year and a
                # resume upload loop.
                return self.defer("; ".join(problems[:6]), state="waiting_browser", blocker_type="validation")
            return await self.recover(obs, "Signing in did not succeed")
        button = self.auth_button(obs, mode)
        if (
            button
            and self.final.search(button["label"])
            and not getattr(self.form, "uploaded", True)
            and any(f["type"] == "file" for f in obs["fields"])
        ):
            # An application form with embedded account fields: attach the resume before typing the password. iCIMS
            # posts the upload and reloads the profile, which clears typed passwords (Iridium 2026-10-21).
            await self.form.fill_current(accept_prefilled=True)
            obs = await self.form.scan()
        passwords = [f for f in obs["fields"] if f["type"] == "password"]
        for f in passwords:
            frame_url = self.form.frames[f["id"]].url
            if not self.accounts.password_allowed(self.id, frame_url, self.job["url"]):
                return self.defer("A password was requested on an unexpected website; not entering it")
        if (
            mode == "create"
            and known
            and not self.attempts["signin_switch"]
            and await self.switch(obs, SIGNIN_LINK)
        ):
            # A known account on a sign-up page: sign in instead of re-creating it.
            self.attempts["signin_switch"] += 1
            self.attempts["create"] -= 1
            return None
        alternate = self.accounts.alternative_credentials(url) if mode == "sign_in" else None
        location = urlsplit(url)
        alternate_key = f"alternate_password:{location.scheme}://{location.netloc.lower()}"
        if alternate and record.get("state") == "reset_requested" and not self.attempts[alternate_key]:
            self.attempts[alternate_key] += 1
            self.emit("auth", "Trying the configured password for this existing account")
        using_alternate = bool(alternate and self.attempts[alternate_key])
        credentials = alternate if using_alternate else self.accounts.credentials(url)
        # Identity fields on sign-up forms (name, country, consent) come from the profile.
        others = [
            f
            for f in obs["fields"]
            if f["type"] not in {"password"}
            and not self.is_login_field(f)
            and not AUTH.search(f["label"])
            and (f["required"] or f["type"] == "checkbox")
        ]
        if others:
            answers = await self.form.resolver.resolve(others)
            for a in answers:
                f = self.form.fields.get(a.field_id)
                if not f:
                    continue
                if a.disposition == "answer":
                    try:
                        await self.form.fill(f, a.value)
                    except Exception:
                        pass
                elif f["required"]:
                    return self.defer("The account form needs an answer", [self.review_for(f, a.reason)])
        for f in obs["fields"]:
            if self.is_login_field(f):
                await self.form.locator(f["id"]).fill(self.login_value(f, credentials), timeout=4000)
        for f in passwords:
            await self.form.locator(f["id"]).fill(credentials["password"], timeout=4000)
            await self.form.locator(f["id"]).blur()
        profile = getattr(getattr(self.form, "resolver", None), "profile", None)
        if mode == "create" and await acknowledge_privacy_notice(
            getattr(self.form, "page", None), authorized=bool(getattr(profile, "accept_all_application_terms", False))
        ):
            self.emit("policy_accepted", "Acknowledged the employer's privacy notice (applicant pre-approved terms)")
        button = self.auth_button(obs, mode)
        if not button:
            return {"fallback": True, "reason": "No sign-in control found"}
        resume = any(f["type"] == "file" for f in obs["fields"])
        if self.final.search(button["label"]) and (resume or len(others) > 6):
            # An application form with an embedded "create a password" section: its Submit is the
            # application submit, so it goes through review/commit like any other final step.
            self.accounts.mark(url, "created_locally", credentials)
            for f in passwords:
                await self.form.locator(f["id"]).evaluate("e=>e.dataset.jpEmbeddedAccountReady='true'")
            self.emit("auth", "Account fields are part of the application form")
            return await self.on_form(await self.form.scan())
        if mode == "create":
            if not await self.arm_mail():
                return self.lease_busy()
            # Saved before the click: the same shared login, so a crash cannot lose it.
            self.accounts.mark(url, "created_locally", credentials)
        await self.click(button, auth=True)
        if challenge := await self.after_auth_click():
            return challenge
        outcome, detail = await self.auth_outcome()
        self.emit("auth", f"{mode.replace('_', ' ')}: {outcome}")
        if outcome == "ok":
            self.release_mail()
            self.accounts.mark(url, "authenticated", credentials)
            return None
        if outcome == "verify":
            self.accounts.mark(url, "verification_pending", credentials)
            return None
        self.release_mail()
        if outcome == "locked":
            self.accounts.mark(url, "locked", credentials, error=detail[:200])
            return self.defer(
                "The employer locked the account after failed sign-ins: " + detail,
                state="waiting_browser",
                blocker_type="account",
            )
        if outcome == "password_rules":
            return self.defer(
                "The shared account password does not meet this employer's rules: " + detail,
                state="waiting_browser",
                blocker_type="account",
            )
        if outcome == "exists" and mode == "create":
            self.accounts.mark(url, "exists", credentials)
            if await self.switch(obs, SIGNIN_LINK):
                return None
            return await self.recover(obs, detail)
        if outcome == "bad_credentials" and mode == "sign_in":
            # An unanswered reset stays settled: overwriting it with "credentials_rejected" requested another reset on
            # every run (L3Harris, 2026-10-21..23: four resets, none delivered).
            unknown = record.get("state") == "password_unknown"
            self.accounts.mark(
                url,
                "password_unknown" if unknown else "credentials_rejected",
                self.accounts.saved(url) or credentials,
                rejected_credentials=self.accounts.credential_fingerprint(url, credentials),
                rejected_at=now(),
            )
            if alternate and not using_alternate and self.attempts[mode] < 2:
                self.attempts[alternate_key] += 1
                self.emit("auth", "Trying the configured password for this existing account")
                return None
            # "No account / couldn't find an account" means create one, even when an
            # earlier rejection stored this tenant as known (a reset cannot help).
            not_found = bool(NOT_FOUND.search(str(detail or "")))
            # SuccessFactors' reset page: "If you do not receive an email then no matching account was found." A reset
            # that went unanswered means this email has no account on the tenant (a saved login from a registration
            # that never finished): create one, once. "Account already exists" restores the saved login.
            absent = (
                self.id == "successfactors"
                and unknown
                and not record.get("absent_create_at")
            )
            if (not known or not_found or absent) and self.accounts.may_create and self.attempts["create"] == 0:
                if absent:
                    self.accounts.mark(url, "password_unknown", self.accounts.saved(url) or credentials,
                                       absent_create_at=now())
                    self.emit("account", "No reset email ever came, so this employer has no account for this email; "
                              "creating one")
                if await self.switch(obs, CREATE_LINK):
                    return None
            return await self.recover(obs, detail)
        return None

    def login_value(self, f, credentials):
        """A "User name"/"User ID" field gets the applicant's username when one is configured."""
        username = getattr(self.e.profile, "account_username", "") if getattr(self, "e", None) else ""
        if (
            username
            and f["type"] != "email"
            and re.search(r"user ?name|user ?id", f["label"] + " " + f.get("automation_id", ""), re.I)
            and not re.search(r"e-?mail", f["label"], re.I)
        ):
            return username
        return credentials["email"]

    def is_login_field(self, f):
        return f["type"] == "email" or (
            f["type"] in {"text", "email"}
            and re.search(
                r"e-?mail|user ?name|user ?id|login(?: id)?|sign.?in name",
                f["label"] + " " + f["automation_id"],
                re.I,
            )
            and not re.search(r"first|last|name of|full name", f["label"], re.I)
        )

    def auth_button(self, obs, mode):
        prefer = {
            "sign_in": r"^(?:sign in|log ?in|continue|next|submit)$",
            "create": r"^(?:create(?: (?:an? )?account)?|register|sign up|create profile|submit|continue|next)$",
            "reset": r"^(?:submit|send|send (?:a |the )?(?:reset )?(?:link|email|code|instructions)|send reset link|"
            r"reset(?: (?:my )?password)?|request (?:a )?reset|continue|set password|change password|save|"
            r"update password)$",
        }[mode]
        hits = [
            b
            for b in obs["controls"]
            if re.search(prefer, b["label"], re.I) or re.search(prefer, b["aria_label"], re.I)
        ]
        if not hits:
            hits = [b for b in obs["controls"] if AUTH_BUTTON.search(b["label"])]
        # Workday overlays the real button with a click_filter that must receive the click.
        filters = [b for b in hits if b["automation_id"] == "click_filter"]
        primary = [b for b in hits if "submit" in b["automation_id"].lower()]
        actions = [b for b in hits if not b.get("href")]
        return (filters or primary or actions or hits or [None])[0]

    async def auth_outcome(self, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await self.form.settle(timeout=4)
            obs = await self.form.scan()
            text = " ".join(obs["errors"]) + "\n" + obs["text"]
            errors = " ".join(obs["errors"]) or text
            if LOCKED.search(errors):
                return "locked", LOCKED.search(errors).group()
            # SuccessFactors' sign-in page asks "Already have an account?"; read as a refusal, Boehringer's sign-in
            # went to a password reset (2026-10-16).
            if exists := EXISTS.search(re.sub(r"already have an account\s*\?", " ", errors, flags=re.I)):
                return "exists", exists.group()
            if obs["errors"] and PASSWORD_RULES.search(errors):
                return "password_rules", obs["errors"][0][:200]
            if BAD_CREDENTIALS.search(errors) and (
                obs["errors"] or any(f["type"] == "password" for f in obs["fields"])
            ):
                return "bad_credentials", BAD_CREDENTIALS.search(errors).group()
            if CHECK_EMAIL.search(text) and not any(f["type"] == "password" for f in obs["fields"]):
                return "verify", ""
            if self.code_fields(obs):
                return "verify", ""
            if not any(f["type"] == "password" for f in obs["fields"]):
                return "ok", ""
            await asyncio.sleep(0.4)
        return "unknown", "; ".join(obs["errors"][:2])

    async def switch(self, obs, pattern):
        # The page usually changed since `obs` (a failed sign-in reloads it); observe it again.
        obs = await self.form.scan()
        links = [b for b in obs["controls"] if pattern.search(b["label"]) or pattern.search(b["aria_label"])]
        if not links:
            return False
        await self.click(links[0], auth=True)
        return True

    async def recover(self, obs, detail):
        """Forgot-password through the connected mailbox, then set the shared password.

        Requested once per employer per run and at most once every five minutes; a reset whose
        email never arrived does not count against the limit. Saved credentials are kept until
        the website confirms the new password."""
        url = self.auth_url(obs)
        record = self.accounts.record(url)
        manual = [
            {
                "question": "Sign in to this employer and import the browser session",
                "options": [],
                "key": "session",
                "reason": detail[:200],
            }
        ]
        unanswered = (
            record.get("state") == "reset_requested"
            and record.get("reset_requested_at")
            and time.time() - float(record["reset_requested_at"]) > UNANSWERED_RESET_SECONDS
        )
        if unanswered or record.get("state") == "password_unknown":
            # The employer holds an account for this email, refuses the saved password, and mails nothing for a reset
            # (SuccessFactors: "If you do not receive an email then no matching account was found"). L3Harris, Entergy,
            # Farmers and Westinghouse (2026-10-21/22) repeated sign-in and reset on every run. Only the owner can
            # recover it; a password saved under Setup > Accounts (state password_reset) is used from then on.
            if unanswered:
                self.accounts.mark(url, "password_unknown", self.accounts.saved(url), password_unknown_at=now())
            return self.defer(
                PASSWORD_UNKNOWN,
                [{"question": PASSWORD_UNKNOWN, "options": [], "key": "manual", "reason": detail[:200]}],
                state="waiting_browser",
                blocker_type="account",
            )
        if not self.config.account_password_reset or self.attempts["reset"]:
            return self.defer(
                f"Could not sign in to this employer ({detail})",
                manual,
                state="waiting_browser",
                blocker_type="account",
            )
        if not self.inbox.configured or not await self.inbox.rule(
            self.mail_job("password_reset"), self.accounts.email
        ):
            # Without a trusted sender, a reset email could never be matched: do not burn a reset.
            return self.defer(
                f"Could not sign in ({detail}). Connect Gmail and an email rule for this employer to "
                "recover the account automatically.",
                manual,
                state="waiting_browser",
                blocker_type="account",
            )
        if time.time() - record.get("reset_requested_at", 0) < 300:
            return self.defer(
                "A password reset was requested recently; awaiting delivery",
                manual,
                state="waiting_browser",
                blocker_type="email_wait",
            )
        if record.get("resets", 0) >= 3:
            return self.defer(
                "Password resets for this employer are exhausted; sign in manually once",
                manual,
                state="waiting_browser",
                blocker_type="account",
            )
        self.attempts["reset"] += 1
        obs = await self.form.scan()
        forgot = [b for b in obs["controls"] if FORGOT.search(b["label"])]
        if not forgot and await self.switch(obs, SIGNIN_LINK):
            # "Forgot password" usually lives on the sign-in page, not the sign-up page.
            obs = await self.form.scan()
            forgot = [b for b in obs["controls"] if FORGOT.search(b["label"])]
        if not forgot:
            return self.defer(
                f"Could not sign in ({detail}) and no password reset was offered",
                state="waiting_browser",
                blocker_type="account",
            )
        self.emit("auth", "Resetting the employer password through your mailbox")
        await self.click(forgot[0], auth=True)
        await self.form.settle(timeout=6)
        return await self.request_reset(await self.form.scan(), detail)

    async def request_reset(self, obs, detail):
        """Explicit reset-email step: fill the email, open the request window, click, then wait."""
        url = self.auth_url(obs) if any(f["type"] == "password" for f in obs["fields"]) else self.page.url
        view = self.dialog_view(obs) or obs
        emails = [f for f in view["fields"] if self.is_login_field(f)]
        if not emails:
            return self.defer("The password reset step has no email field")
        for f in emails:
            await self.form.locator(f["id"]).fill(self.accounts.email, timeout=4000)
            await self.form.locator(f["id"]).blur()
        button = self.auth_button(view, "reset")
        if not button:
            return self.defer("The password reset step has no submit control")
        self.release_mail()
        if not await self.arm_mail("password_reset"):
            return self.lease_busy()
        if not self.challenge:
            return self.defer("The reset email could not be matched safely right now; retry later")
        previous = self.accounts.saved(url)
        self.accounts.mark(url, "reset_requested", previous, reset_requested_at=time.time())
        await self.click(button, auth=True)
        if challenge := await self.after_auth_click():
            return challenge
        self.emit("auth", "Employer password reset requested")
        return await self.complete_password_reset(url)

    async def complete_password_reset(self, url):
        """Consume this owner's request; save credentials only after acceptance."""
        token = await self.wait_email("password_reset")
        if not token:
            reason = self.inbox.reason(self.challenge)
            self.finish_mail("failed", "No reset email")
            # Nothing was delivered: allow another request after the cooldown, keep the count.
            return self.defer(
                "The password reset email did not arrive or was not unique"
                + (f" ({reason})" if reason else ""),
                state="waiting_browser",
            )
        self.emit("auth", "Employer password reset email received")
        if token["kind"] != "link":
            self.finish_mail("failed", "Reset email had no link")
            return self.defer("The reset email did not contain a usable link")
        await self.open_link(token["value"])
        obs = await self.form.scan()
        passwords = [f for f in obs["fields"] if f["type"] == "password"]
        if not passwords:
            self.finish_mail("failed", "Reset link did not open a password form")
            return self.defer("The password reset link did not open a new-password form")
        from ..accounts import PASSWORD_MAXIMUM

        configured = self.accounts.icims_credentials(url)
        # The new password obeys the reset form's stated maximum (Westinghouse: 18 characters).
        maxima = [f.get("maxlength", -1) for f in passwords if f.get("maxlength", -1) > 0]
        maxima += [int(n) for n in PASSWORD_MAXIMUM.findall(obs.get("text", "")) if 1 <= int(n) <= 256]
        credentials = configured or self.accounts.store.shared(max_length=min(maxima) if maxima else None)
        if maxima and not configured and len(credentials["password"]) > min(maxima):
            credentials = {**credentials, "password": self.accounts.store.generated(max(12, min(min(maxima), 16)))}
        for f in passwords:
            if not self.accounts.password_allowed(self.id, self.form.frames[f["id"]].url, self.job["url"]):
                self.finish_mail("failed", "Reset form on an unexpected website")
                return self.defer("The reset page is on an unexpected website; not entering the password")
            await self.form.locator(f["id"]).fill(credentials["password"], timeout=4000)
        button = self.auth_button(obs, "reset")
        if not button:
            self.finish_mail("failed", "No new-password submit control")
            return self.defer("The new-password form had no submit control")
        await self.click(button, auth=True)
        outcome, detail = await self.auth_outcome()
        self.emit("auth", f"reset: {outcome}")
        if outcome == "password_rules":
            self.finish_mail("failed", "Password rules")
            return self.defer("The shared account password does not meet this employer's rules: " + detail)
        if outcome != "ok":
            self.finish_mail("failed", "New password not accepted")
            return self.defer("The employer did not accept the new password: " + (detail or outcome))
        self.finish_mail("verified", "Employer accepted the password reset")
        record = self.accounts.record(url)
        self.accounts.mark(url, "password_reset", credentials, resets=record.get("resets", 0) + 1)
        self.attempts["sign_in"] = self.attempts["create"] = 0
        self.emit("auth", "Password reset to the shared account password")
        await self.form.settle(timeout=6)
        obs = await self.form.scan()
        if self.classify(obs) not in {Step.SIGN_IN, Step.FORM, Step.METHOD}:
            await self.return_to_application()
        return None
