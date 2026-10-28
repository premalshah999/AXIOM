"""BambooHR (Asure, 2026-10-28, seen in REA): a Fabric select toggle opens a portal role=menu of role=menuitem rows that
it names only by data-menu-id, not aria-controls. State and Highest Education read "no offered choices" on every run."""

from jobpilot import widgets

PAGE = """<label id=l>State</label>
<button type=button class=fab-SelectToggle aria-haspopup=true aria-expanded=false aria-label="State –Select–"
  data-menu-id=fab-menu86 data-jp-id=jp1 onclick="document.getElementById('fab-menu86').hidden=false;
  this.setAttribute('aria-expanded','true')">–Select–</button>
<div id=fab-menu86 role=menu class=fab-MenuList hidden>
  <div role=menuitem class=fab-MenuOption onclick="pick(this)">Alabama</div>
  <div role=menuitem class=fab-MenuOption onclick="pick(this)">Maryland</div>
  <div role=menuitem class=fab-MenuOption onclick="pick(this)">Virginia</div>
</div>
<script>function pick(o){const b=document.querySelector('button');b.textContent=o.textContent;
  document.getElementById('fab-menu86').hidden=true;b.setAttribute('aria-expanded','false')}</script>"""


async def test_a_fabric_toggle_reaches_its_portal_menu(page):
    await page.set_content(PAGE)
    chosen = await widgets.dropdown(page.main_frame, page.locator("button"), "Maryland")
    assert chosen == "Maryland"
    assert await page.inner_text("button") == "Maryland"


async def test_a_toggle_that_ignores_synthetic_clicks_gets_a_native_one(page):
    trusted_only = PAGE.replace('onclick="document', 'onclick="if(!event.isTrusted)return;document')
    await page.set_content(trusted_only)
    await page.evaluate("document.querySelector('button').dataset.jpId = 'jp1'")
    assert await widgets.dropdown(page.main_frame, page.locator("button"), "Virginia") == "Virginia"
