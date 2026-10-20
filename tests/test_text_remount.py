import pytest
from jobpilot.widgets import human_type


@pytest.mark.parametrize("event", ["input", "blur"])
async def test_readback_rematches_replaced_input_without_retyping(page, event):
    await page.set_content(f'''<label>City<input data-jp-id=old name=city></label><script>
      window.writes=0;
      document.addEventListener('input',()=>window.writes++);
      document.querySelector('input').addEventListener('{event}',e=>{{
        const next=e.target.cloneNode();next.removeAttribute('data-jp-id');
        next.value=e.target.value;e.target.replaceWith(next);
      }},{{once:true}});
    </script>''')
    async def rematch():
        # The real caller rescans and binds the same question and history-row scope.
        return page.locator('input[name=city]')
    current = await human_type(page.locator('[data-jp-id=old]'), 'Example City', rematch=rematch)
    assert await current.input_value() == 'Example City'
    assert await page.evaluate('window.writes') == 1


async def test_rematch_ambiguity_does_not_write_to_a_sibling(page):
    await page.set_content('''<label>City<input data-jp-id=old onblur="this.outerHTML='<input name=city><input name=city>'"></label>''')
    async def rematch():
        raise ValueError('Ambiguous question scope')
    with pytest.raises(ValueError, match='Ambiguous question scope'):
        await human_type(page.locator('[data-jp-id=old]'), 'Example City', rematch=rematch)
    assert await page.locator('input[name=city]').evaluate_all('els=>els.every(e=>!e.value)')


async def test_form_writer_retains_exact_question_scope_after_blur_replacement(page, service, tmp_path):
    from jobpilot.answers import Resolver
    from jobpilot.forms import FormSession
    from jobpilot.schemas import Profile

    await page.set_content('''<fieldset><legend>Address</legend><label>City<input name=city></label></fieldset>
      <script>document.querySelector('input').addEventListener('blur',e=>{
        const next=e.target.cloneNode();next.removeAttribute('data-jp-id');
        next.value=e.target.value;e.target.replaceWith(next);
      },{once:true});</script>''')
    form = FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path/'unused.pdf', lambda *args: None)
    observed = await form.scan()
    city = next(f for f in observed['fields'] if f['label'] == 'City')
    assert await form.fill(city, 'Example City') == 'Example City'
    current = await form.rematch(city)
    assert current['value'] == 'Example City'
    assert current['group'] == city['group']
    assert form.expected_slots[form.field_key(current)] == 'Example City'


async def test_missing_field_diagnostic_does_not_pay_default_locator_timeout(page):
    import time
    from jobpilot.forms import FormSession

    await page.set_content('<input name=city>')
    form = object.__new__(FormSession)
    form.locator = lambda control_id: page.locator('[data-jp-id=detached]')
    start = time.monotonic()
    assert await form.control_structure('old') == {}
    assert time.monotonic() - start < 1.3


async def test_diagnostic_deadline_also_bounds_unresponsive_renderer():
    import asyncio
    import time
    from types import SimpleNamespace
    from jobpilot.forms import FormSession

    async def hung(*args, **kwargs):
        await asyncio.sleep(30)
    form = object.__new__(FormSession)
    form.locator = lambda control_id: SimpleNamespace(evaluate=hung)
    start = time.monotonic()
    assert await form.control_structure('old') == {}
    assert time.monotonic() - start < 1.3


async def test_phone_blur_remount_checks_live_value_without_retyping(page, service, tmp_path):
    from jobpilot.answers import Resolver
    from jobpilot.forms import FormSession
    from jobpilot.schemas import Profile

    await page.set_content('''<label>Phone Number<input name=phone type=tel></label><script>
      window.writes=0;document.addEventListener('input',()=>window.writes++);
      document.querySelector('input').addEventListener('blur',e=>{
        const next=e.target.cloneNode();next.removeAttribute('data-jp-id');
        next.value=e.target.value;e.target.replaceWith(next);
      },{once:true});</script>''')
    form = FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path/'unused.pdf', lambda *args: None)
    field = (await form.scan())['fields'][0]
    await form.fill(field, '2025550123')
    assert (await form.rematch(field))['value'] == '2025550123'
    assert await page.evaluate('window.writes') == 1
