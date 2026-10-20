"""A main-frame scan that stalls once is retried before it is reported as an observation failure."""

import asyncio

from jobpilot.answers import Resolver
from jobpilot.forms import SCAN, FormSession
from jobpilot.schemas import Profile


class StallOnce:
    """Delegates to a real Playwright frame; its first scan stalls past the 5 s scan budget."""

    def __init__(self, frame):
        self._frame, self.calls = frame, 0

    async def evaluate(self, script, *args):
        if script is SCAN:
            self.calls += 1
        if script is SCAN and self.calls == 1:
            await asyncio.sleep(6)
        return await self._frame.evaluate(script, *args)

    def __getattr__(self, name):
        return getattr(self._frame, name)


class PageProxy:
    def __init__(self, page):
        self._page = page
        self.main = StallOnce(page.main_frame)

    @property
    def main_frame(self):
        return self.main

    @property
    def frames(self):
        return [self.main, *self._page.frames[1:]]

    def __getattr__(self, name):
        return getattr(self._page, name)


def _session(page, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    return FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)


async def test_stalled_main_frame_scan_is_retried_once_before_failure(page, tmp_path):
    await page.set_content("<label for=nick>Nickname</label><input id=nick>")
    session = _session(page, tmp_path)
    emitted = []
    session.emit = lambda kind, *args: emitted.append(kind)
    session.page = PageProxy(page)
    await session.scan()
    assert session.page.main.calls == 2
    assert "observation_failure" not in emitted
    assert any(f["label"] == "Nickname" for f in session.fields.values())
