"""A required-marker-only checkbox below an explicit acceptance sentence."""
import pytest
from jobpilot.forms import SCAN


async def test_sole_checkbox_uses_visible_acceptance_not_hidden_locale(page):
    await page.set_content('''<form><section><h2>Nondisclosure agreement</h2>
      <p>Terms of the interview agreement.</p><p>I agree and accept the terms and conditions of this letter.</p>
      </section><fieldset><div hidden><legend>Hidden translated agreement</legend></div>
      <div><fieldset><div><input type=checkbox id=accept required><label for=accept>*</label>
      <input type=hidden value=0></div></fieldset></div></fieldset></form>''')
    field = next(f for f in (await page.evaluate(SCAN))['fields'] if f['type'] == 'checkbox')
    assert field['label'] == 'I agree and accept the terms and conditions of this letter.'
    assert field['required']


async def test_acceptance_sentence_does_not_override_explicit_label(page):
    await page.set_content('''<form><p>I agree to the terms.</p>
      <input type=checkbox id=c><label for=c>Receive marketing emails</label></form>''')
    field = next(f for f in (await page.evaluate(SCAN))['fields'] if f['type'] == 'checkbox')
    assert field['label'] == 'Receive marketing emails'


async def test_multiple_checkboxes_do_not_borrow_the_agreement(page):
    await page.set_content('''<form><p>I agree to the terms.</p>
      <input type=checkbox id=a><label for=a>*</label>
      <input type=checkbox id=b><label for=b>*</label></form>''')
    fields = [f for f in (await page.evaluate(SCAN))['fields'] if f['type'] == 'checkbox']
    assert all('I agree' not in f['label'] for f in fields)


async def test_unknown_checkbox_does_not_infer_agreement_from_heading(page):
    await page.set_content('''<form><h2>Terms and conditions</h2><p>Read this document carefully.</p>
      <input type=checkbox id=a><label for=a>*</label></form>''')
    field = next(f for f in (await page.evaluate(SCAN))['fields'] if f['type'] == 'checkbox')
    assert not field['label'].startswith('I agree')
