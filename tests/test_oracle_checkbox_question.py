"""Rice University (Oracle CX, 2026-10-27): a required question ("What is the most appropriate initial response?")
offered A-D as unnamed checkboxes inside one labelled .input-row group. Each box read as its own required field, so
the unticked choices were "Required field incomplete" forever and the run stalled."""

from jobpilot.forms import SCAN

QUESTION = """<form><div class="input-row input-row--radiogroup" role="group" aria-labelledby="q-label">
  <label class="input-row__label input-row__label--required" id="q-label"><span>What is the most appropriate
  initial response?</span><span class="input-row__label--required-star" aria-hidden="true"></span></label>
  <div class="input-row__control-container">
    <div class="input-row__control-item"><input type="checkbox" class="input-row__hidden-control" value="1" id="o1"
      aria-labelledby="l1"><label for="o1"><span id="l1">A. Increase storage capacity.</span></label></div>
    <div class="input-row__control-item"><input type="checkbox" class="input-row__hidden-control" value="2" id="o2"
      aria-labelledby="l2"><label for="o2"><span id="l2">B. Examine ingestion logs.</span></label></div>
  </div></div></form>"""


async def test_an_oracle_checkbox_question_is_one_choice_group(page):
    await page.set_content(QUESTION)
    data = await page.main_frame.evaluate(SCAN)
    boxes = [f for f in data["fields"] if f["type"] in {"checkbox", "checkboxgroup"}]
    assert len(boxes) == 1
    group = boxes[0]
    assert group["type"] == "checkboxgroup"
    assert group["options"] == ["A. Increase storage capacity.", "B. Examine ingestion logs."]
    assert "initial response" in group["label"] and group["required"] and not group["valid"]
    await page.check("#o2")
    data = await page.main_frame.evaluate(SCAN)
    group = next(f for f in data["fields"] if f["type"] == "checkboxgroup")
    assert group["value"] == "B. Examine ingestion logs."
