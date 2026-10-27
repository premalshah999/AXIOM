"""Taleo's "Forgot your password?" page asks for a User Name and an Email Address and submits with "OK" beside
"Cancel". The reset step found no submit control and the fallback model pressed Cancel, so Textron's refused login
was never reset (2026-10-27)."""

from jobpilot.adapters.base import Adapter


def control(label):
    return {"id": label, "label": label, "aria_label": "", "automation_id": "", "href": ""}


def test_ok_submits_a_reset_page():
    view = {"controls": [control("Job Search"), control("OK"), control("Cancel")]}
    assert Adapter.reset_button(Adapter.__new__(Adapter), view)["label"] == "OK"


def test_a_named_reset_submit_is_preferred_over_ok():
    view = {"controls": [control("OK"), control("Send reset link")]}
    assert Adapter.reset_button(Adapter.__new__(Adapter), view)["label"] == "Send reset link"


def test_ok_is_not_a_sign_in_or_sign_up_submit():
    view = {"controls": [control("OK")]}
    adapter = Adapter.__new__(Adapter)
    assert adapter.auth_button(view, "sign_in") is None
    assert adapter.auth_button(view, "create") is None
