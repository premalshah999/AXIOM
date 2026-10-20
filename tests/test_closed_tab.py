"""A closed or crashed application tab fails fast and is retried as browser_unavailable.

Measured 2026-10-13: four same-site Workday tabs died together at 19:34; each run kept
working against the dead page for 14 minutes and ended as a control failure.
"""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import BrowserTabClosed, FormSession
from jobpilot.schemas import Profile


async def test_scan_of_a_closed_page_raises_tab_closed(page, tmp_path):
    await page.set_content("<form><input name=a></form>")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    await page.close()
    with pytest.raises(BrowserTabClosed):
        await form.scan()
