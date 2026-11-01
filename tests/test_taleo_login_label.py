"""Taleo prints a required field's mark as a lone dot: its sign-in box is labelled "User Name ." (Textron, 2026-11-01).
The account flow did not recognise it as the login identifier, so the saved login was never typed and every run ended
on "Please provide a user name and a password"."""

import pytest

from jobpilot.accounts.pages import login_identifier


@pytest.mark.parametrize("label", ["User Name .", "User Name .", "Username *", "Login:", "Email Address ."])
def test_a_required_mark_dot_does_not_hide_the_login_box(label):
    assert login_identifier({"type": "text", "label": label})


@pytest.mark.parametrize("label", ["First Name .", "Phone ."])
def test_other_boxes_are_still_not_the_login_box(label):
    assert not login_identifier({"type": "text", "label": label})
