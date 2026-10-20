"""A complete, short dropdown goes straight to the applicant-context choice (REA analysis follow-up, 2026-10-17).

Workday renders every row of a short list, so keyboard type-ahead (0.9 s pause, typing, then a wait, for up to four
aliases) cannot reveal another option there. Slate Auto's Degree list is GED/High School/AA/AS/BA/BS/MA/MS/MBA/PhD/JD;
"BE" took 8.3 s on the first fast Workday runs because type-ahead ran before the owner policy chose BS.
"""

import time
from unittest.mock import AsyncMock

from jobpilot import widgets

MENU = """<button id=degree aria-haspopup=listbox aria-expanded=false aria-controls=list
  onclick="const l=document.getElementById('list');l.hidden=!l.hidden;this.setAttribute('aria-expanded',String(!l.hidden))">Select One</button>
<div id=list role=listbox hidden style="max-height:400px;overflow-y:auto">{options}</div>
<script>
window.typed='';document.addEventListener('keydown',e=>{{if(e.key.length===1)window.typed+=e.key}});
document.querySelectorAll('[role=option]').forEach(o=>o.addEventListener('click',()=>{{
  const b=document.getElementById('degree');b.textContent=o.textContent;
  document.getElementById('list').hidden=true;b.setAttribute('aria-expanded','false');
}}));
</script>"""


def options(*names):
    return "".join(f"<div role=option>{n}</div>" for n in names)


async def test_short_complete_list_chooses_without_type_ahead(page):
    degrees = ["GED", "High School", "AA", "AS", "BA", "BS", "MA", "MS", "MBA", "PhD", "JD"]
    await page.set_content(MENU.format(options=options(*degrees)))
    choose = AsyncMock(return_value="BS")
    started = time.monotonic()
    committed = await widgets.dropdown(page.main_frame, page.locator("#degree"), "BE", choose=choose)
    assert committed == "BS"
    assert time.monotonic() - started < 2
    assert await page.evaluate("window.typed") == ""
    choose.assert_awaited_once_with(degrees)


async def test_an_exact_option_still_needs_no_choice(page):
    await page.set_content(MENU.format(options=options("Yes", "No")))
    choose = AsyncMock()
    assert await widgets.dropdown(page.main_frame, page.locator("#degree"), "No", choose=choose) == "No"
    choose.assert_not_awaited()


VIRTUAL = """<button id=degree aria-haspopup=listbox aria-expanded=false aria-controls=list>Select One</button>
<div id=list role=listbox hidden style="max-height:90px;overflow-y:auto"></div>
<script>
const all={names};let start=0;window.typed='';let buffer='';let timer=null;
const list=document.getElementById('list'),b=document.getElementById('degree');
function render(){{list.innerHTML='';all.slice(start,start+10).forEach(n=>{{const o=document.createElement('div');
  o.setAttribute('role','option');o.textContent=n;o.onclick=()=>{{b.textContent=n;list.hidden=true;b.setAttribute('aria-expanded','false')}};
  list.appendChild(o)}})}}
b.onclick=()=>{{list.hidden=!list.hidden;b.setAttribute('aria-expanded',String(!list.hidden));render()}};
document.addEventListener('keydown',e=>{{if(e.key.length!==1)return;window.typed+=e.key;buffer+=e.key.toLowerCase();
  clearTimeout(timer);timer=setTimeout(()=>buffer='',800);
  const i=all.findIndex(n=>n.toLowerCase().startsWith(buffer));if(i>=0){{start=i;render()}}}});
</script>"""


async def test_a_virtualized_list_types_the_full_name_and_takes_the_policy_equivalent(page):
    # Workday Degree lists render about ten rows at a time (react-virtualized). ABB's Education 2 "BE" timed out at
    # 12 s: "BE" was typed first and the owner's equivalent was chosen only after every alias.
    import json

    names = [f"A{i:02d} Associate Program" for i in range(40)] + [
        "Bachelor of Arts", "Bachelor's Degree", "Bachelor of Science", "Certificate"
    ] + [f"M{i:02d} Master Program" for i in range(30)]
    await page.set_content(VIRTUAL.format(names=json.dumps(names)))
    choose = AsyncMock()
    started = time.monotonic()
    committed = await widgets.dropdown(
        page.main_frame, page.locator("#degree"), "BE", choose=choose,
        prefer=lambda offered: "Bachelor's Degree" if "Bachelor's Degree" in offered else None,
    )
    assert committed == "Bachelor's Degree"
    assert time.monotonic() - started < 6
    assert (await page.evaluate("window.typed")).lower().startswith("bachelor")
    choose.assert_not_awaited()
