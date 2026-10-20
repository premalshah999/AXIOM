"""SuccessFactors (SAP UI5) State/Province/Region inputs commit only a clicked suggestion."""

from types import SimpleNamespace

from jobpilot import widgets
from jobpilot.forms import FormSession

REGION_UI5 = """<label for=region>State/Province/Region</label>
<div class="sapMInputBase sapMComboBox" id=wrap>
  <input id=region class="sapMInputBaseInner" autocomplete=off placeholder=Select>
  <ul id=sugg class=sapMSuggestionsPopover hidden></ul>
</div>
<script>
const OFFERED = ['Maine', 'Maryland', 'Massachusetts'];
const input = document.getElementById('region'), wrap = document.getElementById('wrap'), list = document.getElementById('sugg');
let committed = false;
list.innerHTML = OFFERED.map(s => '<li role=option class=sapMSLI onmousedown="event.preventDefault()" onclick="pick(this)">' + s + '</li>').join('');
function pick(li) {
  input.value = li.textContent; committed = true; list.hidden = true;
  input.setAttribute('aria-invalid', 'false'); wrap.classList.remove('sapMInputBaseStateError');
}
input.addEventListener('input', () => { committed = false; list.hidden = false; });
input.addEventListener('focus', () => { list.hidden = false; });
input.addEventListener('blur', () => {
  list.hidden = true;
  // UI5 clears a typed value that was never picked from the suggestions and shows valueState Error.
  if (!committed && input.value) {
    input.value = '';
    input.setAttribute('aria-invalid', 'true');
    wrap.classList.add('sapMInputBaseStateError');
  }
});
</script>"""

# Same wrapper, no suggestion list: a plain UI5 Input that keeps what was typed.
PLAIN_UI5 = """<label for=pref>Preferred name</label>
<div class="sapMInputBase sapMInput"><input id=pref class=sapMInputBaseInner autocomplete=off></div>"""

# UI5 Input that flags its own value invalid on blur (valueState Error), with no suggestions.
FLAGGED_UI5 = """<label for=pref>Preferred name</label>
<div class="sapMInputBase sapMInput" id=wrap><input id=pref class=sapMInputBaseInner autocomplete=off></div>
<script>
const p = document.getElementById('pref');
p.addEventListener('blur', () => {
  if (p.value) { p.setAttribute('aria-invalid', 'true'); document.getElementById('wrap').classList.add('sapMInputBaseStateError'); }
});
</script>"""


async def _form(page, tmp_path):
    form = FormSession(
        page,
        SimpleNamespace(profile=SimpleNamespace(autonomous=False, phone_country_code="", address=None)),
        tmp_path / "resume.pdf",
        lambda *a, **k: None,
    )
    await form.scan()
    return form


def _field(form, label):
    return next(f for f in form.fields.values() if f["label"] == label)


async def test_ui5_region_input_commits_the_clicked_suggestion(page, tmp_path):
    await page.set_content(REGION_UI5)
    form = await _form(page, tmp_path)
    field = _field(form, "State/Province/Region")
    assert field["type"] == "text"  # The shape that used to skip the UI5 list picker.
    assert await form.fill(field, "Maryland") == "Maryland"
    assert await page.locator("#region").input_value() == "Maryland"
    assert await page.locator("#region").get_attribute("aria-invalid") != "true"


async def test_ui5_region_input_with_an_abbreviation_commits_the_full_option(page, tmp_path):
    await page.set_content(REGION_UI5)
    form = await _form(page, tmp_path)
    field = _field(form, "State/Province/Region")
    assert await form.fill(field, "MD") == "Maryland"
    assert await page.locator("#region").input_value() == "Maryland"


async def test_ui5_region_value_without_an_offered_match_is_a_field_error(page, tmp_path):
    import pytest

    await page.set_content(REGION_UI5)
    form = await _form(page, tmp_path)
    field = _field(form, "State/Province/Region")
    with pytest.raises(ValueError, match="No matching option is offered"):
        await form.fill(field, "Ontario")


async def test_ui5_region_without_any_suggestion_list_keeps_the_typed_value(page, tmp_path, monkeypatch):
    await page.set_content(PLAIN_UI5.replace("Preferred name", "State/Province/Region"))
    monkeypatch.setattr(widgets, "UI5_OPTION_TIMEOUT", 0.6)
    form = await _form(page, tmp_path)
    field = _field(form, "State/Province/Region")
    assert await form.fill(field, "Maryland") == "Maryland"
    assert await page.locator("#pref").input_value() == "Maryland"


async def test_generic_text_branch_raises_when_ui5_input_flags_the_value(page, tmp_path):
    import pytest

    await page.set_content(FLAGGED_UI5)
    form = await _form(page, tmp_path)
    field = _field(form, "Preferred name")
    with pytest.raises(ValueError, match="The website did not commit the observed choice"):
        await form.fill(field, "Alex")


async def test_plain_ui5_input_that_keeps_its_value_still_fills(page, tmp_path):
    await page.set_content(PLAIN_UI5)
    form = await _form(page, tmp_path)
    field = _field(form, "Preferred name")
    assert await form.fill(field, "Alex") == "Alex"
    assert await page.locator("#pref").input_value() == "Alex"


async def test_ui5_probe_of_a_replaced_field_returns_fast(page):
    # Workday re-renders inputs after typing; the probe must not wait Playwright's 30 s default.
    import time

    from jobpilot import widgets

    await page.set_content("<input id=a>")
    gone = page.locator("#replaced-after-typing")
    started = time.monotonic()
    assert await widgets.ui5_uncommitted(gone, "Maryland") is False
    assert time.monotonic() - started < 5
