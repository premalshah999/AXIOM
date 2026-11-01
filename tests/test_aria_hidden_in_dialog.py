"""A sign-up modal (Paycom, GSI Environmental 2026-11-01) wraps its whole form in aria-hidden="true" inside the open
aria-modal dialog. A person sees and fills every field; the scan saw only the Continue button, called the page clean and
pressed it on empty required fields. aria-hidden means hidden behind an open dialog, not inside it."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

FORM = """<label for=f>First Name *</label><input id=f name=firstName required>
<label for=e>Email Address *</label><input id=e name=email required>
<fieldset><legend>Do you consent to text messages?</legend>
<label><input type=radio name=sms value=y>Yes</label><label><input type=radio name=sms value=n>No</label></fieldset>
<span id=q>May we contact you?</span><div role=radiogroup aria-labelledby=q>
<label><input type=radio name=call value=y>Yes</label><label><input type=radio name=call value=n>No</label></div>"""
MODAL = """<div role=dialog aria-modal=true style="width:500px;position:fixed;top:20px;left:50px;background:#fff">
<h2>Getting You Started</h2><div aria-hidden="true">{form}</div><button>Continue To Application</button></div>"""


async def scan(page, tmp_path, html):
    await page.set_content(html)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a, **k: None)
    obs = await form.scan()
    return [f["label"] for f in obs["fields"]], [b["label"] for b in obs["controls"]]


async def test_a_form_inside_the_open_dialog_is_read_even_when_aria_hidden(page, tmp_path):
    fields, controls = await scan(page, tmp_path, MODAL.format(form=FORM))
    assert any("First Name" in f for f in fields) and any("Email" in f for f in fields)
    assert any("text messages" in f for f in fields) and any("contact you" in f for f in fields)
    assert "Continue To Application" in controls


async def test_behind_a_dialog_and_off_its_box_stay_hidden(page, tmp_path):
    behind = f'<main aria-hidden="true">{FORM}</main>' + MODAL.format(form="<p>Hello</p>")
    fields, _ = await scan(page, tmp_path, behind)
    assert not fields
    # A carousel's other slide sits outside the dialog's box; a focus guard is not focusable; a faded slide is transparent.
    off = '<div style="position:absolute;left:900px;width:300px"><input aria-label="Other slide"></div>'
    guard = '<input aria-label="Focus guard" tabindex="-1">'
    faded = '<div style="opacity:0"><input aria-label="Faded slide"></div>'
    fields, _ = await scan(page, tmp_path, MODAL.format(form=off + guard))
    assert not fields
    html = MODAL.replace('<div aria-hidden="true">{form}</div>', f'<div aria-hidden="true" style="opacity:0"><input aria-label="Faded slide"></div>')
    fields, _ = await scan(page, tmp_path, html)
    assert not fields


async def test_a_wrapped_checkbox_pair_reads_once(page, tmp_path):
    """Rippling (2026-10-31): each Yes/No box sits in a role=checkbox row; the rows came back as extra "Unlabelled field" boxes."""
    html = """<p>Are you legally authorized to work in the United States?</p><div role=group>
    <div role=checkbox aria-checked=false tabindex=0><input type=checkbox name=q value=Yes aria-label=Yes><span>Yes</span></div>
    <div role=checkbox aria-checked=false tabindex=0><input type=checkbox name=q value=No aria-label=No><span>No</span></div></div>"""
    fields, _ = await scan(page, tmp_path, html)
    assert len(fields) == 1 and "authorized" in fields[0]
