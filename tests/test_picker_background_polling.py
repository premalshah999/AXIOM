"""Dropdown closure must not wait for throttled background animation frames."""
import time
import pytest
from jobpilot.widgets import dismiss_pickers


async def test_delayed_menu_close_uses_timer_polling_when_animation_frames_stop(page):
    await page.set_content('''<div role=listbox id=menu><div role=option>Choice</div></div>
      <script>
      window.requestAnimationFrame=()=>0;
      document.body.addEventListener('click',()=>setTimeout(()=>menu.hidden=true,60));
      </script>''')
    started = time.monotonic()
    await dismiss_pickers(page.main_frame)
    assert not await page.locator('#menu').is_visible()
    assert time.monotonic() - started < .5


async def test_stubborn_menu_cannot_leak_choices_to_next_field(page):
    await page.set_content('''<div role=listbox><div role=option>Sibling choice</div></div>
      <script>window.requestAnimationFrame=()=>0;</script>''')
    with pytest.raises(ValueError, match='Previous dropdown remains open'):
        await dismiss_pickers(page.main_frame)
