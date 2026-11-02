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


LONG_QUESTION = (
    "10. Are you a current Brown University Health employee? (If you are currently a Brown University Health employee, you MUST apply "
    "using your Brown University Health username and password via the Jobs Hub in Workday)"
)


def test_a_long_question_that_mentions_a_password_is_not_a_credential_in_any_filter():
    """Brown University Health (Workday, 2026-11-01): the required current-employee question was never answered. forms.AUTH was bounded
    (98306be); the answer-quality secret filter and the navigator's supported-answer filter read the same words without the bound."""
    from jobpilot.answer_quality import SECRET

    assert not AUTH.search(LONG_QUESTION) and not SECRET.search(LONG_QUESTION)
    assert SECRET.search("Password") and SECRET.search("Enter the verification code")


def test_the_navigators_supported_answers_include_a_long_question_that_mentions_a_password():
    from types import SimpleNamespace

    from jobpilot.browser import navigation_supported_answers

    field = {"id": "f", "label": LONG_QUESTION, "type": "dropdown", "value": "", "group": ""}
    answer = SimpleNamespace(disposition="answer", value="No", evidence_ids=["fact:x"])
    form = SimpleNamespace(resolver=SimpleNamespace(local=lambda f: answer), pending=[])
    assert navigation_supported_answers(form, {"fields": [field]})
    assert not navigation_supported_answers(form, {"fields": [{**field, "label": "Password"}]})
