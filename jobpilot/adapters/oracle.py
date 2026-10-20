"""Oracle Recruiting Cloud candidate experience (*.oraclecloud.com/hcmUI/CandidateExperience).

Flow: job page "Apply Now" -> email + terms -> optional emailed PIN for returning candidates ->
one or more apply-flow sections (cx-select-pills, oj-select comboboxes, e-signature) -> Submit."""

import re

from .base import CODE_TEXT, CONFIRM_TEXT, Adapter, Step

TERMS = re.compile(r"terms|privacy|agree|consent", re.I)


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
            await save.click(timeout=4000)
            await self.form.settle(timeout=5)
            return None
        cards = self.page.locator('[data-profile-item-tile="true"][data-profile-item-invalid="true"]:visible')
        if await cards.count():
            self.attempts["history_repair"] += 1
            if self.attempts["history_repair"] > 12:
                return self.defer(
                    "Oracle history cards still contain invalid fields", state="waiting_browser"
                )
            edit = cards.first.get_by_role("button", name="Edit", exact=True)
            await edit.click(timeout=4000)
            await self.form.settle(timeout=5)
            return None
        return await super().on_form(obs)

    async def validation_rejection(self):
        obs = await self.form.scan()
        # Oracle explicitly rejects the commit and navigates to its issue fixer.
        # This is distinct from an uncertain network response or a stale ARIA alert.
        if re.search(r"issues? that needs? to be fixed before you can submit", obs["text"], re.I):
            return {
                "type": "explicit_validation_rejection",
                "url": self.page.url,
                "evidence": "Oracle requires corrections before this application can be submitted",
                "fields": [f["label"] for f in obs["fields"] if f.get("error")],
            }
        return None

    def classify_page(self, obs):
        text = obs["text"]
        if CONFIRM_TEXT.search(text) and (self.committed or "Apply Now" not in text):
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

    def code_fields(self, obs):
        found = super().code_fields(obs)
        if found:
            return found
        pins = [
            f for f in obs["fields"] if re.search(r"pin.?code|^pin|digit", f["label"] + " " + f["name"], re.I)
        ]
        return pins if CODE_TEXT.search(obs["text"]) else []
