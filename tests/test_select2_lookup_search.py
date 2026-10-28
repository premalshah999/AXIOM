"""Electronic Arts' Avature registration (2026-10-27): Country, State/Province and Mobile Country Code are select2
lookups. Opened, each lists its first 100 rows (Afghanistan, Aland Islands, ...) and loads the rest only for a search
typed into the menu's own box, so "United States" read as not offered and Create failed with "Country: This field is
required". The menu's search box is now used before the value is declared unoffered."""

from unittest.mock import AsyncMock

import pytest

from jobpilot import widgets

COUNTRIES = [f"Country {i:03d}" for i in range(240)] + ["United States", "United States Minor Outlying Islands"]

SELECT2 = """
<label for=country>Country *</label>
<select id=country aria-hidden=true tabindex=-1 class=select2-hidden-accessible><option value=""></option></select>
<span class="select2 select2-container"><span id=trigger class="select2-selection select2-selection--single"
  role=combobox aria-expanded=false tabindex=0 onclick="openMenu()"><span id=shown>Select an option</span></span></span>
<script>
const ALL = %s;
function render(q) {
  const list = document.getElementById('select2-country-results');
  const rows = (q ? ALL.filter(t => t.toLowerCase().includes(q.toLowerCase())) : ALL).slice(0, 100);
  list.innerHTML = rows.map(t => '<li class="select2-results__option" role=option>' + t + '</li>').join('');
  for (const li of list.children) li.onclick = () => pick(li.textContent);
}
function openMenu() {
  if (document.getElementById('drop')) return;
  const drop = document.createElement('span');
  drop.id = 'drop';
  drop.className = 'select2-container select2-container--open';
  drop.innerHTML = '<span class="select2-dropdown"><span class="select2-search select2-search--dropdown">'
    + '<input class=select2-search__field type=search aria-controls=select2-country-results></span>'
    + '<span class=select2-results><ul class=select2-results__options role=listbox id=select2-country-results></ul></span></span>';
  document.body.appendChild(drop);
  const t = document.getElementById('trigger');
  t.setAttribute('aria-expanded', 'true');
  t.setAttribute('aria-owns', 'select2-country-results');
  const box = drop.querySelector('input');
  box.oninput = () => {  // the next page arrives from the server, after a "Searching…" row
    const list = document.getElementById('select2-country-results');
    list.innerHTML = '<li role=alert aria-live=assertive class="select2-results__option loading-results">Searching…</li>';
    setTimeout(() => render(box.value), window.searchDelay || 150);
  };
  render('');
  box.focus();
}
function pick(text) {
  const select = document.getElementById('country');
  select.innerHTML = '<option value=""></option><option value="x" selected>' + text + '</option>';
  document.getElementById('shown').textContent = text;
  document.getElementById('drop').remove();
  const t = document.getElementById('trigger');
  t.setAttribute('aria-expanded', 'false');
  t.removeAttribute('aria-owns');
}
</script>"""


@pytest.mark.parametrize("value", ["United States", "United States of America"])
async def test_a_lookup_beyond_its_first_page_is_found_by_its_search_box(page, value):
    import json

    await page.set_content(SELECT2 % json.dumps(COUNTRIES))
    choose = AsyncMock(return_value=None)
    chosen = await widgets.combobox(page.main_frame, page.locator("#trigger"), value, choose=choose)
    assert chosen == "United States"
    assert await page.locator("#shown").inner_text() == "United States"
    choose.assert_not_called()


async def test_a_value_the_lookup_does_not_have_is_still_refused(page):
    import json

    await page.set_content(SELECT2 % json.dumps(COUNTRIES))
    with pytest.raises(ValueError):
        await widgets.combobox(page.main_frame, page.locator("#trigger"), "Atlantis", choose=AsyncMock(return_value=None))
    assert await page.locator("#shown").inner_text() == "Select an option"


async def test_a_searching_row_is_not_the_search_result(page):
    # Electronic Arts 2026-10-28: the "Searching…" row was read as the whole result and United States as not offered.
    import json

    await page.set_content(SELECT2 % json.dumps(COUNTRIES))
    await page.evaluate("window.searchDelay = 1500")
    choose = AsyncMock(return_value=None)
    chosen = await widgets.combobox(page.main_frame, page.locator("#trigger"), "United States", choose=choose)
    assert chosen == "United States"
    choose.assert_not_called()


LABELLED = """<span id=lbl>Mobile Country Code *</span>
<select id=cc aria-hidden=true tabindex=-1 class=select2-hidden-accessible><option value=""></option>
  <option value="us" selected>United States (+1)</option></select>
<span class="select2 select2-container"><span class="select2-selection select2-selection--single" role=combobox
  tabindex=0 aria-labelledby="lbl select2-cc-container"><span class=select2-selection__rendered id=select2-cc-container
  role=textbox aria-readonly=true>United States (+1)</span></span></span>"""


async def test_a_select2_trigger_is_not_labelled_by_its_own_choice(page):
    from jobpilot.forms import SCAN

    await page.set_content(LABELLED)
    data = await page.main_frame.evaluate(SCAN)
    combo = [f for f in data["fields"] if f["type"] == "combobox"]
    assert [f["label"] for f in combo] == ["Mobile Country Code *"]
    assert combo[0]["value"] == "United States (+1)"


def test_a_shown_calling_code_satisfies_the_country_answer():
    field = {"type": "combobox", "label": "Mobile Country Code *", "options": []}
    assert widgets.same_value(field, "United States (+1)", "United States of America")
    assert not widgets.same_value(field, "American Samoa (+1)", "United States of America")
    assert not widgets.same_value(field, "Canada (+1)", "United States")
