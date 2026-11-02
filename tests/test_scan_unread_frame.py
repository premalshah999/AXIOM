"""A readable page beside an application frame that cannot be read is not a complete observation, so it cannot verify
(CODEX_DIAGNOSTICS_FOLLOWUP: GSI's form had five required inputs the scan never listed)."""

from jobpilot.answers import Resolver
from jobpilot.forms import SCAN, FormSession
from jobpilot.schemas import Profile

CHROME = "<label for=nick>Nickname</label><input id=nick>"
FRAME = "<label for=first>Legal First Name *</label><input id=first required>"


class UnreadableFrame:
    """Delegates to a real Playwright frame whose scan always fails."""

    def __init__(self, frame):
        self._frame = frame

    async def evaluate(self, script, *args):
        if script is SCAN:
            raise RuntimeError("frame was navigated away")
        return await self._frame.evaluate(script, *args)

    def __getattr__(self, name):
        return getattr(self._frame, name)


class PageProxy:
    def __init__(self, page):
        self._page = page
        self.children = [UnreadableFrame(f) for f in page.frames[1:]]

    @property
    def frames(self):
        return [self._page.main_frame, *self.children]

    def __getattr__(self, name):
        return getattr(self._page, name)


def _session(page, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    session = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    session.emit = lambda *a, **k: None
    return session


async def test_an_unread_application_frame_fails_the_observation_and_blocks_verification(page, tmp_path):
    await page.set_content(f'{CHROME}<iframe srcdoc="{FRAME}" width=600 height=500></iframe>')
    session = _session(page, tmp_path)
    session.page = PageProxy(page)
    obs = await session.scan(force=True)
    assert obs["observation_failure"] is True and obs["scan_success"] is True
    assert [e["application_frame"] for e in obs["frame_errors"]] == [True]
    report = await session.verify()
    assert report["ok"] is False and report["observation_failure"] is True


async def test_an_unread_badge_sized_frame_does_not_block(page, tmp_path):
    await page.set_content(f'{CHROME}<iframe srcdoc="{FRAME}" width=20 height=20></iframe>')
    session = _session(page, tmp_path)
    session.page = PageProxy(page)
    obs = await session.scan(force=True)
    assert obs["observation_failure"] is False
    assert not any(e.get("application_frame") for e in obs["frame_errors"])


async def test_a_page_whose_frames_all_read_is_unchanged(page, tmp_path):
    await page.set_content(f'{CHROME}<iframe srcdoc="{FRAME}" width=600 height=500></iframe>')
    session = _session(page, tmp_path)
    obs = await session.scan(force=True)
    assert obs["observation_failure"] is False and obs["frame_errors"] == []
    assert {f["label"] for f in session.fields.values()} >= {"Nickname"}
