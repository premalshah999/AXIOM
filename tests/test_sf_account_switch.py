"""SuccessFactors accounts (EY, Capgemini 2026-10-15): a sign-in that was never confirmed was retried until
the run stalled although no account existed, and an account created on an earlier day was refused again by
Create as existing. One switch each way per run: a rejected sign-in tries Create, a refused Create signs in."""

from types import SimpleNamespace

from jobpilot.accounts import AccountFlow, account_tenant, origin

URL = "https://career5.successfactors.eu/career?company=EYHRISPRD1&loginFlowRequired=true&lang=en_US"


class FakeLocator:
    async def evaluate(self, *_args):
        return False


class FakeForm:
    def __init__(self, obs):
        self.obs, self.clicked = obs, []
        frame = SimpleNamespace(url=URL)
        self.frames = {item["id"]: frame for item in [*obs["fields"], *obs["controls"]]}
        profile = SimpleNamespace(
            allow_account_creation=True, accept_all_application_terms=False, email="alex@example.test"
        )
        self.resolver = SimpleNamespace(profile=profile)
        self.page = None

    async def scan(self):
        return self.obs

    def locator(self, _id):
        return FakeLocator()

    async def click(self, control_id, auth_control=False):
        self.clicked.append(control_id)


def _flow(service, obs):
    engine = SimpleNamespace(service=service, form=FakeForm(obs), job={"url": URL})
    flow = AccountFlow(engine)
    scope = origin(URL) + ":" + account_tenant(URL, URL)
    return flow, engine.form, scope


def _fields(*passwords):
    return [{"id": "email", "type": "text", "label": "Email Address:"}] + [
        {"id": f"pw{i}", "type": "password", "label": label} for i, label in enumerate(passwords)
    ]


async def test_rejected_sign_in_of_an_unconfirmed_account_tries_create_once(service):
    obs = {
        "fields": _fields("Password:"),
        "controls": [{"id": "signin", "label": "Sign In"}, {"id": "create", "label": "Create an account"}],
        "errors": [],
        "text": "",
    }
    flow, form, scope = _flow(service, obs)
    flow.store.save(URL, flow.store.shared(), "signing_in")
    flow.attempts.add((scope, False))
    assert await flow.handle()
    assert form.clicked == ["create"]
    assert ("switch", "create") in flow.attempts


async def test_create_refused_as_existing_signs_in_once(service):
    obs = {
        "fields": _fields("Choose Password", "Retype Password"),
        "controls": [
            {"id": "signin", "label": "Please sign in"},
            {"id": "create", "label": "Create Account"},
        ],
        "errors": ["Account Already Exists"],
        "text": "",
    }
    flow, form, scope = _flow(service, obs)
    flow.attempts.add((scope, True))
    assert await flow.handle()
    assert form.clicked == ["signin"]
    assert flow.store.state(URL) == "exists"  # The refusal proves the account; later passes never create it.


async def test_career_landing_page_opens_its_sign_in_once(service):
    obs = {
        "fields": [{"id": "lang", "type": "combobox", "label": "Language"}],
        "controls": [{"id": "skip", "label": "Skip to Main Content"}, {"id": "signin", "label": "Sign In"}],
        "errors": [],
        "text": "",
    }
    flow, form, _ = _flow(service, obs)
    form.page = SimpleNamespace(
        url="https://career5.successfactors.eu/career?company=capgemitecP3&lang=en_US"
    )
    flow.e.emit = lambda *a, **k: None
    assert await flow.handle()
    assert form.clicked == ["signin"]
    assert not await flow.handle()  # once per run


async def test_known_account_signs_in_instead_of_registering_again(service):
    # Capgemini: the combined registration form came back after an earlier run created the account.
    obs = {
        "fields": _fields("Choose Password", "Retype Password"),
        "controls": [{"id": "signin", "label": "Please sign in"}, {"id": "apply", "label": "Apply"}],
        "errors": [],
        "text": "",
    }
    flow, form, _ = _flow(service, obs)
    flow.store.save(URL, flow.store.shared(), "prepared_application")
    assert await flow.handle()
    assert form.clicked == ["signin"]


async def test_privacy_statement_page_is_acknowledged_once(service):
    # Capgemini: after sign-in the data privacy statement is a page with Acknowledge and Go Back.
    obs = {
        "fields": [],
        "controls": [{"id": "ack", "label": "Acknowledge"}, {"id": "back", "label": "Go Back"}],
        "errors": [],
        "text": "Data Privacy Statement. We process your personal data ...",
    }
    flow, form, _ = _flow(service, obs)
    form.page = SimpleNamespace(url="https://career5.successfactors.eu/careers?company=capgemitecP3")
    form.resolver.profile.accept_all_application_terms = True
    flow.e.emit = lambda *a, **k: None
    assert await flow.handle()
    assert form.clicked == ["ack"]


async def test_create_form_back_after_create_signs_in_once_without_any_message(service):
    """SAP 2026-10-16: Create sent a passcode, SAP refused it for the account created an hour earlier and
    showed the create form again with no "already exists" text; Create was pressed until the run stalled."""
    obs = {
        "fields": _fields("Choose Password", "Retype Password"),
        "controls": [
            {"id": "signin", "label": "Please sign in"},
            {"id": "create", "label": "Create Account"},
        ],
        "errors": [],
        "text": "",
    }
    flow, form, scope = _flow(service, obs)
    flow.store.save(URL, flow.store.shared(), "created_locally")
    flow.attempts.add((scope, True))
    assert await flow.handle()
    assert form.clicked == ["signin"]
    assert flow.store.state(URL) == "signing_in"
    assert ("switch", "signin") in flow.attempts


async def test_guest_application_is_chosen_before_any_account_when_the_rule_allows(service):
    """Dayforce (York, mPulse 2026-10-16): "Apply without an Account" beside Sign In / Create one now."""
    obs = {
        "fields": [],
        "controls": [
            {"id": "guest", "label": "Apply without an Account"},
            {"id": "signin", "label": "Sign In"},
            {"id": "create", "label": "Create one now."},
        ],
        "errors": [],
        "text": "",
    }
    flow, form, _ = _flow(service, obs)
    form.resolver.answer_rules = SimpleNamespace(guest_apply_first=True)
    form.settle = lambda timeout=0: _noop()
    flow.e.emit = lambda *a, **k: None
    assert await flow.apply_as_guest(obs) and form.clicked == ["guest"]
    assert not await flow.apply_as_guest(obs)  # once per run
    flow2, form2, _ = _flow(service, obs)
    form2.resolver.answer_rules = SimpleNamespace(guest_apply_first=False)
    assert not await flow2.apply_as_guest(obs) and form2.clicked == []


async def _noop():
    return None


class Proceeded(Exception):
    pass


async def test_create_refused_for_password_length_retries_inside_the_limit(service):
    # Westinghouse 2026-10-17: the limits appeared only after Create refused the longer password, and the flow
    # stopped with "Account action did not advance" instead of retrying within them.
    obs = {
        "fields": _fields("Choose Password", "Retype Password"),
        "controls": [{"id": "create", "label": "Create Account"}],
        "errors": [],
        "text": "Password must be at least 8 characters long. Password must not be longer than 18 characters.",
    }
    flow, form, scope = _flow(service, obs)
    flow.attempts.add((scope, True))

    async def fill_current():
        raise Proceeded()

    form.fill_current = fill_current
    try:
        await flow.handle()
    except Proceeded:
        pass
    else:
        raise AssertionError("the refused create stopped instead of retrying within the password limit")
    assert ("password_rules", scope) in flow.attempts


def test_password_limits_are_read_from_the_refusal_text():
    from jobpilot.accounts import PASSWORD_MAXIMUM

    assert PASSWORD_MAXIMUM.findall("Password must not be longer than 18 characters.") == ["18"]


async def test_a_sign_in_the_page_itself_refused_never_switches_to_create(service):
    # Westinghouse 2026-10-16/17: "A password is required." is the page's own check (nothing reached the server); the
    # engine then created a second account for an email that already had one.
    obs = {
        "fields": _fields("Password:"),
        "controls": [{"id": "signin", "label": "Sign In"}, {"id": "create", "label": "Create an account"}],
        "errors": ["A password is required."],
        "text": "Career Opportunities: Sign In Please complete all required fields and re-submit.",
    }
    flow, form, scope = _flow(service, obs)
    flow.store.save(URL, flow.store.shared(), "signing_in")
    flow.attempts.add((scope, False))
    await flow.handle()
    assert "create" not in form.clicked and ("switch", "create") not in flow.attempts


async def test_a_create_link_that_did_not_navigate_is_followed(service):
    """NetApp 2026-10-21: the run scanned Sign In again two seconds after clicking "Create an account" and ended
    "explicitly rejected the account credentials" on a first application."""
    obs = {
        "url": URL,
        "fields": _fields("Password:"),
        "controls": [{"id": "signin", "label": "Sign In"},
                     {"id": "create", "label": "Create an account", "href": "/career?company=EYHRISPRD1&create=1"}],
        "errors": [],
        "text": "",
    }
    flow, form, scope = _flow(service, obs)
    visited = []

    async def goto(url, **_kwargs):
        visited.append(url)

    form.page = SimpleNamespace(goto=goto)
    flow.store.save(URL, flow.store.shared(), "signing_in")
    flow.attempts.add((scope, False))
    assert await flow.handle()
    assert form.clicked == ["create"]
    assert visited == ["https://career5.successfactors.eu/career?company=EYHRISPRD1&create=1"]
