"""React-select clears its search input after retaining the owned selection."""

import pytest

from jobpilot.widgets import combobox, same_value


@pytest.mark.parametrize(
    "section,actual,expected,accepted",
    [
        ("Phone", "+1", "United States +1", True),
        ("Phone", "+44", "United States +1", False),
        ("Phone", "Canada +1", "United States +1", False),
        ("Address", "+1", "United States +1", False),
    ],
)
def test_short_country_code_is_scoped_to_phone_and_preserves_country_conflicts(
    section, actual, expected, accepted
):
    assert (
        same_value({"label": "Country", "type": "combobox", "section": section}, actual, expected) is accepted
    )


@pytest.mark.parametrize(
    "multi,actual", [(False, "LinkedIn"), (True, "LinkedIn"), (False, "Different choice")]
)
async def test_readback_uses_only_the_owned_committed_value(page, multi, actual):
    chosen_class = "select__multi-value__label" if multi else "select__single-value"
    await page.set_content(f"""<div class=select__control id=owner>
      <input id=source class=select__input role=combobox aria-controls=menu aria-expanded=false
        onclick="this.setAttribute('aria-expanded','true');document.querySelector('#menu').hidden=false">
      </div><div class=select__control><span class={chosen_class}>LinkedIn</span></div>
      <div id=menu role=listbox hidden><div role=option onclick="
        const input=document.querySelector('#source');input.value='';input.setAttribute('aria-expanded','false');
        document.querySelector('#owner').insertAdjacentHTML('beforeend','<span class={chosen_class}>{actual}</span>');
        this.parentElement.hidden=true;
      ">LinkedIn</div></div>""")
    if actual == "LinkedIn":
        assert await combobox(page.main_frame, page.locator("#source"), "LinkedIn", multi=multi) == "LinkedIn"
        assert not await page.locator("#source").input_value()
    else:
        with pytest.raises(ValueError, match="did not commit"):
            await combobox(page.main_frame, page.locator("#source"), "LinkedIn", multi=multi)


async def test_greenhouse_phone_country_commits_short_display_through_form(page, service, tmp_path):
    from jobpilot.forms import FormSession
    from jobpilot.answers import Resolver
    from jobpilot.schemas import Address, Profile

    await page.set_content("""<section><h2>Phone</h2><label for=country>Country</label><div class=select__control id=phone>
      <input id=country class=select__input role=combobox aria-controls=countries aria-expanded=false
        onclick="this.setAttribute('aria-expanded','true');document.querySelector('#countries').hidden=false">
      </div><div id=countries role=listbox hidden><div role=option onclick="
        const input=document.querySelector('#country');input.value='';input.setAttribute('aria-expanded','false');
        document.querySelectorAll('#phone .select__single-value').forEach(e=>e.remove());
        document.querySelector('#phone').insertAdjacentHTML('beforeend','<span class=select__single-value>+1</span>');
        this.parentElement.hidden=true;
      ">United States +1</div></div></section>""")
    form = FormSession(
        page,
        Resolver(Profile(address=Address(country="United States")), service.db, service.config),
        tmp_path / "resume.pdf",
        lambda *a: None,
    )
    await form.scan()
    field = next(f for f in form.fields.values() if f["label"] == "Country")
    assert await form.fill(field, "United States") == "+1"
    observed = await form.rematch(field)
    assert observed["value"] == "+1"
    assert observed["section"] == "Phone"
    assert form.expected_slots[form.field_key(observed)] == "+1"
    # The navigator uses the same readback contract as the fast form driver.
    from jobpilot import widgets

    obs = await form.scan()
    opened = await form.control_action("open", observed["id"], obs["revision"], owner_id=obs["owner_id"])
    assert opened.executed
    options = await widgets.visible_options(page.main_frame, "countries")
    obs = await form.scan()
    chosen = await form.control_action(
        "choose", observed["id"], obs["revision"], owner_id=obs["owner_id"], option_id=options[0]["id"]
    )
    assert chosen.executed and not chosen.failure_category
