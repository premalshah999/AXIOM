"""ADP, 2026-10-29: a navigator choose refused as stale, because the menu it had read was closed, was retried for 30
decisions. All pages here are synthetic."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PROFILE = {
    "email": "alexample.apply@gmail.com",
    "linkedin": "https://www.linkedin.com/in/alex-example",
    "github": "https://github.com/alexexample",
}


def session(page, service, tmp_path, employer="ExampleSoft Inc", **profile):
    resolver = Resolver(
        Profile(**{**PROFILE, **profile}), service.db, service.config, employer=employer,
        job={"ats": "custom", "url": "https://x.test"},
    )
    return FormSession(page, resolver, tmp_path / "unused.pdf", lambda *a: None)


CLOSING_MENU = """<label for=s>Social Media Source</label>
<input id=s role=combobox aria-controls=menu aria-expanded=false
  onclick="this.setAttribute('aria-expanded','true');document.querySelector('#menu').hidden=false">
<div id=menu role=listbox hidden>
  <div role=option onclick="const i=document.querySelector('#s');i.value=this.textContent;
    i.setAttribute('aria-expanded','false');document.querySelector('#menu').hidden=true">LinkedIn</div>
  <div role=option onclick="const i=document.querySelector('#s');i.value=this.textContent;
    i.setAttribute('aria-expanded','false');document.querySelector('#menu').hidden=true">Facebook</div>
</div>"""


async def test_a_choice_read_from_a_menu_that_has_since_closed_reopens_the_menu(page, service, tmp_path):
    from jobpilot import widgets

    await page.set_content(CLOSING_MENU)
    form = session(page, service, tmp_path, application_source="LinkedIn")
    obs = await form.scan()
    field = next(f for f in obs["fields"] if f["label"] == "Social Media Source")
    await form.control_action("open", field["id"], obs["revision"], owner_id=obs["owner_id"])
    options = await widgets.visible_options(page.main_frame, "menu")
    linkedin = next(o for o in options if o["text"] == "LinkedIn")
    # The scan between the model's read and its choose closed the menu.
    await page.evaluate("() => { const m = document.querySelector('#menu'); m.hidden = true; "
                        "document.querySelector('#s').setAttribute('aria-expanded', 'false') }")
    obs = await form.scan()
    chosen = await form.control_action(
        "choose", field["id"], obs["revision"], owner_id=obs["owner_id"], option_id=linkedin["id"]
    )
    assert chosen.executed and not chosen.failure_category, chosen
    assert await page.locator("#s").input_value() == "LinkedIn"


async def test_a_choice_no_menu_holds_says_what_to_do_instead(page, service, tmp_path):
    await page.set_content(CLOSING_MENU)
    form = session(page, service, tmp_path, application_source="LinkedIn")
    obs = await form.scan()
    field = next(f for f in obs["fields"] if f["label"] == "Social Media Source")
    refused = await form.control_action(
        "choose", field["id"], obs["revision"], owner_id=obs["owner_id"], option_id="gone"
    )
    assert not refused.executed and refused.failure_category == "unavailable"
    assert "inspect_options" in refused.detail and "Choice is stale or outside this control" in refused.detail
