"""Amazon (2026-10-27): "The login information you provided appears to be incorrect." is a refused password; unrecognised,
the account helper never reset it and seven applications waited for the owner."""

import pytest

from jobpilot.adapters.base import BAD_CREDENTIALS


@pytest.mark.parametrize("text", [
    "The login information you provided appears to be incorrect.",
    "Your sign-in details are not recognised.",
    "Incorrect email or password",
])
def test_refused_logins(text):
    assert BAD_CREDENTIALS.search(text)


@pytest.mark.parametrize("text", ["Enter your login information below", "Sign in with your login details"])
def test_instructions_are_not_refusals(text):
    assert not BAD_CREDENTIALS.search(text)
