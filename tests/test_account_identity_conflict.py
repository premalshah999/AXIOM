"""SuccessFactors' "Account Already Exists: There is already an account with the phone number ..." after the emailed
passcode (2026-10-30). The account is under another email, so no code, reset or sign-in with this one can reach it; the
run used to end as "the employer's password reset email did not arrive" after 20+ emails over days."""

from types import SimpleNamespace

from jobpilot.account_identity import conflict_on_page, result, stop_if_identity_conflict
from jobpilot.accounts import AccountStore
from jobpilot.accounts.overview import one
from jobpilot.accounts.store import save_owner_password
from jobpilot.autopilot import PERSIST_SKIP
from jobpilot.sensitive_ids import stop_if_sensitive

EMAIL = "applicant@example.test"
JOB_URL = "https://careers.examplesoft.test/job/Seattle-Engineer/210722-en"
PAGE_URL = "https://career55.sapsf.eu/career?company=examplesoftP2"
CONFLICT = {
    "url": PAGE_URL,
    "headings": ["Account Already Exists"],
    "text": "Account Already Exists There is already an account with the phone number 555-0100. "
    "Click Sign In to sign in to the account.",
    "fields": [],
    "controls": [{"id": "c1", "label": "Sign In"}],
}
JOB_PAGE = {"url": JOB_URL, "headings": ["Engineer"], "text": "Apply now", "fields": [], "controls": []}


def engine(service, **extra):
    return SimpleNamespace(
        service=service,
        job={"id": "j1", "company": "", "url": JOB_URL, "demo": False},
        armed=False,
        page=SimpleNamespace(url=PAGE_URL),
        emit=lambda *args, **kwargs: None,
        result=None,
        form=SimpleNamespace(resolver=SimpleNamespace(profile=SimpleNamespace(email=EMAIL))),
        **extra,
    )


def test_only_the_phone_number_conflict_page_is_recognised():
    assert conflict_on_page(CONFLICT)
    assert conflict_on_page({"text": "There is already an account with your mobile number."})
    assert not conflict_on_page(
        {"headings": ["Account Already Exists"], "text": "There is already an account with the email address you entered."}
    )
    assert not conflict_on_page({"text": "Already have an account? Sign in."})


async def test_the_conflict_page_stops_the_run_with_one_owner_question_and_marks_the_account(service):
    e = engine(service)
    assert await stop_if_identity_conflict(e, CONFLICT) is True
    assert e.result["state"] == "waiting_browser" and e.result["recovery_cause"] == "account_identity_conflict"
    assert e.result["recoverable"] is False and e.result["blocker_type"] == "account"
    # It names the employer (here its host: link-only jobs have no company) and says whose phone number it is, with no
    # phone number or guessed email in it.
    question = e.result["reviews"][0]["question"]
    assert "careers.examplesoft.test" in question and "phone number" in question and "different email" in question
    assert "555-0100" not in e.result["reason"] + question and "@" not in question
    assert PERSIST_SKIP.search(e.result["reason"])  # the autopilot never retries it
    store = AccountStore(service, EMAIL, JOB_URL)
    assert store.state(PAGE_URL) == "identity_conflict" and store.record(PAGE_URL)["identity_conflict_at"]


async def test_a_marked_account_stops_every_later_page_before_any_create_reset_or_code(service):
    store = AccountStore(service, EMAIL, JOB_URL)
    store.save(PAGE_URL, {"email": EMAIL, "password": "Example-Pass-1"}, "identity_conflict")
    for page in (JOB_PAGE, {**JOB_PAGE, "url": PAGE_URL, "text": "Sign in"}):
        e = engine(service)
        assert await stop_if_identity_conflict(e, page) is True and e.result["recovery_cause"] == "account_identity_conflict"


async def test_the_sensitive_page_hook_carries_the_stop_to_every_owned_page(service):
    e = engine(service)
    assert await stop_if_sensitive(e, CONFLICT) is True and e.result["recovery_cause"] == "account_identity_conflict"


async def test_an_account_this_email_signed_in_to_is_not_called_a_conflict(service):
    AccountStore(service, EMAIL, JOB_URL).save(PAGE_URL, {"email": EMAIL, "password": "Example-Pass-1"}, "authenticated")
    e = engine(service)
    assert await stop_if_identity_conflict(e, CONFLICT) is False and e.result is None


async def test_an_armed_submission_and_a_demo_job_are_never_stopped(service):
    armed = engine(service)
    armed.armed = True
    assert await stop_if_identity_conflict(armed, CONFLICT) is False
    demo = engine(service)
    demo.job["demo"] = True
    assert await stop_if_identity_conflict(demo, CONFLICT) is False


async def test_setup_accounts_shows_the_record_and_a_saved_password_ends_the_stop(service):
    store = AccountStore(service, EMAIL, JOB_URL)
    await stop_if_identity_conflict(engine(service), CONFLICT)
    row = one(service, store.key(PAGE_URL))
    assert row["status"] == "identity_conflict" and row["attention"] and "phone number" in row["detail"]
    assert save_owner_password(service, store.key(PAGE_URL), "Owner-Pass-22") is True
    assert store.state(PAGE_URL) == "password_reset" and "identity_conflict_at" not in store.record(PAGE_URL)


def test_the_result_names_the_company_when_the_job_has_one():
    assert "ExampleSoft" in result({"company": "ExampleSoft", "url": JOB_URL})["reviews"][0]["question"]
