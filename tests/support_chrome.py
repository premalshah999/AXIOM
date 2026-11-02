"""The moment a test's own worker Chrome can take a run."""

import asyncio
import time

import httpx


async def chrome_opens_a_tab(address, ceiling=300):
    """In the app the worker Chrome has been up for minutes when a run attaches. In a test it is seconds old: it writes its port file
    before it can answer, and a run that attached then on a loaded Mac ended "AXIOM's browser wasn't ready" having sent nothing
    (dayforce-code, 2026-11-01). A test starts when Chrome has answered and opened a tab, not after some number of seconds."""
    deadline = time.monotonic() + ceiling
    async with httpx.AsyncClient(timeout=30, trust_env=False, headers={"Host": "localhost"}) as client:
        while True:
            try:
                tab = await client.put(address + "/json/new?about:blank")
                tab.raise_for_status()
                await client.get(address + "/json/close/" + tab.json()["id"])
                return
            except (httpx.HTTPError, OSError, ValueError, KeyError):
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.1)
