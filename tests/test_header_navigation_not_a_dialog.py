"""Two Sigma (Avature, 2026-10-29): the desktop header's menu bar is an aria-modal div labelled "navigation". The scan
read it as the active dialog, the job page as unrecognized, and the guided agent clicked "Careers" and "View open
roles" until it stalled, never reaching Apply. A compact header navigation is not a dialog; a full-screen menu is."""

import pytest

from jobpilot import widgets
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

NAV = """<header><div role=dialog aria-modal=true aria-label="navigation" class="globalHeader__menu"
  style="display:flex;width:100%%;height:%s"><a href="#">About Us</a><a href="#">Careers</a><button>Search</button></div></header>
<main style="margin-top:120px"><h1>Software Engineering Full-Time Campus Hire</h1><a id=apply href="#apply">Apply</a></main>"""


async def test_a_compact_header_navigation_is_not_the_active_dialog(page, tmp_path):
    await page.set_content(NAV % "69px")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a, **k: None)
    assert (await form.scan())["dialogs"] == []
    await widgets.ensure_uncovered(page.locator("#apply"))


async def test_a_full_screen_menu_still_is(page, tmp_path):
    await page.set_content(NAV % "100vh")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a, **k: None)
    assert [d["title"] for d in (await form.scan())["dialogs"]] == ["navigation"]
    with pytest.raises(ValueError, match="covered"):
        await widgets.ensure_uncovered(page.locator("#apply"))
