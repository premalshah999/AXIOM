"""Jabil 2026-10-14: a resumed Workday draft reached Review with "Resume/CV ... No Response".

Review has no upload control; the driver walks Back to My Experience once so the normal
loop attaches the resume, instead of parking a finished application.
"""

from collections import Counter

from jobpilot.adapters import base
from jobpilot.adapters.workday import Workday

PAGES = ["review", "self identify", "voluntary disclosures", "application questions", "my experience"]
NO_RESUME = "Skills Python Resume/CV/Cover Letter/Supporting Documents No Response Websites"


class Form:
    def __init__(self, uploaded=False):
        self.upload_verified = uploaded
        self.at = 0

    async def scan(self):
        name = PAGES[self.at]
        return {
            "automation": [],
            "headings": [name.title()],
            "text": "",
            "controls": [
                {"id": "b", "automation_id": "pageFooterBackButton", "label": "Back", "aria_label": ""}
            ],
        }

    async def settle(self, timeout=0):
        return None

    async def _page_text(self):
        return getattr(self, "page_text", "")


def driver(form):
    w = object.__new__(Workday)
    w.committed, w.attempts, w.events = False, Counter(), []
    w.e = type("Engine", (), {"form": form})()
    w.emit = lambda kind, message, data=None: w.events.append(kind)

    async def click(button, auth=False, step=True):
        form.at += 1

    w.click = click
    return w


async def test_review_without_resume_returns_to_my_experience_once(monkeypatch):
    async def finished(self, obs, report=None):
        return {"state": "reviewed"}

    monkeypatch.setattr(base.Adapter, "on_review", finished)
    form = Form()
    w = driver(form)
    assert await w.on_review({"text": NO_RESUME}) is None
    form.at, w.attempts = 0, Counter()
    form.page_text = NO_RESUME  # Truncated scan text: the full page still shows the missing resume.
    assert await w.on_review({"text": "Review My Information"}) is None
    form.page_text = ""
    assert PAGES[form.at] == "my experience" and w.events == ["resume_repair", "resume_repair"]
    # Never loops: a second Review without the resume falls through to the normal guard.
    form.at = 0
    assert await w.on_review({"text": NO_RESUME}) == {"state": "reviewed"}


async def test_attached_resume_goes_straight_to_submit_checks(monkeypatch):
    async def finished(self, obs, report=None):
        return {"state": "reviewed"}

    monkeypatch.setattr(base.Adapter, "on_review", finished)
    w = driver(Form(uploaded=True))
    assert await w.on_review({"text": NO_RESUME}) == {"state": "reviewed"}
    w = driver(Form())
    assert await w.on_review({"text": "Resume/CV Alex_Shah_Resume.pdf"}) == {"state": "reviewed"}
