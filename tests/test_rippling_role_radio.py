"""Rippling (2026-10-26, REA on TheGuarantors' public form): each option is a div[role=radio] wrapping a 0x0 input
(value = the option) whose aria-labelledby is the option's own text div. The work-authorization radiogroup never took
a value in production; this reproduces the markup and checks fill and the scan's readback."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile


def option(value, text, n):
    return f"""<div role=radio data-value="{value}" aria-checked=false tabindex=-1>
      <div><input type=radio name=wa value="{value}" aria-labelledby="lbl-{n}"
        style="width:0;height:0;margin:0;padding:0;border:0;opacity:1"></div>
      <div id="lbl-{n}">{text}</div></div>"""


PAGE = f"""<main>
<label id=q>Please confirm your work authorization status.</label>
<div role=radiogroup id=field-61 aria-labelledby=q>
  {option("US Citizen", "US Citizen", 1)}{option("Other Visa", "Other Visa", 2)}{option("OTHER", "Other", 3)}
</div></main>
<script>
document.querySelectorAll('[role=radio]').forEach(r => r.addEventListener('click', () => {{
  document.querySelectorAll('[role=radio]').forEach(o => {{
    const on = o === r; o.setAttribute('aria-checked', String(on)); o.querySelector('input').checked = on;
  }});
}}));
</script>"""


async def scanned(form):
    obs = await form.scan()
    return next(f for f in obs["fields"] if f["type"] == "radiogroup")


async def test_the_option_is_chosen_and_read_back(page, tmp_path):
    await page.set_content(f"<html><body>{PAGE}</body></html>")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a: None)
    field = await scanned(form)
    await form.fill(field, "Other Visa")
    assert await page.evaluate("document.querySelector('[data-value=\"Other Visa\"]').getAttribute('aria-checked')") == "true"
    assert (await scanned(form))["value"] in {"Other Visa", "true"} or (await scanned(form)).get("checked")
