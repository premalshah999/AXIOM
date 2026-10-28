"""BambooHR's Fabric pick lists (Performance Software, 2026-10-27): a toggle button[aria-haspopup] opens the menu and
a zero-size aria-hidden <select> beside it mirrors only the chosen value's id. Observed as that select, State, Country
and Highest Education offered nothing to pick and the run stalled."""

from jobpilot import widgets
from jobpilot.forms import SCAN

PAGE = """<form>
<label for=fab-select1>State&thinsp;*</label>
<div class=fab-Select data-fabric-component=Select>
  <div class=fab-SelectToggle__container>
    <button type=button class=fab-SelectToggle aria-haspopup=true aria-expanded=false aria-label="State –Select–">
      <div class=fab-SelectToggle__placeholder>–Select–</div>
    </button>
  </div>
  <select id=fab-select1 name="state.value" class=chzn-ignore aria-hidden=true tabindex=-1 required
    style="border:none;height:0;opacity:0;overflow:hidden;position:absolute;width:0"><option value=""></option></select>
</div>
<label for=fab-select2>Country&thinsp;*</label>
<div class=fab-Select data-fabric-component=Select>
  <div class=fab-SelectToggle__container>
    <button type=button class=fab-SelectToggle aria-haspopup=true aria-expanded=false>
      <div class=fab-SelectToggle__content>United States</div>
    </button>
  </div>
  <select id=fab-select2 name="countryId.value" class=chzn-ignore aria-hidden=true tabindex=-1 required
    style="border:none;height:0;opacity:0;overflow:hidden;position:absolute;width:0"><option value="1">1</option></select>
</div>
</form>
<div id=portal></div>
<script>
const STATES = [["3", "Alabama"], ["24", "Maryland"], ["51", "Virginia"]];
const toggle = document.querySelector('#fab-select1').parentElement.querySelector('button');
toggle.addEventListener('click', () => {
  const portal = document.getElementById('portal');
  if (portal.firstChild) { portal.innerHTML = ''; toggle.setAttribute('aria-expanded', 'false'); return; }
  const menu = document.createElement('div');
  menu.setAttribute('role', 'menu'); menu.id = 'menu1';
  for (const [id, name] of STATES) {
    const item = document.createElement('div');
    item.setAttribute('role', 'menuitem'); item.textContent = name;
    item.addEventListener('click', () => {
      toggle.innerHTML = '<div class=fab-SelectToggle__content>' + name + '</div>';
      const select = document.getElementById('fab-select1');
      select.innerHTML = '<option value="' + id + '">' + id + '</option>'; select.value = id;
      portal.innerHTML = ''; toggle.setAttribute('aria-expanded', 'false'); toggle.removeAttribute('aria-controls');
    });
    menu.appendChild(item);
  }
  portal.appendChild(menu);
  toggle.setAttribute('aria-expanded', 'true'); toggle.setAttribute('aria-controls', 'menu1');
});
</script>"""


async def test_a_fabric_toggle_is_the_field_and_commits_its_menu_item(page):
    await page.set_content(PAGE)
    data = await page.main_frame.evaluate(SCAN)
    state = next(f for f in data["fields"] if f["label"].startswith("State"))
    country = next(f for f in data["fields"] if f["label"].startswith("Country"))
    assert state["type"] == "dropdown" and state["required"] is True and state["value"] == ""
    assert country["type"] == "dropdown" and country["value"] == "United States"
    assert not [f for f in data["fields"] if f["type"] == "select"]  # the mirrors are not questions of their own
    toggle = page.locator("#fab-select1").locator("xpath=..").locator("button")
    assert await widgets.dropdown(page.main_frame, toggle, "Maryland") == "Maryland"
    assert await page.evaluate("document.getElementById('fab-select1').value") == "24"
