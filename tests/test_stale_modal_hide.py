"""Montenson's ADP tax-credit questionnaire (2026-10-29): its focus trap hid the wizard (aria-hidden, marked
data-old-aria-hidden) while the "Quick tip" pop-up was open and left it hidden after the tip closed, so the scan saw
no questions and no Next. With no modal open, a focus-trap library's hide is not hidden; the page's own aria-hidden
and a hide behind a still-open modal are."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

QUESTION = """<main {attrs}><span id=q>Did you or a household member receive SNAP in the past 6 months?</span>
<div role=radiogroup aria-labelledby=q><label><input type=radio name=a value=YES>Yes</label>
<label><input type=radio name=a value=NO>No</label></div><button>Next</button></main>"""
MODAL = '<div role=dialog aria-modal=true style="width:400px"><p>Quick tip</p><button>Okay</button></div>'


async def scan(page, tmp_path, html):
    await page.set_content(html)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a, **k: None)
    obs = await form.scan()
    return [f["label"] for f in obs["fields"]], [b["label"] for b in obs["controls"]]


async def test_a_hide_left_by_a_closed_modal_is_not_hidden(page, tmp_path):
    fields, controls = await scan(page, tmp_path, QUESTION.format(attrs='aria-hidden="true" data-old-aria-hidden="null"'))
    assert any("SNAP" in f for f in fields) and "Next" in controls


async def test_a_hide_behind_an_open_modal_and_the_pages_own_hide_still_hide(page, tmp_path):
    fields, controls = await scan(page, tmp_path,
                                  QUESTION.format(attrs='aria-hidden="true" data-old-aria-hidden="null"') + MODAL)
    assert not any("SNAP" in f for f in fields) and "Next" not in controls
    fields, controls = await scan(page, tmp_path, QUESTION.format(attrs='aria-hidden="true"'))
    assert not any("SNAP" in f for f in fields)


async def test_a_custom_radio_without_its_own_text_is_its_value(page, tmp_path):
    html = """<span id=q>Did you receive SSI benefits?</span><div role=radiogroup aria-labelledby=q>
    <span role=radio aria-checked=false value="YES" tabindex=0></span><span role=radio aria-checked=false value="NO" tabindex=-1></span></div>"""
    await page.set_content(html)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a, **k: None)
    obs = await form.scan()
    field = next(f for f in obs["fields"] if f["type"] == "radiogroup")
    assert field["options"] == ["YES", "NO"]
