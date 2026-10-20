"""One provider snapshot must prove value, validity and closure together."""
import asyncio
import pytest
from jobpilot.widgets_ats import _wait_committed


@pytest.mark.parametrize('family', ['oracle', 'dayforce'])
async def test_readback_survives_browser_roundtrip_latency(family):
    class SlowControl:
        async def count(self):
            await asyncio.sleep(.22)
            return 1
        async def blur(self, **kwargs): await asyncio.sleep(.22)
        async def evaluate(self, script, *args, **kwargs):
            await asyncio.sleep(.22)
            return {'value': 'Maryland', 'closed': True, 'valid': True}
        async def input_value(self, **kwargs):
            raise AssertionError('Value must share the same snapshot as validity/closure')
        async def get_attribute(self, *args, **kwargs):
            raise AssertionError('No independent cross-render attribute reads')
    assert await _wait_committed(SlowControl(), family, 'Maryland',
                                  {'label': 'State', 'type': 'combobox'}) == 'Maryland'
