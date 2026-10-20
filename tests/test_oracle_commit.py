"""Read back only the same owned field when a native selection remounts it."""

from unittest.mock import AsyncMock

import pytest

from jobpilot.widgets_ats import _wait_committed
from jobpilot.browser import BrowserEngine
from jobpilot.forms import FormSession
from jobpilot.answers import Resolver
from jobpilot.schemas import Profile


async def test_oracle_email_preview_popup_does_not_replace_application_owner(page, service, tmp_path):
    root = "https://fixture.oraclecloud.com/hcmUI/CandidateExperience"

    async def route(request):
        body = (
            "<h1>Preview</h1>"
            if request.request.url.endswith("ircEmailPreview")
            else f"""<button onclick="window.open('{root}/ircEmailPreview');document.querySelector('h1').textContent='Enter code'">Continue</button><h1>Email entry</h1>"""
        )
        await request.fulfill(status=200, content_type="text/html", body=body)

    await page.context.route("**/*", route)
    await page.goto(root + "/en/sites/CX/job/1/apply/email")
    form = FormSession(
        page, Resolver(Profile(), service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
    )
    obs = await form.scan()
    await form.click(obs["controls"][0]["id"], auth_control=True)
    assert form.page is page
    assert await page.locator("h1").inner_text() == "Enter code"
    assert len(page.context.pages) == 1


@pytest.mark.parametrize(
    "component,accepted", [("resume-attachment", True), ("cover-letter-attachment", False)]
)
async def test_native_filename_button_proves_only_its_resume_component(
    page, service, tmp_path, component, accepted
):
    resume = tmp_path / "Fixture_Resume.pdf"
    resume.write_bytes(b"synthetic-pinned-resume")
    await page.set_content(
        f'<div class="{component}"><button>{resume.name}</button><button>Remove</button></div>'
    )
    form = FormSession(page, Resolver(Profile(), service.db, service.config), resume, lambda *a: None)
    form.uploaded = True
    assert await form.accepted_resume_tile() is accepted


@pytest.mark.parametrize(
    "component,filled,error,accepted",
    [
        ("resume-upload-button", True, False, True),
        ("cover-letter-upload-button", True, False, False),
        ("resume-upload-button", False, False, False),
        ("resume-upload-button", True, True, False),
    ],
)
async def test_oracle_native_accepted_resume_has_no_visible_label(
    page, service, tmp_path, component, filled, error, accepted
):
    resume = tmp_path / "Fixture_Resume.pdf"
    resume.write_bytes(b"synthetic-pinned-resume")
    tile = "attachment-upload-button__filled" if filled else "attachment-upload-button__container"
    await page.set_content(f'''<{component}><div><div><div><div><div class="{tile}">
      <button class="attachment-upload-button__download">{resume.name}</button><button>Remove</button>
      </div></div></div></div></div>{'<div role="alert">Upload failed</div>' if error else ""}</{component}>''')
    form = FormSession(page, Resolver(Profile(), service.db, service.config), resume, lambda *a: None)
    form.uploaded = True
    assert await form.accepted_resume_tile() is accepted


async def test_oracle_remote_country_searches_equivalent_before_claiming_unavailable(page, service, tmp_path):
    html = """<label for=country>Country</label>
      <input id=country class=cx-select-input role=combobox aria-controls=countries aria-expanded=false
        onclick="this.setAttribute('aria-expanded','true');document.querySelector('#countries').hidden=false"
        oninput="document.querySelector('#countries').innerHTML=this.value.toLowerCase()==='united states'
          ? '<div role=option onclick=&quot;const e=document.querySelector(\\\'#country\\\');e.value=\\\'United States\\\';e.setAttribute(\\\'aria-expanded\\\',\\\'false\\\');this.parentElement.hidden=true&quot;>United States</div>' : ''">
      <div id=countries hidden></div>"""
    await page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    await page.goto("https://fixture.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/1")
    form = FormSession(
        page, Resolver(Profile(), service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
    )
    await form.scan()
    country = next(f for f in form.fields.values() if f["label"] == "Country")
    assert await form.fill(country, "United States of America") == "United States"


@pytest.mark.parametrize("label", ["Month", "Start Date Month", "End Date Month"])
async def test_numeric_history_month_selects_named_oracle_option(page, service, config, tmp_path, label):
    html = f"""<label for=month>{label}</label>
      <input id=month class=cx-select-input role=combobox aria-controls=months aria-expanded=false
        onclick="this.setAttribute('aria-expanded','true');document.querySelector('#months').hidden=false">
      <div id=months hidden><div role=gridcell class=cx-select__list-item onclick="
        const e=document.querySelector('#month');e.value='September';e.setAttribute('aria-expanded','false');
        this.parentElement.hidden=true;">September</div></div>"""
    await page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    await page.goto("https://fixture.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/1")
    form = FormSession(
        page, Resolver(Profile(), service.db, config), tmp_path / "resume.pdf", lambda *a: None
    )
    await form.scan()
    field = next(f for f in form.fields.values() if f["type"] == "combobox")
    assert await form.fill(field, "09") == "September"
    assert await page.locator("#month").input_value() == "September"


@pytest.mark.parametrize("actual,accepted", [("United States", True), ("Canada", False)])
async def test_remounted_country_is_read_back_without_accepting_a_sibling_value(page, actual, accepted):
    await page.set_content(f'<input id=replacement value="{actual}" aria-expanded=false aria-invalid=false>')
    refresh = AsyncMock(return_value=page.locator("#replacement"))
    wanted = {"label": "Country", "type": "combobox"}
    if accepted:
        assert (
            await _wait_committed(page.locator("#old"), "oracle", "United States", wanted, refresh=refresh)
            == "United States"
        )
    else:
        with pytest.raises(ValueError, match="did not commit"):
            await _wait_committed(page.locator("#old"), "oracle", "United States", wanted, refresh=refresh)
    refresh.assert_awaited_once()


async def test_oracle_selection_remounts_and_verifies_through_form_owner(page, service, config, tmp_path):
    html = """<label for=country>Country</label>
      <input id=country class=cx-select-input role=combobox readonly aria-controls=countries
        aria-expanded=false onclick="this.setAttribute('aria-expanded','true');document.querySelector('#countries').hidden=false">
      <div id=countries hidden><div role=option onclick="
        const old=document.querySelector('#country');
        const replacement=old.cloneNode();replacement.value='United States';
        replacement.setAttribute('aria-expanded','false');old.replaceWith(replacement);this.parentElement.hidden=true;
      ">United States</div></div>
      <label for=city>City</label><input id=city value='Different sibling value'>"""
    await page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    await page.goto("https://fixture.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX")
    form = FormSession(
        page, Resolver(Profile(), service.db, config), tmp_path / "resume.pdf", lambda *a: None
    )
    await form.scan()
    country = next(f for f in form.fields.values() if f["label"] == "Country")
    assert await form.fill(country, "United States") == "United States"
    assert await page.locator("#country").input_value() == "United States"
    assert await page.locator("#city").input_value() == "Different sibling value"


async def test_failed_radio_never_activates_an_unrelated_agreement(page, service, config, tmp_path):
    await page.set_content("""<input type=radio id=no hidden><label for=no onclick="event.preventDefault()">No</label>
      <button onclick="window.unrelatedAgreement=true">I Agree</button>""")
    form = FormSession(
        page,
        Resolver(Profile(accept_all_application_terms=True), service.db, config),
        tmp_path / "resume.pdf",
        lambda *a: None,
    )
    with pytest.raises(ValueError, match="selection was not accepted"):
        await form.set_checked(page.locator("#no"), True)
    assert not await page.evaluate("!!window.unrelatedAgreement")


@pytest.mark.parametrize("blocked", ["", "armed", "verification", "field", "confirmation", "other_site"])
async def test_empty_oracle_shell_returns_only_to_owned_unsubmitted_job(page, service, tmp_path, blocked):
    opened = []

    async def route(request):
        opened.append(request.request.url)
        await request.fulfill(status=200, content_type="text/html", body="<h1>Careers</h1>")

    await page.route("**/*", route)
    root = "https://fixture.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX"
    await page.goto(root + ("2" if blocked == "other_site" else "") + "/jobs")
    engine = BrowserEngine(service, "synthetic-run")
    engine.page = page
    engine.job = {"id": "synthetic-job"}
    engine.run_record = {"state": "running", "packet": {"verification_only": blocked == "verification"}}
    engine.armed = blocked == "armed"
    engine.application_entry_url = root + "/job/12345"
    engine.form = FormSession(
        page, Resolver(Profile(), service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
    )
    engine.form.ledger = {"old": {"verified": True}}
    observation = {
        "fields": [{"type": "email"}] if blocked == "field" else [],
        "text": "Thank you for applying" if blocked == "confirmation" else "Careers",
    }
    recovered = await engine.recover_oracle_job_shell(observation)
    assert recovered is (not blocked)
    if blocked:
        assert len(opened) == 1
        assert engine.form.ledger["old"]["verified"]
    else:
        assert opened == [root + "/jobs", root + "/job/12345"]
        assert not engine.form.ledger
        assert not await engine.recover_oracle_job_shell(observation)
        assert len(opened) == 2


async def test_oracle_recommendation_resume_is_never_an_application(page, service, tmp_path):
    from jobpilot.adapters.oracle import Oracle
    from jobpilot.adapters.base import Step

    html = """<label>City, state, country<input id=location role=combobox></label>
      <label>Upload or drag and drop your resume here to get recommended jobs based on your skills and experience.
      <input type=file id=resume></label><button>Search for Jobs and Events</button>"""
    await page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    await page.goto("https://fixture.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs")
    form = FormSession(
        page, Resolver(Profile(), service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
    )
    form.ledger = {"prior": {"verified": True}}
    form.uploaded = form.upload_verified = True
    obs = await form.scan()
    assert Oracle.__new__(Oracle).classify_page(obs) == Step.UNKNOWN
    report = await form.fill_current()
    assert not report["ok"] and not report["resume_attached"]
    assert report["verified_fields"] == 0
    assert form.ledger["prior"]["verified"]  # Previous application checkpoints remain preserved.
    assert not await page.locator("#location").input_value()
    assert await page.locator("#resume").evaluate("e=>e.files.length") == 0
    with pytest.raises(ValueError, match="owned posting"):
        await form.fill(next(f for f in obs["fields"] if f["type"] == "combobox"), "Fixture City")
