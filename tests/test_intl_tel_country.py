"""VAST Data (Comeet, 2026-10-27): intl-tel-input's country button is a combobox labelled "Selected country". Its text
is only that label, so the readback was empty and "United States +1" was refused on every pass although the flag
already showed the US."""

from jobpilot.forms import SCAN

PHONE = """<form><label for=phone>Phone</label><div class="iti iti--allow-dropdown iti--show-flags">
  <div class="iti__country-container"><button type="button" class="iti__selected-country" role="combobox"
    aria-label="Selected country" aria-haspopup="true" aria-expanded="false" aria-controls="dd">
    <div class="iti__selected-country-primary"><div class="iti__flag iti__us"></div>
    <span class="iti__a11y-text">Selected country</span><div class="iti__arrow"></div></div>
    <div class="iti__selected-dial-code">+1</div></button></div>
  <input id=phone type=tel required></div></form>"""


async def test_the_flag_and_dial_code_are_the_selected_country(page):
    await page.set_content(PHONE)
    data = await page.main_frame.evaluate(SCAN)
    country = next(f for f in data["fields"] if f["label"] == "Selected country")
    assert country["value"] == "United States +1"
