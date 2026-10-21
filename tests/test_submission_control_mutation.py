"""Submission permission follows the current action, including shadow DOM."""

from unittest.mock import AsyncMock

import pytest

from jobpilot.answers import Resolver
from jobpilot.browser import GUARD
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile


@pytest.mark.browser
async def test_concurrent_workers_receive_distinct_default_context_tabs(config, tmp_path):
    import asyncio
    from playwright.async_api import async_playwright
    from jobpilot.desktop_cdp import new_worker_page

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(tmp_path / "synthetic-chrome"),
            executable_path=config.chromium_path or None,
            headless=True,
            args=["--no-sandbox"],
        )
        try:
            existing = list(context.pages)
            pages = await asyncio.gather(*[new_worker_page(context.browser, context) for _ in range(6)])
            assert len(set(pages)) == 6
            assert not set(pages).intersection(existing)
            await asyncio.gather(*[p.set_content(f"<h1>Owner {i}</h1>") for i, p in enumerate(pages)])
            assert await asyncio.gather(*[p.locator("h1").inner_text() for p in pages]) == [
                f"Owner {i}" for i in range(6)
            ]
            assert all(not p.is_closed() for p in existing)
        finally:
            await context.close()


async def test_desktop_connection_reuses_live_transport_and_retries_discovery(monkeypatch):
    from types import SimpleNamespace
    import httpx
    from jobpilot import desktop_cdp

    request = httpx.Request("GET", "http://localhost:9224/json/version")
    get = AsyncMock(
        side_effect=[
            httpx.ReadTimeout("local timeout"),
            httpx.Response(
                200,
                request=request,
                json={"webSocketDebuggerUrl": "ws://localhost:9224/devtools/browser/fixture"},
            ),
        ]
    )
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(get=get)
    monkeypatch.setattr(desktop_cdp.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(desktop_cdp.socket, "gethostbyname", lambda host: "127.0.0.1")
    browser = SimpleNamespace(is_connected=lambda: True)
    connect = AsyncMock(return_value=browser)
    pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
    monkeypatch.setattr(
        desktop_cdp, "async_playwright", lambda: SimpleNamespace(start=AsyncMock(return_value=pw))
    )
    monkeypatch.setattr(desktop_cdp, "_connections", {})
    first = await desktop_cdp.connected_browser("http://localhost:9224", discover=True)
    second = await desktop_cdp.connected_browser("http://localhost:9224", discover=True)
    assert first == second == (browser, "ws://127.0.0.1:9224/devtools/browser/fixture")
    assert get.await_count == 2
    connect.assert_awaited_once()


@pytest.mark.parametrize(
    "label",
    ["Explore All Jobs", "Explore All Jobs Emphasized", "Search Jobs", "Careers Home", "Create Alert"],
)
async def test_application_owner_cannot_wander_into_global_job_search(page, service, tmp_path, label):
    await page.set_content(f'<button onclick="window.catalogOpened=true">{label}</button>')
    form = FormSession(
        page, Resolver(Profile(), service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
    )
    await form.scan()
    control = next(iter(form.buttons))
    with pytest.raises(ValueError, match="return_to_job"):
        await form.click(control)
    assert await page.evaluate("Boolean(window.catalogOpened)") is False


async def test_job_alert_frequency_is_not_an_application_question(page, service, tmp_path):
    await page.set_content("""<label>Select how often (in days) to receive an alert:
        <input required type=number></label><button>Create Alert</button>
        <label>First Name<input required></label><button>Apply Now</button>""")
    form = FormSession(
        page, Resolver(Profile(), service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
    )
    obs = await form.scan()
    assert [f["label"] for f in obs["fields"]] == ["First Name"]
    assert any(b["label"] == "Apply Now" for b in obs["controls"])


async def test_field_click_allows_slow_dom_preparation_within_its_control_deadline(monkeypatch):
    import asyncio
    from jobpilot import widgets

    class Handle:
        def __init__(self):
            self.prepared = False
            self.clicks = 0

        async def evaluate(self, script, args=None):
            if not self.prepared:
                await asyncio.sleep(1.1)
                self.prepared = True
            return None

        async def click(self, **kwargs):
            assert self.prepared
            self.clicks += 1

    monkeypatch.setattr(widgets, "ensure_uncovered", AsyncMock())
    handle = Handle()
    await widgets.field_click(handle, timeout=4000)
    assert handle.clicks == 1


@pytest.mark.parametrize("change", ["", "profile", "knowledge", "question", "value", "evidence"])
async def test_cited_text_readback_reuses_only_unchanged_supported_answer(page, service, tmp_path, change):
    await page.set_content(
        "<label>Describe your relevant experience<textarea required>Fixture response</textarea></label>"
    )
    resolver = Resolver(Profile(), service.db, service.config)
    form = FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    field = next(iter(form.fields.values()))
    form.record_answer(field, field["value"], ["knowledge:fixture-confirmed"], verified=True)
    if change == "profile":
        resolver.profile.facts["fixture_fact"] = True
    elif change == "knowledge":
        from types import SimpleNamespace

        resolver.kb = SimpleNamespace(entries=[Profile()], lookup=lambda *args: None)
    elif change == "question":
        field["hint"] = "Include an additional employer-specific requirement"
    elif change == "value":
        field["value"] = "Unverified replacement"
    elif change == "evidence":
        next(iter(form.ledger.values()))["evidence_ids"] = []
    assert form.preselected(field, False) is (not change)


async def test_provider_blocked_required_question_is_visible_in_verification_report(page, service, tmp_path):
    from jobpilot.models import AnswerModelUnavailable

    await page.set_content(
        "<label>Describe your relevant experience<textarea required>Unreviewed native draft</textarea></label>"
    )
    resolver = Resolver(Profile(), service.db, service.config)
    resolver.resolve = AsyncMock(
        side_effect=lambda fields: [
            resolver._invalid(
                field, "Answer model could not complete: AnswerModelUnavailable", "site_transient"
            )
            for field in fields
        ]
    )
    events = []
    form = FormSession(
        page, resolver, tmp_path / "resume.pdf", lambda kind, message, data=None: events.append((kind, data))
    )
    form.uploaded = form.upload_verified = True
    form.settle = AsyncMock()
    with pytest.raises(AnswerModelUnavailable):
        await form.fill_current()
    report = next(data for kind, data in reversed(events) if kind == "verification")
    assert report["ok"] is False
    assert report["pending"][0]["question"] == "Describe your relevant experience"
    assert report["pending"][0]["blocker_type"] == "site_transient"


@pytest.mark.parametrize("required", [False, True])
async def test_native_prefill_cannot_override_missing_personal_disclosure(page, service, tmp_path, required):
    label = "Are there any circumstances, such as any pending or completed disciplinary action against you, whether by the Teaching Regulation Agency or otherwise, that could cause reputation damage?"
    await page.set_content(
        f"<label>{label}<select {'required' if required else ''}><option>No</option><option>Yes</option></select></label>"
    )
    resolver = Resolver(Profile(facts={"criminal_conviction": False}), service.db, service.config)
    form = FormSession(page, resolver, tmp_path / "resume.pdf", lambda *args: None)
    form.accept_prefilled = True
    form.uploaded = form.upload_verified = True
    form.settle = AsyncMock()
    await form.scan()
    field = next(iter(form.fields.values()))
    form.record_answer(field, "No", ["fact:criminal_conviction"], verified=True)
    assert not form.preselected(field, False)
    report = await form.fill_current()
    assert report["ok"] is False
    assert report["pending"][0]["blocker_type"] == "missing_fact"
    assert report["pending"][0]["required"] is True


@pytest.mark.parametrize(
    "control",
    [
        "<button role=combobox aria-haspopup=listbox>Select One</button>",
        '<textarea aria-label="Employment gaps"></textarea>',
        '<select aria-label="Authorization"><option>Yes</option></select>',
    ],
)
async def test_workday_question_draft_is_ready_without_contact_inputs(page, control):
    from types import SimpleNamespace
    from jobpilot.workday import WorkdayAuth

    await page.set_content("<h2>Application Questions</h2>" + control + "<button>Next</button>")
    auth = WorkdayAuth(SimpleNamespace(page=page))
    assert await auth.form_ready()
    await page.locator("body").evaluate("e => e.insertAdjacentHTML('beforeend', '<input type=password>')")
    assert not await auth.form_ready()


async def test_acknowledgement_checkbox_uses_state_instead_of_its_caption(service):
    label = "I understand that my application will not be considered if I do not provide a resume or career profile at time of application."
    resolver = Resolver(Profile(accept_all_application_terms=True), service.db, service.config)
    field = {
        "id": "ack",
        "label": label,
        "type": "checkbox",
        "required": True,
        "options": [label],
        "value": "false",
        "valid": True,
    }
    answer = (await resolver.resolve([field]))[0]
    assert answer.disposition == "answer" and answer.value == "true"
    assert answer.evidence_ids == ["policy:consent"]


async def test_employer_opt_help_reaches_the_owned_question_observation(page, service, tmp_path):
    await page.set_content("""<label for=sponsor>Will you require sponsorship?</label>
      <select id=sponsor aria-describedby=definition><option>Yes</option><option>No</option></select>
      <p id=definition>Select YES if you are currently on OPT.</p>
      <p id=unrelated>Another question's help</p>""")
    form = FormSession(
        page, Resolver(Profile(), service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
    )
    obs = await form.scan()
    f = next(f for f in obs["fields"] if f["type"] == "select")
    assert f["hint"] == "Select YES if you are currently on OPT."
    assert "Another question" not in f["hint"]


async def test_ashby_nested_yesno_uses_its_question_instead_of_first_country_title(page, service, tmp_path):
    await page.set_content("""<div class=ashby-application-form-field-entry>
      <div class=ashby-application-form-question-title>Country*</div><input aria-label=Country>
      <div class=ashby-application-form-question-title>Are you authorized to work in the United States?*</div>
      <div class=ashby-application-form-input-yesno><button aria-pressed=false>Yes</button><button aria-pressed=false>No</button></div>
      <div class=ashby-application-form-question-title>Will you require sponsorship?*</div>
      <div class=ashby-application-form-input-yesno><button aria-pressed=false>Yes</button><button aria-pressed=false>No</button></div>
      </div>""")
    form = FormSession(
        page, Resolver(Profile(), service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
    )
    obs = await form.scan()
    questions = [f["label"] for f in obs["fields"] if f["type"] == "buttonchoice"]
    assert questions == ["Are you authorized to work in the United States?", "Will you require sponsorship?"]


async def test_ashby_question_descriptions_are_scoped_to_each_control(page, service, tmp_path):
    await page.set_content("""<div class=ashby-application-form-field-entry>
      <label class=ashby-application-form-question-title>Authorization</label>
      <div class=ashby-application-form-question-description>Are you legally authorized to work in the United States?</div>
      <div class=ashby-application-form-input-yesno><button aria-pressed=false>Yes</button><button aria-pressed=false>No</button></div>
      <label class=ashby-application-form-question-title>Sponsorship</label>
      <div class=ashby-application-form-question-description>Select YES if you are currently on OPT.</div>
      <div class=ashby-application-form-input-yesno><button aria-pressed=false>Yes</button><button aria-pressed=false>No</button></div>
      </div>""")
    form = FormSession(
        page, Resolver(Profile(), service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
    )
    obs = await form.scan()
    questions = [f for f in obs["fields"] if f["type"] == "buttonchoice"]
    assert [f["hint"] for f in questions] == [
        "Are you legally authorized to work in the United States?",
        "Select YES if you are currently on OPT.",
    ]


async def test_workday_language_menu_never_becomes_an_application_action(page, service, tmp_path):
    await page.set_content("""<div data-automation-id=languageSelectorButton>
      <button>English</button><div data-automation-id=multiselectInputContainer><input role=combobox></div>
      </div><label>Name<input required></label><button>Save and Continue</button>""")
    form = FormSession(
        page, Resolver(Profile(), service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
    )
    obs = await form.scan()
    assert [f["label"] for f in obs["fields"]] == ["Name"]
    assert [b["label"] for b in obs["controls"]] == ["Save and Continue"]


@pytest.mark.parametrize("mismatch", ["", "job", "resume", "origin"])
async def test_closed_tab_route_is_encrypted_and_bound_to_application(page, service, mismatch):
    from jobpilot.browser import BrowserEngine

    await page.route("**/*", lambda route: route.fulfill(body="<h1>Verification</h1>"))
    url = "https://fixture.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/1?private=token"
    await page.goto(url)
    engine = BrowserEngine(service, "synthetic")
    engine.job = {"id": "job", "url": url}
    engine.run_record = {"packet": {"resume_sha": "pinned"}}
    await page.evaluate("sessionStorage.setItem('native-session', 'private-session-value')")
    encrypted = await engine.seal_desktop_route(page)
    assert url not in encrypted
    assert "private-session-value" not in encrypted
    assert engine.desktop_resume_state({"resume_route": encrypted})["session_storage"] == {
        "native-session": "private-session-value"
    }
    if mismatch == "job":
        engine.job["id"] = "another"
    elif mismatch == "resume":
        engine.run_record["packet"]["resume_sha"] = "different"
    elif mismatch == "origin":
        engine.job["url"] = "https://other.oraclecloud.com/job/1"
    assert engine.desktop_resume_route({"resume_route": encrypted}) == (None if mismatch else url)


@pytest.mark.parametrize("protected", ["", "verification_only", "email_wait_resume", "armed", "storage"])
def test_oracle_memory_only_closed_draft_reauthenticates_but_protected_routes_survive(service, protected):
    from jobpilot.browser import BrowserEngine

    engine = BrowserEngine(service, "synthetic")
    root = "https://fixture.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/1"
    route = root + "/apply/section/2"
    engine.job = {"id": "job", "url": root, "ats": "oracle"}
    engine.run_record = {"packet": {"resume_sha": "pinned", protected: True}}
    saved = {
        "resume_route": service.mail.vault.seal(
            {
                "url": route,
                "job_id": "job",
                "resume_sha": "pinned",
                "session_storage": {"native": "fixture"} if protected == "storage" else {},
            }
        ),
        "armed": protected == "armed",
    }
    assert engine.desktop_resume_route(saved) == (route if protected else None)


async def test_reopened_tab_restores_native_session_once_without_replaying_rotated_state(page, service):
    from jobpilot.browser import BrowserEngine

    await page.route("**/*", lambda route: route.fulfill(body="<h1>Application</h1>"))
    engine = BrowserEngine(service, "synthetic")
    url = "https://fixture.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/1"
    await engine.open_application_route(page, url, {"session_storage": {"native-session": "saved"}})
    assert await page.evaluate("sessionStorage.getItem('native-session')") == "saved"
    await page.evaluate("sessionStorage.setItem('native-session','rotated')")
    await page.goto(url + "/apply/section/2")
    assert await page.evaluate("sessionStorage.getItem('native-session')") == "rotated"
    await page.evaluate("sessionStorage.clear()")
    await page.reload()
    assert await page.evaluate("sessionStorage.length") == 0


async def test_workday_auth_label_mutation_cannot_submit(page):
    from types import SimpleNamespace
    from jobpilot.workday import WorkdayAuth

    await page.set_content("""<form onsubmit="event.preventDefault();window.sent=true">
      <button data-automation-id=signInSubmitButton onclick="this.textContent='Submit application'">Sign In</button></form>""")
    await page.evaluate(GUARD)
    auth = WorkdayAuth(SimpleNamespace(page=page, emit=lambda *args: None))
    await auth.click(page.locator("button"), auth_submit=True)
    assert not await page.evaluate("!!window.sent")
    assert await page.evaluate("!document.querySelector('button').dataset.jpAuthControl")


async def test_workday_auth_permission_cannot_authorize_another_submitter(page):
    from types import SimpleNamespace
    from jobpilot.workday import WorkdayAuth

    await page.set_content("""<form onsubmit="event.preventDefault();window.sent=true">
      <button id=auth data-automation-id=signInSubmitButton onclick="event.preventDefault();this.form.requestSubmit(document.querySelector('#final'))">Submit</button>
      <button id=final>Submit application</button></form>""")
    await page.evaluate(GUARD)
    auth = WorkdayAuth(SimpleNamespace(page=page, emit=lambda *args: None))
    await auth.click(page.locator("#auth"), auth_submit=True)
    assert not await page.evaluate("!!window.sent")


async def test_step_permission_cannot_authorize_final_button(page, service, config, tmp_path):
    await page.set_content('<button type=button onclick="window.sent=true">Submit application</button>')
    form = FormSession(
        page, Resolver(Profile(), service.db, config), tmp_path / "resume.pdf", lambda *a: None
    )
    obs = await form.scan()
    with pytest.raises(ValueError, match="submit action"):
        await form.click(obs["controls"][0]["id"], step_control=True)
    assert not await page.evaluate("!!window.sent")


async def test_continue_mutating_before_click_cannot_submit(page, service, config, tmp_path, monkeypatch):
    await page.set_content(
        '<form onsubmit="event.preventDefault();window.sent=true"><button>Save and Continue</button></form>'
    )
    await page.evaluate(GUARD)
    form = FormSession(
        page, Resolver(Profile(), service.db, config), tmp_path / "resume.pdf", lambda *a: None
    )
    obs = await form.scan()

    async def mutate_then_click(handle, **kwargs):
        await handle.evaluate("e=>e.textContent='Submit application'")
        await handle.click()

    monkeypatch.setattr("jobpilot.forms.widgets.safe_click", mutate_then_click)
    monkeypatch.setattr(form, "settle", AsyncMock())
    await form.click(obs["controls"][0]["id"], step_control=True)
    assert not await page.evaluate("!!window.sent")
    assert await page.evaluate(
        "!document.querySelector('button').dataset.jpAuthControl && !document.querySelector('form').__jpAllowedControl"
    )


async def test_label_mutated_in_click_handler_still_cannot_submit(page):
    await page.set_content("""<form onsubmit="event.preventDefault();window.sent=true">
      <button onclick="this.textContent='Submit application'">Save and Continue</button></form>""")
    await page.evaluate(GUARD)
    await page.evaluate("""() => {
      const b=document.querySelector('button');b.dataset.jpAuthControl='true';
      b.dataset.jpControlLabel='Save and Continue';b.dataset.jpFinalAuth='false';
      document.querySelector('form').__jpAllowedControl=b;
    }""")
    await page.get_by_role("button").click()
    assert not await page.evaluate("!!window.sent")


async def test_shadow_final_button_is_blocked_until_armed(page):
    await page.set_content("<div id=host></div>")
    await page.evaluate("""() => {
      const root=document.querySelector('#host').attachShadow({mode:'open'});
      root.innerHTML='<button type=button>Submit application</button>';
      root.querySelector('button').onclick=()=>window.sent=true;
    }""")
    await page.evaluate(GUARD)
    await page.get_by_role("button").click()
    assert not await page.evaluate("!!window.sent")
    await page.evaluate("window.__jpArmed=true")
    await page.get_by_role("button").click()
    assert await page.evaluate("!!window.sent")


async def test_account_code_submit_remains_available(page, service, config, tmp_path, monkeypatch):
    await page.set_content(
        '<form onsubmit="event.preventDefault();window.verified=true"><button>Submit code</button></form>'
    )
    await page.evaluate(GUARD)
    form = FormSession(
        page, Resolver(Profile(), service.db, config), tmp_path / "resume.pdf", lambda *a: None
    )
    monkeypatch.setattr(form, "settle", AsyncMock())
    obs = await form.scan()
    await form.click(obs["controls"][0]["id"], auth_control=True)
    assert await page.evaluate("!!window.verified")


async def test_form_marker_does_not_authorize_another_submitter(page):
    await page.set_content("""<form onsubmit="event.preventDefault();window.sent=true">
      <button type=button>Continue</button><button>Submit application</button></form>""")
    await page.evaluate(GUARD)
    await page.evaluate("""() => {
      const f=document.querySelector('form'),b=f.querySelector('button');
      b.dataset.jpAuthControl='true';b.dataset.jpControlLabel='Continue';
      f.__jpAllowedControl=b;f.dataset.jpAuthControl='true';
    }""")
    await page.evaluate(
        "document.querySelector('form').requestSubmit(document.querySelectorAll('button')[1])"
    )
    assert not await page.evaluate("!!window.sent")
