"""A page that has not changed is not scanned again (speed2); a page that has, always is.

The skip is only worth its 2-4% if no change the scanner can see goes unnoticed. Every case below changes the page after a scan
and expects the next plain scan() to show it. Synthetic pages.
"""

import pytest
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile
from jobpilot.answers import Resolver

pytestmark = pytest.mark.browser


def form(page, service, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    return FormSession(page, Resolver(Profile(), service.db, service.config), resume, lambda *a, **k: None)


def labels(obs):
    return sorted(f["label"] for f in obs["fields"])


async def test_an_untouched_page_is_read_once(page, service, tmp_path):
    await page.set_content("<label for=a>First</label><input id=a>")
    session = form(page, service, tmp_path)
    first = await session.scan()
    assert await session.scan() is first


async def test_a_field_added_to_the_page_shows_in_the_next_scan(page, service, tmp_path):
    await page.set_content("<label for=a>First</label><input id=a><div id=m></div>")
    session = form(page, service, tmp_path)
    await session.scan()
    await page.evaluate("document.getElementById('m').innerHTML = '<label for=b>Second</label><input id=b>'")
    assert labels(await session.scan()) == ["First", "Second"]


SHADOW = (
    "<div id=host></div><script>"
    "const r = document.getElementById('host').attachShadow({mode:'open'});"
    "r.innerHTML = '<label for=a>First</label><input id=a>';</script>"
)


def view(obs):
    """What a step acts on: the fields with their values, the errors and the controls."""
    return ([(f["label"], f.get("value")) for f in obs["fields"]], obs["errors"], [c.get("label") for c in obs["controls"]])


async def same_as_a_fresh_read(session):
    skipped = view(await session.scan())
    return skipped, view(await session.scan(force=True))


async def test_a_field_added_inside_a_shadow_root_shows_in_the_next_scan(page, service, tmp_path):
    await page.set_content(SHADOW)
    session = form(page, service, tmp_path)
    before = view(await session.scan())
    await page.evaluate("document.getElementById('host').shadowRoot.innerHTML += '<label for=b>Second</label><input id=b>'")
    skipped, fresh = await same_as_a_fresh_read(session)
    assert fresh != before, "the scanner does not read shadow roots here; the case proves nothing"
    assert skipped == fresh


async def test_a_value_typed_inside_a_shadow_root_shows_in_the_next_scan(page, service, tmp_path):
    await page.set_content(SHADOW)
    session = form(page, service, tmp_path)
    before = view(await session.scan())
    await page.evaluate("document.getElementById('host').shadowRoot.getElementById('a').value = 'typed'")
    skipped, fresh = await same_as_a_fresh_read(session)
    assert fresh != before, "the scanner does not read shadow roots here; the case proves nothing"
    assert skipped == fresh


async def test_a_message_shown_by_a_stylesheet_rule_shows_in_the_next_scan(page, service, tmp_path):
    # No element, attribute or text changes: only a rule makes the error visible.
    await page.set_content(
        "<label for=a>First</label><input id=a required><div id=e role=alert style=\"display:none\">Required</div>"
        "<style id=s></style>"
    )
    session = form(page, service, tmp_path)
    before = view(await session.scan())
    await page.evaluate("document.getElementById('s').sheet.insertRule('#e{display:block !important}')")
    skipped, fresh = await same_as_a_fresh_read(session)
    assert fresh != before, "the scanner does not read this message; the case proves nothing"
    assert skipped == fresh
