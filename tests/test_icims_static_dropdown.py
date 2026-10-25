"""iCIMS's icimsDropdown over a static <select> (Emory's "highest level of education", 2026-10-24): searching the
whole catalog through the widget ran out of the field budget on three runs. The native select holds every option,
so the choice is written there and its change event runs the field's onchange."""

from jobpilot import widgets

PAGE = """<label for=rcf2011>What is the highest level of education you have completed?</label>
<select id=rcf2011 name=rcf2011 style="display:none" onchange="window.changed=this.value">
  <option value="">— Make a Selection —</option><option value="1">High School</option>
  <option value="2">Bachelor's Degree</option><option value="3">Master's Degree</option><option value="4">Doctorate</option>
</select>
<a id=rcf2011_icimsDropdown role=combobox class=dropdown-select href="#"><span class=dropdown-text>— Make a Selection —</span></a>
<div id=rcf2011_icimsDropdown_ctnr class=dropdown-invisible><input class=dropdown-search><ul id=rcf2011_dropdown-results></ul></div>"""


async def test_a_static_icims_dropdown_is_set_through_its_select(page):
    await page.set_content(PAGE)
    el = page.locator("#rcf2011_icimsDropdown")
    assert await widgets.icims_dropdown_commit(page.main_frame, el, "Master's Degree") == "Master's Degree"
    assert await page.evaluate("[document.getElementById('rcf2011').value, window.changed]") == ["3", "3"]
    assert await page.locator(".dropdown-text").inner_text() == "Master's Degree"


async def test_an_answer_outside_the_static_catalog_is_left_to_the_widget_path(page):
    await page.set_content(PAGE)
    el = page.locator("#rcf2011_icimsDropdown")
    import pytest

    with pytest.raises(ValueError):
        await widgets.icims_dropdown_commit(page.main_frame, el, "Associate Degree", thorough=False)
    assert await page.evaluate("document.getElementById('rcf2011').value") == ""
