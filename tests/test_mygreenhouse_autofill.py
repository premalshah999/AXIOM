"""Braze (Greenhouse) 2026-10-18: the navigator clicked "Autofill my application", which asks for an email and a
security code (MyGreenhouse sign-in) before it fills anything. The form is filled directly instead."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<main><h1>Data Scientist</h1>
  <button type=button onclick="document.body.dataset.autofill=1">Autofill my application</button>
  <button type=button onclick="document.body.dataset.resume=1">Autofill with resume</button>
  <label>First Name<input id=first></label></main>"""


async def test_mygreenhouse_autofill_is_refused_and_resume_autofill_is_not(page, tmp_path):
    await page.set_content(f"<html><body>{PAGE}</body></html>")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a: None)
    await form.scan()
    by_label = {b["label"].strip(): b for b in form.buttons.values()}
    with pytest.raises(ValueError, match="MyGreenhouse autofill"):
        await form.click(by_label["Autofill my application"]["id"])
    assert await page.evaluate("document.body.dataset.autofill") is None
    await form.click(by_label["Autofill with resume"]["id"])
    assert await page.evaluate("document.body.dataset.resume") == "1"
