"""SuccessFactors sign-in vs create (Westinghouse 2026-10-16/17, AMETEK 2026-10-18).

Westinghouse signed in with its saved account on 10-16 04:06; later runs saw the sign-in page come back, switched to
Create Account for that registered email, and saved the new password as the login. AMETEK did the same on 10-18:
its sign-in page returned without a message while another run opened the same career host, Create answered
"Account Already Exists" with no password field, and that page was recorded as a finished sign-in. Logins are now
keyed by career host plus company=, an account the employer is known to hold is never registered again, and a
refused password goes to the mailbox reset."""

from types import SimpleNamespace

from jobpilot.accounts import AccountFlow, AccountStore, account_tenant, origin
from jobpilot.db import Job, Run

SIGN_IN = "https://career4.successfactors.com/career?company=WestinghouseP&loginFlowRequired=true&lang=en_US"
JOB = "https://careers.westinghousenuclear.com/WECTEC_Staffing/job/OTHER-Design-Engineer/1370271500"
LOGIN = {"email": "alex@example.test", "password": "Ap9!sixteenchars"}


class Proceeded(Exception):
    pass


class FakeLocator:
    async def evaluate(self, *_args):
        return False


class FakeForm:
    def __init__(self, obs, url=SIGN_IN):
        self.obs, self.clicked = obs, []
        frame = SimpleNamespace(url=url)
        self.frames = {item["id"]: frame for item in [*obs["fields"], *obs["controls"]]}
        profile = SimpleNamespace(
            allow_account_creation=True, accept_all_application_terms=False, email="alex@example.test"
        )
        self.resolver = SimpleNamespace(profile=profile)
        self.page = SimpleNamespace(url=url)

    async def scan(self):
        return self.obs

    def locator(self, _id):
        return FakeLocator()

    async def click(self, control_id, auth_control=False):
        self.clicked.append(control_id)

    async def fill_current(self):
        raise Proceeded()  # The flow went on to fill and send the sign-in.


def _flow(service, obs, url=SIGN_IN):
    events = []
    engine = SimpleNamespace(
        service=service,
        form=FakeForm(obs, url),
        job={"url": JOB, "ats": "successfactors", "id": "job"},
        emit=lambda *a, **k: events.append(a[:2]),
        result=None,
    )
    flow = AccountFlow(engine)
    canonical = flow.store.canonical(url)
    scope = origin(canonical) + ":" + account_tenant(canonical, JOB)
    return flow, engine, scope


def _sign_in_page(*errors):
    return {
        "fields": [
            {"id": "email", "type": "text", "label": "Email Address:"},
            {"id": "pw", "type": "password", "label": "Password:"},
        ],
        "controls": [
            {"id": "signin", "label": "Sign In"},
            {"id": "forgot", "label": "Forgot your password?"},
            {"id": "create", "label": "Create an account"},
        ],
        "headings": ["Career Opportunities: Sign In", "Already have an account?", "Not a registered user yet?"],
        "errors": list(errors),
        "text": "",
    }


def test_one_login_per_career_host_and_company(service):
    store = AccountStore(service, "alex@example.test", JOB)
    store.save(SIGN_IN, LOGIN, "authenticated")
    for url in (
        "https://career4.successfactors.com/portalcareer?_s.crb=abc",  # signed-in portal pages carry no company=
        "https://careers.westinghousenuclear.com/talentcommunity/login",  # the employer's own career site
        "https://career4.successfactors.com/career?career_company=WestinghouseP&lang=en_US",
    ):
        assert store.get(url) == LOGIN, url
    # Another employer on the same career host keeps its own login.
    other = AccountStore(service, "alex@example.test", "https://corningjobs.corning.com/job/X/1")
    assert other.get("https://career4.successfactors.com/portalcareer?_s.crb=abc") is None


def test_a_login_saved_under_the_employer_site_is_still_found(service):
    # Capgemini, Bunge and Farmers hold both records: pages without company= saved under the career site.
    job = "https://careers.capgemini.com/job/Software-Engineer/1448252033"
    store = AccountStore(service, "alex@example.test", job)
    store.save("https://career5.successfactors.eu/portalcareer?_s.crb=abc", LOGIN, "authenticated")
    store.observe("https://career5.successfactors.eu/careers?company=capgemitecP3")
    assert store.get("https://career5.successfactors.eu/careers?company=capgemitecP3") == LOGIN
    assert store.known("https://career5.successfactors.eu/careers?company=capgemitecP3")


def test_proof_of_the_account_outlives_a_later_sign_in_attempt(service):
    store = AccountStore(service, "alex@example.test", JOB)
    store.save(SIGN_IN, LOGIN, "authenticated")
    store.save(SIGN_IN, LOGIN, "signing_in")  # saved before every Sign In click
    assert store.state(SIGN_IN) == "signing_in" and store.known(SIGN_IN)


async def test_refused_password_of_a_known_account_resets_by_mail_never_creates(service):
    obs = _sign_in_page("Invalid email address or password. Please re-enter your login info.")
    flow, engine, scope = _flow(service, obs)
    flow.store.save(SIGN_IN, LOGIN, "authenticated")
    flow.store.save(SIGN_IN, LOGIN, "signing_in")
    flow.attempts.add((scope, False))
    calls = []

    async def recover(observation, detail):
        calls.append(detail)
        return {"state": "waiting_browser", "reason": "Waiting for the reset email", "blocker_type": "email_wait"}

    engine.fallback_mail_adapter = SimpleNamespace(recover=recover)
    assert await flow.handle()
    assert engine.form.clicked == []  # never "Create an account"
    assert calls and "Invalid email" in calls[0]
    assert engine.result["reason"] == "Waiting for the reset email"


async def test_sign_in_page_back_without_a_refusal_retries_once_then_stops(service):
    flow, engine, scope = _flow(service, _sign_in_page())
    flow.store.save(SIGN_IN, LOGIN, "authenticated")
    flow.store.save(SIGN_IN, LOGIN, "signing_in")
    flow.attempts.add((scope, False))
    try:
        await flow.handle()
    except Proceeded:
        pass
    else:
        raise AssertionError("a known account must sign in once more, not register")
    assert engine.form.clicked == [] and ("retry", scope) in flow.attempts
    flow.attempts.add((scope, False))  # the second sign-in came back too
    assert await flow.handle()
    assert engine.form.clicked == []
    assert engine.result["blocker_type"] == "account" and engine.result["recoverable"] is False


async def test_a_confirmed_application_on_the_career_site_proves_the_account(service):
    # Westinghouse's record read "signing_in" with an unconfirmed password; its 10-16 application was confirmed.
    with service.db.session() as s:
        s.add(Job(id="earlier", identity="earlier", url=JOB.replace("1370271500", "1432638800"), ats="successfactors"))
        s.add(Run(id="done", job_id="earlier", state="confirmed", mode="submit", packet={}))
        s.commit()
    obs = _sign_in_page("Invalid email address or password. Please re-enter your login info.")
    flow, engine, scope = _flow(service, obs)
    flow.store.save(SIGN_IN, LOGIN, "signing_in")
    flow.attempts.add((scope, False))
    calls = []

    async def recover(observation, detail):
        calls.append(detail)
        return {"state": "waiting_browser", "reason": "reset requested", "blocker_type": "email_wait"}

    engine.fallback_mail_adapter = SimpleNamespace(recover=recover)
    assert await flow.handle()
    assert calls and engine.form.clicked == []


async def test_account_already_exists_restores_the_login_create_replaced(service):
    obs = {
        "fields": [
            {"id": "email", "type": "text", "label": "Email Address:"},
            {"id": "pw0", "type": "password", "label": "Choose Password:"},
            {"id": "pw1", "type": "password", "label": "Retype Password:"},
        ],
        "controls": [{"id": "signin", "label": "Please sign in"}, {"id": "create", "label": "Create Account"}],
        "headings": ["Career Opportunities: Create an Account", "Account Already Exists"],
        "errors": [],
        "text": "",
    }
    flow, engine, scope = _flow(service, obs)
    flow.store.save(SIGN_IN, {**LOGIN, "password": "Ap9!generated123"}, "created_locally", previous=LOGIN)
    flow.attempts.add((scope, True))
    assert await flow.handle()
    assert engine.form.clicked == ["signin"]
    assert flow.store.get(SIGN_IN) == LOGIN and flow.store.state(SIGN_IN) == "exists"


def test_a_page_offering_only_sign_in_signed_nobody_in():
    # Westinghouse: Create landed on its job search with "Sign In" in the header (2026-10-16 13:30).
    landing = {"controls": [{"label": "Sign In"}, {"label": "Search Jobs"}]}
    portal = {"controls": [{"label": "Sign Out"}, {"label": "Apply"}]}
    assert AccountFlow.signed_out(landing, SIGN_IN)
    assert not AccountFlow.signed_out(portal, SIGN_IN)
    assert not AccountFlow.signed_out(landing, "https://example.test/login")


async def test_the_sign_in_prompt_already_have_an_account_is_not_a_refusal():
    # Boehringer 2026-10-16: "sign in: exists" twice from the page's own prompt, then a needless password reset.
    from jobpilot.adapters.others import SuccessFactors

    async def settle(timeout=0):
        return None

    async def scan():
        return _sign_in_page()

    adapter = SuccessFactors.__new__(SuccessFactors)
    adapter.e = SimpleNamespace(form=SimpleNamespace(settle=settle, scan=scan))
    adapter.code_fields = lambda obs: []
    outcome, _ = await adapter.auth_outcome(timeout=0.3)
    assert outcome != "exists"


def test_a_reset_continuation_belongs_to_this_jobs_earlier_run(service):
    from jobpilot.adapters.others import SuccessFactors

    with service.db.session() as s:
        s.add(Run(id="parked", job_id="job", state="waiting_browser", mode="submit", packet={}))
        s.add(Run(id="elsewhere", job_id="other", state="waiting_browser", mode="submit", packet={}))
        s.commit()
    adapter = SuccessFactors.__new__(SuccessFactors)
    adapter.service, adapter.job = service, {"id": "job", "url": JOB}
    adapter.accounts = SimpleNamespace(store=AccountStore(service, "alex@example.test", JOB))
    resume = {"kind": "password_reset", "source_run_id": "parked"}
    adapter.run_record = {"packet": {"email_wait_resume": resume}}
    assert adapter.reset_continuation()
    adapter.run_record = {"packet": {"email_wait_resume": {**resume, "source_run_id": "elsewhere"}}}
    assert not adapter.reset_continuation()
    adapter.run_record = {"packet": {"email_wait_resume": {**resume, "kind": "code"}}}
    assert not adapter.reset_continuation()
