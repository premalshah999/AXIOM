"""Adapter.on_form after a CAPTCHA (shared by every ATS adapter).

The iCIMS fix of 2026-10-19 lets on_form keep going when the page it was filling classifies as an account step, because
iCIMS's candidate profile embeds password boxes beside the application fields and saves them with "Submit Profile".
That allowance must stay that narrow: a page that turned into any other step while it was filled (an emailed-code step,
a confirmation, a review) belongs to the main loop, which has a handler for it, not to a Next click.
"""

import itertools
from types import SimpleNamespace

import pytest

from jobpilot.adapters.base import Adapter, Step


class Scripted(Adapter):
    """on_form with every page-facing call scripted: one classification per scan."""

    def __init__(self, classes):
        self.e = SimpleNamespace(quality_gate=None)
        self.classes = itertools.chain(classes, itertools.repeat(classes[-1]))
        self.clicked = []

    @property
    def form(self):
        return SimpleNamespace(scan=self.scan)

    async def scan(self):
        return {"url": "https://jobs.example.test/apply", "errors": [], "fields": [], "controls": []}

    async def fill_page(self):
        return {"ok": True}

    def classify(self, obs):
        return next(self.classes)

    def find_next(self, obs):
        return {"id": "next", "label": "Next"}

    async def observed_final(self, obs):
        return None

    async def solve_captcha(self):
        return None

    def signature(self, step, obs):
        return str(step)

    async def click(self, button, auth=False, step=True):
        self.clicked.append(button["label"])


@pytest.mark.parametrize("step", [Step.EMAIL_CODE, Step.CONFIRMATION, Step.REVIEW, Step.BLOCKED, Step.CLOSED])
async def test_a_page_that_became_another_step_is_left_to_the_main_loop(step):
    # Classified as `step` after the fields were filled, and still `step` after the CAPTCHA.
    adapter = Scripted([step])
    assert await adapter.on_form({"url": "https://jobs.example.test/apply"}) is None
    assert adapter.clicked == []


@pytest.mark.parametrize("step", [Step.CREATE_ACCOUNT, Step.SIGN_IN])
async def test_an_application_form_embedding_account_fields_is_still_advanced(step):
    # iCIMS's candidate profile: password boxes make it classify as account creation, and Submit Profile is its Next.
    adapter = Scripted([step])
    await adapter.on_form({"url": "https://jobs.example.test/apply"})
    assert adapter.clicked == ["Next"]


async def test_an_ordinary_form_is_advanced():
    adapter = Scripted([Step.FORM])
    await adapter.on_form({"url": "https://jobs.example.test/apply"})
    assert adapter.clicked == ["Next"]
