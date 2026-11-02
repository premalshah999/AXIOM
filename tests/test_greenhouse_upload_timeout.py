"""The Greenhouse resume widget removes its file input once it lists the file, so a second set_input_files times out
there. That timeout ended runs as "Browser control failed at forms.py:5788" although the resume was attached."""

from pathlib import Path
from types import SimpleNamespace

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from jobpilot.forms import FormSession


class Input:
    def __init__(self, fail):
        self.fail, self.sets = fail, 0

    async def evaluate(self, *_a, **_k):
        raise PlaywrightError("input is gone")

    async def set_input_files(self, *_a, **_k):
        self.sets += 1
        if self.fail:
            raise PlaywrightTimeoutError("Locator.set_input_files: Timeout 15000ms exceeded.")


def session(field, listed):
    async def listed_now():
        return listed.pop(0) if listed else False

    async def wait(_ms):
        return None

    return SimpleNamespace(
        locator=lambda _id: field,
        resume_path=Path("resume.pdf"),
        greenhouse_resume_listed=listed_now,
        page=SimpleNamespace(wait_for_timeout=wait),
    )


async def test_a_vanished_input_after_the_widget_listed_the_file_is_success():
    field = Input(fail=True)
    await FormSession.upload_greenhouse_resume(session(field, [True]), "f1")
    assert field.sets == 1


async def test_an_input_that_never_takes_the_file_does_not_raise_a_browser_error():
    field = Input(fail=True)
    await FormSession.upload_greenhouse_resume(session(field, []), "f1")
    assert field.sets == 3  # The form's own upload check then reports the missing resume.
