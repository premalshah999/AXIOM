"""ASSA ABLOY (SuccessFactors) 2026-10-14: the RCM paginated picklist for State/Ethnicity.

Its list renders at the end of <body> without option roles and the input drops a typed
value unless an entry is clicked, so both "MD" and "Maryland" were rejected 6 runs in a row.
"""

import pytest

from jobpilot import widgets

PICKLIST = """<div id=picklist_state><span class=sfCascadingPicklist>
<div id="17:_selectContainer" class=paginatedPicklistContainer>
  <input id="17:_input" role=combobox class="rcmpaginatedselectinput rcmpaginatedselectitem" autocomplete=off>
  <button id="17:_selectButton" type=button class=rcmpaginatedselectbutton>v</button>
</div></span></div>
<script>
const ALL = ['Maine', 'Maryland', 'Massachusetts', 'Texas'];
const input = document.getElementById('17:_input');
let picked = '';
function show(items) {
  document.getElementById('dd')?.remove();
  const d = document.createElement('div'); d.id = 'dd';
  d.innerHTML = items.map(s => '<div class=item><a href="#" onclick="pick(event, this)">' + s + '</a></div>').join('');
  document.body.appendChild(d);
}
function pick(ev, a) { ev.preventDefault(); picked = input.value = a.textContent; document.getElementById('dd').remove(); }
input.addEventListener('input', () => show(ALL.filter(s => s.toLowerCase().startsWith(input.value.toLowerCase()))));
document.getElementById('17:_selectButton').addEventListener('click', () => show(ALL));
input.addEventListener('blur', () => setTimeout(() => { if (input.value !== picked) input.value = ''; }, 300));
</script>"""


@pytest.mark.parametrize("value", ["Maryland", "MD"])
async def test_rcm_picklist_commits_the_clicked_entry(page, value):
    await page.set_content(PICKLIST)
    el = page.locator("[id='17:_input']")
    assert await widgets.combobox(page.main_frame, el, value) == "Maryland"
    assert await el.input_value() == "Maryland"


async def test_rcm_picklist_without_the_answer_fails_cleanly(page):
    await page.set_content(PICKLIST)
    with pytest.raises(ValueError):
        await widgets.combobox(page.main_frame, page.locator("[id='17:_input']"), "Ontario")


SCHOOLS = PICKLIST.replace(
    "const ALL = ['Maine', 'Maryland', 'Massachusetts', 'Texas'];",
    "const ALL = [...Array(150).keys()].map(i => 'University of Place ' + i)"
    ".concat(['University of Michigan-Ann Arbor', 'University of Michigan-Dearborn']);",
).replace(
    "show(ALL.filter(s => s.toLowerCase().startsWith(input.value.toLowerCase())))",
    "show(ALL.filter(s => s.toLowerCase().includes(input.value.toLowerCase())).slice(0, 20))",
)


async def test_rcm_school_catalog_is_searched_by_name_and_matches_its_spelling(page):
    # Cargill 2026-10-16: "Univ" listed 20 of hundreds of universities, and the catalog spells the
    # campus with a hyphen; the profile's "University of Michigan, Ann Arbor" was never found.
    await page.set_content(SCHOOLS)
    el = page.locator("[id='17:_input']")
    committed = await widgets.combobox(page.main_frame, el, "University of Michigan, Ann Arbor")
    assert committed == "University of Michigan-Ann Arbor"
