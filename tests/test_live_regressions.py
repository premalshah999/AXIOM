"""Owned fixtures reproducing behaviors observed on the live employer sites."""

from unittest.mock import AsyncMock

from jobpilot.adapters.base import Adapter
from jobpilot.adapters.oracle import Oracle
from jobpilot.schemas import Profile
from jobpilot.telegram import api, configured
from jobpilot.widgets import combobox
from test_browser import page as page  # noqa: F401
from test_trust import engine_for


async def test_delayed_oracle_legal_dialog_is_accepted(service, page, tmp_path):
    await page.set_content("""
      <input id=terms type=checkbox style="display:none">
      <label for=terms onclick="event.preventDefault();setTimeout(()=>dialog.hidden=false,500)">Terms</label>
      <div id=dialog hidden><button onclick="terms.checked=true;dialog.hidden=true">I Agree</button></div>
    """)
    engine = engine_for(service, page, tmp_path)
    engine.form.resolver.profile = Profile(accept_all_application_terms=True)
    await engine.form.set_checked(page.locator("#terms"), True)
    assert await page.locator("#terms").is_checked()


async def test_oracle_grid_country_search_uses_observed_alias(page):
    await page.set_content("""
      <input id=country role=combobox aria-controls=countries aria-autocomplete=list
       oninput="countries.innerHTML=this.value.toLowerCase()==='united states' ?
       '<div role=row><div role=gridcell class=cx-select__list-item onclick=choose(this)>United States</div></div><div role=row><div role=gridcell class=cx-select__list-item>United States Minor Outlying Islands</div></div>' : ''">
      <div id=countries role=grid></div>
      <div role=gridcell>Unrelated grid cell</div>
      <script>function choose(e){country.value=e.textContent;window.chosen=e.textContent;countries.innerHTML=''}</script>
    """)
    answer = await combobox(page.main_frame, page.locator("#country"), "United States of America")
    assert answer == "United States"
    assert await page.evaluate("window.chosen") == "United States"


async def test_address_picker_waits_for_key_events_and_delegates_ambiguous_zip(page):
    await page.set_content("""
      <input id=zip role=combobox aria-controls=choices onkeyup="clearTimeout(window.timer);window.timer=setTimeout(load,400)">
      <div id=choices role=grid></div>
      <script>
      function load(){if(zip.value==='00000')choices.innerHTML=
        '<div class=cx-select__list-item role=gridcell onclick=pick(this)>00000, Darnestown, Montgomery, MD</div>'+
        '<div class=cx-select__list-item role=gridcell onclick=pick(this)>00000, Springfield, Montgomery, MD</div>'}
      function pick(e){window.selected=e.textContent;zip.value='00000';choices.innerHTML=''}
      </script>
    """)
    choose = AsyncMock(return_value="00000, Springfield, Montgomery, MD")
    result = await combobox(page.main_frame, page.locator("#zip"), "00000", choose=choose)
    assert result == "00000"
    choose.assert_awaited_once()
    assert await page.evaluate("window.selected") == "00000, Springfield, Montgomery, MD"


async def test_known_answer_control_failure_does_not_ask_applicant(service, page, tmp_path):
    engine = engine_for(service, page, tmp_path)
    engine.ask = AsyncMock(side_effect=AssertionError("Must not ask again for a known fact"))
    engine.form.fill_current = AsyncMock(
        return_value={
            "pending": [
                {
                    "question": "Country",
                    "reason": "Could not commit the answer: No matching option is offered",
                }
            ],
            "ok": False,
        }
    )
    result = await Adapter(engine).fill_page()
    assert result["state"] == "waiting_browser"
    engine.ask.assert_not_awaited()


async def test_telegram_disabled_with_credentials_makes_no_network_request(config, monkeypatch):
    config.telegram_enabled = False
    config.telegram_bot_token = "fixture-token"
    config.telegram_user_id = "123"
    assert not configured(config)
    monkeypatch.setattr(
        "jobpilot.telegram.httpx.AsyncClient", lambda **kw: (_ for _ in ()).throw(AssertionError("network"))
    )
    assert await api(config, "sendMessage", {"text": "fixture"}) is None


async def test_optional_url_is_validated_and_bare_domain_is_normalized(service, page, tmp_path):
    await page.set_content('<label>Website<input type=url value="example.test"></label>')
    engine = engine_for(service, page, tmp_path)
    await engine.form.scan()
    report = await engine.form.verify()
    assert not report["ok"]
    field = next(iter(engine.form.fields.values()))
    await engine.form.fill(field, "example.test")
    assert await page.locator("input").input_value() == "https://example.test"
    assert (await engine.form.verify())["ok"]


async def test_oracle_required_pills_and_explicit_validation_rejection(service, page, tmp_path):
    await page.set_content("""
      <p>You have 3 issues that need to be fixed before you can submit your application.</p>
      <div class=input-row><label class=input-row__label--required>Are you at least 18 years of age?</label>
        <ul role=radiogroup aria-label="Are you at least 18 years of age?">
          <button role=radio aria-checked=false>Yes</button><button role=radio aria-checked=false>No</button>
        </ul>
      </div>""")
    engine = engine_for(service, page, tmp_path)
    observation = await engine.form.scan()
    assert observation["fields"][0]["required"]
    assert not (await engine.form.verify())["ok"]
    rejection = await Oracle(engine).validation_rejection()
    assert rejection["type"] == "explicit_validation_rejection"


async def test_workday_date_segments_activate_their_visible_display(page):
    from jobpilot.widgets import date_parts

    parts = []
    html = '<script>window.activePart="month";</script>'
    for kind in ("month", "day", "year"):
        parts.append({"kind": kind, "dom_id": kind})
        html += f'''<div>
          <div data-automation-id="dateSection{kind.title()}-display"
               onclick="window.activePart='{kind}'">{kind}</div>
          <input data-jp-id="{kind}" oninput="if(window.activePart!=='{kind}')this.value=''">
        </div>'''
    await page.set_content(html)
    assert await date_parts(page, {"parts": parts}, "2026-10-04") == "10/04/2026"
    assert await page.locator("input").evaluate_all("es=>es.map(e=>e.value)") == ["10", "04", "2026"]


async def test_icims_wrong_username_is_an_explicit_credential_rejection(service, page, tmp_path):
    await page.set_content(
        "<label>Password<input type=password></label>"
        "<div role=alert>Wrong username or password</div><button>LOG IN</button>"
    )
    adapter = Adapter(engine_for(service, page, tmp_path))
    outcome, _ = await adapter.auth_outcome(timeout=2)
    assert outcome == "bad_credentials"
