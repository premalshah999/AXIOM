"""A click target React or Taleo re-renders while loading is re-resolved and clicked, up to three times (Dayforce NCMEC
2026-10-16; Taleo Bealls 2026-10-17 detached its Apply button twice)."""

import pytest
from playwright.async_api import Error as PlaywrightError

from jobpilot import widgets


class Handle:
    def __init__(self, owner):
        self.owner = owner

    async def evaluate(self, *args, **kwargs):
        return None

    async def click(self, **kwargs):
        self.owner.clicks += 1
        if self.owner.clicks <= self.owner.detach:
            raise PlaywrightError("ElementHandle.click: Element is not attached to the DOM")

    async def dispose(self):
        pass


class Locator:
    def __init__(self, detach):
        self.detach, self.clicks = detach, 0

    async def element_handle(self, timeout=None):
        return Handle(self)


@pytest.fixture(autouse=True)
def no_page_helpers(monkeypatch):
    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(widgets, "ensure_uncovered", nothing)


async def test_a_button_detached_twice_is_clicked_on_the_third_resolution():
    button = Locator(detach=2)
    await widgets.safe_click(button)
    assert button.clicks == 3


async def test_a_button_that_never_settles_still_fails_after_bounded_retries():
    button = Locator(detach=10)
    with pytest.raises(PlaywrightError):
        await widgets.safe_click(button)
    assert button.clicks == 4
