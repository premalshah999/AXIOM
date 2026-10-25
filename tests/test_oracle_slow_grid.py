"""Oracle CX selects render their option grid through deferred effects (REA on the CX bundle, 2026-10-25). In a
throttled or busy tab the grid appeared after the engine's 2 s wait: months, years and states that were offered were
read as "no options" (53 runs), the typing fallback then filtered complete lists, and an Escape on the closed select
cleared its value. Oracle's app on an employer's own domain (Dell, careers.oracle.com) never got this driver."""

from types import SimpleNamespace

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile
from jobpilot.widgets_ats import combo_family

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def page_html(delay_ms, readonly=True, label="Start Date Month", scripts=""):
    return f"""{scripts}<label for=month>{label}</label>
<input id=month class=cx-select-input role=combobox aria-controls=months aria-expanded=false {'readonly' if readonly else ''}>
<div id=months role=status></div>
<script>
const el = document.getElementById('month'), list = document.getElementById('months');
window.escapes = 0;
el.addEventListener('click', () => {{
  if (el.getAttribute('aria-expanded') === 'true') return;
  el.setAttribute('aria-expanded', 'true');
  setTimeout(() => {{
    list.setAttribute('role', 'grid');
    list.innerHTML = {MONTHS!r}.map((m, i) => `<div role=gridcell id=months-listitem-${{i}}>${{m}}</div>`).join('');
  }}, {delay_ms});
}});
list.addEventListener('click', ev => {{
  const cell = ev.target.closest('[role=gridcell]');
  if (!cell) return;
  el.value = cell.textContent; el.setAttribute('aria-expanded', 'false');
  list.innerHTML = ''; list.setAttribute('role', 'status');
}});
el.addEventListener('keydown', ev => {{
  if (ev.key !== 'Escape') return;
  window.escapes += 1;
  if (el.getAttribute('aria-expanded') !== 'true') el.value = '';  // Clears a committed value when closed.
}});
</script>"""


async def open_form(page, service, config, tmp_path, html, url="https://fixture.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/1"):
    async def fulfill(route):
        if route.request.url.endswith(".js"):
            await route.fulfill(status=200, content_type="application/javascript", body="")
        else:
            await route.fulfill(status=200, content_type="text/html", body=html)

    await page.route("**/*", fulfill)
    await page.goto(url)
    form = FormSession(page, Resolver(Profile(), service.db, config), tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    return form, next(f for f in form.fields.values() if f["type"] == "combobox")


@pytest.mark.parametrize("delay_ms", [0, 2500])
async def test_a_month_whose_grid_renders_late_is_still_committed(page, service, config, tmp_path, delay_ms):
    form, field = await open_form(page, service, config, tmp_path, page_html(delay_ms))
    assert await form.fill(field, "December") == "December"
    assert await page.locator("#month").input_value() == "December"
    assert await page.evaluate("window.escapes") == 0


async def test_a_value_the_list_lacks_fails_without_clearing_the_field(page, service, config, tmp_path):
    form, field = await open_form(page, service, config, tmp_path, page_html(0))
    await page.evaluate("document.getElementById('month').value = 'March'")
    with pytest.raises(ValueError):
        await form.fill(field, "Smarch")
    assert await page.locator("#month").input_value() == "March"  # Escape was pressed only while open.


async def test_oracles_app_on_an_employers_own_domain_gets_the_oracle_driver(page, service, config, tmp_path):
    cdn = '<script src="https://static.oracle.com/cdn/fa/oj-hcm-ce/2607/js/main-minimal.js"></script>'
    url = "https://careers.employer.example/hcmUI/CandidateExperience/en/sites/CX/job/1"
    form, field = await open_form(page, service, config, tmp_path, page_html(1500, scripts=cdn), url=url)
    assert await combo_family(page.main_frame, form.locator(field["id"])) == "oracle"
    assert await form.fill(field, "December") == "December"


async def test_a_lookalike_page_without_oracles_app_gets_no_oracle_driver(page, service, config, tmp_path):
    url = "https://careers.employer.example/hcmUI/CandidateExperience/en/sites/CX/job/1"
    form, field = await open_form(page, service, config, tmp_path, page_html(0), url=url)
    assert await combo_family(page.main_frame, form.locator(field["id"])) is None
