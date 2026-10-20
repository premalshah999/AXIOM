"""Greenhouse phone (intl-tel-input style) must keep its digits and block submit when refused."""

import pytest

from jobpilot import widgets
from jobpilot.forms import FormSession as Form

WIDGET = """
<div class=iti><select id=country><option value=us>+1</option><option value=gb>+44</option></select>
<input id=phone type=tel></div>
<script>
  const phone = document.querySelector('#phone');
  let keys = 0, country = '';
  phone.addEventListener('keydown', () => keys++);
  // A value written without keyboard events is dropped by the widget, as intl-tel-input does.
  phone.addEventListener('input', e => {
    if (!keys) phone.value = '';
    keys = 0;
    if (country && phone.value && !/^\\d+$/.test(phone.value)) phone.value = phone.value.replace(/\\D/g, '');
  });
  document.querySelector('#country').addEventListener('change', e => { country = e.target.value; phone.value = ''; });
</script>
"""


def test_refused_empty_phone_is_a_problem_even_when_optional():
    field = {
        "id": "f1",
        "label": "Phone",
        "type": "tel",
        "required": False,
        "value": "",
        "valid": False,
        "error": "Phone number is too short",
    }
    assert Form._phone_input(field) and Form.phone_refused(field)
    assert not Form.phone_refused({**field, "error": ""})
    assert not Form.phone_refused({**field, "value": "5555550100"})


@pytest.mark.asyncio
async def test_widget_keeps_digits_typed_with_key_events(page):
    await page.set_content(WIDGET)
    el = page.locator("#phone")
    await el.fill("5555550100")  # Written without keys: the widget drops it.
    assert await widgets.phone_rejected(el, "5555550100")
    await el.fill("")
    await el.press_sequentially("5555550100", delay=20)
    assert not await widgets.phone_rejected(el, "5555550100")
    assert await el.input_value() == "5555550100"
