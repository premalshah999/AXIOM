"""Oracle Recruiting Cloud candidate experience (*.oraclecloud.com/hcmUI/CandidateExperience).

Flow: job page "Apply Now" -> email + terms -> optional emailed PIN for returning candidates ->
one or more apply-flow sections (cx-select-pills, oj-select comboboxes, e-signature) -> Submit."""

import re
from .. import widgets

from .base import CODE_TEXT, Adapter, Step, confirmation_wording

TERMS = re.compile(r"terms|privacy|agree|consent", re.I)
# "Too Many Attempts. Try Again Later." / "You reached the maximum number of attempts. Try again in 30 minutes."
# Fortinet's tenant locked verification after the 10-17 retry storm, and the navigator waited on the notice (10-18).
LOCKED = re.compile(r"too many attempts|reached the maximum number of attempts", re.I)
LOCK_SECONDS = 35 * 60
# Tenants with ORA_IRC_KEEP_CANDIDATE_SIGNED_IN_ENABLED (GM Financial, Goldman Sachs, SAIC, TriNet, Pearson, UTHealth
# Houston on 2026-10-18) offer "Keep me signed in" on the verification step. The worker's Chrome keeps that session,
# so later applications at the employer skip the emailed code; Goldman waited 179-224 s per code that day.
KEEP_SIGNED_IN = re.compile(r"^\s*keep me signed in\b", re.I)


class Oracle(Adapter):
    id = "oracle"
    name = "Oracle Recruiting"
    next_ids = ("apply-flow-next", "next-button")

    async def on_form(self, obs):
        saved_resume = self.page.locator(".attachment-upload-button__filled").filter(
            has=self.page.get_by_role("button", name=re.compile(r"^Remove Resume$", re.I))
        )
        if await saved_resume.count() == 1:
            filename = saved_resume.get_by_role("button", name=self.form.resume_path.name, exact=True)
            if await filename.count() == 1:
                self.form.uploaded = self.form.upload_verified = True
                self.form.resume_seen = True
        # Resume-parsed history is displayed as collapsed cards; its mandatory fields
        # are not in the DOM observation until Edit opens that card.
        save = self.page.get_by_role("button", name=re.compile(r"^Save$", re.I))
        if await save.count() == 1 and await save.is_visible():
            report = await self.fill_page()
            if report.get("state"):
                return report
            await widgets.safe_click(save, timeout=4000)
            await self.form.settle(timeout=5)
            return None
        cards = self.page.locator('[data-profile-item-tile="true"][data-profile-item-invalid="true"]:visible')
        if await cards.count():
            self.attempts["history_repair"] += 1
            if self.attempts["history_repair"] > 12:
                return self.defer(
                    "Oracle history cards still contain invalid fields", state="waiting_browser"
                )
            card = cards.first
            edit = card.get_by_role("button", name="Edit", exact=True)
            if await edit.count() == 1 and await edit.is_visible():
                await edit.press("Enter", timeout=4000)
            elif await card.evaluate("""e => e.tabIndex >= 0 &&
                ['group','button'].includes(e.getAttribute('role')) &&
                /a11y\\.keysHandler/.test(e.getAttribute('data-bind')||'') &&
                /keys:\\s*['"]enter['"]/.test(e.getAttribute('data-bind')||'')"""):
                # Enter focuses/selects this tile and reveals its edit actions.
                # The initial collapsed tile contains no Edit button.
                await card.press("Enter", timeout=4000)
                await edit.wait_for(state="visible", timeout=4000)
                await edit.press("Enter", timeout=4000)
            else:
                return {
                    "state": "waiting_browser",
                    "blocker_type": "control_failure",
                    "recoverable": True,
                    "recovery_cause": "control_observation",
                    "reason": "Oracle's invalid history row has no observed edit control",
                }
            await self.form.settle(timeout=5)
            opened = self.page.get_by_role("button", name=re.compile(r"^Save$", re.I))
            if not await opened.count() or not await opened.first.is_visible():
                return {
                    "state": "waiting_browser",
                    "blocker_type": "control_failure",
                    "recoverable": True,
                    "recovery_cause": "control_observation",
                    "reason": "Oracle did not open the selected history row for correction",
                }
            self.emit("history_repair", "Opened Oracle's invalid history row for correction")
            return None
        return await super().on_form(obs)

    async def validation_rejection(self):
        obs = await self.form.scan()
        # Oracle explicitly rejects the commit and navigates to its issue fixer.
        # This is distinct from an uncertain network response or a stale ARIA alert.
        if re.search(r"issues? that needs? to be fixed before you can submit", obs["text"], re.I):
            return {
                "type": "explicit_validation_rejection",
                "category": "validation",
                "url": self.page.url,
                "evidence": "Oracle requires corrections before this application can be submitted",
                "missing_fields": [
                    f["label"] for f in obs["fields"] if f.get("error") and not f.get("valid", True)
                ],
            }
        return None

    async def keep_signed_in(self, obs):
        for f in obs["fields"]:
            if f["type"] == "checkbox" and f.get("value") != "true" and KEEP_SIGNED_IN.search(f.get("label") or ""):
                try:
                    await self.form.fill(f, "true")
                    self.emit("account", "Kept the candidate signed in on this employer's career site")
                except Exception:
                    pass  # A convenience only; verification does not depend on it.

    async def on_email_entry(self, obs):
        await self.keep_signed_in(obs)
        return await super().on_email_entry(obs)

    async def on_email_code(self, obs):
        await self.keep_signed_in(obs)
        return await super().on_email_code(obs)

    def site_lock(self, obs):
        if not LOCKED.search(obs.get("text", "")):
            return None
        self.release_mail()  # Nothing more can be sent during the lock; free the shared sender.
        return {
            "state": "waiting_browser",
            "blocker_type": "site_transient",
            "recoverable": True,
            "recovery_cause": "site_temporary",
            "site_cooldown_seconds": LOCK_SECONDS,
            "reason": "Oracle locked email verification for 30 minutes after too many attempts; nothing was "
            "submitted, and the application resumes after the lock",
        }

    def classify_page(self, obs):
        from ..ats import oracle_job_catalog

        if oracle_job_catalog(obs["url"]) or LOCKED.search(obs["text"]):
            return Step.UNKNOWN
        text = obs["text"]
        if confirmation_wording(text, self.committed) and (self.committed or "Apply Now" not in text):
            if len([f for f in obs["fields"] if f["type"] != "checkbox"]) <= 1:
                return Step.CONFIRMATION
        pins = [f for f in obs["fields"] if re.search(r"pin|code|digit", f["label"] + f["name"], re.I)]
        if pins and CODE_TEXT.search(text):
            return Step.EMAIL_CODE
        fields = [f for f in self.app_fields(obs) if f["type"] != "checkbox"]
        emails = [f for f in fields if f["type"] == "email" or re.search(r"e-?mail", f["label"], re.I)]
        if emails and len(fields) == len(emails) and any(TERMS.search(f["label"]) for f in obs["fields"]):
            return Step.EMAIL_ENTRY
        return super().classify_page(obs)

    async def on_unknown(self, obs):
        from ..ats import oracle_job_catalog

        if not self.committed and (locked := self.site_lock(obs)):
            return locked
        if oracle_job_catalog(obs["url"]):
            return {
                "state": "waiting_browser",
                "blocker_type": "control_failure",
                "recoverable": False,
                "retryable": False,
                "recovery_cause": "catalog_entry_unavailable",
                "reason": "The owned Oracle posting returned a job recommendation catalog; no application form is open",
            }
        return await super().on_unknown(obs)

    def code_fields(self, obs):
        found = super().code_fields(obs)
        if found:
            return found
        pins = [
            f for f in obs["fields"] if re.search(r"pin.?code|^pin|digit", f["label"] + " " + f["name"], re.I)
        ]
        return pins if CODE_TEXT.search(obs["text"]) else []
