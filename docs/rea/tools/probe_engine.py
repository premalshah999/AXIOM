"""Run the engine's own combobox discovery against the dedicated REA Chrome (port 9339) and compare with the DOM."""
import asyncio, json, sys, time
from playwright.async_api import async_playwright
from jobpilot import widgets

FIELDS = sys.argv[1:] or ["#jobPostingApplication_personalInfo_preferredContactMethod"]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9339")
        page = browser.contexts[0].pages[0]
        frame = page.main_frame
        for css in FIELDS:
            el = page.locator(css).first
            row = {"field": css}
            t0 = time.monotonic()
            try:
                await widgets.dismiss_pickers(frame)
                await widgets.open_combobox(el)
                row["expanded_after_open"] = await el.get_attribute("aria-expanded")
                scope = await widgets._popup_scope(el)
                row["scope"] = None if scope is None else "found"
                opts = await widgets.wait_options(frame, scope, timeout=1.2, owner=el)
                row["engine_options"] = len(opts)
                row["engine_sample"] = [o.get("text") for o in opts[:4]]
            except Exception as exc:
                row["engine_error"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
            row["engine_seconds"] = round(time.monotonic() - t0, 2)
            # What the page really holds right now.
            row["dom_role_option"] = await page.locator("[role=option]").count()
            row["dom_visible_items"] = await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option").count()
            row["aria_controls"] = await el.get_attribute("aria-controls")
            try:
                await widgets.close_picker(el, frame)
            except Exception as exc:
                row["close_error"] = type(exc).__name__
            print(json.dumps(row))
        await browser.close()

asyncio.run(main())
