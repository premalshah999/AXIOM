"""Bounded email challenges in the owning browser; secrets never go to an LLM."""

import asyncio
import re
import time
from playwright.async_api import Error as PlaywrightError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .answers import job_catalog_search_box
from .mail import origin
from .mail_errors import MailPending, MailSenderBusy, code_inline_wait
from .network import public_url

CODE = re.compile(
    r"verification code|validation code|security code|one.time (?:code|password)|authentication code|\botp\b|enter (?:the |your )?code",
    re.I,
)
CODE_LABEL = re.compile(
    r"^(?:please )?(?:(?:enter|type)(?: the| your)? )?(?:(?:email|verification|validation|security|authentication|confirmation|one.time)(?: verification)? )?(?:code|passcode|otp)"
    r"(?: digit (?:[1-9]|10)(?: of (?:[4-9]|10|four|five|six|seven|eight|nine|ten))?)?[.: ]*$",
    re.I,
)
WAITING = re.compile(
    r"check your (?:email|inbox)|(?:verification|confirmation|sign.in) (?:email|link) (?:has been |was )?sent|(?:sent|emailed) (?:you )?(?:a |an )?(?:verification |confirmation )?(?:link|code)",
    re.I,
)
SEND = re.compile(
    r"^(?:send|email|request)(?: me| a| the| my)?(?: verification| confirmation| sign.in| security)? (?:code|link|email)$",
    re.I,
)
CONFIRM = re.compile(
    r"^(?:verify(?: email| code| email address)?|confirm(?: email| code)?|continue|next|sign in|log in|submit(?: code)?)$",
    re.I,
)


def autocomplete_code_field(field, page_text=""):
    """An autofill hint cannot turn a named application question into authentication."""
    if field.get("autocomplete") != "one-time-code":
        return False
    label = str(field.get("label") or "").strip().strip("* ")
    # Some ATSs use this attribute on every input just to disable browser autofill.
    # Unlabelled inputs need an explicit code instruction; named inputs must
    # independently match the code-label grammar at the caller.
    generic = not label or label.casefold() in {"unlabelled field", "unlabeled field"}
    return bool(generic and CODE.search(page_text))


def digit_box(field):
    """One box of a split verification code: a single character, or an unlabeled/"digit N" input."""
    if field.get("type") not in {"text", "tel", "number"}:
        return False
    label = str(field.get("label") or "").strip()
    return (
        field.get("maxlength") == 1
        or not label
        or bool(
            re.fullmatch(r"(?:digit|character|code|otp)?\s*\d{0,2}(?:\s*of\s*\d{1,2})?", label, re.I)
            # Starbucks (Eightfold): "Please enter OTP character 1" ... "6".
            or re.search(r"\b(?:otp|code|digit|character|pin)\b.{0,20}?\b\d{1,2}\b", label, re.I)
        )
    )


class VerificationGuard:
    """CDP sees every redirected request; Playwright routes only intercept the first hop."""

    def __init__(self, page, allowed, allow_private=False):
        self.page, self.allowed, self.allow_private = page, allowed, allow_private
        self.blocked = False
        self.tasks = set()
        self.requests = set()

    async def start(self):
        self.session = await self.page.context.new_cdp_session(self.page)
        self.session.on("Fetch.requestPaused", self.schedule)
        await self.session.send(
            "Fetch.enable",
            {"patterns": [{"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}]},
        )
        return self

    def schedule(self, event):
        self.requests.add(event["requestId"])
        task = asyncio.create_task(self.check(event))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def check(self, event):
        permitted = False
        try:
            url = event["request"]["url"]
            permitted = origin(url) in self.allowed
            if permitted:
                await asyncio.wait_for(public_url(url, self.allow_private), 5)
        except Exception:
            permitted = False
        try:
            if permitted:
                await self.session.send("Fetch.continueRequest", {"requestId": event["requestId"]})
            else:
                self.blocked = True
                await self.session.send(
                    "Fetch.failRequest", {"requestId": event["requestId"], "errorReason": "BlockedByClient"}
                )
        except Exception:
            self.blocked = True
        finally:
            self.requests.discard(event["requestId"])

    async def close(self):
        self.session.remove_listener("Fetch.requestPaused", self.schedule)
        pending = list(self.requests)
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=2)
        # Fail still-paused requests before releasing the interceptor. Never
        # resume an unchecked redirect merely because its helper is stopping.
        await asyncio.gather(
            *(
                asyncio.wait_for(
                    self.session.send(
                        "Fetch.failRequest", {"requestId": request, "errorReason": "BlockedByClient"}
                    ),
                    1,
                )
                for request in pending
            ),
            return_exceptions=True,
        )
        try:
            await asyncio.wait_for(self.session.send("Fetch.disable"), 2)
        except (PlaywrightError, TimeoutError):
            pass
        finally:
            try:
                await asyncio.wait_for(self.session.detach(), 2)
            except (PlaywrightError, TimeoutError):
                pass


class EmailBrowser:
    def __init__(self, engine):
        self.engine = engine
        self.mail = engine.service.mail
        self.rule = self.mail.rule_for(engine.job["url"])
        self.challenge_id = None
        self.attempts = 0
        self.secret_fields = []
        self.deferred = False

    def state(self, observation):
        from .adapters.base import code_label

        fields = observation["fields"]
        # A code wait needs an observed input (scan.js only emits visible controls).
        # Page text can name a code step, but it never opens a code wait by itself.
        self.state_branch = "none"
        codes = [
            f
            for f in fields
            if f["type"] in {"text", "tel", "number", "password"}
            and (
                CODE_LABEL.fullmatch(code_label(f["label"]))
                or autocomplete_code_field(f, observation["text"])
            )
        ]
        if codes:
            self.state_branch = "code_input"
        if (not codes or all(f["maxlength"] == 1 for f in codes)) and CODE.search(observation["text"]):
            digits = [f for f in fields if f["maxlength"] == 1 and f["type"] in {"text", "tel", "number"}]
            if not 4 <= len(digits) <= 10:
                # Starbucks' six boxes carry no maxlength: an unlabeled box group is the code entry.
                digits = [f for f in fields if digit_box(f)]
            if 4 <= len(digits) <= 10:
                codes = digits
                self.state_branch = "digit_boxes"
        if codes and not getattr(getattr(self, "engine", None), "armed", False):
            from .adapters.base import dedicated_code_step

            if not dedicated_code_step(fields, codes):
                # Before submit, a code-like field inside a full application form is not a code
                # step (John Deere). After submit, Greenhouse adds its code box inside the form.
                codes = []
        if codes:
            return "code", codes
        if WAITING.search(observation["text"]):
            self.state_branch = "check_email_text"
            return "link", []
        return None, []

    async def inside_code_dialog(self, code_field, others):
        """When the code input sits in an open modal (Starbucks' "Check your email" dialog),
        only fields inside that modal can mix with it; the page behind it does not count."""
        form = self.engine.form
        dom = {f["id"]: (form.fields.get(f["id"]) or {}).get("dom_id") for f in others}
        try:
            inside = await form.locator(code_field["id"]).evaluate(
                """(e, ids) => {
                  const d = e.closest('[role=dialog],[aria-modal="true"],dialog');
                  if (!d) return null;
                  return ids.filter(id => { const n = id && document.querySelector('[data-jp-id="' + id + '"]'); return n && d.contains(n); });
                }""",
                [d for d in dom.values() if d],
                timeout=1500,
            )
        except Exception:
            return others
        if inside is None:
            return others
        inside = set(inside)
        # Fields in another frame or without a DOM id stay counted (conservative).
        return [f for f in others if not dom.get(f["id"]) or dom[f["id"]] in inside]

    @staticmethod
    def application_fields(obs, code_ids):
        """Fields that make a verification step a mixed application step.

        Site chrome is not an application question: a job board's own search box
        (Eightfold) and a type=search input in a header or nav.
        """
        return [
            f
            for f in obs["fields"]
            if f["id"] not in code_ids
            and f["type"] not in {"email", "checkbox", "search"}
            and not job_catalog_search_box(f)
        ]

    def begin(self, kind="auto", since=None):
        if not self.rule:
            raise ValueError("Add an email verification rule for this employer in Settings")
        recipient = self.engine.run_record["packet"]["profile"]["email"]
        c = self.mail.begin(
            self.engine.run_id,
            self.rule,
            recipient,
            kind,
            since=since,
            observed=since is not None and kind == "link",
        )
        self.challenge_id = c["id"]
        self.engine.emit(
            "email_wait",
            "Waiting for a matching employer verification email",
            {
                "challenge_id": c["id"],
                "request_id": c["request_id"],
                "generation": c["generation"],
                "kind": kind,
                "state_branch": getattr(self, "state_branch", "unknown"),
            },
        )
        return c

    async def acquire(self, kind="auto"):
        """begin() after waiting out a sibling's hold on the sender: briefly, or on platforms whose one sender serves
        every employer for the holder's whole code step. Fortinet's and GM Financial's navigators clicked Next into
        "Another application is verifying" until they stalled (Oracle, 2026-10-18)."""
        from .inbox import LEASE_BUSY, lease_wait_cap

        engine = getattr(self, "engine", None)
        deadline = time.monotonic() + lease_wait_cap((getattr(engine, "job", None) or {}).get("ats"))
        while True:
            try:
                return self.begin(kind)
            except ValueError as exc:
                if LEASE_BUSY not in str(exc):
                    raise
                if time.monotonic() >= deadline:
                    raise MailSenderBusy(kind) from None
                await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))

    async def prepare_submission(self, kind="auto"):
        if not self.rule or self.challenge_id:
            return
        await self.acquire(kind)

    def greenhouse_submission(self, obs):
        return (
            self.engine.job.get("ats") == "greenhouse"
            and self.engine.armed
            and bool(re.search(r"to submit your application.{0,100}code", obs["text"], re.I | re.S))
        )

    def employer_owned(self, page_origin):
        from .mail import employer_label, tenant_tokens

        parts = urlsplit(page_origin)
        if parts.scheme != "https" or not parts.hostname:
            return False
        job = self.engine.job
        tokens = {t for t in tenant_tokens(job["url"], job.get("company", "")) if len(t) >= 4}
        return employer_label(parts.hostname) in tokens

    async def reopen_eightfold_apply(self):
        """Starbucks 2026-10-14: after the code, Eightfold may land on its site home; the
        navigator then wandered into the job search. The session is now verified, so reopen
        this job's apply route directly."""
        e = self.engine
        await asyncio.sleep(2)
        if "/careers/apply" in urlsplit(e.page.url).path:
            return
        m = re.search(r"/careers/job/(\d+)", urlsplit(e.job["url"]).path) or re.search(
            r"[?&]pid=(\d+)", e.job["url"]
        )
        if not m:
            return
        await e.page.goto(
            f"{origin(e.job['url'])}/careers/apply?pid={m.group(1)}",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        e.emit(
            "browser_recovery",
            "Reopened the Eightfold application after email verification",
            {"route_path": "/careers/apply"},
        )

    def embedded_code_frames_allowed(self, fields, obs):
        """A Greenhouse form embedded on the employer's own site may take its emailed code in the board iframe.

        Roblox, Stripe, Waymo and Toast open the employer's page (careers.roblox.com, stripe.com) and render the
        application in a job-boards.greenhouse.io iframe. The page-level origin check can never pass for them: the
        top-level URL is the employer's, and the mail rule lists only email-link origins. 13 of the 21 embedded jobs
        never succeeded; Roblox ended with "Add this employer's login origin..." (2026-10-18), Stripe and Waymo
        ended unknown 2 to 11 s after the commit, before any mail was read.

        Safe because the code is typed only into inputs whose own frame passes the strict per-frame check
        (code_origin_allowed: Greenhouse board or embed URL of this exact job, https, no credentials, the
        post-submit code prompt on screen). The employer page itself never receives the code.
        """
        frames = getattr(self.engine.form, "frames", None) or {}
        return bool(
            fields
            and self.greenhouse_submission(obs)
            and all(f["id"] in frames and self.code_origin_allowed(frames[f["id"]].url, obs) for f in fields)
        )

    def code_origin_allowed(self, url, obs):
        """Official Greenhouse board aliases may verify only this exact job.

        This permission is for a code input on the reserved application. It
        never expands the verification rule's allowed email-link origins.
        """
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError:
            return False
        if self.engine.job.get("ats") != "greenhouse" and self.employer_owned(origin(url)):
            # The employer's own login origin (careers.qualcomm.com, Qualcomm Eightfold 2026-10-14)
            # may take this run's emailed code; shared ATS hosts never qualify.
            return True
        if self.greenhouse_submission(obs) and host in {
            "boards.greenhouse.io",
            "job-boards.greenhouse.io",
            "boards.eu.greenhouse.io",
            "job-boards.eu.greenhouse.io",
        }:
            from .ats import canonicalize

            if (
                parsed.scheme != "https"
                or port not in {None, 443}
                or parsed.username
                or parsed.password
                or not (
                    re.fullmatch(r"/[^/]+/jobs/[0-9]+(?:/application)?/?", parsed.path)
                    or parsed.path in {"/embed/job_app", "/embed/job_app/"}
                )
            ):
                return False
            try:
                return canonicalize(url)[1] == canonicalize(self.engine.job["url"])[1]
            except ValueError:
                return False
        return origin(url) in self.rule["link_origins"]

    async def before_click(self, control_id):
        if self.challenge_id:
            return
        obs = await self.engine.form.scan()
        button = next((b for b in obs["controls"] if b["id"] == control_id), None)
        delivery = bool(
            button
            and re.fullmatch(r"send(?: code)?", button["label"], re.I)
            and re.search(
                r"where (?:would|do) you (?:like|want) to (?:receive|get)\b.{0,40}\bcode", obs["text"], re.I
            )
        )
        if delivery:
            # A delivery chooser has no email input. Acquire its request lease
            # before Send, and never silently request a phone code instead.
            if not await self.engine.choose_email_code_delivery():
                raise ValueError("The verification delivery choice is not confirmed as email")
            if not self.rule:
                self.rule = await self.mail.ensure_rule(
                    self.engine.job, self.engine.form.resolver.profile.email
                )
            if not self.rule:
                raise ValueError("No trusted verification sender is available for this employer")
            if origin(self.engine.page.url) not in self.rule["link_origins"]:
                raise ValueError("Verification delivery is outside the employer rule")
            await self.acquire("code")
            return True
        if not self.rule:
            return
        ordinary = [f for f in obs["fields"] if f["type"] != "checkbox"]
        email_only = ordinary and all(
            f["type"] == "email" or f["label"].lower() in {"email", "email address"} for f in ordinary
        )
        if button and email_only and (SEND.fullmatch(button["label"]) or CONFIRM.fullmatch(button["label"])):
            await self.acquire()  # Persist request window before the website sends the email.
            return True

    def extend_pre_armed_request(self):
        """Persist the pre-click request window for the employer's emailed-code step after submit."""
        if self.challenge_id:
            try:
                self.mail.persist_wait(self.challenge_id)
            except ValueError:
                pass  # Already closed or past its own deadline; nothing left to extend.

    async def clear_secrets(self):
        for el in self.secret_fields:
            try:
                await el.fill("", timeout=500)
            except Exception:
                pass
        self.secret_fields = []

    async def confirm_existing_application(self):
        """Finish an explicitly unconfirmed Oracle portal card for this exact job."""
        e = self.engine
        if (
            e.job.get("ats") != "oracle"
            or origin(e.page.url) != origin(e.job["url"])
            or "/my-profile" not in urlsplit(e.page.url).path
        ):
            return False
        expected = urlsplit(e.job["url"])
        cards = []
        for card in await e.page.locator("li.cs-application-tile:visible").all():
            if await card.evaluate(
                """(el,job) => [...el.querySelectorAll('a[href]')].some(a => {
              const u=new URL(a.href);return u.origin===job.origin && u.pathname.replace(/\\/$/,'')===job.path;
            })""",
                {"origin": origin(e.job["url"]), "path": expected.path.rstrip("/")},
            ):
                cards.append(card)
        if len(cards) != 1 or not re.search(r"Status\s*Unconfirmed\b", await cards[0].inner_text(), re.I):
            return False
        confirm = cards[0].get_by_role("button", name="Confirm", exact=True)
        if await confirm.count() != 1 or not await confirm.is_enabled():
            return False
        if self.challenge_id:
            from .db import MailChallenge

            with self.mail.db.session() as s:
                previous = s.get(MailChallenge, self.challenge_id)
                if not previous or previous.state != "verified":
                    return False
            self.challenge_id, self.attempts = None, 0
        await e.form.scan()
        dom_id = await confirm.get_attribute("data-jp-id")
        controls = [
            b["id"]
            for b in e.form.buttons.values()
            if b.get("dom_id") == dom_id and e.form.frames[b["id"]] == e.page.main_frame
        ]
        if len(controls) != 1:
            return False
        await self.prepare_submission("code")
        await e.form.click(controls[0], auth_control=True)
        e.emit(
            "email_step", "Clicked confirmation for the existing job-specific unconfirmed portal application"
        )
        await e.form.settle(timeout=5)
        if self.state(await e.form.scan())[0]:
            await self.handle()
        return True

    async def handle(self):
        e = self.engine
        obs = await e.form.scan()
        kind, fields = self.state(obs)
        if (
            not kind
            and e.run_record["packet"].get("verification_only")
            and e.run_record["packet"].get("verification_repair")
        ):
            ordinary = [f for f in obs["fields"] if f["type"] != "checkbox"]
            email = [
                f
                for f in ordinary
                if f["type"] == "email" or re.fullmatch(r"email(?: address)?", f["label"], re.I)
            ]
            actions = [
                b
                for b in obs["controls"]
                if re.fullmatch(r"next|continue|sign in|log in|send(?: verification)? code", b["label"], re.I)
            ]
            if (
                len(ordinary) == len(email) == len(actions) == 1
                and self.rule
                and self.code_origin_allowed(e.page.url, obs)
            ):
                # Reconcile an uncertain submission through the employer's email-only login.
                # This owner still cannot fill application fields or submit the application.
                await e.form.locator(email[0]["id"]).fill(e.form.resolver.profile.email, timeout=3000)
                await self.before_click(actions[0]["id"])
                await e.form.click(actions[0]["id"], auth_control=True)
                for _ in range(20):
                    obs = await e.form.scan()
                    kind, fields = self.state(obs)
                    if kind:
                        break
                    await asyncio.sleep(0.25)
        if not kind:
            return False
        if re.search(
            r"(?:sent|texted).{0,60}(?:your (?:phone|mobile)|sms)|(?:sms|text message) (?:code|verification)",
            obs["text"],
            re.I,
        ):
            raise ValueError("SMS verification requires an employer session; Gmail cannot supply this code")
        if not self.rule:
            from datetime import datetime

            started = e.run_record.get("started_at")
            since = datetime.fromisoformat(started).timestamp() if started else time.time()
            e.emit("email_wait", "Identifying this employer’s verification email automatically")
            for _ in range(6):
                self.rule = await self.mail.discover_rule(e.job, e.form.resolver.profile.email, since)
                if self.rule:
                    break
                await asyncio.sleep(3)
            if not self.rule:
                raise ValueError(
                    "No authenticated verification email could be linked uniquely to this employer"
                )
        if self.attempts:
            raise ValueError("Email verification did not complete; automatic resends are disabled")
        page_origin = origin(e.page.url)
        if (
            kind != "code"
            and page_origin not in self.rule["link_origins"]
            and self.employer_owned(page_origin)
        ):
            # Qualcomm's Eightfold site runs on careers.qualcomm.com: an HTTPS origin whose domain
            # label is this employer's own name joins its rule (2026-10-14).
            self.rule["link_origins"] = self.mail.add_link_origin(self.rule["id"], page_origin)
        if not (
            (self.code_origin_allowed(e.page.url, obs) or self.embedded_code_frames_allowed(fields, obs))
            if kind == "code"
            else page_origin in self.rule["link_origins"]
        ):
            raise ValueError("Add this employer's login origin to its email verification rule")
        # Verification is allowed only on a dedicated authentication step.
        code_ids = {f["id"] for f in fields}
        other = self.application_fields(obs, code_ids)
        if other and fields:
            other = await self.inside_code_dialog(fields[0], other)
        if other and 4 <= len(fields) <= 10 and all(f.get("maxlength") == 1 or digit_box(f) for f in fields):
            # A one-character box group is a code entry by construction (Starbucks' modal sits over
            # a hidden 17-field form that scan still reports).
            other = []
        if other and not self.greenhouse_submission(obs):

            def shape(f):
                return {
                    "label": str(f.get("label", ""))[:40],
                    "type": f.get("type"),
                    "maxlength": f.get("maxlength"),
                }

            e.emit(
                "email_mixed_fields",
                "Verification step mixed with other fields (shapes only)",
                {
                    "kind": kind,
                    "branch": self.state_branch,
                    "code_fields": [shape(f) for f in fields][:10],
                    "other_fields": [shape(f) for f in other][:12],
                    "other_count": len(other),
                },
            )
            raise ValueError(
                "Email challenge is mixed with application fields; requires an employer-specific adapter"
            )
        self.attempts += 1
        if not self.challenge_id:
            if e.run_record["packet"].get("verification_repair"):
                resend = [
                    b
                    for b in obs["controls"]
                    if re.fullmatch(r"send (?:a )?new code|resend(?: (?:the )?code)?", b["label"], re.I)
                ]
                if kind != "code" or len(resend) != 1:
                    raise ValueError("The retained verification page offers no unique new-code action")
                self.begin(kind)
                await e.form.click(resend[0]["id"], auth_control=True)
                e.emit("email_verification", "Requested a fresh code for the retained submission draft")
        if not self.challenge_id:
            # Navigation itself can have sent the challenge. Use this run's start, not an old inbox window.
            from datetime import datetime

            started = self.engine.run_record.get("started_at")
            since = datetime.fromisoformat(started).timestamp() if started else time.time()
            self.begin(kind, since=since)
        self.mail.restrict_kind(self.challenge_id, e.run_id, kind)
        guard = None
        try:
            token = await self.mail.wait(
                self.challenge_id, max_wait_seconds=code_inline_wait(e.job.get("ats")) if kind == "code" else 3
            )
            if token["kind"] == "code":
                # Sites may rerender or redirect while delivery is pending. Reinspect before injecting a secret.
                obs = await e.form.scan()
                _, fields = self.state(obs)
                if not (
                    self.code_origin_allowed(e.page.url, obs) or self.embedded_code_frames_allowed(fields, obs)
                ) or any(not self.code_origin_allowed(e.form.frames[f["id"]].url, obs) for f in fields):
                    raise ValueError("Verification page changed to an origin outside the employer rule")
                code_ids = {f["id"] for f in fields}
                if not self.greenhouse_submission(obs) and self.application_fields(obs, code_ids):
                    raise ValueError("Verification page changed to a mixed application step")
                if not fields:
                    raise ValueError("Email contained a code but the website has no supported code input")
                code = token["value"]
                if len(fields) > 1 and len(code) != len(fields):
                    raise ValueError("Verification code length does not match the website")
                buttons = [b for b in obs["controls"] if CONFIRM.fullmatch(b["label"])]
                if self.greenhouse_submission(obs):
                    buttons = [
                        b for b in obs["controls"] if re.fullmatch(r"submit application", b["label"], re.I)
                    ]
                if len(buttons) > 1:
                    raise ValueError("Verification control is ambiguous")
                button = await e.form.locator(buttons[0]["id"]).element_handle() if buttons else None
                button_label = buttons[0]["label"] if buttons else ""
                filled = 0
                from .adapters.base import enter_code

                boxes = [e.form.locator(f["id"]) for f in fields]
                for el in boxes:
                    # Hold the original DOM node, never a locator that could match a new page.
                    self.secret_fields.append(await el.element_handle())
                await enter_code(boxes, code, timeout=3000)
                filled = len(boxes)
                e.emit(
                    "email_step",
                    "Verification code entered in employer form",
                    {
                        "fields": len(fields),
                        "filled": filled,
                    },
                )
                if self.greenhouse_submission(obs) and not button:
                    # Greenhouse enables Submit only after all code digits are filled.
                    refreshed = await e.form.scan()
                    _, refreshed_fields = self.state(refreshed)
                    if not self.greenhouse_submission(refreshed) or not (
                        self.code_origin_allowed(e.page.url, refreshed)
                        or self.embedded_code_frames_allowed(refreshed_fields, refreshed)
                    ):
                        raise ValueError("Application verification changed before confirmation")
                    enabled = [
                        b
                        for b in refreshed["controls"]
                        if re.fullmatch(r"submit application", b["label"], re.I)
                    ]
                    if len(enabled) == 1:
                        button = await e.form.locator(enabled[0]["id"]).element_handle()
                        button_label = enabled[0]["label"]
                if button and await button.is_visible():
                    # Capability is scoped to this inspected auth control, not the application's submit guard.
                    marked = await button.evaluate(
                        r"""(el,expected) => {
                      const label=(el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
                      const clean=s => s.replace(/\s+/g,' ').trim().toLowerCase();
                      if(clean(label)!==clean(expected))return false;
                      el.dataset.jpAuthControl='true';el.dataset.jpControlLabel=label;
                      el.dataset.jpFinalAuth='true';return true;
                    }""",
                        button_label,
                    )
                    if not marked:
                        raise ValueError("Verification control changed before confirmation")
                    try:
                        await button.click(timeout=5000)
                    finally:
                        try:
                            await button.evaluate(
                                "el => {delete el.dataset.jpAuthControl;delete el.dataset.jpControlLabel;delete el.dataset.jpFinalAuth}"
                            )
                        except Exception:
                            pass
                    e.emit("email_step", "Employer verification control clicked")
                # Zero buttons supports code inputs which auto-submit on the final character.
            else:
                e.verification_origins = set(self.rule["link_origins"])
                guard = await VerificationGuard(
                    e.page, e.verification_origins, e.config.allow_private_urls
                ).start()
                await e.page.goto(token["value"], wait_until="domcontentloaded", timeout=15000)
            for _ in range(60):
                if guard and guard.blocked:
                    raise ValueError("Verification redirect is outside the employer rule")
                await asyncio.sleep(0.25)
                after = await e.form.scan()
                pending, _ = self.state(after)
                personal = [
                    f
                    for f in after["fields"]
                    if f["type"] == "file"
                    or re.search(r"^(?:first name|last name|full name|phone|resume|cv)$", f["label"], re.I)
                ]
                explicit = re.search(
                    r"email (?:address )?(?:has been |is )?verified|verification (?:complete|successful)|successfully (?:verified|signed in)|thank you for applying|application (?:has been |was )?(?:successfully )?submitted|(?:your )?application (?:has been |was )?received|received your application",
                    after["text"],
                    re.I,
                )
                # The owned job-specific receipt is stronger than lingering
                # "check your email" copy on the acknowledgement page. Do not
                # discard a real submission proof through a second text regex.
                receipt = await e.form.proof()
                profile_ready = (
                    e.job.get("ats") == "oracle"
                    and origin(e.page.url) == origin(e.job["url"])
                    and "/my-profile" in urlsplit(e.page.url).path
                    and any(re.fullmatch("Profile", h, re.I) for h in after.get("headings", []))
                    and any(
                        re.fullmatch("Active Job Applications", h, re.I) for h in after.get("headings", [])
                    )
                )
                # A code step that left the page entirely (Starbucks moved on to its Terms of Service
                # popup): no code inputs and no code/"check your email" copy remain.
                moved_on = (
                    token["kind"] == "code"
                    and not pending
                    and not CODE.search(after["text"])
                    and not WAITING.search(after["text"])
                    and bool(after["text"].strip())
                )
                if receipt or (not pending and (personal or explicit or profile_ready or moved_on)):
                    secret_keys = []
                    if token["kind"] == "link":
                        link = urlsplit(token["value"])
                        secret_keys = [k for k, _ in parse_qsl(link.query)]
                        if not link.query and not link.fragment and urlsplit(e.page.url).path == link.path:
                            # A one-use token may be embedded in the path. Leave that URL before an agent observes it.
                            await e.page.goto(e.job["url"], wait_until="domcontentloaded", timeout=15000)
                            restored = await e.form.scan()
                            if self.state(restored)[0] or not restored["fields"]:
                                raise ValueError(
                                    "Email was accepted but the application did not resume; requires an employer adapter"
                                )
                    self.mail.outcome(self.challenge_id, "verified", "Website accepted email verification")
                    e.emit(
                        "email_verified",
                        "Employer email verification completed",
                        {"challenge_id": self.challenge_id},
                    )
                    await e.page.evaluate(
                        """keys => {const u=new URL(location.href);for(const k of [...u.searchParams.keys()])if(keys.includes(k)||/token|code|secret|ticket/i.test(k))u.searchParams.delete(k);u.hash='';history.replaceState(null,'',u);} """,
                        secret_keys,
                    )
                    if e.job.get("ats") == "eightfold":
                        await self.reopen_eightfold_apply()
                    return True
            after = await e.form.scan()
            e.emit(
                "email_step",
                "Verification did not reach a confirmed transition",
                {
                    "code_fields_remaining": len(self.state(after)[1]),
                    "confirmation_found": bool(await e.form.proof()),
                    "submit_controls": sum(
                        bool(re.fullmatch(r"submit application", b["label"], re.I)) for b in after["controls"]
                    ),
                },
            )
            raise ValueError("No verified transition after entering the code or opening the link")
        except MailPending:
            self.deferred = True
            raise
        except Exception as exc:
            self.mail.outcome(
                self.challenge_id, "failed", "Verification did not complete; inspect the employer session"
            )
            if guard and guard.blocked:
                raise ValueError("Verification redirect is outside the employer rule") from None
            if isinstance(exc, ValueError):
                raise
            # Browser exceptions include the tokenized URL. Never pass them to the model or log.
            raise ValueError(
                "Email verification failed in the browser; inspect the employer session"
            ) from None
        except BaseException:
            self.mail.outcome(self.challenge_id, "cancelled", "Browser attempt interrupted")
            raise
        finally:
            if guard:
                try:
                    await guard.close()
                except Exception:
                    pass
            e.verification_origins = None
            await self.clear_secrets()

    def close(self):
        if self.deferred:
            return  # The durable email wait owns this request after the browser slot is released.
        result = getattr(self.engine, "result", None) or {}
        if (
            result.get("waiting_kind") == "email"
            and result.get("wait_handle", {}).get("challenge_id") == self.challenge_id
        ):
            # The ATS adapter can park the same request this helper armed before
            # its send click. One helper's cleanup must not cancel that transfer.
            return
        if self.challenge_id:
            from .db import MailChallenge

            with self.mail.db.session() as s:
                c = s.get(MailChallenge, self.challenge_id)
                state = c.state if c else None
            if (
                state == "matched"
                and getattr(self.engine, "armed", False)
                and result.get("state") == "submission_unknown"
            ):
                # Gmail already matched this run's code, but no browser step is left to enter it.
                # Hand the still-live request to the durable wait instead of dropping the code.
                try:
                    handle = MailPending(self.mail.persist_wait(self.challenge_id)).challenge
                except ValueError:
                    handle = None
                if handle:
                    result.update(
                        state="submission_unknown",
                        blocker_type="email_wait",
                        waiting_kind="email",
                        wait_handle=handle,
                        reason="Waiting for the employer verification email; other applications continue",
                    )
                    return
            if state in {"pending", "matched", "consumed"}:
                self.mail.outcome(self.challenge_id, "cancelled", "Browser attempt ended")


def redacted_url(url):
    p = urlsplit(url)
    return urlunsplit(
        (
            p.scheme,
            p.netloc,
            p.path,
            urlencode(
                [
                    (k, "REDACTED" if re.search("token|code|secret|state|ticket|session", k, re.I) else v)
                    for k, v in parse_qsl(p.query)
                ]
            ),
            "",
        )
    )
