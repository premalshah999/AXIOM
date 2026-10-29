"""TikTok's careers site (2026-10-27): "Forgot password?" opens /reset-password with "Enter your email", "Set your
password" and Continue. The new-password box read as the sign-in form still shown, so four runs reported that the reset
page "did not open", and reopened runs typed the old password into it as a sign-in. A reset form that takes the new
password with the email is a reset step: the shared password goes in with the request, and the emailed code confirms
it."""

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jobpilot.accounts import Accounts
from jobpilot.adapters.base import Adapter, Step
from jobpilot.schemas import Profile

SITE = "https://careers.example.test"


def field(key, label, kind="text"):
    return {"id": key, "label": label, "type": kind, "automation_id": "", "required": False, "value": "",
            "options": [], "maxlength": -1}


def control(key, label, href=""):
    return {"id": key, "label": label, "href": href, "automation_id": "", "aria_label": ""}


def page(path, fields, controls, text="", headings=("Sign in",)):
    return {"url": SITE + path, "fields": fields, "controls": controls, "errors": [], "headings": list(headings),
            "text": text, "dialogs": []}


RESET = page(
    "/reset-password",
    [field("lang", "English", "select"), field("email", "Enter your email"), field("pw", "Set your password", "password")],
    [control("nav", "Sign in", "/login"), control("go", "Continue")],
    "Sign in Reset Password Your password must be a combination of numbers and English letters",
)
SIGN_IN = page(
    "/login",
    [field("email", "Email"), field("pw", "Password", "password")],
    [control("forgot", "Forgot password?", "/reset-password"), control("go", "Sign in"), control("new", "Create account")],
    "Sign in Forgot password? Create account",
)
CREATE = page(
    "/create-account",
    [field("email", "Email"), field("pw", "Set your password", "password")],
    [control("go", "Create account"), control("nav", "Sign in", "/login")],
    "Create account",
)
RESET_LINK_SIGN_IN = page(
    "/login",
    [field("email", "Email"), field("pw", "Password", "password")],
    [control("reset", "Reset password", "/account/recover"), control("go", "Sign in")],
    "Sign in Reset password",
)
CODE = page(
    "/reset-password",
    [field("code", "Verification code")],
    [control("go", "Continue")],
    "Enter the verification code we sent to your email",
)


def adapter(service):
    value = Adapter.__new__(Adapter)
    locators = {}

    def locator(key):
        return locators.setdefault(key, SimpleNamespace(
            fill=AsyncMock(), blur=AsyncMock(), press_sequentially=AsyncMock(), evaluate=AsyncMock(), click=AsyncMock(),
        ))

    form = SimpleNamespace(scan=AsyncMock(), settle=AsyncMock(), locator=locator,
                           frames={key: SimpleNamespace(url=SITE + "/reset-password") for key in ("email", "pw", "code")},
                           page=SimpleNamespace(url=SITE + "/reset-password"))
    events = []
    value.e = SimpleNamespace(form=form, emit=lambda *args: events.append(args))
    value.service, value.config = service, service.config
    value.job = {"id": "job", "url": SITE + "/search/42", "ats": "custom"}
    value.accounts = Accounts(service, Profile(email="alex@example.test", allow_account_creation=True), value.job["url"])
    value.attempts = Counter()
    value.committed = False
    value.challenge = {"id": "mail"}
    value.click = AsyncMock()
    value.arm_mail = AsyncMock(return_value=True)
    value.after_auth_click = AsyncMock(return_value=None)
    value.release_mail = lambda: None
    value.finished = []
    value.finish_mail = lambda state, reason: value.finished.append(state)
    value.defer = lambda reason, *a, **k: {"state": k.get("state", "needs_review"), "reason": reason}
    value.return_to_application = AsyncMock()
    value.locators, value.events = locators, events
    return value


def test_a_reset_form_with_its_own_password_box_is_a_reset_step(service):
    value = adapter(service)
    assert value.one_page_reset(RESET)
    assert value.classify_page(RESET) == Step.RESET_PASSWORD
    # The sign-in page offers the reset; a sign-up page and a sign-in page's own "Reset password" link are no reset.
    for other in (SIGN_IN, CREATE, RESET_LINK_SIGN_IN):
        assert not value.one_page_reset(other), other["url"]
    assert value.classify_page(SIGN_IN) == Step.SIGN_IN


async def test_the_one_page_reset_sends_the_new_password_and_confirms_it_with_the_emailed_code(service):
    value = adapter(service)
    password = value.accounts.store.shared()["password"]
    # After Continue the form gives way to the code step; the code is accepted and the sign-in page returns.
    pages = [CODE, CODE, CODE, SIGN_IN]
    value.form.scan.side_effect = lambda: pages.pop(0) if len(pages) > 1 else pages[0]
    value.wait_email = AsyncMock(return_value={"kind": "code", "value": "123456"})
    value.auth_outcome = AsyncMock(return_value=("unknown", ""))
    result = await value.request_reset(RESET, "Signing in did not succeed")
    assert result is None
    value.locators["email"].fill.assert_awaited_with("alex@example.test", timeout=4000)
    value.locators["pw"].press_sequentially.assert_awaited_with(password, delay=25, timeout=15000)
    value.locators["code"].fill.assert_awaited_with("123456", timeout=4000)
    value.wait_email.assert_awaited_with("code")
    # The request window takes the emailed code (TikTok, 2026-10-28); a password_reset window refused it.
    value.arm_mail.assert_awaited_with("auto")
    assert value.click.await_args_list[0].args[0]["label"] == "Continue"
    record = value.accounts.record(SITE + "/reset-password")
    assert record["state"] == "password_reset" and value.finished == ["verified"]
    assert value.accounts.saved(SITE + "/login")["password"] == password


async def test_a_puzzle_left_unsolved_stops_the_reset_with_its_reason(service):
    value = adapter(service)
    value.form.scan.return_value = RESET
    value.solve_inpage_puzzle = AsyncMock(return_value={"state": "waiting_browser", "reason": "Puzzle kept"})
    result = await value.request_reset(RESET, "Signing in did not succeed")
    assert result["state"] == "waiting_browser" and "picture puzzle" in result["reason"]
    assert value.finished == ["failed"]


async def test_a_sign_in_page_is_still_reported_as_a_reset_that_did_not_open(service):
    value = adapter(service)
    result = await value.request_reset(SIGN_IN, "Signing in did not succeed")
    assert "did not open" in result["reason"]
    value.click.assert_not_awaited()


async def test_reset_uses_configured_username_and_email_for_their_respective_fields(service):
    value = adapter(service)
    value.e.profile = SimpleNamespace(account_username="candidate123")
    reset = page("/forgot", [field("user", "User Name"), field("email", "Email Address", "email")],
                 [control("go", "Send")], "Forgot password", ())
    value.reset_outcome = AsyncMock(return_value=("sent", ""))
    value.complete_password_reset = AsyncMock(return_value=None)
    await value.request_reset(reset, "refused")
    value.locators["user"].fill.assert_awaited_once_with("candidate123", timeout=4000)
    value.locators["email"].fill.assert_awaited_once_with("alex@example.test", timeout=4000)


TIKTOK_CODE = page(
    "/reset-password",
    [field(f"d{n}", "") for n in range(6)],
    [control("resend", "Resend")],
    "Verify Email Account Enter the code in the email sent to alex@example.test Didn't receive the code? Resend",
)


async def test_bare_digit_boxes_under_enter_the_code_take_the_emailed_code(service):
    value = adapter(service)
    for n in range(6):
        value.form.frames[f"d{n}"] = SimpleNamespace(url=SITE + "/reset-password")
    pages = [TIKTOK_CODE, TIKTOK_CODE, TIKTOK_CODE, SIGN_IN]
    value.form.scan.side_effect = lambda: pages.pop(0) if len(pages) > 1 else pages[0]
    value.wait_email = AsyncMock(return_value={"kind": "code", "value": "754570"})
    value.auth_outcome = AsyncMock(return_value=("unknown", ""))
    assert await value.confirm_one_page_reset(SITE + "/reset-password", {"email": "alex@example.test",
                                                                         "password": "Synthetic9!x"}) is None
    value.wait_email.assert_awaited_with("code")
    typed = [value.locators[f"d{n}"].press_sequentially.await_args.args[0] for n in range(6)]
    assert "".join(typed) == "754570"
    assert value.accounts.record(SITE + "/login")["state"] == "password_reset"
