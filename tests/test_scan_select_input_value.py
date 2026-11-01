"""A react-select style box keeps its chosen value beside the typing input, never in the question (Paylocity, 2026-11-01)."""

from jobpilot.forms import SCAN

# Paylocity's address block: the typing <input> carries aria-autocomplete but no role; the popup is declared on the wrapper.
SELECT_INPUT = """<form>
<div data-automation-id="public-site-address-country-wrapper"><label for=ctry>
<span>Country</span>
<div><div class="pcty-input-select-full-container" aria-expanded="false" aria-haspopup="listbox" id=wrap required>
<div class="pcty-input-select-input-container"><div>
<div class="input-select-input-single-value">United States</div>
<div class="pcty-input-select__input"><input aria-autocomplete="list" id=ctry class="x" required type="text" value=""></div>
</div></div></div></div></label></div>
<div><label for=st><span>State</span>
<div><div class="pcty-input-select-full-container" aria-haspopup="listbox"><div class="pcty-input-select-input-container"><div>
<div class="input-select-input-single-value">Maryland</div>
<div class="pcty-input-select__input"><input aria-autocomplete="list" id=st required type="text" value=""></div>
</div></div></div></div></label></div>
<div><label for=cty><span>Province</span>
<div><div class="pcty-input-select-full-container" aria-haspopup="listbox"><div class="pcty-input-select-input-container"><div>
<div class="input-select-input-single-value">Select a province</div>
<div class="pcty-input-select__input"><input aria-autocomplete="list" id=cty required type="text" value=""></div>
</div></div></div></div></label></div>
<div><label for=emp>Employer</label><input id=emp type=text required></div>
</form>"""


async def test_the_chosen_value_is_not_part_of_the_question(page):
    await page.set_content(SELECT_INPUT)
    data = await page.main_frame.evaluate(SCAN)
    got = {f["label"]: f for f in data["fields"]}
    assert "Country" in got and "State" in got, list(got)
    assert got["Country"]["type"] == "combobox" and got["Country"]["value"] == "United States"
    assert got["State"]["value"] == "Maryland"
    assert "Employer" in got and got["Employer"]["type"] == "text"


async def test_an_unchosen_select_reads_as_empty_not_as_its_prompt(page):
    await page.set_content(SELECT_INPUT)
    data = await page.main_frame.evaluate(SCAN)
    empty = next(f for f in data["fields"] if f["label"] == "Province")
    assert empty["type"] == "combobox" and not empty["value"]
