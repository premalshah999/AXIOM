"""Deloitte's tax-credit (WOTC) questionnaire, reached from Caesars' Oracle application (2026-10-27).

Its radio component nests three role=radiogroup elements around one Yes/No pair and prints the question as a caption
inside the innermost group, tied to nothing. The scan reported three required "Unlabelled field" radiogroups, the
model could not tell what they asked, and the run went to the owner."""

from jobpilot.forms import FormSession

PAGE = """<form><wotc-personal-info>
  <div class="row">
    <wotc-text-input class="form__group"><div class="wotc-control">
      <label class="wotc-control__label" for="fn"><span class="wotc-control__title required">First Name</span></label>
      <dds-input><input id="fn" type=text required class="dds-input__field" value="Alex"></dds-input>
    </div></wotc-text-input>
  </div>
  <div class="row">
    <wotc-radio-button role="radiogroup" tabindex="0" class="form__group ng-invalid">
      <dds-radio-group role="radiogroup">
        <div role="radiogroup">
          <div class="wotc-control radio-label-options-row">
            <div class="wotc-control__label radio-label">
              <span class="wotc-control__title required">Are you under the age of 40?</span>
            </div>
            <div class="radio-btns wotc-control__input radio-options-group">
              <dds-radio><label class="dds-custom-control"><input role="radio" type="radio" name="group1" value="1"
                required aria-required="true" class="dds-custom-control__field"><span
                class="dds-custom-control__icon"></span><span class="dds-custom-control__text">Yes</span></label></dds-radio>
              <dds-radio><label class="dds-custom-control"><input role="radio" type="radio" name="group1" value="0"
                required aria-required="true" class="dds-custom-control__field"><span
                class="dds-custom-control__icon"></span><span class="dds-custom-control__text">No</span></label></dds-radio>
            </div>
          </div>
        </div>
      </dds-radio-group>
    </wotc-radio-button>
  </div>
  <h4>Why do we ask your age group?</h4>
  <p>Some WOTC categories are based on a candidate's age range.</p>
</wotc-personal-info></form>"""


async def test_nested_radiogroups_are_one_question_read_from_their_inner_caption(page, tmp_path):
    await page.set_content(PAGE)
    form = FormSession(page, None, tmp_path / "unused.pdf", lambda *a: None)
    groups = [f for f in (await form.scan())["fields"] if f["type"] in {"radio", "radiogroup"}]
    assert len(groups) == 1
    field = groups[0]
    assert field["label"] == "Are you under the age of 40?"
    assert field["options"] == ["Yes", "No"] and field["required"]
    await form.fill(field, "No")
    assert await page.locator("input[value='0']").is_checked()
