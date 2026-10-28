"""Format placeholders must not hide an unassociated visible field caption."""
import pytest
from jobpilot.forms import FormSession
from jobpilot.widgets import same_value


@pytest.mark.parametrize('mask', ['___-___-____', '(___) ___-____'])
async def test_phone_caption_precedes_format_mask_and_readback_accepts_formatting(page, tmp_path, mask):
    await page.set_content(f'''<form><div class="field"><div><span>Phone Number*</span></div>
      <div><custom-input><input type=text required placeholder="{mask}"></custom-input></div></div></form>''')
    form = FormSession(page, None, tmp_path / 'unused.pdf', lambda *a: None)
    field, = (await form.scan())['fields']
    assert field['label'].rstrip('*').strip() == 'Phone Number'
    assert same_value(field, '202-555-0100', '(202) 555-0100')
    assert not same_value(field, '202-555-0199', '(202) 555-0100')


async def test_mask_is_not_inferred_to_be_a_phone_number(page, tmp_path):
    await page.set_content('<form><input type=text placeholder="___-__-____"></form>')
    form = FormSession(page, None, tmp_path / 'unused.pdf', lambda *a: None)
    field, = (await form.scan())['fields']
    assert field['label'] == '___-__-____'


async def test_adjacent_fields_do_not_share_caption(page, tmp_path):
    await page.set_content('''<form><div><label for=a>Phone Number</label><input id=a type=tel>
      <input type=text placeholder="___-__-____"></div></form>''')
    form = FormSession(page, None, tmp_path / 'unused.pdf', lambda *a: None)
    fields = (await form.scan())['fields']
    assert fields[1]['label'] == '___-__-____'
