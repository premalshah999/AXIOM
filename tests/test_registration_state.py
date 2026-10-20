"""Dayforce ID registration continues after its first page: security questions, then (the account now exists) an
emailed code and multifactor setup. Marking the account authenticated when the password fields vanished at the
security questions sent the next pass to "Sign In" for an account that was never created (2026-10-16)."""

import pytest

from jobpilot.accounts import registration_state


@pytest.mark.parametrize("creating,path,headings,passwords,expected", [
    (True, "/register/securityquestions", ["Security Questions"], 0, None),
    (True, "/globalidentity/users/verification", ["Enter Verification Code"], 0, "verification_pending"),
    (True, "/register/mfasetup", ["Multifactor Authentication"], 0, "verification_pending"),
    (True, "/careers/jobs/123/apply", ["Personal Information"], 0, "authenticated"),  # one-page registration done
    (False, "/careers/jobs/123/apply", ["My Information"], 0, "authenticated"),  # signed in
    (False, "/login", ["Sign In"], 1, None),  # the form came back
    (True, "/globalidentity/account/login", ["Create Your Account"], 2, None),
])
def test_an_account_action_records_only_what_its_result_proves(creating, path, headings, passwords, expected):
    assert registration_state(creating, path, headings, passwords) == expected
