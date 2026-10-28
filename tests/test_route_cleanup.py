"""Route cleanup failures cannot replace the navigation result."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from jobpilot.browser import BrowserEngine


@pytest.mark.parametrize('stage', ['script', 'detach'])
@pytest.mark.parametrize('navigation_fails', [False, True])
async def test_route_cleanup_preserves_navigation_outcome(stage, navigation_fails):
    async def send(command, params=None):
        if command == 'Page.addScriptToEvaluateOnNewDocument':
            return {'identifier': 'temporary-script'}
        if command == 'Page.removeScriptToEvaluateOnNewDocument' and stage == 'script':
            raise TimeoutError('cleanup')
        return {}
    cdp = SimpleNamespace(send=AsyncMock(side_effect=send), detach=AsyncMock(
        side_effect=TimeoutError('detach') if stage == 'detach' else None))
    page = SimpleNamespace(context=SimpleNamespace(new_cdp_session=AsyncMock(return_value=cdp)),
                           goto=AsyncMock(side_effect=ValueError('navigation') if navigation_fails else None,
                                          return_value='loaded'))
    engine = SimpleNamespace(emit=Mock())
    call = BrowserEngine.open_application_route(engine, page, 'https://careers.example.test/apply',
                                                {'session_storage': {'native': 'synthetic'}})
    if navigation_fails:
        with pytest.raises(ValueError, match='navigation'):
            await call
    else:
        assert await call == 'loaded'
    cdp.detach.assert_awaited_once()
    engine.emit.assert_called_once()
