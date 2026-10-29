import pytest

from jobpilot.forms import FormSession, SCAN


MARKUP = '''<form>
<label for="relocate">Are you open to relocation? *</label>
<div class="ant-checkbox-group" id="relocate" aria-required="true">
  <label><input type="checkbox">Not Applicable (I am local or the position is Remote)</label>
  <label><input type="checkbox">NO</label><label><input type="checkbox">YES</label>
</div>
<label for="interests">Which interests apply?</label><div id="interests">
  <label><input type="checkbox">Analytics</label><label><input type="checkbox">Engineering</label>
</div></form>'''


async def test_labelled_containers_are_grouped_and_only_yes_no_is_single_choice(page):
    await page.set_content(MARKUP)
    fields = (await page.main_frame.evaluate(SCAN))["fields"]
    assert len(fields) == 2
    single, multi = fields
    assert single["label"] == "Are you open to relocation?"
    assert single["type"] == "checkboxgroup" and single["widget"] == "single-choice-checkboxes"
    assert single["options"] == ["Not Applicable (I am local or the position is Remote)", "NO", "YES"]
    assert single["required"] and not single["valid"]
    assert multi["type"] == "checkboxgroup" and multi["widget"] == "container-checkboxes"
    await page.locator('#relocate input').nth(1).check()
    await page.locator('#relocate input').nth(2).check()
    single = (await page.main_frame.evaluate(SCAN))["fields"][0]
    assert not single["valid"]


async def test_single_choice_rejects_multiple_answers_before_changing_controls(page, tmp_path):
    await page.set_content(MARKUP)
    form = FormSession(page, None, tmp_path / "unused.pdf", lambda *a: None)
    field = next(f for f in (await form.scan())["fields"] if f["label"].startswith("Are you open"))
    with pytest.raises(ValueError, match="exactly one"):
        await form.fill(field, "NO\nYES")
    assert await page.locator('#relocate input:checked').count() == 0
    await form.fill(field, "NO")
    assert await page.locator('#relocate input:checked').count() == 1
    await form.fill(field, "YES")
    assert await page.locator('#relocate input:checked').count() == 1
    assert await page.locator('#relocate input').nth(2).is_checked()
