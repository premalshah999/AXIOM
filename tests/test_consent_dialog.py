"""Eightfold "Please review before continuing" consent modal (Starbucks checkbox; Qualcomm radio)."""

from jobpilot.forms import consent_dialog_action

TOP = {
    "title": "Please review before continuing",
    "text": "We value your privacy. Read our privacy notice.",
    "field_ids": ["c", "r"],
    "control_ids": ["s"],
}
SUBMIT = {"id": "s", "label": "Submit"}


def obs(*fields):
    return {"fields": list(fields), "controls": [SUBMIT]}


def test_checkbox_only_consent_modal_qualifies():
    assert consent_dialog_action(TOP, obs({"id": "c", "type": "checkbox", "value": "true"}))["id"] == "s"


def test_agreeing_radio_qualifies_but_unselected_or_other_inputs_do_not():
    checkbox = {"id": "c", "type": "checkbox", "value": "true"}
    assert (
        consent_dialog_action(TOP, obs(checkbox, {"id": "r", "type": "radio", "value": "I agree"}))["id"]
        == "s"
    )
    assert consent_dialog_action(TOP, obs(checkbox, {"id": "r", "type": "radio", "value": ""})) is None
    assert (
        consent_dialog_action(TOP, obs(checkbox, {"id": "r", "type": "radio", "value": "I do not agree"}))
        is None
    )
    assert consent_dialog_action(TOP, obs(checkbox, {"id": "r", "type": "text", "value": "x"})) is None
