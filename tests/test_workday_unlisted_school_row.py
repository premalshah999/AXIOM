"""Jefferson Health's Workday school list is search-only and has neither the applicant's overseas university nor an Other
entry (2026-10-28): every rerun stalled on Education 2's required school. That added row is deleted with its own Delete
button, and the first row (the highest degree) is never touched."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

HTML = """
<div role="group" aria-labelledby="Education-1-panel"><h5 id="Education-1-panel">Education 1</h5>
  <button onclick="this.closest('[role=group]').remove()">Delete</button>
  <label for="s1">School or University</label><input id="s1" name="schoolName"></div>
<div role="group" aria-labelledby="Education-2-panel"><h5 id="Education-2-panel">Education 2</h5>
  <button onclick="this.closest('[role=group]').remove()">Delete</button>
  <label for="s2">School or University</label><input id="s2" name="schoolName"></div>
<button>Delete Resume.pdf</button>
"""


def unlisted():
    error = ValueError("No matching option is offered")
    error.offered = {"Example Technical University": ["No Items."], "Other": []}
    return error


async def workday_form(page, tmp_path, monkeypatch, workday=True):
    monkeypatch.setattr(FormSession, "workday", property(lambda self: workday))
    await page.set_content(HTML)
    events = []
    value = FormSession(page, Resolver(Profile(), None, None), tmp_path / "resume.pdf", lambda *a: events.append(a))
    value.events = events
    await value.scan()
    return value


def school(form, group):
    # The scanner names Workday's rows from their panels (production events read "Education 2").
    row = int(group.split()[-1]) - 1
    field = [f for f in form.fields.values() if f["label"] == "School or University"][row]
    return {**field, "type": "prompt", "group": group}


async def panels(page):
    return await page.evaluate("[...document.querySelectorAll('[role=group] h5')].map(h => h.textContent)")


async def test_an_added_row_whose_school_is_not_listed_is_deleted(page, tmp_path, monkeypatch):
    form = await workday_form(page, tmp_path, monkeypatch)
    assert await form.drop_unlisted_school_row(school(form, "Education 2"), unlisted()) is True
    assert await panels(page) == ["Education 1"]
    assert form.dropped_rows == {"education": 1}
    assert form.events[-1][0] == "history_row_dropped"


async def test_the_first_row_and_other_failures_are_left_for_the_normal_path(page, tmp_path, monkeypatch):
    form = await workday_form(page, tmp_path, monkeypatch)
    assert await form.drop_unlisted_school_row(school(form, "Education 1"), unlisted()) is False
    assert await form.drop_unlisted_school_row(school(form, "Education 2"), TimeoutError()) is False
    monkeypatch.setattr(FormSession, "workday", property(lambda self: False))
    assert await form.drop_unlisted_school_row(school(form, "Education 2"), unlisted()) is False
    assert await panels(page) == ["Education 1", "Education 2"]
    assert form.dropped_rows == {}
