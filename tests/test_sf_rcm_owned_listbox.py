"""SuccessFactors RCM paginated picklists: "Could not commit …" on Country of Residence (Capgemini x8), education,
"How did you hear", gender and yes/no questions (Acuity, Corning, Cargill, Hexion, AMETEK; 2026-10-14..16).

The REA map (2026-10-18) of RCMPaginatedPicklistSingleSelect: the input owns a listbox ("10:_listSelect") of
li[role=option]; options load 100 per page, so "United States" (230 of 253) is absent until the employer's search
answers about 1.4 s after typing starts; only a click or Enter on an option commits, and _onBlur reverts typed text.
"""

import pytest

from jobpilot import widgets

COUNTRIES = [f"Country {i:03d}" for i in range(229)] + [
    "United States",
    "United States Minor Outlying Islands",
    "Uruguay",
]

PICKLIST = """<div id=picklist_custcountry><span class=sfCascadingPicklist>
<div id="9:_selectContainer" class=paginatedPicklistContainer>
  <input id="9:_input" role=combobox class="rcmpaginatedselectinput rcmpaginatedselectitem" aria-owns="10:_listSelect"
    placeholder="No Selection" autocomplete=off>
  <button id="9:_selectButton" type=button class=rcmpaginatedselectbutton>v</button>
  <input type=hidden id=tor__fcustcountry name=tor__fcustcountry class=appFieldRequired>
</div></span></div>
<ul id="10:_listSelect" role=listbox hidden></ul>
<script>
const ALL = %s;
const input = document.getElementById('9:_input'), list = document.getElementById('10:_listSelect');
const hidden = document.getElementById('tor__fcustcountry');
let committed = '', timer = null, shown = [];
function render(items) {
  shown = items;
  list.innerHTML = items.map((s, k) => '<li role=option id="10:item' + k + '">' + s + '</li>').join('');
  list.hidden = false;
}
function choose(k) {
  committed = input.value = shown[k]; hidden.value = String(ALL.indexOf(shown[k]));
  list.hidden = true; input.removeAttribute('aria-activedescendant');
}
input.addEventListener('click', () => render(ALL.slice(0, 100)));  // page 1 only
document.getElementById('9:_selectButton').addEventListener('click', () => render(ALL.slice(0, 100)));
input.addEventListener('input', () => {
  // The employer searches once, 1.4 s after typing starts (DWR filterType=contains).
  if (!timer) timer = setTimeout(() => {
    timer = null;
    render(ALL.filter(s => s.toLowerCase().includes(input.value.toLowerCase())).slice(0, 100));
  }, 1400);
});
list.addEventListener('click', ev => { const li = ev.target.closest('li'); if (li && CLICKS) choose([...list.children].indexOf(li)); });
input.addEventListener('keydown', ev => {
  const ids = shown.map((_, k) => '10:item' + k);
  const at = ids.indexOf(input.getAttribute('aria-activedescendant'));
  if (ev.key === 'ArrowDown') input.setAttribute('aria-activedescendant', ids[Math.min(at + 1, ids.length - 1)]);
  if (ev.key === 'Enter' && at >= 0) choose(at);
});
input.addEventListener('blur', () => { if (list.hidden && input.value !== committed) input.value = committed; });
</script>"""


def _page(clicks=True):
    import json

    return PICKLIST.replace("%s", json.dumps(COUNTRIES)).replace("CLICKS", "true" if clicks else "false")


async def test_searches_then_clicks_the_owned_option_and_keeps_it_after_blur(page):
    await page.set_content(_page())
    el = page.locator("[id='9:_input']")
    assert await widgets.combobox(page.main_frame, el, "United States") == "United States"
    await el.evaluate("e => e.blur()")
    assert await el.input_value() == "United States"
    assert await page.locator("#tor__fcustcountry").input_value() == "229"


async def test_enter_on_the_option_commits_when_a_click_does_not(page):
    await page.set_content(_page(clicks=False))
    el = page.locator("[id='9:_input']")
    assert await widgets.combobox(page.main_frame, el, "United States") == "United States"
    assert await page.locator("#tor__fcustcountry").input_value() == "229"


async def test_an_answer_the_list_does_not_offer_is_not_committed(page):
    await page.set_content(_page())
    with pytest.raises(ValueError):
        await widgets.combobox(page.main_frame, page.locator("[id='9:_input']"), "Atlantis")
    assert await page.locator("#tor__fcustcountry").input_value() == ""
