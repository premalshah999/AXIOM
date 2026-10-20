"""Sentry Insurance (Workday) 2026-10-18: the gender list is Female / Male / Not Specified, and the owner's decline
policy chooses Not Specified. The page scanner reads "Not Specified" as an unselected prompt, so read-back reported
"Value not accepted" and the navigator chose the same option five times before stalling. The control's own text
decides for such a value."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

URL = "https://acme.wd5.myworkdayjobs.com/en-US/acme/job/X/apply/applyManually"
PAGE = """<div data-automation-id="formField-gender"><label for="personalInfoUS--gender">Please select your gender.</label>
<div><button aria-haspopup="listbox" type="button" value="" aria-label="Please select your gender. Select One"
  name="gender" id="personalInfoUS--gender">Select One</button><input type="text" value="" style="display:none"></div></div>
<ul id=list role=listbox hidden style="position:absolute;top:120px"><li role=option>Female</li><li role=option>Male</li>
<li role=option>Not Specified</li></ul>
<script>
const b=document.getElementById('personalInfoUS--gender'),l=document.getElementById('list');
b.onclick=()=>{l.hidden=!l.hidden;b.setAttribute('aria-expanded',String(!l.hidden))};
l.querySelectorAll('[role=option]').forEach(o=>o.onclick=()=>{b.textContent=o.textContent;
  b.setAttribute('aria-label','Please select your gender. '+o.textContent);l.hidden=true;b.setAttribute('aria-expanded','false')});
</script>"""


async def test_not_specified_is_a_committed_choice(page, tmp_path):
    await page.route("https://acme.wd5.myworkdayjobs.com/**",
                     lambda route: route.fulfill(body=f"<html><body>{PAGE}</body></html>", content_type="text/html"))
    await page.goto(URL)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a: None)
    await form.scan()
    field = next(f for f in form.fields.values() if f["type"] == "dropdown")
    await form.fill(field, "Not Specified")
    assert await page.locator("#personalInfoUS--gender").inner_text() == "Not Specified"
    assert "Value not accepted: Please select your gender." not in (await form.verify())["problems"]
    # An untouched prompt is still not an answer.
    await page.goto(URL)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a: None)
    await form.scan()
    field = next(f for f in form.fields.values() if f["type"] == "dropdown")
    form.expected[field["id"]] = "Not Specified"
    assert "Value not accepted: Please select your gender." in (await form.verify())["problems"]
