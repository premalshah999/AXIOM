"""Create an exact owned worker tab using the user's visibility preference."""

import asyncio
import socket
from weakref import WeakKeyDictionary
from urllib.parse import urlsplit, urlunsplit

import httpx

from playwright.async_api import Error as PlaywrightError, async_playwright

from .playwright_fast import install as _bounded_playwright_stacks

_bounded_playwright_stacks()

_connections = {}
_page_locks = WeakKeyDictionary()


async def connected_browser(endpoint, *, discover=False):
    """Share the transport, while every application retains its own page writer."""
    loop = asyncio.get_running_loop()
    state = _connections.setdefault(loop, {"lock": asyncio.Lock(), "pw": None, "browser": None})
    async with state["lock"]:
        browser = state["browser"]
        if browser and browser.is_connected() and state.get("endpoint") == endpoint:
            return browser, state["address"]
        address = endpoint
        if discover:
            # A healthy shared connection needs no new HTTP discovery per owner.
            # Retry one transient local failure before declaring Chrome unavailable.
            parsed = urlsplit(endpoint)
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                for attempt in range(2):
                    try:
                        response = await client.get(
                            endpoint.rstrip("/") + "/json/version", headers={"Host": "localhost"}
                        )
                        response.raise_for_status()
                        ws = urlsplit(response.json()["webSocketDebuggerUrl"])
                        host = await asyncio.to_thread(socket.gethostbyname, parsed.hostname)
                        address = urlunsplit(("ws", f"{host}:{parsed.port or 80}", ws.path, "", ""))
                        break
                    except (httpx.HTTPError, OSError):
                        if attempt:
                            raise
                        await asyncio.sleep(0.25)
        if state["pw"]:
            try:
                await asyncio.wait_for(state["pw"].stop(), 3)
            except (PlaywrightError, TimeoutError):
                pass
            finally:
                state.update(pw=None, browser=None)
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.connect_over_cdp(address, no_defaults=True, timeout=15000)
        except BaseException:
            try:
                await asyncio.wait_for(pw.stop(), 3)
            except (PlaywrightError, TimeoutError):
                pass
            raise
        state.update(pw=pw, browser=browser, endpoint=endpoint, address=address)
        return browser, address


async def disconnect_browsers():
    """Shutdown detaches automation; it never closes the user's Chrome."""

    async def detach(state):
        if state["pw"]:
            try:
                await asyncio.wait_for(state["pw"].stop(), 3)
            except (PlaywrightError, TimeoutError):
                pass
            state.update(pw=None, browser=None)

    current = asyncio.get_running_loop()
    for loop, state in list(_connections.items()):
        if loop is current:
            await detach(state)
        elif loop.is_running():
            future = asyncio.run_coroutine_threadsafe(detach(state), loop)
            try:
                await asyncio.wait_for(asyncio.wrap_future(future), 4)
            except (PlaywrightError, TimeoutError):
                pass
        _connections.pop(loop, None)


async def new_worker_page(browser, context, *, visible=False):
    # Target creation/Playwright attachment is shared browser infrastructure.
    # Serialize only this short handoff; owners navigate and fill independently.
    lock = _page_locks.setdefault(browser, asyncio.Lock())
    async with lock:
        return await _new_worker_page(browser, context, visible=visible)


async def _new_worker_page(browser, context, *, visible=False):
    """Desktop attachment uses Chrome's default context; match the exact new target."""
    commands = await browser.new_browser_cdp_session()
    known = set(context.pages)
    target = None
    stage = "create_target"
    try:
        result = await asyncio.wait_for(
            # Background even when visible: a foreground target brings Chrome in front of the owner's work.
            commands.send("Target.createTarget", {"url": "about:blank", "background": True}), 6
        )
        target = result["targetId"]
        stage = "attach_target"
        async with asyncio.timeout(10):
            while True:
                for page in context.pages:
                    if page in known or page.is_closed():
                        continue
                    session = None
                    try:
                        session = await asyncio.wait_for(context.new_cdp_session(page), 3)
                        info = await asyncio.wait_for(session.send("Target.getTargetInfo"), 3)
                        if info["targetInfo"]["targetId"] == target:
                            return page
                        known.add(page)
                    except (PlaywrightError, TimeoutError):
                        continue
                    finally:
                        if session:
                            try:
                                await asyncio.wait_for(session.detach(), 1)
                            except (PlaywrightError, TimeoutError):
                                pass
                await asyncio.sleep(0.05)
    except BaseException as exc:
        if target:
            try:
                await asyncio.wait_for(commands.send("Target.closeTarget", {"targetId": target}), 2)
            except (PlaywrightError, TimeoutError):
                pass
        if isinstance(exc, (PlaywrightError, TimeoutError)):
            from .browser import BrowserUnavailable

            raise BrowserUnavailable(
                f"Chrome worker tab initialization failed at {stage} ({type(exc).__name__}). "
                "No employer form was opened."
            ) from None
        raise
    finally:
        try:
            await asyncio.wait_for(commands.detach(), 1)
        except (PlaywrightError, TimeoutError):
            pass
