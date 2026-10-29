import pytest
from jobpilot.forms import SCAN

QUESTION = "1. Are you one of the following? Citizen, resident, or another listed status."

@pytest.mark.parametrize("intervening,expected", [
    ("<div class=clearfix></div>", QUESTION),
    ("<div><label>Another question<input></label></div>", "Make Selection Here"),
    ("<div>Unrelated section</div>", "Make Selection Here"),
])
async def test_generic_rcm_label_uses_only_immediately_owned_question(page, intervening, expected):
    await page.set_content(f"""<div class=row>
      <div><div class='RCMFormField instructionField' role=note>{QUESTION}</div></div>
      {intervening}
      <div><div class=RCMFormField><label for=choice>Make Selection Here</label>
      <input id=choice aria-label='Make Selection Here' role=combobox></div></div>
      <div><label for=other>Other answer</label><input id=other></div>
    </div>""")
    fields = (await page.evaluate(SCAN))["fields"]
    field = next(f for f in fields if f.get("label") == expected)
    assert field["type"] == "combobox"
    assert next(f for f in fields if f["label"] == "Other answer")["type"] == "text"
