"""Qualcomm (Eightfold) 2026-10-17: after the emailed code, a "Create your profile" popup (names, phone, Back,
type=submit "Submit") registers the applicant. It passed the final-submit guard as an account form, then the bare
submit-control guard refused it, so every run stalled in front of it."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<main><h1>AI Resident (Research)</h1><p>Job description</p></main>
<div class="overlay"><div class="panel" role="dialog" aria-modal="true"><h2>Create your profile</h2><p>Required fields are marked with *</p>
<form onsubmit="window.sent = true; return false">
<label for="fn">First Name</label><input id="fn" value="Alex" required>
<label for="ln">Last Name</label><input id="ln" value="Rivera" required>
<label for="cc">Country code</label><select id="cc"><option>(+1) United States of America</option></select>
<label for="ph">Phone Number</label><input id="ph" value="(240) 555-0100" required>
<button type="button">Back</button><button type="submit">Submit</button></form></div></div>"""


async def test_the_profile_popup_submit_is_an_account_step(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    obs = await form.scan()
    submit = next(b for b in obs["controls"] if b["label"] == "Submit")
    await form.click(submit["id"])
    assert await page.evaluate("window.sent") is True


async def test_adapter_uses_same_scoped_intent_as_form_guard(page, tmp_path):
    from jobpilot.adapters.base import Adapter, Step
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / 'r.pdf', lambda *a, **k: None)
    obs = await form.scan()
    submit = next(b for b in obs['controls'] if b['label'] == 'Submit')
    adapter = object.__new__(Adapter)
    from types import SimpleNamespace
    adapter.e = SimpleNamespace(form=form)
    adapter.committed, adapter.last_step = False, Step.FORM
    adapter.emit = lambda *a: None
    async def dismiss():
        pass
    adapter.dismiss_overlays = dismiss
    await adapter.click(submit)
    assert await page.evaluate('window.sent') is True


async def test_final_application_cannot_pass_as_account_intent(page, tmp_path):
    import pytest
    await page.set_content(PAGE.replace('Create your profile', 'Review your application')
                           .replace('<button type="button">Back', '<input type=file required><button type="button">Back'))
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / 'r.pdf', lambda *a, **k: None)
    obs = await form.scan()
    submit = next(b for b in obs['controls'] if b['label'] == 'Submit')
    assert not await form.intermediate_submit(submit['id'], obs)
    with pytest.raises(ValueError, match='final submission'):
        await form.click(submit['id'])
    assert not await page.evaluate('!!window.sent')
