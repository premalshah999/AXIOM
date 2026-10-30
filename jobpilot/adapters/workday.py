"""Workday candidate experience (myworkdayjobs.com / myworkdaysite.com).

Contracts used: data-automation-id attributes (adventureButton, applyManually, useMyLastApplication,
autofillWithResume, email/password/verifyPassword, createAccountSubmitButton, signInSubmitButton,
click_filter overlays, bottom-navigation-next-button, progressBar, multiselectInputContainer,
dateInputWrapper, file-upload-input-ref), plus visible headings as a fallback."""

import re

from playwright.async_api import Error as PlaywrightError

from .base import CHECK_EMAIL, CONFIRM_TEXT, Adapter, Step

STEP_PAGES = {
    "applyFlowMyInfoPage": "my information",
    "applyFlowMyExpPage": "my experience",
    "applyFlowPrimaryQuestionsPage": "application questions",
    "applyFlowSecondaryQuestionsPage": "application questions",
    "applyFlowVoluntaryDisclosuresPage": "voluntary disclosures",
    "applyFlowSelfIdentifyPage": "self identify",
    "applyFlowReviewPage": "review",
}
# Review lists every step's answers; an unattached resume reads "Resume/CV/... No Response".
REVIEW_NO_RESUME = re.compile(r"\bresume\b[\w/ ,&()-]{0,70}?\s+no response", re.I)
BACK_IDS = ("pageFooterBackButton", "bottom-navigation-back-button", "wd-CommandButton_back")
ADD = {
    "work": re.compile(r"^add(?: another)? work experience$|^add(?: another)? experience$", re.I),
    "education": re.compile(r"^add(?: another)? education$", re.I),
}


class Workday(Adapter):
    id = "workday"
    name = "Workday"
    link_resume = False  # its own continuation of a parked mail wait
    max_steps = 50
    back_ids = BACK_IDS
    next_ids = ("bottom-navigation-next-button", "pageFooterNextButton", "wd-CommandButton_next")
    apply_ids = ("adventureButton", "applyButton", "jobPostingApplyButton")

    @staticmethod
    def prepare_url(url):
        # /apply and /apply/applyManually resume the flow; the posting itself is the stable entry.
        return re.sub(r"/apply(?:/[^/?#]*)?(?=$|[?#])", "", url)

    def page_name(self, obs):
        auto = set(obs["automation"])
        for key, name in STEP_PAGES.items():
            if key in auto:
                return name
        heads = " ".join(obs["headings"][:6]).lower()
        for name in (
            "my information",
            "my experience",
            "application questions",
            "voluntary disclosures",
            "self identify",
            "review",
        ):
            if name in heads:
                return name
        return ""

    def classify_page(self, obs):
        if self.in_assessment():
            return Step.FORM  # Its Submit and thank-you page belong to the questionnaire, never the application.
        auto = set(obs["automation"])
        text = obs["text"]
        if self.committed and CONFIRM_TEXT.search(text):
            return Step.CONFIRMATION
        if {"applyManually", "autofillWithResume", "useMyLastApplication"} & auto:
            return Step.METHOD
        if "verifyPassword" in auto or "createAccountSubmitButton" in auto:
            return Step.CREATE_ACCOUNT
        if "signInSubmitButton" in auto or ("password" in auto and "email" in auto):
            return Step.SIGN_IN
        code = self.code_fields(obs)
        if code:
            return Step.EMAIL_CODE
        in_flow = bool({"progressBar", "bottom-navigation-next-button", "pageFooterNextButton"} & auto)
        if in_flow:
            if self.page_name(obs) == "review":
                return Step.REVIEW
            return Step.FORM
        if CHECK_EMAIL.search(text) and not obs["fields"]:
            return Step.VERIFY_LINK
        if set(self.apply_ids) & auto and not self.committed:
            return Step.JOB
        return super().classify_page(obs)

    async def on_apply_method(self, obs):
        by_id = {b["automation_id"]: b for b in obs["controls"] if b["automation_id"]}
        profile = self.e.profile
        account = self.accounts.record(self.page.url)
        if "useMyLastApplication" in by_id and account.get("state") in {"authenticated", "password_reset"}:
            choice = by_id["useMyLastApplication"]
        elif "applyManually" in by_id and (profile.work or "autofillWithResume" not in by_id):
            choice = by_id["applyManually"]
        elif "autofillWithResume" in by_id:
            # No structured history in the profile: let Workday parse the selected resume.
            choice = by_id["autofillWithResume"]
        else:
            choice = by_id.get("applyManually") or next(iter(by_id.values()))
        self.emit("apply_method", f"Workday: {choice['label'] or choice['automation_id']}")
        await self.click(choice)
        self.apply_url = self.page.url

    async def on_sign_in(self, obs):
        return await self.workday_account()

    async def on_create_account(self, obs):
        return await self.workday_account()

    async def workday_account(self):
        """One Workday account implementation (WorkdayAuth) for both engines."""
        from ..workday import WorkdayAuth

        auth = getattr(self.e, "workday_auth", None) or WorkdayAuth(self.e)
        self.e.workday_auth = auth
        self.attempts["workday_auth"] += 1
        if self.attempts["workday_auth"] > 2:
            return self.defer("Workday kept returning to account access")
        try:
            try:
                ok = await auth.run()
            except PlaywrightError:
                import sys
                import traceback

                frames = traceback.extract_tb(sys.exc_info()[2])
                own = [f for f in frames if "/jobpilot/" in f.filename]
                self.emit(
                    "account_error",
                    "Workday account control failed",
                    {"function": own[-1].name if own else "", "line": own[-1].lineno if own else 0},
                )
                raise ValueError("Workday account step did not respond as expected") from None
        except ValueError as exc:
            state = "waiting_browser" if self.config.browser_cdp_url else "needs_review"
            return {
                "state": state,
                "reason": str(exc)[:400],
                "reviews": [],
                "blocker_type": "account",
                "recovery_cause": "account_acceptance",
            }
        if self.e.result:
            return self.e.result
        if not ok:
            return self.defer(
                "Workday requires an account. Enable “Create and reuse employer accounts” in your profile."
            )
        self.e.workday_session_ok = True
        return None

    async def on_form(self, obs):
        auth = getattr(self.e, "workday_auth", None)
        if auth:
            try:
                if await auth.recover_error_page():
                    return None
            except ValueError as exc:
                return {"state": "waiting_browser", "reason": str(exc), "recovery_cause": "site_temporary"}
        if self.in_assessment():
            return await self.on_assessment()
        if await self.launch_assessment(obs):
            return None
        if self.page_name(obs) == "my experience":
            await self.add_rows(obs)
        return await super().on_form(obs)

    async def on_review(self, obs, report=None):
        """A resumed draft can reach Review without the resume (Jabil: "Resume/CV ... No Response").

        Review has no upload control, so go back to My Experience once; the normal loop
        attaches the resume there and walks forward to Review again.
        """
        if (
            not self.committed
            and not self.form.upload_verified
            and self.attempts["review_resume"] < 1
            # The scan keeps only the head and tail of a long Review page; read the whole page.
            and REVIEW_NO_RESUME.search(obs.get("text", "") + " " + await self.form._page_text())
        ):
            self.attempts["review_resume"] += 1
            self.emit(
                "resume_repair", "Workday review lists no resume; returning to My Experience to attach it"
            )
            if await self.back_to("my experience"):
                return None
        return await super().on_review(obs, report)

    async def back_to(self, name, limit=6):
        """Click the application's own Back control until the named step is showing."""
        for _ in range(limit):
            obs = await self.form.scan()
            if self.page_name(obs) == name:
                return True
            back = next((b for b in obs["controls"] if b["automation_id"] in BACK_IDS), None) or next(
                (b for b in obs["controls"] if re.fullmatch(r"back", b["label"] or "", re.I)), None
            )
            if not back:
                return False
            await self.click(back)
            await self.form.settle(timeout=4)
        return self.page_name(await self.form.scan()) == name

    async def add_rows(self, obs):
        """Open one Work Experience / Education panel per profile entry, keeping existing rows."""
        profile = self.e.profile
        for kind, entries in (("work", profile.work), ("education", profile.education)):
            if not entries:
                continue
            label = "work experience" if kind == "work" else "education"
            # A row deleted because the employer cannot list its answer is not added again.
            wanted = len(entries) - getattr(self.form, "dropped_rows", {}).get(kind, 0)
            for _ in range(len(entries)):
                obs = await self.form.scan()
                groups = {
                    f["group"].lower()
                    for f in obs["fields"]
                    if re.match(label + r" \d+$", (f["group"] or "").lower())
                }
                if len(groups) >= wanted:
                    break
                add = [b for b in obs["controls"] if ADD[kind].search(b["aria_label"] or b["label"])]
                if not add:
                    add = [
                        b
                        for b in obs["controls"]
                        if re.fullmatch(r"add(?: another)?", b["label"], re.I)
                        and label in (b["aria_label"] or "").lower()
                    ]
                if not add:
                    # Some tenants put a plain Add button under the section
                    # heading and omit the section name from aria-label.
                    section = self.form.page.get_by_role(
                        "heading", name=re.compile(r"^" + label + r"$", re.I)
                    )
                    if await section.count() == 1:
                        box = section.locator(
                            'xpath=ancestor::*[.//button[normalize-space()="Add" or normalize-space()="Add Another"]][1]'
                        )
                        button = box.get_by_role("button", name=re.compile(r"^Add(?: Another)?$", re.I))
                        if await button.count() == 1:
                            await button.click(timeout=3000)
                            await self.form.settle(timeout=2)
                            continue
                    break
                await self.form.click(add[0]["id"], step_control=True)
