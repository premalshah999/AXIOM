"""Liberty Mutual (iCIMS) 2026-10-21: "Race (EEO)" is a native <select multiple> (iCIMS_Forms_MultiSelect) that refused
Playwright's select_option on every attempt; the page answered "Race (EEO): required". The fallback selects the
labelled options in the page and fires input and change."""

from jobpilot.forms import SELECT_LABELS_JS

PAGE = """<select id=race multiple class=iCIMS_Forms_MultiSelect style="opacity:0;position:absolute">
<option>American Indian or Alaska Native</option><option>Asian</option><option>White</option><option> Opt Out </option>
</select><script>window.changes = 0; race.addEventListener('change', () => window.changes++)</script>"""


async def test_the_fallback_selects_exactly_the_labelled_options_and_fires_change(page):
    await page.set_content(PAGE)
    race = page.locator("#race")
    await race.evaluate(SELECT_LABELS_JS, ["Opt Out"])
    assert await race.evaluate("e => [...e.selectedOptions].map(o => o.text.trim())") == ["Opt Out"]
    assert await page.evaluate("window.changes") == 1
    await race.evaluate(SELECT_LABELS_JS, ["asian", "White"])
    assert await race.evaluate("e => [...e.selectedOptions].map(o => o.text.trim())") == ["Asian", "White"]


def test_a_career_site_search_box_is_never_an_application_field():
    from jobpilot.forms import site_search

    for label in ["Search", "Submit search", "Search jobs", "Keywords", "search by keyword"]:
        assert site_search({"type": "text", "label": label}), label
    for field in [{"type": "text", "label": "Search firm or agency that referred you"}, {"type": "select", "label": "Search"},
                  {"type": "text", "label": "Email"}]:
        assert not site_search(field), field
