import pytest

from jobpilot.playwright_fast import install


async def test_bounded_stack_keeps_api_names(page):
    install()
    await page.set_content("<button>Go</button>")
    await page.locator("button").click()
    with pytest.raises(Exception, match=r"Locator\.click"):
        await page.locator("#missing").click(timeout=200)
