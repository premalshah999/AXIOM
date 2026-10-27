"""Pinpoint (Kenway Consulting, 2026-10-27): a react-select with classNamePrefix "react-select". Its 4 px input sits
under the placeholder, so a click on it never landed; its option rows have no role, only their generated ids; and the
pick shows in `.react-select__single-value`, which the read-back (written for Greenhouse's `select__`) never read.
The Country commit hit the field deadline on every run."""

from jobpilot import widgets

PAGE = """<div id=wrap style="width:400px">
<div class="react-select__control" id=ctl style="position:relative;height:38px;border:1px solid #ccc">
  <div class="react-select__value-container" style="position:relative;height:100%">
    <div class="react-select__placeholder" id=ph style="position:absolute;inset:0;padding:8px">Country</div>
    <div style="display:inline-block;position:relative"><input id=country class="react-select__input" role=combobox
      aria-haspopup=true aria-autocomplete=list aria-expanded=false style="width:4px;border:0;outline:0"></div>
  </div></div>
<div id=host></div></div>
<script>
const COUNTRIES = ['United Arab Emirates', 'United Kingdom', 'United States', 'Uruguay'];
const inp = document.getElementById('country'), host = document.getElementById('host');
const vc = document.querySelector('.react-select__value-container');
function render() {
  const q = inp.value.toLowerCase();
  host.innerHTML = '<div class="react-select__menu" id="react-select-2-listbox"><div class="react-select__menu-list">' +
    COUNTRIES.filter(t => t.toLowerCase().includes(q)).map((t, i) =>
      '<div class="react-select__option" id="react-select-2-option-' + i + '" aria-disabled="false">' + t + '</div>').join('') +
    '</div></div>';
  inp.setAttribute('aria-expanded', 'true');
  host.querySelectorAll('.react-select__option').forEach(o => o.onmousedown = e => { e.preventDefault(); pick(o.textContent); });
}
function pick(text) {
  host.innerHTML = ''; inp.value = ''; inp.setAttribute('aria-expanded', 'false');
  document.getElementById('ph')?.remove();
  vc.querySelector('.react-select__single-value')?.remove();
  vc.insertAdjacentHTML('afterbegin', '<div class="react-select__single-value">' + text + '</div>');
}
document.getElementById('ctl').addEventListener('mousedown', e => { if (!e.target.closest('.react-select__option')) { render(); inp.focus(); } });
inp.addEventListener('input', render);
</script>"""


async def test_a_prefixed_react_select_with_a_covered_input_commits(page):
    await page.set_content(PAGE)
    el = page.locator("#country")
    assert await widgets.combobox(page.main_frame, el, "United States") == "United States"
    assert await page.locator(".react-select__single-value").inner_text() == "United States"
