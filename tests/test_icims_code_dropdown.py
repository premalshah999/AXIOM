"""iCIMS's email step asks a phone country code with the dropdown look but no <select> (Emory, Stifel 2026-10-21/22):
<a id=dropdown class=dropdown-select role=combobox aria-controls=dropdownOptions>, whose container holds
input.dropdown-search (filterOptions on input) and ul.dropdown-results of li[role=option] labelled "(+1) United
States". The scan labelled the hidden search box "Phone Country Code"; clicking it timed out, and typing into the
anchor was "Value not accepted"."""

import pytest

from jobpilot import widgets

PAGE = """
<div class="country-code"><div class="country-code iCIMS_InfoData">
<a id="dropdown" class="dropdown-select" onclick="toggleDropdown()" role="combobox" aria-controls="dropdownOptions"
   aria-required="true" tabindex="0"><span id="dropdown-select-span" class="dropdown-text">
   <span class="dropdown-placeholder">— Make a Selection —</span></span></a>
<input type="text" id="selectedCountryCode" name="countryCodeSelect" value="" style="display:none">
<div class="dropdown-container" id="dropdownOptions" style="display:none">
  <input type="text" class="dropdown-search" id="countryCode" name="countryCodeSearch" oninput="filterOptions()">
  <ul class="dropdown-results" id="dropdownResults" role="listbox">
    <li role="option" id="r-af" value="AF" aria-label="(+93) Afghanistan" class="dropdown-result">(+93) Afghanistan</li>
    <li role="option" id="r-ca" value="CA" aria-label="(+1) Canada" class="dropdown-result">(+1) Canada</li>
    <li role="option" id="r-in" value="IN" aria-label="(+91) India" class="dropdown-result">(+91) India</li>
    <li role="option" id="r-us" value="US" aria-label="(+1) United States" class="dropdown-result">(+1) United States</li>
    <li role="option" id="r-um" value="UM" aria-label="(+1) United States Minor Outlying Islands"
        class="dropdown-result">(+1) United States Minor Outlying Islands</li>
  </ul>
</div></div></div>
<label for="countryCode">Phone Country Code</label>
<script>
function toggleDropdown() { const c = document.getElementById('dropdownOptions');
  c.style.display = c.style.display === 'none' ? 'block' : 'none'; }
function filterOptions() { const q = document.getElementById('countryCode').value.toLowerCase();
  for (const li of document.querySelectorAll('#dropdownResults li'))
    li.style.display = li.innerText.toLowerCase().includes(q) ? '' : 'none'; }
document.getElementById('dropdownResults').addEventListener('click', e => {
  const li = e.target.closest('li');
  document.getElementById('selectedCountryCode').value = li.getAttribute('value');
  document.getElementById('dropdown-select-span').textContent = li.getAttribute('aria-label');
  document.getElementById('dropdownOptions').style.display = 'none'; });
</script>"""


@pytest.mark.parametrize("target", ["#dropdown", "#countryCode"])
@pytest.mark.parametrize("value,code", [("United States", "US"), ("+1", "US"), ("India", "IN")])
async def test_the_code_is_picked_by_name_from_the_anchor_or_its_hidden_search_box(page, target, value, code):
    await page.set_content(PAGE)
    picked = await widgets.combobox(page.main_frame, page.locator(target), value)
    assert await page.input_value("#selectedCountryCode") == code
    assert picked.endswith({"US": "United States", "IN": "India"}[code])


async def test_an_unoffered_country_is_refused(page):
    await page.set_content(PAGE)
    with pytest.raises(ValueError):
        await widgets.combobox(page.main_frame, page.locator("#dropdown"), "Atlantis")
    assert await page.input_value("#selectedCountryCode") == ""
