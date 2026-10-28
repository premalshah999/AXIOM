"""iCIMS's candidate profile (ICE, iPipeline 2026-10-19): password boxes beside the resume and name fields, saved by
"Submit Profile". It classifies as account creation, so authenticate() hands it to on_form, which refused every
non-FORM page after its CAPTCHA check: Submit Profile was never pressed and the third pass reported "Could not sign
in". A person's run of the same flow (REI Systems, network-captured) went profile -> /eeo -> forms -> submit_apply."""

from unittest.mock import AsyncMock

from jobpilot.adapters.base import Step
from jobpilot.browser import form_shape
from support_icims import adapter

PROFILE = "https://careers-acme.icims.com/jobs/42/java-developer/candidate?from=login&hashed=1&uploadResume=1"
EEO = "https://careers-acme.icims.com/jobs/42/java-developer/eeo?back=1&in_iframe=1"


def page(url, fields, controls, heading):
    for f in fields:
        f.setdefault("value", "")
        f.setdefault("error", "")
        f.setdefault("automation_id", "")
    return {"url": url, "fields": fields, "controls": controls, "errors": [], "headings": [heading], "text": heading}


def profile():
    return page(
        PROFILE,
        [
            {"id": "resume", "type": "file", "label": "Resume", "required": False},
            {"id": "pw", "type": "password", "label": "Password", "required": True},
            {"id": "pw2", "type": "password", "label": "Confirm Password", "required": True},
            {"id": "first", "type": "text", "label": "First Name", "required": True, "value": "Alex"},
            {"id": "last", "type": "text", "label": "Last Name", "required": True, "value": "Doe"},
        ],
        [{"id": "save", "label": "Submit Profile", "href": ""}],
        "Candidate Profile",
    )


def eeo():
    return page(
        EEO,
        [{"id": "gender", "type": "select", "label": "Gender", "required": True}],
        [{"id": "next", "label": "Next", "href": ""}],
        "Equal Employment Opportunity",
    )


def flow(service, scans):
    value = adapter(service, profile())
    value.form.scan = AsyncMock(side_effect=scans)
    value.fill_page = AsyncMock(return_value={"ok": True, "problems": [], "verified_fields": 4})
    value.solve_captcha = AsyncMock(return_value=None)
    value.e.quality_gate = AsyncMock(return_value=None)
    return value


async def test_the_profile_classifies_as_account_creation(service):
    assert adapter(service, profile()).classify(profile()) == Step.CREATE_ACCOUNT


async def test_submit_profile_is_pressed_on_the_embedded_account_form(service):
    value = flow(service, [profile(), profile(), profile(), eeo()])
    assert await value.on_form(profile()) is None
    value.click.assert_awaited_once()
    assert value.click.await_args.args[0]["label"] == "Submit Profile"


async def test_a_page_the_captcha_callback_submitted_is_filled_before_any_click(service):
    # The site's hCaptcha callback posts the profile itself; EEO's Next must wait for EEO's own answers.
    value = flow(service, [profile(), profile(), eeo()])
    assert await value.on_form(profile()) is None
    value.click.assert_not_awaited()


def test_a_form_post_is_recorded_as_names_and_sizes_only():
    boundary = "----x"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"PersonProfileFields.FirstName\"\r\n\r\nAlex\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"PersonProfileFields.Password\"\r\n\r\nhunter2-secret\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"PortalProfileFields.Resume_File\"; filename=\"\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"rcf2072_File\"; filename=\"cv.pdf\"\r\n"
        "Content-Type: application/pdf\r\n\r\n%PDF-1\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    shape = form_shape(body, f"multipart/form-data; boundary={boundary}")
    assert shape == [
        ["PersonProfileFields.FirstName", 4],
        ["PersonProfileFields.Password", None],
        ["PortalProfileFields.Resume_File", "file::0"],
        ["rcf2072_File", "file:pdf:6"],
    ]
    assert "hunter2" not in repr(shape) and "Alex" not in repr(shape)
    assert form_shape(b"ps=1&Password=abc&behavior-type=other", "application/x-www-form-urlencoded") == [
        ["ps", 1], ["Password", None], ["behavior-type", 5]
    ]
    assert form_shape(None, "multipart/form-data; boundary=x") == []


def refused_profile():
    value = profile()
    value["errors"] = ["Error: Invalid Data Error. Please check all information before trying again."]
    return value


async def test_a_refused_profile_marks_the_flagged_fields_then_reports_the_refusal(service, monkeypatch):
    from types import SimpleNamespace

    from jobpilot.adapters.base import Adapter

    parent = AsyncMock(return_value=None)
    monkeypatch.setattr(Adapter, "on_create_account", parent)
    value = adapter(service, refused_profile())
    value.leave_saved_connect_profile = AsyncMock(return_value=False)
    frame = SimpleNamespace(evaluate=AsyncMock(return_value=["End Date (Month / Day / Year)"]))
    value.e.form.page.frames = [frame]
    events = []
    value.e.emit = lambda kind, message, data=None: events.append((kind, data))
    value.attempts["create"] = 1  # one Submit Profile was refused: answer the flagged fields and try once more
    assert await value.on_create_account(refused_profile()) is None
    parent.assert_awaited_once()
    assert ("employer_refused", {"error": refused_profile()["errors"][0], "flagged": ["End Date (Month / Day / Year)"]}) in events
    value.attempts["create"] = 2  # refused again: say why instead of "Could not sign in"
    result = await value.on_create_account(refused_profile())
    assert result["state"] == "waiting_browser" and result["blocker_type"] == "validation"
    assert "refused the profile" in result["reason"] and "End Date" in result["reason"]
    assert "sign in" not in result["reason"].lower()
    parent.assert_awaited_once()


async def test_an_email_already_in_use_signs_in_from_the_posting(service):
    obs = profile()
    obs["errors"] = ["This email address is already in use. Please login or use another email address."]
    value = adapter(service, obs)
    value.leave_saved_connect_profile = AsyncMock(return_value=False)
    value.e.form.page.goto = AsyncMock()
    value.form.settle = AsyncMock()
    assert await value.on_create_account(obs) is None
    assert "/jobs/login?loginOnly=1" in value.e.form.page.goto.await_args.args[0]
    assert value.accounts.record(value.job["url"]).get("state") == "exists"


async def test_an_email_step_that_keeps_returning_is_recorded_and_stopped(service):
    from types import SimpleNamespace

    obs = {"url": "https://eb-acme.icims.com/jobs/9917/ai-engineer/login?step=email", "errors": [], "headings": [],
           "text": "Enter Your Information", "controls": [],
           "fields": [{"id": "e", "name": "css_loginName", "type": "email", "label": "Email", "required": False, "value": ""}]}
    value = adapter(service, obs)
    page_text = "Enter Your Information Email alex@example.test Phone 2405551234 We could not verify you"
    frame = SimpleNamespace(locator=lambda sel: SimpleNamespace(inner_text=AsyncMock(return_value=page_text)),
                            content=AsyncMock(return_value="<input name='css_loginName'>"))
    value.e.form.page.frames = [frame]
    events = []
    value.e.emit = lambda kind, message, data=None: events.append((kind, data))
    assert await value.email_step_repeat(obs) is None and await value.email_step_repeat(obs) is None
    result = await value.email_step_repeat(obs)
    assert result["state"] == "waiting_browser" and "We could not verify you" in result["reason"]
    recorded = next(data for kind, data in events if kind == "email_step_repeat")
    assert "alex@example.test" not in str(recorded) and "2405551234" not in str(recorded)


async def test_an_email_and_phone_already_in_use_go_to_the_returning_candidate_login(service):
    obs = {"url": "https://eb-acme.icims.com/jobs/9917/ai-engineer/login?step=email", "errors": [], "headings": [],
           "text": "Welcome page The email address and phone number are already in use. Please login or use another "
                   "email address and phone number. Enter Your Information", "controls": [],
           "fields": [{"id": "e", "name": "css_loginName", "type": "email", "label": "Email", "required": False, "value": ""}]}
    value = adapter(service, obs)
    value.job["url"] = "https://eb-acme.icims.com/jobs/9917/ai-engineer/job"
    value.e.form.page.goto = AsyncMock()
    value.form.settle = AsyncMock()
    assert await value.returning_candidate_login(obs) is True
    value.e.form.page.goto.assert_awaited_once()
    assert value.e.form.page.goto.await_args.args[0] == "https://eb-acme.icims.com/jobs/login?loginOnly=1&in_iframe=1"
    assert value.accounts.record(value.job["url"]).get("state") == "exists"
    assert await value.returning_candidate_login(obs) is False  # once per run


async def test_an_exhausted_profile_reports_the_form_problem_not_a_failed_sign_in(service):
    # Schwab and Iridium (2026-10-21) both ended "Could not sign in" over a graduation year and a resume loop.
    value = flow(service, [profile()])
    value.attempts["create"] = 2
    value.form.last_problems = ["Value not accepted: Graduation Date Year"]
    result = await value.authenticate(profile(), "create")
    assert result["state"] == "waiting_browser" and result["blocker_type"] == "validation"
    assert "Graduation Date Year" in result["reason"]


async def test_the_resume_is_attached_before_the_password_is_typed(service):
    # iCIMS posts the upload and reloads the profile, clearing typed passwords (Iridium 2026-10-21).
    from types import SimpleNamespace

    order = []
    value = flow(service, [profile(), profile(), profile(), eeo()])
    value.form.uploaded = False
    value.form.fill_current = AsyncMock(side_effect=lambda **kwargs: order.append("upload"))
    value.form.resolver = SimpleNamespace(resolve=AsyncMock(return_value=[]))
    for key in ("pw", "pw2"):
        value.locators[key].fill = AsyncMock(side_effect=lambda *args, **kwargs: order.append("password"))
        value.locators[key].evaluate = AsyncMock()
    value.on_form = AsyncMock(return_value=None)  # The embedded form is handed over; its handling is tested above.
    await value.authenticate(profile(), "create")
    assert order[0] == "upload" and "password" in order
    value.on_form.assert_awaited_once()


async def test_submit_profile_passes_the_form_guard_as_a_vouched_step(service):
    # America's Tire, Noblis 2026-10-26: after the profile's CAPTCHA the step click reached the form guard, which saw
    # only the word "submit" and refused it ("Use the submit action for final submission").
    from types import SimpleNamespace

    from jobpilot.adapters.base import Adapter

    value = adapter(service, profile())
    value.committed = False
    value.dismiss_overlays = AsyncMock()
    value.form.click = AsyncMock(return_value=True)
    value.form.page = SimpleNamespace(url=PROFILE, context=SimpleNamespace(pages=[]))
    value.emit = lambda *args, **kwargs: None
    await Adapter.click(value, {"id": "save", "label": "Submit Profile", "href": ""}, step=True)
    assert value.form.click.await_args.kwargs["auth_control"] is True


def returning_profile():
    """A candidate signed in through Central: the profile still shows "Create a login", saved by "Update Profile"."""
    value = profile()
    value["fields"].insert(1, {"id": "login", "type": "text", "label": "Login", "required": True, "value": "", "error": "", "automation_id": ""})
    value["controls"] = [
        {"id": "later", "label": "Finish Later", "href": "", "aria_label": ""},
        {"id": "save", "label": "Update Profile", "href": "", "aria_label": ""},
    ]
    return value


async def test_update_profile_hands_the_embedded_account_form_to_on_form(service):
    # Rivian 2026-10-24..27: "No sign-in control found", then the guided agent's clicks never posted the profile.
    from types import SimpleNamespace

    value = flow(service, [returning_profile()] * 3)
    value.accounts.mark(value.job["url"], "authenticated")
    value.form.resolver = SimpleNamespace(resolve=AsyncMock(return_value=[]))
    for key in ("pw", "pw2"):
        value.locators[key].evaluate = AsyncMock()
    value.locators["login"] = SimpleNamespace(fill=AsyncMock(), blur=AsyncMock())
    value.form.frames["login"] = SimpleNamespace(url=PROFILE)
    value.e.profile = SimpleNamespace(account_username="")
    value.on_form = AsyncMock(return_value=None)
    assert value.auth_button(returning_profile(), "create")["label"] == "Update Profile"
    assert await value.authenticate(returning_profile(), "create") is None
    value.on_form.assert_awaited_once()
    value.click.assert_not_awaited()  # never clicked as a sign-in: on_form solves the CAPTCHA and saves the profile
    assert value.accounts.record(PROFILE)["state"] == "authenticated"  # a signed-in account is not downgraded
