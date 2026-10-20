"""A provider may hide and reuse its catalog instead of rebuilding its nodes."""
import pytest
from jobpilot import widgets


async def test_rcm_catalog_marks_are_scoped_to_current_opening(page):
    await page.set_content('<input><div id=menu><a>Maryland</a></div>')
    await page.evaluate(widgets.RCM_MARK)
    await page.locator('#menu').evaluate("e=>e.hidden=true")
    await page.evaluate(widgets.RCM_MARK)
    await page.locator('#menu').evaluate("e=>e.hidden=false")
    assert await page.evaluate(widgets.RCM_FIND, ['Maryland']) == 1


async def test_rcm_duplicate_visible_labels_do_not_mark_arbitrary_choice(page):
    await page.set_content('<input><div id=menu hidden><a>Maryland</a><a>Maryland</a></div>')
    await page.evaluate(widgets.RCM_MARK)
    await page.locator('#menu').evaluate("e=>e.hidden=false")
    assert await page.evaluate(widgets.RCM_FIND, ['Maryland']) == 2
    assert await page.locator('[data-ap-pick]').count() == 0


async def test_rcm_driver_can_select_from_reused_catalog_twice(page):
    await page.set_content('''<input id="pick_input" class=rcmpaginatedselectinput role=combobox>
      <button id="pick_selectButton" type=button>Open</button>
      <div id=menu hidden><a>Maryland</a></div><script>
      const input=document.getElementById('pick_input'),menu=document.getElementById('menu');
      input.addEventListener('input',()=>{menu.hidden=false});
      document.getElementById('pick_selectButton').onclick=()=>{menu.hidden=false};
      menu.onclick=()=>{input.value='Maryland';menu.hidden=true};
      </script>''')
    el = page.locator('#pick_input')
    assert await widgets._rcm_paginated(page.main_frame, el, 'MD') == 'Maryland'
    # A later scan/attempt has previously seen the catalog while it was visible.
    await page.locator('#menu').evaluate('e=>e.hidden=false')
    await page.evaluate(widgets.RCM_MARK)
    await page.locator('#menu').evaluate('e=>e.hidden=true')
    assert await widgets._rcm_paginated(page.main_frame, el, 'MD') == 'Maryland'
