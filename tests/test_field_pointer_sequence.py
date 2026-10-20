"""Fast field actions must retain pointer/mouse semantics used by real controls."""
from jobpilot import widgets


async def test_combobox_opens_on_mousedown_without_needing_an_onclick_handler(page):
    await page.set_content('''<div id=owner><input id=field role=combobox aria-controls=menu aria-expanded=false></div>
      <div role=listbox id=menu hidden><div role=option onclick="field.value='Maryland';menu.hidden=true;field.setAttribute('aria-expanded','false')">Maryland</div></div>
      <script>
      owner.addEventListener('mousedown', e=>{
        e.preventDefault();menu.hidden=false;field.setAttribute('aria-expanded','true');
      });
      </script>''')
    assert await widgets.combobox(page.main_frame, page.locator('#field'), 'Maryland') == 'Maryland'


async def test_field_pointer_sequence_dispatches_once_and_never_submits_parent_form(page):
    await page.set_content('''<form onsubmit="window.submitted=true;return false">
      <button id=field>Choose</button></form><script>
      window.events=[];
      for(const type of ['pointerdown','mousedown','pointerup','mouseup','click'])
        field.addEventListener(type,()=>events.push(type));
      </script>''')
    await widgets.field_click(page.locator('#field'))
    assert await page.evaluate('events') == ['pointerdown','mousedown','pointerup','mouseup','click']
    assert not await page.evaluate('!!window.submitted')
    assert await page.locator('#field').get_attribute('type') is None


async def test_pointerdown_replacement_is_not_followed_by_click_on_detached_node(page):
    await page.set_content('''<button id=field onmousedown="this.remove()" onclick="window.lateClick=true">Choice</button>''')
    await widgets.field_click(page.locator('#field'))
    assert not await page.evaluate('!!window.lateClick')
    assert await page.locator('#field').count() == 0
