"""Oracle question blocks label the control only "Please provide a response" and state the question in the block's
instructions. Goldman Sachs' government-position question reached the owner with no subject (2026-10-18); the owner's
standing policy answers it No."""

from jobpilot.forms import SCAN

QUESTION = ("Do you currently hold, or have you held in the past five years, a position with a government, regulatory, "
            "or Intergovernmental Organization (IGO)?")


async def test_a_generic_prompt_takes_its_blocks_question(page):
    await page.set_content(f"""<div class="apply-flow-block">
      <div class="apply-flow-block__header"><h2 class="apply-flow-block__title"><span>GOVERNMENT OR REGULATORY ENTITY</span></h2>
        <p class="apply-flow-block__instructions"></p></div>
      <div class="apply-flow-block__form-list" role="region">
        <div class="input-row"><label class="input-row__label input-row__label--required" for="q38" id="q38-label">
          <span id="labelText-q38">Please provide a response</span></label>
          <div role="radiogroup" aria-labelledby="q38-label">
            <label><input type="radio" name="q38" value="No">No</label>
            <label><input type="radio" name="q38" value="Yes">Yes</label></div></div>
        <div class="input-row"><label for="emp">Name of Latest Employer</label><input id="emp"></div>
      </div></div>""")
    # Oracle's Knockout binding writes the instructions as HTML, nesting a <p> that static markup cannot.
    await page.evaluate(
        "q => document.querySelector('.apply-flow-block__instructions').innerHTML = "
        "`<p><strong>${q}</strong></p> Government, regulatory, and intergovernmental entities include national, "
        "state, municipal and local governments.`",
        QUESTION,
    )
    labels = [f["label"] for f in (await page.main_frame.evaluate(SCAN))["fields"]]
    assert QUESTION in labels and "Please provide a response" not in labels
    assert "Name of Latest Employer" in labels

