"""Readiness follows form changes rather than unrelated page activity."""
import time
from jobpilot.forms import FormSession


def form_for(page):
    form = object.__new__(FormSession)
    form.network = None
    async def frames():
        return [page.main_frame]
    form.application_frames = frames
    form.emit = lambda *args: None
    return form


async def test_decorative_and_hidden_ticks_do_not_hold_form_readiness(page):
    await page.set_content('''<form><label>Name<input></label></form>
      <footer id=clock></footer><div hidden id=template></div><script>
      setInterval(()=>{clock.textContent=Date.now();template.innerHTML='<input value="'+Date.now()+'">'}, 20);
      </script>''')
    start = time.monotonic()
    assert await form_for(page).settle(timeout=1.5, quiet=.15)
    assert time.monotonic() - start < .9


async def test_changing_question_does_not_become_ready_until_stable(page):
    await page.set_content('''<form><label id=question>Name</label><input></form><script>
      window.tick=setInterval(()=>question.textContent='Question '+Date.now(),20);
      </script>''')
    form = form_for(page)
    assert not await form.settle(timeout=.35, quiet=.15)
    await page.evaluate('clearInterval(tick)')
    assert await form.settle(timeout=1, quiet=.15)


async def test_visible_loading_indicator_still_blocks_readiness(page):
    await page.set_content('<form aria-busy=true><label>Name<input></label></form>')
    form = form_for(page)
    assert not await form.settle(timeout=.3, quiet=.1)
    await page.locator('form').evaluate("e => e.setAttribute('aria-busy', 'false')")
    assert await form.settle(timeout=1, quiet=.1)
