"""Sign-up, sign-in and forgot-password controls of career sites the engine had not met (read off saved sign-in pages,
2026-10-25..11-01): each anchored wording below was absent from the patterns, so the account route stayed unopened."""

import pytest

from jobpilot.adapters.base import CREATE_LINK, FORGOT, SIGNIN_LINK


@pytest.mark.parametrize("label", ["Register With Us", "Register now", "Sign up here", "Sign Up for an account",
                                   "Create a New Account", "Create an Account", "Create profile!", "Create a login"])
def test_create_wordings(label):
    assert CREATE_LINK.search(label)


@pytest.mark.parametrize("label", ["Sign in now", "Sign in here", "Returning candidate login", "Returning User Login",
                                   "Log in here", "Already have an account? Sign in"])
def test_signin_wordings(label):
    assert SIGNIN_LINK.search(label)


@pytest.mark.parametrize("label", ["Forgot your username or password?", "Forgotten password", "Need help signing in?",
                                   "Can't log in?", "Forgot your password?", "Trouble logging in"])
def test_forgot_wordings(label):
    assert FORGOT.search(label)


@pytest.mark.parametrize("label", ["Register for the job fair", "Sign up for job alerts today and more", "Join our talent community",
                                   "Registration details"])
def test_unrelated_controls_stay_unmatched_by_the_create_pattern(label):
    assert not CREATE_LINK.search(label)


@pytest.mark.parametrize("label", ["Applicant Login", "Applicant privacy policy", "Forgot your username?", "Sign in with Google"])
def test_unrelated_controls_stay_unmatched_by_the_forgot_and_sign_in_patterns(label):
    assert not FORGOT.search(label) and not SIGNIN_LINK.search(label)
