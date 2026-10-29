"""iCIMS's icimsDropdown (REA 2026-10-19, evidence ev_ab677d4c…, script/common/icimsDropdown.js): an
<a id="<select>_icimsDropdown" role=combobox>, a "<that id>_ctnr" container with input.dropdown-search (hidden when the
field has no search), and ul#"<select>_dropdown-results" of li.dropdown-result[role=option] rows (aria-label = option
text), 25 per page with more loaded on scroll. A keyup in the search box runs a server search after a debounce; a
click on a row writes the select (a server-searched select then holds only the pick), updates .dropdown-text,
collapses, and runs the field's onchange (Country -> State).

Atlassian's profile (2026-10-19) read the first 25 rows as the whole catalog, and then read the old first page as the
answer to a search that had not come back yet, so the country and state were "not offered"."""

import json

import pytest
import pytest_asyncio

from jobpilot import widgets

COUNTRIES = ["United States"] + [f"Country {i:03d}" for i in range(120)] + ["India", "Zambia"]
STATES = (
    ["Alabama", "Alaska", "American Samoa", "Arizona", "Arkansas", "Armed Forces Americas", "Armed Forces Europe",
     "Armed Forces Pacific", "California", "Colorado", "Connecticut", "Delaware", "District of Columbia",
     "Federated States of Micronesia", "Florida", "Georgia", "Guam", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
     "Kansas", "Kentucky", "Louisiana", "Maine", "Marshall Islands", "Maryland", "Massachusetts", "Michigan"]
)

WIDGET = """
<style>.dropdown-invisible{display:none} .dropdown-results{max-height:200px;overflow:auto;margin:0}</style>
<div id="fields"></div>
<a id="plain" role="combobox">Pick</a>
<script>
window.changes = [];
function makeDropdown(id, catalog, {ajax = true, delay = 500, search = true, onchange = () => {}, match = (t, q) => t.toLowerCase().includes(q), keep = false, first = 0} = {}) {
  document.getElementById('fields').insertAdjacentHTML('beforeend', `
    <select id="${id}" style="display:none">${ajax ? '' : catalog().map((t, i) => `<option value="${i}">${t}</option>`).join('')}</select>
    <a id="${id}_icimsDropdown" class="dropdown-select" role="combobox" tabindex="0"><span class="dropdown-text">— Make a Selection —</span></a>
    <div id="${id}_icimsDropdown_ctnr" class="dropdown-container dropdown-invisible">
      <input type="text" class="dropdown-search${search ? '' : ' dropdown-invisible'}" role="combobox">
      <div><ul id="${id}_dropdown-results" class="dropdown-results" role="listbox"></ul><span role="status"></span></div>
    </div>`);
  const select = document.getElementById(id), anchor = document.getElementById(id + '_icimsDropdown');
  const ctnr = document.getElementById(id + '_icimsDropdown_ctnr'), box = ctnr.querySelector('input');
  const ul = document.getElementById(id + '_dropdown-results'), status = ctnr.querySelector('[role=status]');
  let words = [], pages = 1, timer;
  const render = (query) => {
    const q = (query ?? box.value).toLowerCase();
    if (q) (window.searches = window.searches || []).push(id + ':' + q);
    const all = catalog().filter(t => match(t, q));
    words = all.slice(0, 25 * pages);
    status.textContent = all.length ? `${all.length} results` : 'No Results';
    ul.innerHTML = '<li class="dropdown-result" role="option" dropdown-index="-1" aria-label="— Make a Selection —">— Make a Selection —</li>' +
      words.map((t, i) => `<li class="dropdown-result result-selectable" role="option" dropdown-index="${i}" aria-label="${t}">${t}</li>`).join('');
  };
  // `first`: the first page (an empty query) answers that long after opening, whatever is typed meanwhile.
  anchor.addEventListener('click', () => { ctnr.classList.remove('dropdown-invisible'); if (!keep) box.value = ''; pages = 1;
    if (first) setTimeout(() => render(''), first); else render(); if (search) box.focus(); });
  box.addEventListener('keyup', () => { clearTimeout(timer); timer = setTimeout(() => { pages = 1; render(); }, ajax ? delay : 0); });
  ul.addEventListener('scroll', () => { if (ul.scrollTop + ul.clientHeight >= ul.scrollHeight * 0.75) { pages += 1; render(); } });
  ul.addEventListener('click', e => {
    const li = e.target.closest('li'), i = Number(li.getAttribute('dropdown-index'));
    if (i < 0) return;
    const text = words[i];
    if (ajax) { select.options.length = 0; select.add(new Option(text, 'v-' + text, true, true)); }
    else select.selectedIndex = catalog().indexOf(text);
    anchor.querySelector('.dropdown-text').textContent = text;
    ctnr.classList.add('dropdown-invisible');
    onchange(text);
  });
}
const COUNTRIES = %(countries)s, STATES = %(states)s;
// A server search that answers well after the 1.6 s the first version waited (Atlassian 2026-10-19).
makeDropdown('-1_PersonProfileFields.AddressCountry', () => COUNTRIES,
  {delay: 2200, onchange: text => { window.country = text; window.changes.push(text); }});
makeDropdown('-1_PersonProfileFields.AddressState', () => window.country === 'United States' ? STATES : []);
// No search box: the value sits past the first page and loads on scroll.
makeDropdown('-1_PersonProfileFields.rcf3217', () => STATES, {ajax: false, search: false});
makeDropdown('rcf2010', () => ['He/him/his', 'She/her/hers', 'They/them/theirs'], {ajax: false});
// PepsiCo 2026-10-27: phone country codes read "United States (1)" and the server searches the name only.
makeDropdown('rcf2045', () => ['United Kingdom (44)', 'United States (1)', 'India (91)'],
  {match: (t, q) => t.replace(/\\s*\\(\\d+\\)$/, '').toLowerCase().includes(q)});
// The widget keeps its last search: reopened, it already lists only that search's rows (PepsiCo 2026-10-27).
makeDropdown('rcf2046', () => ['United Kingdom (44)', 'United States (1)', 'India (91)'],
  {keep: true, match: (t, q) => t.replace(/\\s*\\(\\d+\\)$/, '').toLowerCase().includes(q)});
</script>"""

COUNTRY = '[id="-1_PersonProfileFields.AddressCountry_icimsDropdown"]'
STATE = '[id="-1_PersonProfileFields.AddressState_icimsDropdown"]'


@pytest_asyncio.fixture(loop_scope="session")
async def form(page):
    await page.set_content(WIDGET % {"countries": json.dumps(COUNTRIES), "states": json.dumps(STATES)})
    return page


async def test_a_slow_server_search_is_awaited_and_its_row_picked(form):
    country = form.locator(COUNTRY)
    assert await widgets.icims_dropdown(country) == "-1_PersonProfileFields.AddressCountry"
    assert await widgets.icims_select_texts(country) == []  # server-searched: the select holds no catalog
    assert await widgets.combobox(form.main_frame, country, "India") == "India"
    assert await form.evaluate(
        "(s => [s.options.length, s.options[s.selectedIndex].text])(document.getElementById('-1_PersonProfileFields.AddressCountry'))"
    ) == [1, "India"]
    assert await country.inner_text() == "India"
    assert await form.evaluate("window.changes") == ["India"]


async def test_state_is_searched_after_its_country_is_picked(form):
    await widgets.combobox(form.main_frame, form.locator(COUNTRY), "United States")
    assert await widgets.combobox(form.main_frame, form.locator(STATE), "Maryland") == "Maryland"
    assert await form.locator(STATE).inner_text() == "Maryland"


async def test_a_list_without_search_scrolls_to_a_later_page(form):
    state = form.locator('[id="-1_PersonProfileFields.rcf3217_icimsDropdown"]')
    assert len(await widgets.icims_select_texts(state)) == len(STATES)
    assert await widgets.combobox(form.main_frame, state, "Maryland") == "Maryland"
    assert await form.evaluate("document.getElementById('-1_PersonProfileFields.rcf3217').selectedIndex") == STATES.index(
        "Maryland"
    )


async def test_an_optional_picker_never_pages_the_employers_list(form):
    # Every scrolled page is a server request; optional pickers preceded both CloudFront 403s (2026-10-19). A static
    # catalog is answered from its select (2026-10-24): the widget never opens, so nothing is paged or requested.
    state = form.locator('[id="-1_PersonProfileFields.rcf3217_icimsDropdown"]')
    assert await widgets.combobox(form.main_frame, state, "Maryland", thorough=False) == "Maryland"
    assert await form.evaluate("document.getElementById('-1_PersonProfileFields.rcf3217').selectedIndex") == STATES.index(
        "Maryland"
    )
    hidden = await form.evaluate(
        "document.getElementById('-1_PersonProfileFields.rcf3217_icimsDropdown_ctnr').classList.contains('dropdown-invisible')"
    )
    assert hidden  # never opened


async def test_a_static_catalog_is_read_whole_and_picked(form):
    pronouns = form.locator('[id="rcf2010_icimsDropdown"]')
    assert await widgets.icims_select_texts(pronouns) == ["He/him/his", "She/her/hers", "They/them/theirs"]

    async def choose(offered):
        return "He/him/his" if "He/him/his" in offered else None

    assert await widgets.combobox(form.main_frame, pronouns, "He/Him", choose=choose) == "He/him/his"
    assert await form.evaluate("document.getElementById('rcf2010').selectedIndex") == 0


async def test_an_absent_choice_is_still_not_offered(form):
    with pytest.raises(ValueError, match="No matching option is offered"):
        await widgets.combobox(form.main_frame, form.locator(COUNTRY), "Atlantis")


async def test_other_comboboxes_are_not_icims_dropdowns(form):
    assert await widgets.icims_dropdown(form.locator("#plain")) is None


async def test_a_dependent_list_that_answers_late_is_searched_again(form):
    # Committing Country makes iCIMS rebuild the State list asynchronously; searched at once, State answered nothing and
    # failed its first commit on every tenant (2026-10-19). Here the list fills 1.2 s after the first keystroke.
    await form.evaluate("""() => {
      const box = document.getElementById('-1_PersonProfileFields.AddressState_icimsDropdown_ctnr').querySelector('input');
      box.addEventListener('keyup', () => { if (!window.armed) { window.armed = true;
        setTimeout(() => { window.country = 'United States'; }, 1200); } }, true);
    }""")
    assert await widgets.combobox(form.main_frame, form.locator(STATE), "Maryland") == "Maryland"


async def test_a_major_the_catalog_lacks_is_searched_as_the_fallback_and_picked_by_name(form):
    """SAS's server-searched "Field of Study" (2026-10-21) had no Data Science; a model guess among the search results
    outran the 12 s write budget on every attempt. The fallback major is searched and picked by name."""
    majors = ["Accounting", "Biology", "Computer Engineering", "Computer Science", "Criminal Justice", "Economics"]
    await form.evaluate(f"makeDropdown('15384612_PersonProfileFields.rcf2035', () => {json.dumps(majors)})")
    field = form.locator('[id="15384612_PersonProfileFields.rcf2035_icimsDropdown"]')

    def by_name(texts):
        return next((t for t in texts if t == "Computer Science"), None)

    async def no_guess(texts):
        return None

    picked = await widgets.combobox(
        form.main_frame, field, "Data Science", choose=no_guess, resolve=by_name, search_queries=["Computer Science"]
    )
    assert picked == "Computer Science"
    assert await field.inner_text() == "Computer Science"


async def test_a_dial_code_row_is_searched_by_its_country_name(form):
    code = form.locator('[id="rcf2045_icimsDropdown"]')
    assert await widgets.combobox(form.main_frame, code, "United States (1)") == "United States (1)"
    assert [q for q in await form.evaluate("window.searches || []") if q.startswith("rcf2045:")] == [
        "rcf2045:united states"
    ]


async def test_a_prefilled_dial_code_is_kept_without_a_search(form):
    """PepsiCo 2026-10-27: the profile page held "United States (1)" from the account; searching for it again
    outlasted the field's deadline on every run."""
    await form.evaluate("""() => {
      const s = document.getElementById('rcf2045'); s.add(new Option('United States (1)', 'v-us', true, true));
      document.querySelector('[id="rcf2045_icimsDropdown"] .dropdown-text').textContent = 'United States (1)';
      window.searches = [];
    }""")
    code = form.locator('[id="rcf2045_icimsDropdown"]')
    assert await widgets.combobox(form.main_frame, code, "United States (1)") == "United States (1)"
    assert await form.evaluate("window.searches") == []


async def test_a_country_name_alone_matches_its_dial_code_row(form):
    """A repair answered PepsiCo's phone code "United States"; the rows read "United States (1)" (2026-10-27)."""
    code = form.locator('[id="rcf2045_icimsDropdown"]')
    assert await widgets.combobox(form.main_frame, code, "United States") == "United States (1)"
    await form.evaluate("window.searches = []")
    assert await widgets.combobox(form.main_frame, code, "United States") == "United States (1)"
    assert await form.evaluate("window.searches") == [], "the held row is kept without a search"


async def test_a_search_answered_by_the_rows_already_shown_does_not_wait_out_the_field(form):
    import time

    ctnr = '[id="rcf2046_icimsDropdown_ctnr"]'
    await form.evaluate(f"document.querySelector('{ctnr} input').value = 'united states'")
    await form.locator('[id="rcf2046_icimsDropdown"]').click()
    before = await widgets.icims_rows_now(form.main_frame, "rcf2046")
    assert before == [("0", "United States (1)")]
    await form.locator(f"{ctnr} input").fill("")
    await form.locator(f"{ctnr} input").press_sequentially("United States", delay=35)
    started = time.monotonic()
    rows = await widgets.icims_rows(form.main_frame, "rcf2046", before=before, timeout=6, query="United States")
    assert rows == before and time.monotonic() - started < 3


async def test_the_first_page_that_answers_after_opening_is_not_read_as_the_search(form):
    """Publicis and Medpace (2026-10-26..27): the Country list's first page ("United Kingdom", "Afghanistan", ...)
    answered while "United States" was being typed and was read as the search's answer; the alias searches then found
    nothing and the field outlasted its deadline. The first page is awaited before the search is typed."""
    import time

    catalog = ["United Kingdom"] + [f"Country {i:03d}" for i in range(40)] + ["United States", "Uruguay"]
    await form.evaluate(
        f"makeDropdown('-1_PersonProfileFields.AddressCountry2', () => {json.dumps(catalog)}, {{first: 700, delay: 1500}})"
    )
    country = form.locator('[id="-1_PersonProfileFields.AddressCountry2_icimsDropdown"]')
    started = time.monotonic()
    assert await widgets.combobox(form.main_frame, country, "United States", thorough=False) == "United States"
    assert time.monotonic() - started < 6  # one search, inside an optional picker's budget
