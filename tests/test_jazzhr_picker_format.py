"""Axtria (JazzHR, 2026-10-28): "Completion date of your highest degree (mm/yyyy)" is a jQuery UI datepicker whose own
format is yy-mm-dd, and the server refused the caption's "05/2026" with "Your answer must be a date" in 17 runs. The page
here copies that structure with synthetic data: the label asks mm/yyyy, the picker's dateFormat is yy-mm-dd, and the
local "server" accepts only a full date. The real scan, resolver and fill must type a date it accepts and the form must
reach its local submit. The applicant is synthetic."""

import json

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

# jQuery UI is not loaded from a CDN in tests: this stand-in answers the one call the scan makes, as the library does.
PAGE = """<html><body><form id=f action="https://jobs.example.test/apply" method=post>
  <label for=d>Completion date of your highest degree (mm/yyyy)<i class=asterisk>*</i></label>
  <input id=d name=q type=text class="form-control resumator-field resumator-datepicker hasDatepicker" required>
  <div id=err></div><button type=submit id=go>Submit Application</button></form>
<script>
  window.jQuery = el => ({datepicker: (cmd, key) => cmd === 'option' && key === 'dateFormat' ? 'yy-mm-dd' : null});
  window.jQuery.fn = {datepicker: () => null};
  document.getElementById('f').addEventListener('submit', e => {
    const v = document.getElementById('d').value;
    // JazzHR's validator: a full date, not a month and year.
    if (!/^(?:\\d{4}-\\d{2}-\\d{2}|\\d{2}\\/\\d{2}\\/\\d{4})$/.test(v)) {
      e.preventDefault(); document.getElementById('err').textContent = 'Your answer must be a date.';
    }
  });
</script></body></html>"""


async def test_a_picker_whose_format_has_a_day_is_typed_as_a_full_date_and_submits(page, service, tmp_path):
    posts = []

    async def accept(route):
        posts.append(route.request.post_data or "")
        await route.fulfill(status=200, content_type="text/html", body="<h1>Application received</h1>")

    await page.route("https://jobs.example.test/apply", accept)
    await page.set_content(PAGE)
    profile = Profile.model_validate(
        {
            "education": [{"school": "State University", "degree": "Master of Science", "end": "2026-05"}],
            # The owner's rule for a month-only history date on a form that needs a day (without it the question is asked).
            "answer_rules": {"history_day": "first"},
        }
    )
    resolver = Resolver(profile, service.db, service.config, job={"ats": "jazzhr", "url": "https://example.test/job"})
    form = FormSession(page, resolver, tmp_path / "unused.pdf", lambda *a: None)
    field = next(f for f in (await form.scan())["fields"] if f["label"].startswith("Completion date"))
    assert field["widget"] == "date-text" and field["date_format"] == "ymd"
    answer = resolver.local(field)
    assert answer.disposition == "answer"
    await form.fill(field, answer.value)
    typed = await page.locator("#d").input_value()
    assert typed.startswith("2026-05"), typed
    await page.click("#go")
    await page.wait_for_load_state()
    assert await page.locator("#err").count() == 0 and "Application received" in await page.content()
    assert len(posts) == 1 and "2026-05" in json.dumps(posts)
