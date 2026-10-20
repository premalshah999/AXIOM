"""ASSA ABLOY (SuccessFactors) 2026-10-14: State/Province/Region is a UI5 Web Components
``ui5-input`` with value help; its suggestion list lives in the shadow root and a typed
value is discarded unless an entry is picked."""

import pytest

from jobpilot import widgets

WC = """<ui5-input id=host aria-label="State/Province/Region"><span slot=icon id=vh>v</span></ui5-input>
<script>
const ALL = ['Maine', 'Maryland', 'Massachusetts'];
customElements.define('ui5-input', class extends HTMLElement {
  constructor() {
    super(); const r = this.attachShadow({mode: 'open'}); this.value = '';
    r.innerHTML = '<input class="ui5-input-inner" aria-haspopup="dialog" aria-label="State/Province/Region" placeholder="Select">'
      + '<slot name="icon"></slot><ul role="listbox" hidden></ul>';
    const input = r.querySelector('input'), list = r.querySelector('ul');
    const show = items => { list.innerHTML = items.map(s => '<li role="option">' + s + '</li>').join(''); list.hidden = !items.length; };
    list.addEventListener('click', ev => { this.value = input.value = ev.target.textContent; list.hidden = true; });
    input.addEventListener('input', () => show(ALL.filter(s => s.toLowerCase().startsWith(input.value.toLowerCase()))));
    this.addEventListener('click', ev => { if (ev.target.slot === 'icon') show(ALL); });
    input.addEventListener('blur', () => setTimeout(() => { if (input.value !== this.value) input.value = ''; }, 300));
  }
});
</script>"""


@pytest.mark.parametrize("value", ["Maryland", "MD"])
async def test_ui5wc_region_commits_a_picked_entry(page, value):
    await page.set_content(WC)
    el = page.locator("ui5-input input.ui5-input-inner")
    assert await widgets.is_ui5wc_input(el)
    assert await widgets.ui5wc_pick(page.main_frame, el, value) == "Maryland"
    assert await page.evaluate("document.getElementById('host').value") == "Maryland"


async def test_ui5wc_region_without_the_answer_fails(page):
    await page.set_content(WC)
    with pytest.raises(ValueError):
        await widgets.ui5wc_pick(page.main_frame, page.locator("ui5-input input.ui5-input-inner"), "Ontario")
