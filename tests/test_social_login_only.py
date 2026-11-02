"""A provider-only sign-in or sign-up page ends the run plainly instead of looping."""

from types import SimpleNamespace

from jobpilot.social_login_only import provider_only, stop_if_social_login_only


def page(*labels, fields=()):
    return {"fields": list(fields), "controls": [{"label": label} for label in labels]}


SIGN_IN = ["Close modal", "Sign in using Microsoft", "Sign in using LinkedIn", "Sign in using Google",
           "Sign in using Facebook", "Create an account"]
SIGN_UP = ["Close modal", "Sign up using Microsoft", "Sign up using LinkedIn", "Sign up using Google",
           "Sign up using Facebook", "Sign In"]


def engine():
    events = []
    return SimpleNamespace(armed=False, result=None, emit=lambda *a: events.append(a), events=events)


def test_first_sign_in_list_is_left_to_the_agent_to_try_create_account():
    e = engine()
    assert not stop_if_social_login_only(e, page(*SIGN_IN))
    assert e.result is None


def test_sign_up_list_with_only_providers_stops_plainly():
    e = engine()
    assert stop_if_social_login_only(e, page(*SIGN_UP))
    assert e.result["state"] == "closed" and "nothing was submitted" in e.result["reason"]
    assert "Microsoft" in e.result["reason"] and e.events[0][0] == "social_login_only"


def test_third_sight_of_the_sign_in_list_stops():
    e = engine()
    assert not stop_if_social_login_only(e, page(*SIGN_IN))
    assert not stop_if_social_login_only(e, page(*SIGN_IN))
    assert stop_if_social_login_only(e, page(*SIGN_IN))


def test_an_email_way_in_or_a_field_is_not_a_dead_end():
    assert provider_only(page(*SIGN_IN, "Sign in with email")) is None
    assert provider_only(page(*SIGN_IN, fields=[{"label": "Email"}])) is None
    assert provider_only(page("Sign in using Google", "Apply")) is None


def test_armed_run_is_never_stopped():
    e = engine()
    e.armed = True
    assert not stop_if_social_login_only(e, page(*SIGN_UP))
