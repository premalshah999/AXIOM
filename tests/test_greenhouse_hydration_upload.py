"""Greenhouse's React upload widget ignores a file set before hydration and crashes on an emptied input.

66degrees / ALTEN / OneImaging / Perpay 2026-10-29: the resume sat in input.files but the widget never listed it, a
second set_input_files of the same file was ignored by React's value tracker, and the repair path's
set_input_files([]) crashed the widget ("Cannot read properties of undefined (reading 'uploadFile')")."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

# A stand-in for the React widget: it hydrates 600 ms after load, only reacts to a change whose value differs from the
# value it tracked at hydration (React's input value tracker), and crashes when the change leaves no file.
PAGE = """
<html><body><label>Name<input value='Alex Doe'></label>
<div class="file-upload" role="group" aria-labelledby="upload-label-resume">
  <div id="upload-label-resume" class="label upload-label">Resume/CV</div>
  <div class="file-upload__wrapper" id="wrapper">
    <button type="button">Attach</button><label class="visually-hidden" for="resume">Attach</label>
    <input id="resume" class="visually-hidden" type="file">
    <button type="button">Enter manually</button>
  </div>
</div>
<script>
  const input = document.querySelector('#resume'), wrapper = document.querySelector('#wrapper');
  setTimeout(() => {
    let tracked = input.value;
    const native = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    // React's input value tracker: a write to .value updates what the widget believes the value is.
    Object.defineProperty(input, 'value', {configurable: true, get() { return native.get.call(this); },
      set(v) { tracked = v; native.set.call(this, v); }});
    input.__reactProps$x = {};
    input.addEventListener('change', () => {
      if (!input.files.length) { wrapper.innerHTML = ''; return; }
      if (input.value === tracked) return;
      tracked = input.value;
      wrapper.innerHTML = '<div class="file-upload__filename"><p>' + input.files[0].name + '</p></div>';
    });
  }, 600);
</script></body></html>
"""


async def session(page, tmp_path, hydrated):
    resume = tmp_path / "Alex_Doe_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    await page.set_content(PAGE)
    if hydrated:
        await page.wait_for_function("document.querySelector('#resume').__reactProps$x")
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    await form.scan()
    return form


async def test_a_file_set_before_hydration_is_attached_after_it(page, tmp_path):
    form = await session(page, tmp_path, hydrated=False)
    await page.locator("#resume").set_input_files(str(form.resume_path))  # The earlier pass: ignored by the widget.
    assert await form.greenhouse_resume_listed() is False
    await form.upload_greenhouse_resume(form.resume_fields()[0]["id"])
    assert await form.greenhouse_resume_listed() is True


async def test_the_first_upload_waits_for_hydration(page, tmp_path):
    form = await session(page, tmp_path, hydrated=False)
    await form.upload_greenhouse_resume(form.resume_fields()[0]["id"])
    assert await form.greenhouse_resume_listed() is True


async def test_clearing_a_rejected_resume_does_not_crash_the_widget(page, tmp_path):
    form = await session(page, tmp_path, hydrated=False)
    field_id = form.resume_fields()[0]["id"]
    await page.locator("#resume").set_input_files(str(form.resume_path))  # Ignored before hydration.
    await page.wait_for_function("document.querySelector('#resume').__reactProps$x")
    await form.clear_resume_input(field_id)
    assert await page.evaluate("document.querySelector('#resume').files.length") == 0
    assert await page.locator("#resume").count() == 1  # An empty change event would have emptied the widget.
    await form.clear_resume_input(field_id)  # Nothing to clear once the widget lists a file and drops its input.


async def test_verify_reports_the_unlisted_resume_and_fill_current_retries_it(page, tmp_path, service):
    form = await session(page, tmp_path, hydrated=False)
    form.resolver = Resolver(Profile(), service.db, service.config)
    await page.locator("#resume").set_input_files(str(form.resume_path))
    await page.wait_for_function("document.querySelector('#resume').__reactProps$x")
    form.uploaded = True
    report = await form.verify()
    assert "Resume upload has not been accepted by the website" in report["problems"]
    await form.fill_current(accept_prefilled=True)
    report = await form.verify()
    assert "Resume upload has not been accepted by the website" not in report["problems"]
    assert report["resume_attached"] is True
