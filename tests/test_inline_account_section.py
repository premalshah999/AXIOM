"""An optional account section inside an application is the form; a real sign-up page stays an account step (Meta, 2026-11-01)."""

import asyncio
from collections import Counter
from unittest.mock import AsyncMock, MagicMock

import pytest

from jobpilot.adapters.base import Adapter, Step


def adapter():
    value = Adapter.__new__(Adapter)
    value.attempts = Counter()
    value.apply_ids = set()
    value.committed = False
    value.link_resume = False
    value.in_assessment = lambda: False
    value.e = MagicMock()
    value.e.form.frames = {}
    return value


def field(label, kind="text", required=False, **extra):
    return {"id": label, "label": label, "type": kind, "required": required, "options": [], **extra}


def control(label):
    return {"id": label, "label": label, "aria_label": "", "automation_id": "", "href": ""}


def page(fields, controls, url="https://jobs.example.test/apply/1", text=""):
    return {"fields": fields, "controls": [control(c) for c in controls], "text": text, "headings": [], "errors": [], "dialogs": [], "url": url}


APPLICATION = [field("Upload resume", "file"), field("First name"), field("Last name"), field("Email", "email"), field("Phone number")]


def test_an_optional_account_section_inside_the_application_is_the_form():
    obs = page([*APPLICATION, field("Password", "password"), field("Confirm password", "password")], ["Cancel", "Submit", "Sign up"],
               text="Create a Career Profile account (optional) Use a password Already have an account")
    assert adapter().classify_page(obs) == Step.FORM


def test_a_required_password_pair_in_the_application_stays_an_account_step():
    obs = page([*APPLICATION, field("Password", "password", True), field("Confirm password", "password", True)], ["Create account"])
    assert adapter().classify_page(obs) == Step.CREATE_ACCOUNT


def test_a_sign_up_page_without_the_applications_fields_stays_an_account_step():
    obs = page([field("Email", "email"), field("Password", "password"), field("Confirm password", "password")], ["Create account"],
               url="https://jobs.example.test/create-account")
    assert adapter().classify_page(obs) == Step.CREATE_ACCOUNT


def test_optional_passwords_on_a_create_account_address_stay_an_account_step():
    obs = page([*APPLICATION, field("Password", "password"), field("Confirm password", "password")], ["Submit"],
               url="https://jobs.example.test/sign-up")
    assert adapter().classify_page(obs) == Step.CREATE_ACCOUNT


def outcome(text, errors):
    value = adapter()
    value.e.form.settle = AsyncMock()
    value.e.form.scan = AsyncMock(return_value={"errors": errors, "text": text, "fields": [field("Password", "password")], "controls": []})
    value.new_password_step = lambda obs: False
    value.code_fields = lambda obs: []
    return asyncio.run(value.auth_outcome(timeout=0.5))


def test_already_have_an_account_as_a_choice_on_the_page_is_not_an_existing_account():
    assert outcome("Use a password Use a one-time code Already have an account", [])[0] != "exists"


@pytest.mark.parametrize("message", ["An account with this email already exists.", "This email is already registered."])
def test_a_page_that_says_the_email_has_an_account_still_does(message):
    assert outcome("Create account " + message, [message])[0] == "exists"
    assert outcome("Create account " + message, [])[0] == "exists"
