"""A sign-in form that marks no error (ClearCompany, 2026-10-30): its "Incorrect username or password" is plain text, and its "Forgot password or your
first time signing in?" is the only way to give a password to an account the employer made from an earlier application. Both led to a stop that
said the employer neither accepted nor refused the login."""

from types import SimpleNamespace

import pytest

from jobpilot.accounts import AccountFlow, account_tenant, origin
from jobpilot.accounts.pages import REFUSED_LOGIN_TEXT, optional_account_section
from jobpilot.forms import FINAL

URL = "https://careers.example.test/jobs/1/apply"
FORGOT = "Forgot password or your first time signing in?"


class FakeForm:
    def __init__(self, obs):
        self.obs = obs
        frame = SimpleNamespace(url=URL)
        self.frames = {item["id"]: frame for item in [*obs["fields"], *obs["controls"]]}
        self.resolver = SimpleNamespace(profile=SimpleNamespace(allow_account_creation=True, accept_all_application_terms=False, email="alex@example.test"))
        self.page = SimpleNamespace(url=URL)

    async def scan(self):
        return self.obs

    def locator(self, _id):
        return SimpleNamespace(evaluate=self.evaluate)

    async def evaluate(self, *_args):
        return False


def known_account(service, obs):
    engine = SimpleNamespace(service=service, form=FakeForm(obs), job={"url": URL}, result=None, emit=lambda *a, **k: None)
    flow = AccountFlow(engine)
    flow.store.save(URL, flow.store.shared(), "exists")
    tenant = account_tenant(URL, URL)
    scope = origin(URL) + (":" + tenant if tenant and tenant != "unresolved_shared_tenant" else "")
    flow.attempts.add((scope, False))
    calls = []

    async def recover(scope_, detail):
        calls.append(detail)
        return True

    flow.recover_sign_in = recover
    return flow, scope, calls


def sign_in_page(text=""):
    return {"fields": [{"id": "user", "type": "email", "label": "Username"}, {"id": "pw", "type": "password", "label": "Password"}],
            "controls": [{"id": "forgot", "label": FORGOT}, {"id": "go", "label": "Continue"}], "errors": [], "text": text, "headings": []}


async def test_a_refusal_in_the_pages_text_goes_straight_to_the_reset(service):
    flow, _, calls = known_account(service, sign_in_page("Sign In Incorrect username or password " + FORGOT))
    assert await flow.handle()
    assert calls == ["Incorrect username"]


async def test_a_second_silent_return_of_the_form_uses_its_reset_link(service):
    flow, scope, calls = known_account(service, sign_in_page("Sign In"))
    flow.attempts.add(("retry", scope))
    assert await flow.handle()
    assert len(calls) == 1 and not flow.e.result


async def test_a_form_with_no_reset_link_still_stops_and_says_so(service):
    obs = sign_in_page("Sign In")
    obs["controls"] = [{"id": "go", "label": "Continue"}]
    flow, scope, calls = known_account(service, obs)
    flow.attempts.add(("retry", scope))
    assert await flow.handle()
    assert not calls and "without accepting or refusing" in flow.e.result["reason"]


@pytest.mark.parametrize("text", ["Incorrect username or password", "Invalid password.", "Wrong email"])
def test_a_refusal_reads_as_one(text):
    assert REFUSED_LOGIN_TEXT.search(text)


@pytest.mark.parametrize("text", ["I don't have an account", "Sign in with username and password instead", "Forgot password or your first time signing in?"])
def test_a_sign_in_pages_own_words_do_not(text):
    assert not REFUSED_LOGIN_TEXT.search(text)


def field(label, kind="text", required=False):
    return {"id": label, "label": label, "type": kind, "required": required}


APPLICATION = [field("Resume", "file"), field("First name"), field("Last name"), field("Email", "email")]
PASSWORDS = [field("Password", "password"), field("Confirm password", "password")]


def controls(*labels):
    return [{"id": x, "label": x} for x in labels]


def test_the_optional_section_is_recognised_by_every_caller():
    assert optional_account_section([*APPLICATION, *PASSWORDS], controls("Sign up", "Submit"), PASSWORDS, FINAL)


def test_a_required_password_or_a_page_without_the_applications_fields_is_not_one():
    required = [field("Password", "password", True)]
    assert not optional_account_section([*APPLICATION, *required], controls("Submit"), required, FINAL)
    assert not optional_account_section([field("Email", "email"), *PASSWORDS], controls("Submit"), PASSWORDS, FINAL)
    assert not optional_account_section([*APPLICATION, *PASSWORDS], controls("Create account"), PASSWORDS, FINAL)
