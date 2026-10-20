import asyncio
import time
from types import SimpleNamespace
from jobpilot import widgets
from jobpilot.forms import FormSession


async def test_plain_text_uses_input_change_and_readback_without_key_delays(page):
    await page.set_content('''<input id=f oninput="window.inputs=(window.inputs||0)+1"
      onchange="window.changes=(window.changes||0)+1"
      onkeydown="window.keys=(window.keys||0)+1">''')
    await widgets.human_type(page.locator('#f'), 'A sufficiently long ordinary text field')
    assert await page.locator('#f').input_value() == 'A sufficiently long ordinary text field'
    assert await page.evaluate('window.inputs > 0 && window.changes > 0 && !window.keys')


async def test_rejected_fill_gets_one_keyboard_fallback(page):
    await page.set_content('''<input id=f oninput="if(!window.typed)this.value=''"
      onkeydown="window.typed=true">''')
    await widgets.human_type(page.locator('#f'), 'Example')
    assert await page.locator('#f').input_value() == 'Example'


async def test_ready_field_click_does_not_wait_for_native_animation_frames(page, monkeypatch):
    await page.set_content('<button id=f onclick="window.clicked=(window.clicked||0)+1">Choice</button>')
    async def native_click(*args, **kwargs):
        raise AssertionError('Ready field must not enter native stability wait')
    monkeypatch.setattr(type(await page.locator('#f').element_handle()), 'click', native_click)
    await widgets.field_click(page.locator('#f'))
    assert await page.evaluate('window.clicked') == 1


async def test_covered_or_disabled_field_is_not_dispatched(page):
    await page.set_content('''<button id=f onclick="window.clicked=true" disabled>Choice</button>
      <div style="position:fixed;inset:0;z-index:10"></div>''')
    handle = await page.locator('#f').element_handle()
    assert not await widgets.dispatch_owned_click(handle)
    assert not await page.evaluate('!!window.clicked')


async def test_settle_deadline_includes_hung_renderer():
    class HungFrame:
        async def evaluate(self, *args):
            await asyncio.sleep(30)
    async def frames():
        return [HungFrame()]
    form=object.__new__(FormSession)
    form.application_frames=frames
    form.network=None
    start=time.monotonic()
    assert not await form.settle(timeout=.05)
    assert time.monotonic()-start < .5


async def test_field_write_bounds_nested_widget_retries(monkeypatch):
    import pytest
    from jobpilot import forms
    form=object.__new__(FormSession)
    async def hung(*args):
        await asyncio.sleep(30)
    form._fill=hung
    form.emit=lambda *args: None
    monkeypatch.setattr(forms, 'FIELD_WRITE_SECONDS', .02)
    start=time.monotonic()
    with pytest.raises(TimeoutError):
        await form.fill({'type':'combobox','label':'Country'}, 'United States')
    assert time.monotonic()-start < .5


async def test_alias_option_does_not_wait_for_literal_query_timeout(page):
    await page.set_content('<div role=listbox id=menu><div role=option>United States</div></div>')
    start=time.monotonic()
    result=await widgets.wait_options(page.main_frame, 'menu', query='United States of America', timeout=2)
    assert result[0]['text']=='United States'
    assert time.monotonic()-start < 1


def test_telemetry_does_not_hold_form_readiness_but_application_requests_do():
    from jobpilot.forms import NetworkMonitor
    class Target:
        def on(self, *args): pass
    class Request:
        resource_type='fetch'
        def __init__(self,url):self.url=url
    monitor=NetworkMonitor(Target())
    monitor.last=0
    monitor._start(Request('https://www.google-analytics.com/g/collect'))
    assert monitor.quiet(0)
    real=Request('https://employer.test/application/country-options')
    monitor._start(real)
    assert not monitor.quiet(0)
    monitor._end(real)
    assert monitor.quiet(0)
