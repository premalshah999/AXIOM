"""ASSA ABLOY (SuccessFactors) Job-Specific Information, 2026-10-15: "How did you hear", work
eligibility, Ethnicity, Disability and Veteran status are value-help inputs (UI5 Web Components
under scoped tag names) whose entries appear only when the list is opened; Gender is a ui5-select.
Typed text was discarded, so every required pick list stayed empty. The stand-ins below keep the
real structure: scoped tags that carry the unscoped name as an attribute, the inner input with
aria-haspopup=dialog, an icon slot that opens the list, and ui5-option children of the select."""

from jobpilot import widgets
from jobpilot.forms import SCAN

PAGE = """
<x-input ui5-input id=eth><span slot=icon>v</span></x-input>
<x-select ui5-select id=gender><x-option ui5-option>Select</x-option><x-option ui5-option>Male</x-option>
<x-option ui5-option>Female</x-option></x-select>
<script>
const ETHNICITY = ['Hispanic or Latino', 'Asian (Not Hispanic or Latino)', 'I do not wish to self-identify'];
customElements.define('x-input', class extends HTMLElement {
  constructor() {
    super(); const r = this.attachShadow({mode: 'open'}); this.value = '';
    r.innerHTML = '<input class="ui5-input-inner" aria-haspopup="dialog" aria-label="Ethnicity" placeholder="Select">'
      + '<slot name="icon"></slot><div hidden></div>';
    const input = r.querySelector('input'), list = r.querySelector('div');
    const show = items => { list.innerHTML = items.map(s => '<p ui5-li>' + s + '</p>').join(''); list.hidden = !items.length; };
    list.addEventListener('click', ev => { this.value = input.value = ev.target.textContent; list.hidden = true; });
    input.addEventListener('input', () => show(ETHNICITY.filter(s => s.toLowerCase().startsWith(input.value.toLowerCase()))));
    this.addEventListener('click', ev => { if (ev.target.slot === 'icon') show(ETHNICITY); });
    document.addEventListener('keydown', ev => { if (ev.key === 'Escape') list.hidden = true; });
    input.addEventListener('blur', () => setTimeout(() => { if (input.value !== this.value) input.value = ''; }, 300));
  }
});
customElements.define('x-select', class extends HTMLElement {
  constructor() {
    super(); const r = this.attachShadow({mode: 'open'});
    r.innerHTML = '<div class="ui5-select-label-root" role="combobox" aria-haspopup="listbox" aria-label="Gender"'
      + ' tabindex="0">Select</div><div hidden></div>';
    const label = r.querySelector('[role=combobox]'), list = r.querySelector('div[hidden]');
    label.addEventListener('click', () => {
      list.innerHTML = [...this.children].map(o => '<p ui5-li>' + o.textContent + '</p>').join('');
      list.hidden = false;
    });
    list.addEventListener('click', ev => {
      for (const o of this.children) o.selected = o.textContent === ev.target.textContent;
      label.textContent = ev.target.textContent; list.hidden = true;
    });
  }
  get selectedOption() { return [...this.children].find(o => o.selected) || this.children[0]; }
});
</script>"""


async def _fields(page):
    return {f["label"]: f for f in (await page.evaluate(SCAN))["fields"]}


async def test_pick_lists_are_choice_fields(page):
    await page.set_content(PAGE)
    fields = await _fields(page)
    assert (fields["Ethnicity"]["type"], fields["Ethnicity"]["widget"]) == ("combobox", "ui5wc-valuehelp")
    assert (fields["Gender"]["type"], fields["Gender"]["widget"]) == ("combobox", "ui5wc-select")


async def test_value_help_entries_are_read_and_picked(page):
    await page.set_content(PAGE)
    el = page.locator(f'[data-jp-id="{(await _fields(page))["Ethnicity"]["id"]}"]')
    options = await widgets.ui5wc_options(page.main_frame, el)
    assert options == [
        "Hispanic or Latino",
        "Asian (Not Hispanic or Latino)",
        "I do not wish to self-identify",
    ]
    assert await widgets.ui5wc_pick(page.main_frame, el, options[2]) == "I do not wish to self-identify"
    assert (await _fields(page))["Ethnicity"]["value"] == "I do not wish to self-identify"


async def test_ui5_select_commits_the_option(page):
    await page.set_content(PAGE)
    el = page.locator(f'[data-jp-id="{(await _fields(page))["Gender"]["id"]}"]')
    assert await widgets.ui5wc_select_options(el) == ["Select", "Male", "Female"]
    assert await widgets.ui5wc_select(page.main_frame, el, "Male") == "Male"
    assert await page.evaluate("document.getElementById('gender').selectedOption.textContent") == "Male"


RADIOS = """
<div id="q-l">Do you hold a PhD in Computer Science?</div>
<div role="radiogroup" aria-labelledby="q-l" aria-required="true">
  <x-radio ui5-radio-button text="Yes"></x-radio><x-radio ui5-radio-button text="No"></x-radio>
</div>
<script>
customElements.define('x-radio', class extends HTMLElement {
  constructor() {
    super(); this.checked = false;
    this.attachShadow({mode: 'open'}).innerHTML = '<div role="radio" aria-checked="false">'
      + '<input type="radio" style="opacity:0;position:absolute"><label></label></div>';
  }
  connectedCallback() {
    this.text = this.getAttribute('text');
    this.shadowRoot.querySelector('label').textContent = this.text;
    this.addEventListener('click', () => {
      for (const r of this.parentElement.children) r.checked = r === this;
    });
  }
});
</script>"""


async def test_ui5_radio_buttons_read_their_host_text_and_select_on_click(page):
    # ASSA ABLOY's Additional Information questions: role=radio and the native input live in each
    # radio button's shadow root, so the group had no options and every answer failed as stale.
    await page.set_content(RADIOS)
    field = (await _fields(page))["Do you hold a PhD in Computer Science?"]
    assert (field["type"], field["options"], field["value"]) == ("radiogroup", ["Yes", "No"], "")
    host = page.locator(f'[data-jp-id="{field["members"][1]}"]')
    assert await widgets.ui5wc_radio(host)
    assert (await _fields(page))["Do you hold a PhD in Computer Science?"]["value"] == "No"
