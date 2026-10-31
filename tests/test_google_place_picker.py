"""A Google Places box keeps only a place chosen from its suggestions. IXL's <gmpx-place-picker> (2026-10-31) took the typed
city, left its hidden location input empty, and the form answered "This field is required" on every run."""

import pytest

from jobpilot import widgets
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

SUGGESTIONS = """
const box = document.getElementById('box'), kept = document.getElementById('kept');
const list = document.createElement('div');
list.className = 'pac-container';
list.style.display = 'none';
document.body.append(list);
const places = [['Springfield', 'OR, USA'], ['Springfield', 'IL, USA'], ['Springfield Gardens', 'NY, USA']];
box.addEventListener('input', () => {
  list.innerHTML = '';
  list.style.display = box.value ? 'block' : 'none';
  if (!box.value) return;
  for (const [city, region] of places) {
    const row = document.createElement('div');
    row.className = 'pac-item';
    row.innerHTML = '<span class="pac-icon"></span><span class="pac-item-query">' + city + '</span><span>' + region + '</span>';
    row.addEventListener('mousedown', () => { box.value = city + ', ' + region; kept.value = box.value; list.style.display = 'none'; });
    list.append(row);
  }
});
"""


def plain_page():
    return f"""<form><label for=box>Your Location (Search by City)</label>
    <input id=box class="pac-target-input" required><input id=kept type=hidden name=location>
    </form><script>{SUGGESTIONS}</script>"""


def picker_page():
    return f"""<form><label for=host>Your Location (Search by City)</label>
    <gmpx-place-picker id=host type="(cities)"></gmpx-place-picker><input id=kept type=hidden name=location>
    </form><script>
    const host = document.getElementById('host');
    const root = host.attachShadow({{mode: 'open'}});
    root.innerHTML = '<input id=box>';
    const realGet = document.getElementById.bind(document);
    document.getElementById = id => id === 'box' ? root.getElementById('box') : realGet(id);
    {SUGGESTIONS}
    </script>"""


def session(page, service, tmp_path):
    profile = Profile(address={"city": "Springfield", "state": "IL", "country": "USA"})
    return FormSession(page, Resolver(profile, service.db, service.config), tmp_path / "r.pdf", lambda *a, **k: None)


@pytest.mark.parametrize("markup", [plain_page, picker_page])
async def test_the_applicants_place_is_chosen_and_the_page_keeps_it(page, service, tmp_path, markup):
    await page.set_content(markup())
    form = session(page, service, tmp_path)
    box = page.locator("#host #box, #box").first
    assert await widgets.is_place_search(box)
    choose = lambda texts: next((t for t in texts if "IL" in t), None)  # noqa: E731
    shown = await widgets.pick_place(page, box, "Springfield, IL", choose)
    assert shown == "Springfield, IL, USA"
    assert await page.locator("#kept").input_value() == "Springfield, IL, USA"
    assert form is not None


@pytest.mark.parametrize("markup", [plain_page, picker_page])
async def test_the_form_fills_a_place_box_through_its_suggestions(page, service, tmp_path, markup):
    await page.set_content(markup())
    form = session(page, service, tmp_path)
    field = next(f for f in (await form.scan())["fields"] if f["label"].startswith("Your Location"))
    await form.fill(field, "Springfield, IL")
    assert await page.locator("#kept").input_value() == "Springfield, IL, USA"


async def test_with_no_address_rule_the_first_suggestion_naming_the_city_is_taken(page):
    await page.set_content(plain_page())
    assert await widgets.pick_place(page, page.locator("#box"), "Springfield, IL") == "Springfield, OR, USA"


async def test_no_matching_suggestion_picks_nothing(page):
    await page.set_content(plain_page())
    box = page.locator("#box")
    assert await widgets.pick_place(page, box, "Atlantis", timeout=0.3) is None
    assert await page.locator("#kept").input_value() == ""


async def test_a_plain_text_box_is_not_a_place_search(page):
    await page.set_content("<input id=box>")
    assert not await widgets.is_place_search(page.locator("#box"))
