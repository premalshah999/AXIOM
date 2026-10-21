"""iCIMS career portals (careers-<company>.icims.com).

The public job page wraps the real application in #icims_content_iframe. Loading the frame URL
directly (in_iframe=1) removes a cross-document hop. Flow: Apply -> email -> (sign in | create
profile with password) -> resume upload/parse -> profile -> screening -> EEO -> Submit."""

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from sqlalchemy import select

from ..db import Event, MailChallenge, MailRule, Run, Setting, now
from ..accounts import account_tenant
from ..schemas import Answer

from .base import Adapter, Step, KNOWN_ACCOUNT

# Schwab labels it "Apply for this job" (no "online"); unmatched, every Schwab run left the iCIMS flow at the job page
# and the generic navigator clicked an email step it cannot pass (2026-10-21). The link target still has to be this job's.
# The application steps after the profile, by their own path (the forms packet is /form?form=...&isPacket=1).
EARLY_STEP = re.compile(r"/jobs/\d+/[^/]+/(?:candidate|questions)/?$")
PROTOCOL_STEP = re.compile(r"/jobs/\d+/[^/]+/(?:questions|eeo|form)/?$")
APPLY_ICIMS = re.compile(r"apply for this job(?: online)?|^apply(?: now)?$|apply online", re.I)
PROFILE_CREATE = re.compile(r"^(?:create|submit)(?: (?:my|your|a))? profile$", re.I)
PROFILE_SAVE = re.compile(r"^(?:create|submit|update|save)(?: (?:my|your|a))? profile$", re.I)
# Central (Auth0) offers optional passkey/MFA enrollment after a sign-in: /u/mfa-webauthn-platform-enrollment, then
# /u/mfa-webauthn-platform-error-enrollment when the device cannot register (Rovisys 2026-10-19).
CENTRAL_ENROLLMENT = re.compile(r"^/u/(?:mfa-[a-z-]*enrollment|passkey-enrollment[a-z-]*)$", re.I)
ENROLLMENT_DECLINES = ("remind me later", "not now", "maybe later", "skip for now", "skip", "no thanks", "not on this device")
# iCIMS writes its progress bar for screen readers: "Step 2 of 4. Voluntary Self-Identification(Current Step)" (ICE
# 2026-10-19: Candidate Profile, Voluntary Self-Identification, Job Specific Questions, Portal Specific Forms). Only
# the last step submits the application; an earlier step's "Submit" opens the next one. ICE's run committed on EEO's
# Submit, waited for a receipt and stopped at "Job Specific Questions" as submission_unknown.
CURRENT_STEP = re.compile(r"Step\s+(\d+)\s+of\s+(\d+)\.?\s*([^\n]{0,80}?)\s*\(Current Step\)", re.I)
# The last step counts its forms: "Portal Specific Forms (1/2)" (ICE 2026-10-19); form 1 of 2 is not the last page.
PACKET_FORM = re.compile(r"\((\d+)\s*/\s*(\d+)\)")
# The self-identification forms' radios carry no labels; their surrounding markup is recorded once for REA.
UNLABELED_JS = r"""(names) => {
  const markup = names.flatMap(n => [...document.querySelectorAll('input[name="' + CSS.escape(n) + '"]')].slice(0, 4)
    .map(e => ((e.parentElement && e.parentElement.parentElement) || e.parentElement || e).outerHTML.replace(/\s+/g, ' ').slice(0, 700)));
  const submits = markup.length ? [...document.querySelectorAll('input[type=submit],input[type=image],button,a[onclick*=submit]')]
    .slice(0, 6).map(e => e.outerHTML.replace(/\s+/g, ' ').slice(0, 240)) : [];
  return markup.concat(submits.map(s => 'SUBMIT ' + s));
}"""
STEP_SUBMIT = re.compile(r"submit|next|continue|save(?: (?:and|&) continue)?", re.I)
# Portal Specific Forms posts one form after another; each but the last names its successor in a hidden "next" (REI
# Systems, network-captured 2026-10-19: the veteran form posted next=…, the disability form did not, and its Submit
# completed the application).
PACKET_CONTINUES_JS = "() => !!document.querySelector('form input[name=\"next\"]:not([value=\"\"])[value]')"
# A refused Submit Profile comes back with one banner, and each refused field is marked only by colour: a red label
# with an icon and red borders (ICE 2026-10-19: a current job's blank End Date).
INVALID_DATA = re.compile(r"invalid data error|please check all information before trying again", re.I)
# "This email address is already in use" (profile) and "The email address and phone number are already in use. Please
# login ..." (Eide Bailly's email step, which registers new candidates; 2026-10-19).
EMAIL_IN_USE = re.compile(r"email address(?: and phone number)? (?:is|are) already in use", re.I)
# A workflow can end on an optional third-party assessment (Goldbelt 2026-10-19: an Employment Tax Credit questionnaire,
# "optional ... navigate away"). iCIMS has already received the application; its candidate dashboard says so
# ("2026-20717 Junior Data Analyst Received Submission").
DASHBOARD_RECEIVED = re.compile(
    r"\b(?:received submission|submitted|under review|in review|application received)\b", re.I
)
# A row that says the application is unfinished or unsent is never a receipt, whatever else it contains
# ("Incomplete - application not submitted"). Its action links ("Continue Application", "Withdraw") are on every row.
DASHBOARD_UNFINISHED = re.compile(
    r"\b(?:incomplete|draft|in progress|unsubmitted|not (?:yet )?(?:been )?(?:submitted|received|sent))\b", re.I
)


def dashboard_status(row, title=""):
    """The status words of this requisition's dashboard row when it reads as received, else None.

    The row text carries the job title, so the title is removed before the status is read: a posting called
    "Submitted Claims Analyst" made every one of its rows look submitted. Rows that say incomplete or not submitted are
    never received, which kept a false confirmation (or a false "already applied") from ending a run.
    """
    text = re.sub(re.escape(title), " ", row or "", flags=re.I) if title else row or ""
    if DASHBOARD_UNFINISHED.search(text):
        return None
    found = DASHBOARD_RECEIVED.search(text)
    return found[0] if found else None

FLAGGED_JS = r"""() => {
  const red = c => { const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(c || ''); return !!m && +m[1] >= 150 && +m[2] <= 90 && +m[3] <= 90; };
  const out = [];
  for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]),select,textarea')) {
    const box = el.getBoundingClientRect();
    if (!box.width || !box.height) continue;
    if (!red(getComputedStyle(el).borderTopColor) && el.getAttribute('aria-invalid') !== 'true') continue;
    el.setAttribute('aria-required', 'true');
    el.dataset.jpEmployerFlagged = '1';
    const label = (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]')) || el.closest('div')?.querySelector('label');
    out.push(((label && label.innerText) || el.name || el.id || '').replace(/\s+/g, ' ').trim().slice(0, 80));
  }
  return [...new Set(out)];
}"""


class ICIMS(Adapter):
    id = "icims"
    name = "iCIMS"
    apply_ids = ()
    # The email step posts css_loginName from <input type=submit id=enterEmailSubmitButton>; its label is "Next" on most
    # employers and "I Acknowledge the Privacy Notice" on Schwab (2026-10-21).
    email_submit_ids = ("enterEmailSubmitButton",)

    async def captcha_auth_handoff(self):
        """A delivered callback allows native auth; it does not prove login."""
        location = urlsplit(self.page.url)
        captcha = getattr(self.e, "captcha", None)
        if not (
            captcha
            and location.scheme == "https"
            and location.hostname == "login.icims.com"
            and (
                self.central_transaction(self.page.url, signup=True)
                or self.central_transaction(self.page.url)
                or location.path.startswith("/u/reset-password/request/")
            )
            and await captcha.token_present(self.page)
        ):
            return False
        obs = await self.form.scan()
        controls = [
            b
            for b in obs["controls"]
            if not b.get("disabled")
            and not b.get("href")
            and re.fullmatch(r"continue|next|log ?in|sign up", b["label"], re.I)
        ]
        return bool(
            not obs.get("errors")
            and len(controls) == 1
            and any(self.is_login_field(f) or f["type"] == "password" for f in obs["fields"])
        )

    async def captcha_open(self):
        # A provider iframe can remain visible after its owned callback.
        # The enabled native form must consume it and report acceptance.
        if await self.captcha_auth_handoff():
            return False
        location = urlsplit(self.page.url)
        if location.hostname == "login.icims.com" and (
            self.central_transaction(self.page.url)
            or self.central_transaction(self.page.url, signup=True)
            or location.path.startswith("/u/reset-password/request/")
        ):
            # A retained Central widget may predate observation. Its native
            # Continue starts execute(), which binds the owned pending promise.
            # Do not block the account controller on an unbound iframe first.
            captured = await self.page.evaluate("""()=>!!window.__jpTokenCaptcha?.entries.some(
                e=>e.kind==='hcaptcha'&&(e.pending||e.callback))""")
            if not captured:
                obs = await self.form.scan()
                controls = [
                    b
                    for b in obs["controls"]
                    if not b.get("disabled")
                    and not b.get("href")
                    and re.fullmatch(r"continue|next|log ?in|sign up", b["label"], re.I)
                ]
                if len(controls) == 1 and any(
                    self.is_login_field(f) or f["type"] == "password" for f in obs["fields"]
                ):
                    return False
        return await super().captcha_open()

    def connect_profile_submit(self, button):
        """Connect's last step ("Submit Profile" on /connect/profile) saves the talent-network profile that
        find_apply follows as account preparation; it is not this application. Schwab 2026-10-17: the final-submit
        guard refused it five times, so the application was never reached."""
        location, employer = urlsplit(self.page.url), urlsplit(self.job["url"])
        return bool(
            location.scheme == employer.scheme == "https"
            and location.netloc == employer.netloc
            and location.path.rstrip("/") == "/connect/profile"
            and not button.get("href")
            and re.fullmatch(r"submit(?: (?:my |your )?profile)?", button["label"].strip(), re.I)
        )

    def candidate_profile_submit(self, button):
        """The job's own candidate profile ("Submit Profile" on /jobs/<id>/<slug>/candidate) creates the account and
        saves the profile; EEO, the forms packet and the application's own submit follow (REI Systems, a person's run
        network-captured 2026-10-19). Its label says Submit, so the form guard refused it as the final submit (ICE)."""
        frame = getattr(self.form, "frames", {}).get(button.get("id"))
        location, employer = urlsplit(getattr(frame, "url", None) or self.page.url), urlsplit(self.job["url"])
        return bool(
            location.scheme == employer.scheme == "https"
            and location.netloc == employer.netloc
            and re.fullmatch(r"/jobs/\d+(?:/[^/]+)?/candidate/?", location.path)
            and not button.get("href")
            and PROFILE_SAVE.fullmatch(button["label"].strip())
        )

    @staticmethod
    def application_step(obs):
        """(current, total, name) from iCIMS's progress bar, or None when the page has none."""
        found = CURRENT_STEP.search(obs.get("text", "") or "")
        if not found or not 0 < int(found[1]) <= int(found[2]):
            return None
        return int(found[1]), int(found[2]), found[3].strip()

    def step_before_last(self, obs):
        step = self.application_step(obs)
        if not step:
            # Without a readable progress caption, the protocol still knows two steps that always precede another:
            # the candidate profile and the global questions. The recovery agent committed VTG's application on an
            # intermediate Submit and parked it at step 3 of 4 (EEO) as submission_unknown (2026-10-21).
            return any(
                EARLY_STEP.search(urlsplit(u).path) and (not urlsplit(u).path.endswith("/questions")
                                                         or "global=1" in urlsplit(u).query)
                for u in {obs.get("url", "")} | {f.get("frame_url") or "" for f in obs.get("fields", [])}
            )
        if step[0] < step[1]:
            return True
        # The last step's own counter, in its screen-reader name or its visible caption ("Portal Specific Forms (1/2)").
        name = re.escape(re.sub(r"\s*\(\d+\s*/\s*\d+\)\s*$", "", step[2]))
        counter = PACKET_FORM.search(step[2]) or re.search(name + r"\s*" + PACKET_FORM.pattern, obs.get("text", "") or "")
        return bool(counter and int(counter[1]) < int(counter[2]))

    def step_submit(self, obs):
        """The one Submit/Next control of an application step, or None."""
        controls = [
            b
            for b in obs["controls"]
            if not b.get("disabled") and not b.get("href") and STEP_SUBMIT.fullmatch(b["label"].strip())
        ]
        return controls[0] if len(controls) == 1 else None

    async def packet_continues(self):
        for frame in self.page.frames:
            try:
                if await frame.evaluate(PACKET_CONTINUES_JS):
                    return True
            except Exception:
                continue
        return False

    async def intermediate_step(self, obs):
        """A step whose Submit opens another step: before the last step, or a packet form that names its successor."""
        return self.step_before_last(obs) or (bool(self.step_submit(obs)) and await self.packet_continues())

    async def intermediate_submit(self):
        """The guided flow's submit on an intermediate step: open the next step without the one-time reservation.
        None when this page's Submit is the application's own."""
        obs = await self.form.scan()
        control = self.step_submit(obs)
        if not control or not await self.intermediate_step(obs):
            return None
        step = self.application_step(obs)
        await super().click(control, auth=True, step=True)
        self.step_submits = getattr(self, "step_submits", 0) + 1
        await self.form.settle(timeout=6)
        self.emit(
            "step_submit",
            "Submitted an application step before the last; the application continues",
            {"label": control["label"], "step": list(step[:2]) if step else None, "name": step[2] if step else ""},
        )
        return json.dumps({"executed": True, "intermediate_step": True, "label": control["label"]})

    async def dashboard_row(self, navigate=True):
        """This requisition's row on iCIMS's candidate dashboard: (frame URL, row text), or (None, None)."""
        job = urlsplit(self.job["url"])
        number = re.search(r"/jobs/(\d+)", job.path)
        if not number:
            return None, None
        if navigate:
            try:
                await self.page.goto(
                    f"{job.scheme}://{job.netloc}/jobs/dashboard?in_iframe=1", wait_until="domcontentloaded", timeout=20000
                )
                await self.form.settle(timeout=6)
            except Exception:
                return None, None
        for frame in self.page.frames:
            try:
                text = await frame.locator("body").inner_text(timeout=2000)
            except Exception:
                continue
            if row := re.search(rf"\b\d{{4}}-{number[1]}\b[^\n]{{0,200}}", text):
                return frame.url, re.sub(r"\s+", " ", row[0]).strip()[:200]
        return None, None

    async def dashboard_receipt(self, navigate=True):
        """This requisition's row as a confirmed result when it reads as received, or None."""
        url, row = await self.dashboard_row(navigate)
        status = dashboard_status(row, self.job.get("title", ""))
        if not status:
            return None
        return {
            "state": "confirmed",
            "reason": f"The employer's candidate dashboard lists this application as {status}",
            "receipt": {
                "url": url,
                "confirmation": row,
                "title": await self.page.title(),
                "type": "explicit_confirmation_page",
                "evidence_version": 2,
                **self.form.evidence_context(),
                "evidence_selector": "icims_candidate_dashboard_row",
                "status_region": "job_application_status",
            },
        }

    async def on_dashboard(self, obs):
        """The candidate dashboard a returning-candidate login lands on (Eide Bailly 2026-10-19: read as a confirmation
        page, so the run stopped while the row said "Incomplete"). Continue an incomplete application from the posting;
        a received one is this run's receipt only when this run walked its steps, otherwise it was already applied."""
        url, row = await self.dashboard_row(navigate=False)
        if dashboard_status(row, self.job.get("title", "")):
            if getattr(self, "step_submits", 0) or self.committed:
                return await self.dashboard_receipt(navigate=False)
            return {
                "state": "already_applied",
                "reason": "The employer's candidate dashboard already lists this application as received",
                "receipt": {"type": "employer_already_applied", "url": url, "evidence": row},
            }
        if self.attempts["dashboard_return"] >= 2:
            return self.defer("The candidate dashboard kept returning", state="waiting_browser", blocker_type="navigation")
        self.attempts["dashboard_return"] += 1
        self.emit("step", "Continuing the application from the posting after the candidate dashboard", {"row": row})
        await self.e.return_to_job()
        return None

    async def on_unknown(self, obs):
        if urlsplit(obs.get("url", "")).path.rstrip("/").endswith("/jobs/dashboard"):
            return await self.on_dashboard(obs)
        step = self.application_step(obs)
        if (
            step
            and step[0] == step[1]
            and re.search(r"assessment", step[2], re.I)
            and getattr(self, "step_submits", 0)
            and (receipt := await self.dashboard_receipt())
        ):
            # This run walked the earlier steps; the last one is an optional assessment the applicant need not take.
            self.emit("step_submit", "The workflow ends on an optional assessment; the dashboard shows the submission")
            return receipt
        # EEO and the forms packet paint their progress bar before their fields (ICE 2026-10-19: both were scanned empty,
        # read as unknown and handed to the guided flow). Give the step a moment to render before falling back.
        # iPipeline's job page is the employer's wrapper around iCIMS's content frame; scanned before that frame loaded,
        # it showed no Apply and the whole run went to the guided flow (2026-10-19).
        path = urlsplit(obs.get("url", "")).path
        job_page = re.search(r"/jobs/\d+(?:/[^/]+)?/job/?$", path)
        # A field-less page between steps (after the email step it redirects to Central) is transient too (iPipeline).
        rendering = not obs["fields"] or (job_page and not self.find_apply(obs))
        if rendering and self.attempts[("step_render", path)] < 3:
            self.attempts[("step_render", path)] += 1
            await self.form.settle(timeout=4)
            await asyncio.sleep(1.5)
            return None
        return await super().on_unknown(obs)

    async def on_review(self, obs, report=None):
        # A packet form that names its successor is not the application's last page: open the next form.
        if not self.committed and (outcome := await self.intermediate_submit()):
            self.last_step_submit = outcome
            return None
        return await super().on_review(obs, report)

    async def click(self, button, auth=False, step=True):
        if self.candidate_profile_submit(button):
            return await super().click(button, auth=True, step=step)
        if (
            not auth
            and not button.get("href")
            and STEP_SUBMIT.fullmatch(button["label"].strip())
            and await self.intermediate_step(await self.form.scan())
        ):
            # Not the application's submit: the form guard reserves Submit for the commit step.
            self.step_submits = getattr(self, "step_submits", 0) + 1
            return await super().click(button, auth=True, step=step)
        if self.connect_profile_submit(button):
            # Saved as account preparation, never read as a receipt: go back to the posting's own Apply.
            result = await super().click(button, auth=True, step=step)
            await self.form.settle(timeout=4)
            self.accounts.mark(self.job["url"], "exists", connect_profile=True)
            self.emit("account", "Saved the employer's candidate profile; returning to the job's application")
            await self.e.return_to_job()
            return result
        # CAPTCHA acceptance can enable an Auth0 control without submitting
        # its form. Keep only this actual action's metadata, never secrets.
        self.central_auth_action = None
        self.central_auth_continued = False
        location = urlsplit(self.page.url)
        captcha = getattr(self.e, "captcha", None)
        if (
            auth
            and captcha
            and location.scheme == "https"
            and location.hostname == "login.icims.com"
            and (
                self.central_transaction(self.page.url)
                or location.path.startswith("/u/reset-password/request/")
            )
            and re.fullmatch(r"continue|next|log ?in", button["label"], re.I)
        ):
            self.central_auth_action = {
                "url": self.page.url,
                "label": button["label"],
                "accepted_before": bool(await captcha.token_present(self.page)),
                "acceptance_attempt_before": getattr(captcha, "acceptance_attempt", None),
            }
        return await super().click(button, auth=auth, step=step)

    async def after_auth_click(self):
        action = getattr(self, "central_auth_action", None)
        self.central_auth_action = None
        result = await super().after_auth_click()
        captcha = getattr(self.e, "captcha", None)
        if result:
            if result.get("recovery_cause") != "security_challenge" or not await self.captcha_auth_handoff():
                return result
            self.emit("auth", "Delivered CAPTCHA callback handed to the native Central account form")
        if not action or not captcha:
            return None
        fresh_acceptance = bool(
            getattr(captcha, "acceptance_attempt", None)
            and action.get("acceptance_attempt_before") != captcha.acceptance_attempt
        )
        if action["accepted_before"] and not fresh_acceptance:
            return None
        if self.page.url != action["url"] or not await captcha.token_present(self.page):
            return None
        obs = await self.form.scan()
        controls = [
            b
            for b in obs["controls"]
            if not b.get("disabled") and not b.get("href") and b["label"] == action["label"]
        ]
        identifiers = [f for f in obs["fields"] if self.is_login_field(f) or f["type"] == "password"]
        if (
            obs.get("url") == action["url"]
            and not obs.get("errors")
            and identifiers
            and all(f.get("value") and f.get("valid", True) for f in identifiers)
            and len(controls) == 1
        ):
            # One native continuation of the already-owned login/reset form.
            # Do not recurse or turn an unchanged page into another paid loop.
            await super().click(controls[0], auth=True)
            self.central_auth_continued = True
            self.emit("auth", "Submitted the owned Central account form after native CAPTCHA acceptance")
            return await super().after_auth_click()
        return None

    async def complete_password_reset(self, url):
        # A prearmed mail window is not evidence that Auth0 sent anything.
        # Its first Continue may only have opened a CAPTCHA.
        location = urlsplit(self.page.url)
        if location.hostname == "login.icims.com" and location.path.startswith("/u/reset-password/request/"):
            from .base import CHECK_EMAIL

            await self.form.settle(timeout=4)
            obs = await self.form.scan()
            if not CHECK_EMAIL.search(obs.get("text", "")):
                self.finish_mail("failed", "The reset request form was not acknowledged by the website")
                return self.defer(
                    "The employer has not acknowledged the password reset request",
                    state="waiting_browser",
                    blocker_type="account",
                )
        return await super().complete_password_reset(url)

    @staticmethod
    def prepare_url(url):
        p = urlsplit(url)
        if not (p.hostname or "").endswith("icims.com") or "/jobs/" not in p.path:
            return url
        query = dict(parse_qsl(p.query))
        query.setdefault("in_iframe", "1")
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), p.fragment))

    def find_apply(self, obs):
        accounts = getattr(self, "accounts", None)
        record = accounts.record(self.job["url"]) if accounts else {}
        if record.get("state") in KNOWN_ACCOUNT | {"created_locally"} and not self.employer_session(obs):
            # Atlassian offers a requisition-owned returning route; use it for the saved employer account.
            for button in obs["controls"]:
                target = urlsplit(urljoin(obs["url"], button.get("href", "")))
                job = re.search(r"/jobs/(\d+)(?:/|$)", urlsplit(self.job["url"]).path)
                target_job = re.search(r"/jobs/(\d+)(?:/|$)", target.path)
                if (
                    not button.get("disabled") and job and target_job and job[1] == target_job[1]
                    and target.scheme == "https" and target.netloc == urlsplit(self.job["url"]).netloc
                    and target.path.endswith("/login") and parse_qs(target.query).get("loginOnly") == ["1"]
                ):
                    return button
        if (
            accounts
            and accounts.may_create
            and not self.employer_session(obs)
            # Only an employer whose Central sign-in is stuck unverified. With no record at all, VTG's "Join our
            # talent network" was taken for Apply and the run filled the talent community instead (2026-10-21).
            and record
            and self.central_unverified(record)
        ):
            # Some employers offer candidate registration separately from the
            # primary Apply link. Follow only the actual owned signup href,
            # including its opaque query, then return to the original job.
            signup = self.offered_profile_signup(obs)
            if signup:
                return signup
        # Returning-user links can point at this very requisition. Never
        # confuse that path match with the new application entry.
        controls = [
            b
            for b in obs["controls"]
            if not b.get("disabled")
            and not re.search(
                r"log back in|returning (?:user|candidate)|^sign in$|^log ?in$", b["label"], re.I
            )
            and "loginOnly" not in parse_qs(urlsplit(b.get("href", "")).query)
        ]
        job_path = re.search(r"/jobs/(\d+)(?:/|$)", obs["url"])
        for b in controls:
            target = urlsplit(urljoin(obs["url"], b.get("href", "")))
            query = parse_qs(target.query)
            target_job = re.search(r"/jobs/(\d+)(?:/|$)", target.path)
            if (
                job_path
                and target_job
                and job_path[1] == target_job[1]
                and target.scheme == "https"
                and target.netloc == urlsplit(obs["url"]).netloc
                and query.get("mode") == ["apply"]
                and query.get("apply") == ["yes"]
            ):
                # Observed iCIMS entry uses /job?mode=apply&apply=yes,
                # including VTG's combined "Apply HereApply" label. The protocol target is authoritative.
                return b
        job_specific = [b for b in controls if b["label"].strip().casefold() == "apply for this job online"]
        if job_specific:
            return job_specific[0]
        for b in controls:
            href = b.get("href", "")
            if (
                job_path
                and APPLY_ICIMS.search(b["label"])
                and job_path.group() in href
                and re.search(r"/(?:login|apply)(?:[/?]|$)", href, re.I)
            ):
                return b
        # A generic Apply in the employer's global navigation can lead to a
        # customer product. Do not let the generic adapter pick it by position.
        return None

    def offered_profile_signup(self, obs):
        # An offered same-employer Connect route can expose candidate signup.
        # Its destination must still pass connect_identifier/profile checks;
        # joining a community never counts as applying to the requisition.
        employer = urlsplit(self.job["url"])
        candidates = []
        for button in obs["controls"]:
            if button.get("disabled") or not button.get("href"):
                continue
            target = urlsplit(urljoin(obs["url"], button["href"]))
            if (
                target.scheme == employer.scheme == "https"
                and target.netloc == employer.netloc
                and target.path.rstrip("/") == "/connect"
                and re.fullmatch(r"sign[ -]?up|create (?:a |your )?profile|connect with us", button["label"].strip(), re.I)
            ):
                candidates.append(button)
        return candidates[0] if len(candidates) == 1 else None

    def find_next(self, obs):
        # Connect's resume step uses a native submit button labelled Upload.
        # It parses the selected master resume before profile creation; it is
        # not the application's final submit. Keep ownership and attachment
        # evidence instead of permitting arbitrary HTML submit buttons.
        if (
            urlsplit(obs["url"]).hostname == urlsplit(self.job["url"]).hostname
            and getattr(self.form, "uploaded", False)
            and any(f["type"] == "file" for f in obs["fields"])
            and re.search(r"include resume|upload.{0,30}resume", obs.get("text", ""), re.I)
        ):
            uploads = [
                b
                for b in obs["controls"]
                if not b.get("disabled") and not b.get("href") and b["label"].strip().casefold() == "upload"
            ]
            if len(uploads) == 1:
                return uploads[0]
        # Profile creation/update precedes screening and the real job submit.
        profiles = [
            b
            for b in obs["controls"]
            if not b.get("disabled") and not b.get("href") and PROFILE_SAVE.fullmatch(b["label"])
        ]
        if len(profiles) == 1:
            return profiles[0]
        if self.step_before_last(obs) and (submit := self.step_submit(obs)):
            return submit  # "Step 2 of 4": its Submit opens step 3
        return super().find_next(obs)

    def find_final(self, obs):
        if self.step_before_last(obs):
            return None
        return super().find_final(
            {**obs, "controls": [b for b in obs["controls"] if not PROFILE_SAVE.fullmatch(b["label"])]}
        )

    def is_final_control(self, button):
        # Keep the action guard consistent with find_next/find_final: saving
        # the candidate profile precedes screening and application submission.
        return not PROFILE_SAVE.fullmatch(button["label"]) and super().is_final_control(button)

    def auth_button(self, obs, mode):
        if mode == "create":
            creates = [
                b
                for b in obs["controls"]
                if not b.get("disabled")
                and not b.get("href")
                and re.fullmatch(
                    r"(?:create|submit)(?: (?:my|your|a))? profile|"
                    r"create(?: (?:an? |your )?account)?|register|sign[ _-]?up",
                    b["label"],
                    re.I,
                )
            ]
            if len(creates) == 1:
                return creates[0]
            if creates:
                return None
            # Creation must never fall back to the adjacent returning-user
            # control merely because its id contains 'submit'.
            continuations = [
                b
                for b in obs["controls"]
                if not b.get("disabled")
                and not b.get("href")
                and re.fullmatch(r"continue|next|submit", b["label"], re.I)
            ]
            return continuations[0] if len(continuations) == 1 else None
        return super().auth_button(obs, mode)

    def classify_page(self, obs):
        location = urlsplit(obs["url"])
        path = location.path
        if self.central_enrollment(obs):
            return Step.CONSENT
        if path.rstrip("/").endswith("/jobs/dashboard"):
            return Step.UNKNOWN  # The candidate dashboard: on_unknown reads this job's row (never a confirmation page).
        if self.connect_identifier(obs):
            return Step.CREATE_ACCOUNT
        if self.employer_profile_editor(obs):
            return Step.FORM
        if (
            location.scheme == "https"
            and location.hostname == urlsplit(self.job["url"]).hostname
            and any(f["type"] == "password" for f in obs["fields"])
            and any(not b.get("href") and PROFILE_CREATE.fullmatch(b["label"]) for b in obs["controls"])
        ):
            return Step.CREATE_ACCOUNT
        # The identifier phase is an account transaction, even though it has
        # no password yet. Dispatch it to the same owner as its password phase;
        # the generic email-entry handler would issue an unrelated mail wait.
        identifier_fields = [f for f in obs["fields"] if self.is_login_field(f)]
        if identifier_fields and all(
            f in identifier_fields or f["type"] == "checkbox" for f in obs["fields"]
        ):
            if self.central_transaction(obs["url"], signup=True):
                return Step.CREATE_ACCOUNT
            if self.central_transaction(obs["url"]):
                return Step.SIGN_IN
        # iCIMS disables Next until email/privacy fields are filled. The
        # generic classifier sees no enabled button and calls this a form.
        fields = obs["fields"]
        # iPipeline's careers portal wraps the email step in its own site, whose header carries "Search" boxes; read as a
        # generic form, the email was refused "The format of the email address is not valid" (2026-10-19).
        own = [f for f in fields if not re.fullmatch(r"search", f["label"].strip(), re.I)]
        # Schwab's "Privacy Notice and Sign-In" email form loads inside the job page's content frame, so the page path
        # stays /job. Read as unknown, the navigator clicked its submit without the invisible hCaptcha the email step
        # needs, and four runs stalled on an unchanged page (2026-10-21). The email field's own frame decides.
        framed = [f for f in own if urlsplit(f.get("frame_url") or "").path.endswith("/login")]
        scope = own if path.endswith("/login") else framed
        if any(f.get("icims_name") == "css_loginName" for f in own) and not any(
            f["type"] == "password" for f in own
        ):
            # The iCIMS email step by protocol: it posts css_loginName. Schwab's also asks an optional phone, so the
            # email-and-checkboxes shape below read it as a form with "No way forward" (2026-10-21).
            return Step.EMAIL_ENTRY
        if (
            scope
            and any(f["type"] == "email" for f in scope)
            and all(f["type"] in {"email", "checkbox"} for f in scope)
        ):
            return Step.EMAIL_ENTRY
        protocol_step = PROTOCOL_STEP.search(path) or any(
            PROTOCOL_STEP.search(urlsplit(f.get("frame_url") or "").path) for f in fields
        )
        if (self.application_step(obs) or protocol_step) and fields and not any(f["type"] == "password" for f in fields):
            # EEO, Job Specific Questions and the forms packet: application steps, filled before their Submit. Their
            # URL names the step; Prime's packet page has no progress caption and was read as unknown (2026-10-21).
            return Step.FORM
        if (
            re.fullmatch(r"/jobs/\d+/[^/]+/job/?", path)
            and self.find_apply(obs)
            and all(f.get("html_id") == "footer-language-selector" and f.get("name") == "url" for f in fields)
        ):
            return Step.JOB  # Atlassian's footer locale menu is not an application question.
        return super().classify_page(obs)

    def mail_job(self, kind):
        # Employers use Central's shared authentication host. Its reset links
        # belong to that observed identity service, not the originating careers
        # host. Keep ordinary application verification scoped to the employer.
        location = urlsplit(self.page.url)
        if kind == "password_reset" and (
            location.scheme == "https"
            and location.hostname == "login.icims.com"
            or self.central_reset_continuation()
        ):
            return {**self.job, "url": "https://login.icims.com/", "ats": "icims"}
        return super().mail_job(kind)

    def captcha_account_transition(self, before_url, before, after):
        """An accepted employer email step may redirect to Central, not email a code.

        This only permits continuing the observed account transaction. It does
        not authenticate an identity or establish an application submission.
        """
        source, employer = urlsplit(before_url), urlsplit(self.job["url"])
        source_stage = self.classify_page(before)
        captured = (
            getattr(self, "auth_transition_source", None)
            or getattr(self, "pending_auth_transition_source", None)
            or {}
        )
        if (
            captured.get("stage") == Step.EMAIL_ENTRY
            and captured.get("challenge_id") == (self.challenge or {}).get("id")
            and captured.get("url")
        ):
            source = urlsplit(captured["url"])
            source_stage = Step.EMAIL_ENTRY
        current = after.get("url", "")
        identifiers = [f for f in after.get("fields", []) if self.is_login_field(f)]
        return bool(
            source.scheme == employer.scheme == "https"
            and source.hostname == employer.hostname
            and source.port in {None, 443}
            and not source.username
            and not source.password
            and source.path.endswith("/login")
            and source_stage == Step.EMAIL_ENTRY
            and current == self.page.url
            and self.central_transaction(current)
            and urlsplit(current).path == "/u/login/identifier"
            and identifiers
            and all(f in identifiers or f["type"] == "checkbox" for f in after["fields"])
            and self.classify_page(after) == Step.SIGN_IN
            and len(
                [
                    b
                    for b in after.get("controls", [])
                    if not b.get("disabled") and re.fullmatch(r"continue", b["label"], re.I)
                ]
            )
            == 1
        )

    def central_reset_continuation(self):
        handle = getattr(self, "run_record", {}).get("packet", {}).get("email_wait_resume", {})
        if handle.get("kind") != "password_reset" or not handle.get("source_run_id"):
            return False
        from ..db import Run

        with self.service.db.session() as session:
            challenge = session.get(MailChallenge, handle.get("challenge_id", handle.get("id", "")))
            source = session.get(Run, handle["source_run_id"])
            rule = session.get(MailRule, challenge.rule_id) if challenge else None
            return bool(
                challenge
                and source
                and rule
                and challenge.run_id == source.id
                and source.job_id == self.job["id"]
                and challenge.kind == "password_reset"
                and rule.employer_origin == "https://login.icims.com"
            )

    async def _drive(self):
        if self.central_reset_continuation():
            # Transfer through MailService's owner, recipient and generation
            # checks. A resumed reset never clicks Send a second time.
            if not await self.arm_mail("password_reset"):
                return self.lease_busy()
            if result := await self.complete_password_reset("https://login.icims.com/"):
                return result
        return await super()._drive()

    def reset_pending(self):
        """A sender lease prevents overlapping requests; only its owner may claim it."""
        with self.service.db.session() as session:
            return bool(
                session.scalar(
                    select(MailChallenge.id)
                    .join(MailRule, MailRule.id == MailChallenge.rule_id)
                    .where(
                        MailRule.employer_origin == "https://login.icims.com",
                        MailChallenge.recipient == self.accounts.email.lower(),
                        MailChallenge.kind == "password_reset",
                        MailChallenge.state.in_(["pending", "matched"]),
                        MailChallenge.expires > time.time(),
                    )
                )
            )

    @staticmethod
    def central_transaction(url, *, signup=False):
        """Only an observed, HTTPS Central transaction may own these credentials."""
        try:
            location = urlsplit(url)
            trusted = (
                location.scheme == "https"
                and location.hostname == "login.icims.com"
                and location.port in {None, 443}
                and not location.username
                and not location.password
            )
        except ValueError:
            return None
        paths = (
            {"/u/signup", "/u/signup/identifier", "/u/signup/password"}
            if signup
            else {"/u/login/identifier", "/u/login/password"}
        )
        state = parse_qs(location.query).get("state", [])
        return state[0] if trusted and location.path in paths and len(state) == 1 and state[0] else None

    @staticmethod
    def central_unverified(record):
        # Identifier-first login and reset-request acknowledgement prove neither
        # existence nor ownership. Verified/created records remain protected.
        return not (
            record.get("authenticated_at")
            or record.get("identity_exists_at")
            or record.get("state")
            in {
                "authenticated",
                "password_reset",
                "exists",
                "verification_pending",
                "created_locally",
                "locked",
            }
        )

    def central_signup_link(self, obs):
        state = self.central_transaction(self.page.url)
        if not state or self.central_transaction(obs.get("url", "")) != state:
            return None
        links = [
            b
            for b in obs["controls"]
            if not b.get("disabled")
            and re.fullmatch(
                r"sign[ _-]?up|create (?:an? |your )?(?:account|profile)|register|new (?:user|candidate)",
                b["label"],
                re.I,
            )
            and (
                (not b.get("href") and b.get("type") in {"button", "submit"})
                or self.central_transaction(urljoin(self.page.url, b["href"]), signup=True) == state
            )
        ]
        return links[0] if len(links) == 1 else None

    async def try_central_signup(self, obs):
        record = self.accounts.record(self.page.url)
        if not self.central_unverified(record):
            return await super().recover(obs, "The shared Central identity needs account recovery")
        if not self.accounts.may_create or record.get("signup_checked_at"):
            return self.defer(
                "This unverified iCIMS identity cannot start another account creation attempt. "
                "A reset cannot establish an account.",
                state="waiting_browser",
                blocker_type="account",
            )
        if self.reset_pending():
            return self.defer(
                "The shared Central identity has a pending reset; its owner must finish it first",
                state="waiting_browser",
                blocker_type="email_wait",
            )
        fresh = await self.form.scan()
        link = self.central_signup_link(fresh)
        if not link and self.config.account_password_reset:
            # Owner (2026-10-17): "set whatever password you want with iCIMS". Central accepted this email and
            # rejected the configured password, so the identity exists; reset it and set our own password.
            return await super().recover(fresh, "Central offers no signup; resetting the account password")
        if not link:
            return self.defer(
                "The Central account is unverified and no signup link in this transaction was observed. "
                "A password reset is not evidence that an account exists.",
                state="waiting_browser",
                blocker_type="account",
            )
        state = self.central_transaction(self.page.url)
        await self.click(link, auth=True)
        fresh = await self.form.scan()
        if self.central_transaction(self.page.url, signup=True) != state:
            return self.defer(
                "The offered signup link did not retain the Central transaction",
                state="waiting_browser",
                blocker_type="account",
            )
        return await self.on_create_account(fresh)

    async def recover(self, obs, detail):
        if self.central_transaction(self.page.url):
            record = self.accounts.record(self.page.url)
            if self.central_unverified(record):
                return await self.try_central_signup(obs)
        return await super().recover(obs, detail)

    async def on_reset_password(self, obs):
        return await self.request_reset(obs, "The password reset was requested from this page")

    async def request_reset(self, obs, detail):
        if (
            urlsplit(self.page.url).hostname == "login.icims.com"
            and not self.config.account_password_reset
            and self.central_unverified(self.accounts.record(self.page.url))
        ):
            return self.defer(
                "This iCIMS identity has no verified account; find the employer's new-profile route",
                state="waiting_browser",
                blocker_type="account",
            )
        return await super().request_reset(obs, detail)

    @staticmethod
    def central_enrollment(obs):
        """The decline control on Central's optional passkey/MFA enrollment prompt, or None elsewhere. Its buttons are
        type=submit, so the guided agent refused "Remind me later" and "No Thanks" as possible application submits;
        pressing Continue tried to register a passkey and failed (Rovisys 2026-10-19)."""
        location = urlsplit(obs.get("url", ""))
        if (
            location.scheme != "https"
            or location.hostname not in {"login.icims.com", "login.icims.eu"}
            or not CENTRAL_ENROLLMENT.match(location.path)
        ):
            return None
        offered = {
            re.sub(r"[\s,.!]+", " ", b["label"]).strip().casefold(): b
            for b in obs["controls"]
            if not b.get("disabled") and not b.get("href")
        }
        return next((offered[label] for label in ENROLLMENT_DECLINES if label in offered), None)

    async def guided_step(self, obs):
        """Central's passkey prompt while the guided flow drives: its type=submit buttons were refused as application
        submits and iPipeline's run stopped on "Log In Faster on This Device" (2026-10-19)."""
        return bool(self.central_enrollment(obs)) and await self.on_consent(obs) is None

    async def on_consent(self, obs):
        if decline := self.central_enrollment(obs):
            self.attempts["central_enrollment"] += 1
            if self.attempts["central_enrollment"] > 3:
                return self.defer(
                    "Central kept asking to register a passkey", state="waiting_browser", blocker_type="account"
                )
            self.emit("auth", "Declined Central's optional passkey enrollment", {"control": decline["label"]})
            await self.click(decline, auth=True)
            await self.form.settle(timeout=6)
            return None
        return await super().on_consent(obs)

    def central_authenticated(self, obs):
        """Password disappearance alone can be loading or a return to identifier."""
        location, employer = urlsplit(obs.get("url", "")), urlsplit(self.job["url"])
        return bool(
            location.scheme == "https"
            and location.hostname == employer.hostname
            and not re.search(r"/(?:login|signup|sign-in)(?:/|$)", location.path, re.I)
            and not any(f["type"] == "password" for f in obs["fields"])
            and any(re.fullmatch(r"log ?out|sign out", b["label"], re.I) for b in obs["controls"])
            and not any(re.fullmatch(r"log ?in|sign in", b["label"], re.I) for b in obs["controls"])
        )

    async def central_password_step(self, state, *, signup=False, timeout=4):
        """Observe a phase transition without another account action or stale IDs."""
        last = None
        try:
            async with asyncio.timeout(timeout):
                while True:
                    last = await self.form.scan()
                    current = self.central_transaction(last.get("url", ""), signup=signup)
                    if (
                        current != state
                        or last.get("errors")
                        or any(f["type"] == "password" for f in last["fields"])
                    ):
                        return last
                    await asyncio.sleep(0.15)
        except TimeoutError:
            return last

    async def employer_flagged(self):
        """Mark the inputs the employer drew as refused required, so the next pass answers them or asks a precise
        question instead of sending them blank again. Returns their labels."""
        flagged = []
        for frame in self.page.frames:
            try:
                flagged += await frame.evaluate(FLAGGED_JS)
            except Exception:
                continue
        return flagged

    async def describe_refusal(self, refused, flagged):
        """One masked screenshot at the first refused field, read by the configured vision model, for the report."""
        from ..vision import available, inspect_masked

        if not available(self.config):
            return ""
        try:
            await self.page.evaluate(
                "() => document.querySelector('[data-jp-employer-flagged]')?.scrollIntoView({block: 'center'})"
            )
            masked = await self.form.masked_vision_observation()
            hint = await inspect_masked(
                self.config,
                self.service.db,
                masked["path"],
                self.e.run_id,
                instructions=f"The employer refused this profile with: {refused[:160]!r}. Name each field shown with a "
                "validation error (red label, red border or error icon), its section, and what it appears to need.",
            )
        except Exception:
            return ""
        if not hint:
            return ""
        self.emit("vision_observation", "Visual specialist read the refused profile", hint.model_dump())
        return hint.summary[:400]

    async def on_create_account(self, obs):
        if await self.leave_saved_connect_profile(obs):
            return None
        if await self.returning_candidate_login(obs):
            return None
        refused = next((str(e) for e in obs.get("errors", []) if INVALID_DATA.search(str(e))), "")
        if refused and urlsplit(self.page.url).hostname not in {"login.icims.com", "login.icims.eu"}:
            flagged = await self.employer_flagged()
            self.emit("employer_refused", "The employer refused the profile", {"error": refused[:200], "flagged": flagged[:20]})
            if self.attempts["create"] >= 2:
                # A third Submit Profile would only repeat the refusal; it is not a failed sign-in.
                seen = await self.describe_refusal(refused, flagged)
                return self.defer(
                    f"The employer refused the profile ({refused[:120]})"
                    + (f"; flagged: {', '.join(flagged[:6])}" if flagged else "")
                    + (f". {seen}" if seen else ""),
                    state="waiting_browser",
                    blocker_type="validation",
                )
        if self.connect_identifier(obs):
            if not self.accounts.ready or not self.accounts.may_create or self.attempts["connect_identifier"]:
                return self.defer(
                    "Candidate signup did not advance", state="waiting_browser", blocker_type="account"
                )
            self.attempts["connect_identifier"] += 1
            field = obs["fields"][0]
            await self.form.locator(field["id"]).fill(self.accounts.email, timeout=4000)
            signup = next(
                b
                for b in obs["controls"]
                if not b.get("disabled")
                and not b.get("href")
                and re.fullmatch(r"sign[ -]?up", b["label"], re.I)
            )
            await self.click(signup, auth=True)
            return await self.after_auth_click()
        # Central is special: a rejected identifier-first password is not a
        # known account, and the generic known-account switch would undo signup.
        if urlsplit(self.page.url).hostname != "login.icims.com":
            result = await super().on_create_account(obs)
            # A linked Connect registration prepares the candidate account.
            # Its editable password fields remain after successful creation.
            # Return to this owner's job, never treat the profile as a receipt.
            if result is None:
                await self.leave_saved_connect_profile(await self.form.scan())
            return result
        state = self.central_transaction(self.page.url, signup=True)
        if not state or not self.accounts.may_create:
            return self.defer(
                "Central signup is not authorized in an observed transaction",
                state="waiting_browser",
                blocker_type="account",
            )
        if self.reset_pending():
            return self.defer(
                "Central signup is waiting for the existing reset owner",
                state="waiting_browser",
                blocker_type="email_wait",
            )
        url = self.page.url
        credentials = self.accounts.icims_credentials(url) or self.accounts.store.shared()
        # Reserve once per origin + recipient, across employers and concurrent
        # workers. Keep an existing encrypted credential and state unchanged.
        with self.service.db.exclusive() as session:
            row = session.get(Setting, self.accounts.store.key(url))
            record = row.value if row else {}
            if record.get("signup_checked_at") or not self.central_unverified(record):
                return self.defer(
                    "Central signup was already checked or the identity is verified",
                    state="waiting_browser",
                    blocker_type="account",
                )
            value = {**record, "signup_checked_at": now()}
            if row:
                row.value = value
            else:
                session.add(
                    Setting(
                        key=self.accounts.store.key(url),
                        value={
                            **value,
                            "origin": "https://login.icims.com",
                            "tenant": account_tenant(url, self.job["url"]),
                            "state": "signup_checked",
                            "credentials": self.service.mail.vault.seal(credentials),
                        },
                    )
                )
        # Auth0 may split identifier and password signup. Follow only the
        # offered Continue/Sign up action, once on each of at most two screens.
        for phase in range(2):
            if (
                self.central_transaction(self.page.url, signup=True) != state
                or self.central_transaction(obs.get("url", ""), signup=True) != state
            ):
                return self.defer(
                    "Central signup left its observed transaction",
                    state="waiting_browser",
                    blocker_type="account",
                )
            passwords = [f for f in obs["fields"] if f["type"] == "password"]
            identifiers = [f for f in obs["fields"] if self.is_login_field(f)]
            if any(f.get("required") and f not in passwords + identifiers for f in obs["fields"]):
                return self.defer(
                    "Central signup needs a confirmed profile or consent answer",
                    state="waiting_browser",
                    blocker_type="account",
                )
            for field in passwords:
                if self.central_transaction(self.form.frames[field["id"]].url, signup=True) != state:
                    return self.defer(
                        "Central password field is on an unexpected website or transaction",
                        state="waiting_browser",
                        blocker_type="account",
                    )
            buttons = [
                b
                for b in obs["controls"]
                if not b.get("href")
                and not b.get("disabled")
                and re.fullmatch(r"sign[ _-]?up|create (?:an? )?account|continue", b["label"], re.I)
            ]
            if len(buttons) != 1 or not (passwords or identifiers) or (phase and not passwords):
                return self.defer(
                    "Central signup has no unambiguous offered account action",
                    state="waiting_browser",
                    blocker_type="account",
                )
            for field in identifiers:
                await self.form.locator(field["id"]).fill(credentials["email"], timeout=4000)
            for field in passwords:
                await self.form.locator(field["id"]).fill(credentials["password"], timeout=4000)
                await self.form.locator(field["id"]).blur()
            if passwords and not await self.arm_mail():
                return self.lease_busy()
            await self.click(buttons[0], auth=True)
            if challenge := await self.after_auth_click():
                return challenge
            if not passwords:
                obs = await self.central_password_step(state, signup=True)
                if not obs:
                    return self.defer(
                        "Central signup's next phase could not be observed",
                        state="waiting_browser",
                        blocker_type="observation_failure",
                    )
                continue
            outcome, _ = await self.auth_outcome()
            self.emit("auth", "Central signup: " + outcome)
            if outcome == "ok" and not self.central_authenticated(await self.form.scan()):
                self.release_mail()
                return self.defer(
                    "Central signup has no positive authenticated employer-session evidence",
                    state="waiting_browser",
                    blocker_type="account",
                )
            if outcome in {"ok", "verify"}:
                self.accounts.mark(
                    url,
                    "authenticated" if outcome == "ok" else "verification_pending",
                    credentials,
                    **({"authenticated_at": now()} if outcome == "ok" else {}),
                )
                if outcome == "ok":
                    self.release_mail()
                return None
            self.release_mail()
            if outcome == "exists":
                # Do not replace an existing password merely because signup
                # rejected it. The accepted-reset path owns that replacement.
                with self.service.db.exclusive() as session:
                    row = session.get(Setting, self.accounts.store.key(url))
                    row.value = {**row.value, "state": "exists", "signup_outcome": "exists"}
                return await super().recover(
                    await self.form.scan(), "Central signup reports an existing account"
                )
            return self.defer(
                "Central signup did not establish an account; outcome: " + outcome,
                state="waiting_browser",
                blocker_type="account",
            )
        return self.defer(
            "Central signup did not reach an offered password form",
            state="waiting_browser",
            blocker_type="account",
        )

    def employer_session(self, obs):
        location, employer = urlsplit(obs.get("url", "")), urlsplit(self.job["url"])
        profile_frames = {
            f["frame_url"] for f in obs["fields"]
            if f.get("frame_url") and (f.get("icims_name") or "").startswith("PersonProfileFields.")
        }
        if not profile_frames:
            profile_frames = {
                b["frame_url"] for b in obs["controls"]
                if b.get("frame_url") and urlsplit(b["frame_url"]).netloc == employer.netloc
                and re.fullmatch(r"log ?out|sign out", b["label"], re.I)
            }
        controls = [b for b in obs["controls"] if not profile_frames or b.get("frame_url") in profile_frames]
        return bool(
            location.scheme == employer.scheme == "https"
            and location.netloc == employer.netloc
            and not obs.get("errors")
            and any(re.fullmatch(r"log ?out|sign out", b["label"], re.I) for b in controls)
            and not any(re.fullmatch(r"log ?in[.!]?|sign in", b["label"], re.I) for b in controls)
        )

    def employer_profile_editor(self, obs):
        return bool(
            self.employer_session(obs)
            and any(
                not b.get("href")
                and re.fullmatch(r"(?:update|save)(?: (?:my|your))? profile", b["label"], re.I)
                for b in obs["controls"]
            )
            and not any(PROFILE_CREATE.fullmatch(b["label"]) for b in obs["controls"])
        )

    async def returning_candidate_login(self, obs):
        """The employer already holds this email (and phone): sign in through iCIMS's returning-candidate login instead of
        registering again. Eide Bailly's email step answered "The email address and phone number are already in use.
        Please login ..." to every post, and returning to the posting led back to the same registration (2026-10-19)."""
        said = " ".join([str(e) for e in obs.get("errors", [])] + [str(obs.get("text", ""))[:4000]])
        if not EMAIL_IN_USE.search(said) or self.attempts["returning_login"]:
            return False
        self.attempts["returning_login"] += 1
        self.accounts.mark(self.job["url"], "exists")
        job = urlsplit(self.job["url"])
        self.emit("account", "The employer already holds this email; signing in through its returning-candidate login")
        await self.page.goto(
            f"{job.scheme}://{job.netloc}/jobs/login?loginOnly=1&in_iframe=1", wait_until="domcontentloaded", timeout=30000
        )
        await self.form.settle(timeout=8)
        return True

    async def email_agreement_answer(self, field):
        # VTG's eu_resident is an assertion of residence, not consent to read a notice. REA (2026-10-21)
        # reached hCaptcha with it unchecked. The displayed region varies while the protocol name stays fixed.
        name = field.get("icims_name") or field.get("name")
        label = field.get("label", "").lower()
        if name == "eu_resident" and field["type"] == "checkbox" and "california" in label:
            profile = self.form.resolver.profile
            country = str(profile.facts.get("country_of_residence") or profile.address.country or "").strip().lower()
            state = profile.address.state.strip().lower()
            us = country in {"us", "usa", "united states", "united states of america"}
            uk = country in {"uk", "gb", "gbr", "united kingdom", "great britain"}
            if not country or (us and not state):
                return Answer(field_id=field["id"], disposition="review", blocker_type="missing_fact",
                              reason="The residence checkbox requires a known country and state")
            resident = (us and state in {"ca", "california"}) or (uk and bool(re.search(r"united kingdom|\buk\b", label)))
            return Answer(field_id=field["id"], value="true" if resident else "false", disposition="answer",
                          evidence_ids=["profile:address", "fact:country_of_residence"] if profile.facts.get("country_of_residence")
                          else ["profile:address"], reason="Residence from the applicant's saved address")
        return await super().email_agreement_answer(field)

    async def on_email_entry(self, obs):
        if await self.returning_candidate_login(obs):
            return None
        # Schwab validates these before executing hCaptcha, even without native required attributes.
        # The country picker owns countryCodeSelect; its visible protocol id is dropdown.
        if any(f.get("icims_name") == "css_loginName" for f in obs["fields"]):
            phone_fields = [
                {**f, "label": label}
                for name, label in (("countryCodeSelect", "Phone Country Code"), ("css_phoneNumber", "Phone Number"))
                for f in obs["fields"]
                if f.get("icims_name") == name or (name == "countryCodeSelect" and f.get("html_id") == "dropdown")
            ]
            if phone_fields:
                answers = await self.form.resolver.resolve(phone_fields)
                for field, answer in zip(phone_fields, answers):
                    if answer.disposition != "answer":
                        return self.defer("The iCIMS email step requires the applicant's phone country and number",
                                          [self.review_for(field)], blocker_type="missing_fact")
                    await self.form.fill(field, answer.value)
        return await super().on_email_entry(obs)

    def employer_holds_resume(self, obs):
        """iCIMS keeps the resume posted with an accepted candidate profile, and the later steps of that application
        carry no upload of their own. VTG (2026-10-21) resumed at its last step in a new run, and the final submit was
        refused for want of a resume attachment although run 153ca3d8 had posted the profile with it (302).

        True on a step after the profile when an earlier run of this job posted the profile with a stored resume
        (Resume_TEMP or an attached file) and iCIMS accepted it."""
        paths = {urlsplit(obs.get("url", "")).path} | {urlsplit(f.get("frame_url") or "").path for f in obs["fields"]}
        if not any(PROTOCOL_STEP.search(path) for path in paths) or self.form.resume_fields():
            return False
        with self.service.db.session() as session:
            posts = session.scalars(
                select(Event.data)
                .join(Run, Run.id == Event.run_id)
                .where(Run.job_id == self.job["id"], Event.kind == "form_post")
            )
            for data in posts:
                shape = {str(name): str(size) for name, size in (data or {}).get("shape") or []}
                stored = shape.get("PortalProfileFields.Resume_TEMP", "0") not in {"", "0"} or re.fullmatch(
                    r"file:[^:]*:[1-9]\d*", shape.get("PortalProfileFields.Resume_File", "")
                )
                if (
                    str((data or {}).get("path", "")).endswith("/candidate")
                    and (data or {}).get("status") == 302
                    and stored
                ):
                    return True
        return False

    def adopt_employer_resume(self, obs):
        if getattr(self.form, "upload_verified", True) or not hasattr(self.form, "resume_fields"):
            return  # Already attached in this run, or a form without upload tracking.
        if self.employer_holds_resume(obs):
            self.form.uploaded = self.form.upload_verified = self.form.resume_on_file = True
            self.emit("resume_on_file", "The employer holds the resume from this application's accepted profile")

    async def on_form(self, obs):
        self.adopt_employer_resume(obs)
        if self.employer_profile_editor(obs):
            self.accounts.mark(obs["url"], "authenticated")
        if await self.returning_candidate_login(obs):
            return None
        if repeated := await self.email_step_repeat(obs):
            return repeated
        await self.record_unlabeled(obs)
        return await super().on_form(obs)

    async def email_step_repeat(self, obs):
        """The email step returned unchanged after its own post (Eide Bailly 2026-10-19, an existing account: Email,
        Country Code and Phone, posted with a trusted token, came back as the same form four times). Record what the
        employer's frame says, masked, and stop instead of posting again."""
        if not (
            urlsplit(obs.get("url", "")).path.endswith("/login")
            and any(f.get("name") == "css_loginName" for f in obs["fields"])
        ):
            return None
        self.attempts["email_step_seen"] += 1
        if self.attempts["email_step_seen"] < 3:
            return None
        said = []
        for frame in self.page.frames:
            try:
                text = await frame.locator("body").inner_text(timeout=2000)
            except Exception:
                continue
            if "Enter Your Information" in text or "css_loginName" in (await frame.content())[:200000]:
                text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", text)
                said.append(re.sub(r"\d[\d\s().-]{5,}\d", "[number]", re.sub(r"\s+", " ", text)).strip()[:700])
        dialog = getattr(self.e, "last_dialog", None)
        self.emit("email_step_repeat", "The employer returned the email step unchanged after it was posted",
                  {"frame_text": said[:2], "dialog": dialog})
        return self.defer(
            "The employer returned its email step unchanged after it was posted"
            + (f": {said[0][:300]}" if said else ""),
            state="waiting_browser",
            blocker_type="account",
        )

    async def record_unlabeled(self, obs):
        """Once per run: the markup around fields whose question or choices read only as their field name."""
        names = [
            f["name"]
            for f in obs["fields"]
            if f.get("name") and (f.get("label") == f["name"] or f["name"] in (f.get("options") or []))
        ]
        if not names or getattr(self, "unlabeled_recorded", False):
            return
        self.unlabeled_recorded = True
        markup = []
        for frame in self.page.frames:
            try:
                markup += await frame.evaluate(UNLABELED_JS, names[:3])
            except Exception:
                continue
        if markup:
            self.emit("unlabeled_markup", "Recorded the markup of fields without labels", {"names": names[:3], "markup": markup[:6]})

    def connect_identifier(self, obs):
        target, employer = urlsplit(obs.get("url", "")), urlsplit(self.job["url"])
        return bool(
            target.scheme == employer.scheme == "https"
            and target.netloc == employer.netloc
            and target.path.rstrip("/") == "/connect"
            and len(obs.get("fields", [])) == 1
            and self.is_login_field(obs["fields"][0])
            and obs["fields"][0]["type"] == "email"
            and sum(
                not b.get("disabled")
                and not b.get("href")
                and bool(re.fullmatch(r"sign[ -]?up", b["label"], re.I))
                for b in obs["controls"]
            )
            == 1
        )

    async def leave_saved_connect_profile(self, obs):
        # A signed-in Connect editor is account preparation, not the owned
        # requisition. Password editors do not make its session unauthenticated.
        if urlsplit(obs.get("url", "")).path.startswith("/connect/") and self.employer_profile_editor(obs):
            self.accounts.mark(obs["url"], "authenticated")
            await self.e.return_to_job()
            return True
        return False

    async def auth_outcome(self, timeout=12):
        obs = await self.form.scan()
        if self.employer_session(obs):
            return "ok", ""
        return await super().auth_outcome(timeout=timeout)

    async def retry_email_step(self, obs):
        """A twin-minted token is sometimes still scored low, and iCIMS answers the email step with its returning-
        candidate Central login (1 of 6 on 2026-10-19). Central cannot create accounts, so for an employer with no
        known account the email step is retried from the job page once per run, with a fresh token."""
        captcha = getattr(self.e, "captcha", None)
        delivered = getattr(captcha, "twin_delivered_at", None)
        store = getattr(self.accounts, "store", None)
        url = self.auth_url(obs)
        if not (
            delivered is not None
            and time.monotonic() - delivered < 180
            and self.central_transaction(url)
            and urlsplit(url).path == "/u/login/identifier"
            and not (store and store.known(self.job["url"]))
            and not self.attempts["twin_email_retry"]
        ):
            return False
        self.attempts["twin_email_retry"] += 1
        self.emit("account", "iCIMS sent the email step to its returning-candidate login; retrying the email step once")
        # The job page is a new document: its widget must be solved again, not remembered as delivered.
        captcha.twin_tried.clear()
        captcha.token_delivered_at.clear()
        captcha.twin_delivered_at = None
        await self.page.goto(self.prepare_url(self.job["url"]), wait_until="domcontentloaded", timeout=25000)
        await self.form.settle(timeout=6)
        return True

    async def on_sign_in(self, obs):
        if await self.leave_saved_connect_profile(obs):
            return None
        if await self.retry_email_step(obs):
            return None
        url = self.auth_url(obs)
        transaction = self.central_transaction(url)
        if (
            transaction
            and self.central_unverified(self.accounts.record(url))
            and not self.accounts.icims_credentials(url)
            and (urlsplit(url).path != "/u/login/identifier" or self.central_signup_link(obs))
        ):
            # Prefer an offered signup. The identifier-only phase routes an
            # email before exposing the next account screen; it neither checks
            # a password nor establishes account existence. Allow it once,
            # then inspect signup before any returning-user password/reset.
            return await self.try_central_signup(obs)
        if transaction and urlsplit(url).path == "/u/login/identifier":
            # Identifier -> password is progress, not a failed password attempt.
            # The generic auth loop waited for the password screen to vanish,
            # exhausted its sign-in budget, then incorrectly tried signup.
            identifiers = [f for f in obs["fields"] if self.is_login_field(f)]
            buttons = [
                b
                for b in obs["controls"]
                if not b.get("disabled")
                and not b.get("href")
                and re.fullmatch(r"continue|next|log ?in|sign in", b["label"], re.I)
            ]
            if len(identifiers) != 1 or len(buttons) != 1 or self.attempts["central_identifier"]:
                return self.defer(
                    "Central identifier step did not reach its password screen",
                    state="waiting_browser",
                    blocker_type="account",
                )
            field = identifiers[0]
            if self.central_transaction(self.form.frames[field["id"]].url) != transaction:
                return self.defer(
                    "Central identifier is outside the owned transaction",
                    state="waiting_browser",
                    blocker_type="account",
                )
            self.attempts["central_identifier"] += 1
            await self.form.locator(field["id"]).fill(self.accounts.email, timeout=4000)
            captcha = getattr(self.e, "captcha", None)
            verified_before = bool(captcha and await captcha.token_present(self.page))
            acceptance_before = getattr(captcha, "acceptance_attempt", None)
            await self.click(buttons[0], auth=True)
            if challenge := await self.after_auth_click():
                return challenge
            if (
                captcha
                and not getattr(self, "central_auth_continued", False)
                and (
                    not verified_before
                    or bool(getattr(captcha, "acceptance_attempt", None))
                    and captcha.acceptance_attempt != acceptance_before
                )
                and await captcha.token_present(self.page)
                and self.central_transaction(self.page.url) == transaction
                and urlsplit(self.page.url).path == "/u/login/identifier"
            ):
                # Central's first Continue opens CAPTCHA without submitting
                # the identifier. Native acceptance enables Continue; it does
                # not dispatch that employer control for us.
                verified = await self.form.scan()
                continuations = [
                    b
                    for b in verified["controls"]
                    if not b.get("disabled")
                    and not b.get("href")
                    and re.fullmatch(r"continue|next|log ?in|sign in", b["label"], re.I)
                ]
                if (
                    self.central_transaction(verified.get("url", "")) == transaction
                    and not verified.get("errors")
                    and len(continuations) == 1
                    and any(self.is_login_field(f) and f.get("value") for f in verified["fields"])
                    and not any(f["type"] == "password" for f in verified["fields"])
                ):
                    await self.click(continuations[0], auth=True)
                    self.emit(
                        "auth", "Continued the owned Central identifier after native CAPTCHA acceptance"
                    )
                    if challenge := await self.after_auth_click():
                        return challenge
            fresh = await self.central_password_step(transaction)
            if not fresh:
                return self.defer(
                    "Central identifier's next phase could not be observed",
                    state="waiting_browser",
                    blocker_type="observation_failure",
                )
            if self.central_transaction(fresh.get("url", ""), signup=True) == transaction:
                return await self.on_create_account(fresh)
            if (
                self.central_transaction(fresh.get("url", "")) == transaction
                and urlsplit(fresh["url"]).path == "/u/login/password"
                and any(f["type"] == "password" for f in fresh["fields"])
                and not fresh.get("errors")
            ):
                self.emit("auth", "Central identifier accepted; password step reached")
                return None
            if self.central_authenticated(fresh):
                self.accounts.mark(url, "authenticated", authenticated_at=now())
                return None
            return self.defer(
                "Central identifier was not accepted or its next step was not observed",
                state="waiting_browser",
                blocker_type="account",
            )
        record = self.accounts.record(url)
        if record.get("state") in {
            "authenticated",
            "password_reset",
            "verification_pending",
            "created_locally",
            "exists",
        }:
            # Preserve existence evidence before a rejected sign-in changes state.
            self.accounts.mark(
                url, record["state"], self.accounts.saved(url), identity_exists_at=record.get("identity_exists_at") or now()
            )
            record = self.accounts.record(url)
        # A reset request is not proof that the currently configured credential
        # was rejected. Old unverified state used to suppress every employer.
        try:
            recent_rejection = (
                datetime.now(UTC) - datetime.fromisoformat(record.get("rejected_at", ""))
            ).total_seconds() < 3600
        except (ValueError, TypeError):
            recent_rejection = False
        if (
            urlsplit(url).hostname == "login.icims.com"
            and record.get("state") in {"reset_requested", "credentials_rejected"}
            and recent_rejection
            and record.get("rejected_credentials") == self.accounts.credential_fingerprint(url)
            and not self.accounts.alternative_credentials(url)
        ):
            # The same employer cannot make its rejected password valid by
            # restarting. Other employers have independent account records.
            if (
                self.central_transaction(url)
                and self.central_unverified(record)
                and not record.get("signup_checked_at")
            ):
                return await self.try_central_signup(obs)
            pending = self.reset_pending()
            if not pending and self.config.account_password_reset:
                # The rejected password cannot become valid by restarting; reset it to our own (owner-authorized).
                return await super().recover(obs, "iCIMS rejected the saved password; resetting it")
            return self.defer(
                "Another application holds the iCIMS password-reset sender lease. "
                "This job will not send a competing reset request."
                if pending
                else "iCIMS rejected the saved account password. No live reset request remains; "
                "the generated shared password requires a verified account reset before reuse.",
                state="waiting_browser",
                blocker_type="email_wait" if pending else "account",
            )
        return await super().on_sign_in(obs)
