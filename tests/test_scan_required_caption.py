from jobpilot.forms import SCAN


async def test_class_marked_required_captions_and_shadow_place_picker(page):
    # IXL's Greenhouse-hosted form: required is a label class with a CSS asterisk,
    # and the location search input lives inside a Google place-picker component.
    await page.set_content("""<style>.req-label::after{content:"*"}</style>
      <div class=field><label class="iffb__label job-application-required-field-label" for=hidden-loc>
        <span>Your Location <span>(Search by City)</span></span></label>
        <div><input type=hidden aria-required=true name=location id=hidden-loc></div>
        <place-picker></place-picker></div>
      <div class=field><label class="iffb__label job-application-required-field-label" for=study-x>
        <span>Field of study</span></label><div><input role=combobox id=study></div></div>
      <div class=field><label class="iffb__label" for=nick>Nickname</label><input id=nick></div>
      <div class=field><label class="req-label">Graduation year</label><input id=grad></div>
      <script>
        customElements.define('place-picker', class extends HTMLElement {
          connectedCallback(){ this.attachShadow({mode:'open'}).innerHTML='<div><div><input placeholder="Enter a location"></div></div>'; }
        });
      </script>""")
    data = await page.main_frame.evaluate(SCAN)
    fields = {f["label"]: f for f in data["fields"]}
    assert fields["Your Location (Search by City)"]["required"] is True
    assert fields["Field of study"]["required"] is True
    assert fields["Graduation year"]["required"] is True
    assert fields["Nickname"]["required"] is False


async def test_jazzhr_bare_yes_checkbox_takes_its_question(page):
    # OmegaHires (JazzHR) 2026-10-14: the question labels a hidden input; the checkbox says only "Yes".
    await page.set_content("""<form>
      <div><label for=q1>Willing to relocate<i>*</i></label><input type=hidden id=q1 value="">
        <label for=c1><input type=checkbox id=c1 value=Yes>Yes</label></div>
      <div><label for=q2>Open to Travel (within United States)<i>*</i></label><input type=hidden id=q2 value="">
        <label for=c2><input type=checkbox id=c2 value=Yes>Yes</label></div>
      <div><label for=c3><input type=checkbox id=c3>Send me job alerts</label></div></form>""")
    data = await page.main_frame.evaluate(SCAN)
    fields = {f["label"]: f for f in data["fields"]}
    assert (
        fields["Willing to relocate"]["required"] is True
        and fields["Willing to relocate"]["type"] == "checkbox"
    )
    assert fields["Open to Travel (within United States)"]["required"] is True
    assert fields["Send me job alerts"]["required"] is False


async def test_jazzhr_yes_no_checkboxes_are_one_choice(page):
    # Healthpeak (JazzHR) 2026-10-14: separate Yes/No checkboxes under one required question.
    await page.set_content("""<form><div>
      <label id="resumator-questionnaire-q77-label" for="resumator-questionnaire-q77">Were you referred by a current employee?<i>*</i></label>
      <input type=hidden id="resumator-questionnaire-q77" value="">
      <label for="resumator-checkbox-77-1"><input type=checkbox id="resumator-checkbox-77-1" name="resumator-checkbox-77-1" value=Yes>Yes</label>
      <label for="resumator-checkbox-77-2"><input type=checkbox id="resumator-checkbox-77-2" name="resumator-checkbox-77-2" value=No>No</label>
    </div></form>""")
    data = await page.main_frame.evaluate(SCAN)
    fields = [f for f in data["fields"] if "referred" in f["label"]]
    assert len(fields) == 1
    assert (
        fields[0]["type"] == "checkboxgroup"
        and fields[0]["options"] == ["Yes", "No"]
        and fields[0]["required"]
    )


async def test_scan_cache_never_hides_changed_question_or_visibility(page):
    await page.set_content('<form><label for=f id=l>First question</label><input id=f value=original></form>')
    first = await page.evaluate(SCAN)
    assert first['fields'][0]['label'] == 'First question'
    await page.evaluate("l.textContent='Changed question'; f.value='new'; f.required=true")
    changed = await page.evaluate(SCAN)
    assert changed['fields'][0]['label'] == 'Changed question'
    assert changed['fields'][0]['value'] == 'new' and changed['fields'][0]['required']
    await page.evaluate("f.style.display='none'")
    hidden = await page.evaluate(SCAN)
    assert not hidden['fields']
