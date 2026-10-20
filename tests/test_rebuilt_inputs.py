"""Inputs rebuilt before the write (REA analysis 2026-10-17).

Workday's TextField is keyed by its value and its sections re-render after a prompt selection, so the node the scan
tagged with data-jp-id is often gone before the next field is written. Writing to the old tag waited out the whole
field deadline: HPE lost ~31 s on each of six My Information fields until a rescan filled them in 5 s, and 15 Workday
runs hit 95 such deadlines on 10-17. The writer must find the rebuilt node at once.
"""

import time

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

REBUILD = """() => document.querySelectorAll('input').forEach(e => {
  const n = e.cloneNode(); n.removeAttribute('data-jp-id'); e.replaceWith(n);
})"""


def session(page, service, tmp_path):
    return FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path / "unused.pdf", lambda *a: None)


async def test_fields_rebuilt_before_the_write_are_found_by_their_stable_name(page, service, tmp_path):
    await page.set_content("""
      <div data-automation-id="formField-legalName--firstName"><label for=a>First Name</label><input id=a name="legalName--firstName"></div>
      <div data-automation-id="formField-legalName--lastName"><label for=b>Last Name</label><input id=b name="legalName--lastName"></div>
      <div data-automation-id="formField-city"><label for=c>City</label><input id=c name="city"></div>""")
    form = session(page, service, tmp_path)
    fields = {f["label"]: f for f in (await form.scan())["fields"]}
    await page.evaluate(REBUILD)
    started = time.monotonic()
    for label, value in [("First Name", "Alex"), ("Last Name", "Rivera"), ("City", "Springfield")]:
        assert await form.fill(fields[label], value) == value
    assert time.monotonic() - started < 3
    assert await page.locator('input[name="legalName--firstName"]').input_value() == "Alex"
    assert await page.locator('input[name="legalName--lastName"]').input_value() == "Rivera"
    assert await page.locator('input[name="city"]').input_value() == "Springfield"


async def test_a_rebuilt_field_with_an_ambiguous_name_falls_back_to_the_scoped_rescan(page, service, tmp_path):
    # Two work-history rows reuse the same input name; only the full rescan knows which row this field is.
    await page.set_content("""
      <fieldset><legend>Work Experience 1</legend><label for=a>Job Title</label><input id=a name=jobTitle></fieldset>
      <fieldset><legend>Work Experience 2</legend><label for=b>Job Title</label><input id=b name=jobTitle></fieldset>""")
    form = session(page, service, tmp_path)
    second = [f for f in (await form.scan())["fields"] if f["label"] == "Job Title"][1]
    await page.evaluate(REBUILD)
    try:
        await form.fill(second, "Data Scientist")
    except ValueError:
        pass  # A scope the rescan cannot prove is refused, never written to a sibling row.
    values = await page.locator("input[name=jobTitle]").evaluate_all("els => els.map(e => e.value)")
    assert values in (["", "Data Scientist"], ["", ""])


@pytest.mark.parametrize("label", ["Phone Number"])
async def test_a_rebuilt_phone_input_is_written_without_waiting(page, service, tmp_path, label):
    await page.set_content(
        f'<div data-automation-id="formField-phoneNumber"><label for=p>{label}</label><input id=p name=phoneNumber type=tel></div>'
    )
    form = session(page, service, tmp_path)
    phone = (await form.scan())["fields"][0]
    await page.evaluate(REBUILD)
    started = time.monotonic()
    await form.fill(phone, "3015550123")
    assert time.monotonic() - started < 4
    assert "3015550123" in await page.locator("input[name=phoneNumber]").input_value()
