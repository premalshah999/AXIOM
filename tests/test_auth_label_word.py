"""1Password (Ashby) 2026-10-27: the required "Why 1Password?" was read as a password field, left blank, and every run
stopped as "Employer sign-in must complete before application submission"."""

import pytest

from jobpilot.forms import AUTH


@pytest.mark.parametrize("label", ["Why 1Password?", "What do you like about 1Password's product?"])
def test_an_employer_named_password_is_not_a_sign_in_field(label):
    assert not AUTH.search(label)


@pytest.mark.parametrize("label", ["Password", "Confirm password*", "Re-enter your Password", "Passwords must match",
                                   "Enter the verification code", "One-time code"])
def test_sign_in_fields_still_match(label):
    assert AUTH.search(label)
