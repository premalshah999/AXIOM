"""The applicant's own location is a search-only typeahead (REA live benchmark 2026-10-18).

Greenhouse "Location (City)" (Geocode Earth search), Ashby "Where are you located?" and SmartRecruiters "City" list
nothing until the city is typed, yet discovery opened each one and waited for options: 1.4-2.3 s for zero choices,
then "No offered choices were readable". The resolver answers these from the profile and the driver types the city and
picks the offered row, so discovery skips them. Fixed lists (an office location picker) are still read.
"""

import time

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

TYPEAHEAD = """<label id=l1 for=loc>{label}</label>
<input id=loc role=combobox aria-autocomplete=list aria-expanded=false aria-labelledby=l1
  onkeyup="const m=document.getElementById('m');m.hidden=!this.value;m.innerHTML=this.value?'<div role=option>Springfield, Maryland, United States</div>':''">
<div id=m role=listbox hidden></div>"""

FIXED = """<label id=l2 for=office>Preferred work location</label>
<input id=office role=combobox aria-autocomplete=list aria-expanded=false aria-labelledby=l2 aria-controls=offices
  onclick="document.getElementById('offices').hidden=false;this.setAttribute('aria-expanded','true')">
<div id=offices role=listbox hidden><div role=option>Denver, CO</div><div role=option>Long Beach, CA</div></div>"""


def session(page, service, tmp_path, events):
    return FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path / "r.pdf",
                       lambda kind, message, data=None: events.append((kind, message)))


async def test_applicant_location_typeahead_is_not_opened_for_choices(page, service, tmp_path):
    for label in ("Location (City)", "Where are you located?", "City", "Where are you based from today?"):
        await page.set_content(TYPEAHEAD.format(label=label))
        events = []
        form = session(page, service, tmp_path, events)
        field = next(f for f in (await form.scan())["fields"] if f["label"] == label)
        started = time.monotonic()
        await form.discover_options(field)
        assert time.monotonic() - started < 0.5, label
        assert field["options"] == [] and field["options_partial"] and field.get("search_only")
        assert not any(kind == "option_observation" for kind, _ in events)


async def test_a_fixed_location_list_is_still_read(page, service, tmp_path):
    await page.set_content(FIXED)
    form = session(page, service, tmp_path, [])
    field = next(f for f in (await form.scan())["fields"] if f["label"] == "Preferred work location")
    await form.discover_options(field)
    assert field["options"] == ["Denver, CO", "Long Beach, CA"]


GEOCODER = """<label id=l1 for=loc>Location (City)</label>
<input id=loc role=combobox aria-autocomplete=list aria-expanded=false aria-labelledby=l1 aria-controls=m>
<div id=m role=listbox hidden></div>
<script>
const box = document.getElementById('loc'), menu = document.getElementById('m');
const places = ['Springfield, Illinois, United States', 'Springfield, Maryland, United States'];
box.addEventListener('input', () => {
  const q = box.value.trim().toLowerCase();
  // A place search: the whole "City, State, Country" string finds nothing, the city name does.
  const rows = q && !q.includes(',') ? places.filter(p => p.toLowerCase().startsWith(q)) : [];
  menu.innerHTML = rows.map(p => `<div role=option>${p}</div>`).join('');
  menu.hidden = !rows.length; box.setAttribute('aria-expanded', String(rows.length > 0));
});
menu.addEventListener('click', ev => {
  const row = ev.target.closest('[role=option]'); if (!row) return;
  box.value = row.textContent; menu.hidden = true; box.setAttribute('aria-expanded', 'false');
});
</script>"""


async def test_a_location_typeahead_is_searched_by_city_and_takes_the_applicants_state(page, service, tmp_path):
    """Lyft (Greenhouse) 2026-10-25: the whole "City, State, Country" found nothing, and the run stalled."""
    from jobpilot.schemas import Address

    await page.set_content(GEOCODER)
    profile = Profile(address=Address(city="Springfield", state="MD", country="United States"))
    form = FormSession(page, Resolver(profile, service.db, service.config), tmp_path / "r.pdf", lambda *a: None)
    field = next(f for f in (await form.scan())["fields"] if f["label"] == "Location (City)")
    committed = await form.fill(field, "Springfield, Maryland, United States")
    assert committed == "Springfield, Maryland, United States"
    assert await page.locator("#loc").input_value() == "Springfield, Maryland, United States"
