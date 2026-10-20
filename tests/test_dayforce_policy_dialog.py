"""Dayforce guest application (York Space, 2026-10-16): the privacy statement dialog has its own
"I agree to the Privacy Statement" checkbox and Save / Cancel. The navigator pressed Cancel and left the
guest application for the reCAPTCHA-protected Dayforce ID login."""

from types import SimpleNamespace

from jobpilot.browser import BrowserEngine


class FakeForm:
    def __init__(self):
        self.filled, self.clicked, self.page = [], [], None
        self.resolver = SimpleNamespace(profile=SimpleNamespace(accept_all_application_terms=True))

    async def fill(self, field, value):
        self.filled.append((field["id"], value))

    async def click(self, control_id, auth_control=False):
        self.clicked.append(control_id)


def engine():
    return SimpleNamespace(form=FakeForm(), page=None, emit=lambda *a, **k: None)


def obs(box_label="I agree to the Privacy Statement", buttons=("Cancel", "Save")):
    controls = [{"id": f"b{i}", "label": label} for i, label in enumerate(buttons)]
    return {
        "dialogs": [{"text": "Use Of Biometric Data and Identifiers. Please review the policy below. "
                             "I agree to the Privacy Statement Cancel Save",
                     "control_ids": [c["id"] for c in controls], "field_ids": ["agree"]}],
        "fields": [{"id": "agree", "type": "checkbox", "label": box_label}],
        "controls": controls,
    }


async def test_agreement_box_and_save_accept_the_statement():
    e = engine()
    assert await BrowserEngine.accept_policy_dialog(e, obs())
    assert e.form.filled == [("agree", "true")] and e.form.clicked == ["b1"]


async def test_unrelated_checkbox_or_ambiguous_buttons_are_left_to_the_navigator():
    e = engine()
    assert not await BrowserEngine.accept_policy_dialog(e, obs(box_label="Subscribe to job alerts"))
    assert not await BrowserEngine.accept_policy_dialog(e, obs(buttons=("Save", "Continue")))
    assert e.form.clicked == []
