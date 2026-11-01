"""A Chrome window the owner closes by hand is "the window was closed", not "Browser control failed at browser.py".

The check is made on the error Playwright really raises from a page that was closed under a running call."""

import pytest
from playwright.async_api import Error as PlaywrightError

from jobpilot.browser import net_failure, window_closed


async def test_a_page_closed_under_a_call_is_a_closed_window(page):
    await page.set_content("<button id=b>x</button>")
    await page.close()
    with pytest.raises(PlaywrightError) as caught:
        await page.locator("#b").click(timeout=2000)
    assert window_closed(caught.value, page)
    assert net_failure(caught.value) is None  # not mistaken for the employer's site failing


@pytest.mark.parametrize(
    "message",
    ["Browser has been closed", "Connection closed while reading from the driver", "Target closed",
     "Page.evaluate: Target page, context or browser has been closed"],
)
def test_the_messages_playwright_uses_when_chrome_went_away(message):
    assert window_closed(PlaywrightError(message))


@pytest.mark.parametrize(
    "message", ["Locator.click: Timeout 6000ms exceeded.", "Page.goto: net::ERR_CONNECTION_CLOSED at <url>", "Locator is stale"]
)
def test_other_failures_are_not_a_closed_window(message):
    assert not window_closed(PlaywrightError(message))
