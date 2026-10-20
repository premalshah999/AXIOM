"""Same-IP hCaptcha (owner-authorized 2026-10-14).

Honeywell and JPMorgan reject hCaptcha tokens bought from a solver, which are issued to the
solver's IP. Here the image challenge is answered in the worker's own browser instead: only the
isolated puzzle image (never the application) goes to 2Captcha's CoordinatesTask, the returned
points are clicked in the page, and hCaptcha itself delivers the token to the employer, so
the token belongs to this browser and IP. The puzzle logic is CaptchaSolver.visual in
captcha_visual.py (from the Oct 4 engine, unchanged).
"""

import asyncio
from types import SimpleNamespace

from playwright.async_api import Error as PlaywrightError

from .captcha_observation import challenge_frame, checkbox_frame
from .captcha_visual import CaptchaSolver


async def solve_same_ip(config, page, emit, *, pending=False):
    """(accepted, reason). Accepted only when the website closed the challenge with a response."""
    if not config.twocaptcha_api_key:
        return False, "2Captcha is not configured"
    settings = SimpleNamespace(
        twocaptcha_api_key=config.twocaptcha_api_key,
        captcha_timeout=min(config.captcha_timeout, 180),
        captcha_max_attempts=config.captcha_max_attempts,
        captcha_max_rounds=config.captcha_image_rounds,
        capsolver_api_key="",
    )
    solver = CaptchaSolver(settings, emit)
    clicked = False
    if box := await checkbox_frame(page):
        try:
            await box.locator("#checkbox").click(timeout=3000)
            clicked = True
        except PlaywrightError:
            pass
    found = None
    # A clicked checkbox or a pending invisible execute() may take a few seconds to open its puzzle.
    for _ in range(16 if clicked or pending else 2):
        if found := await challenge_frame(page):
            break
        await asyncio.sleep(0.5)
    if not found:
        # No puzzle to answer here (or hCaptcha passed silently): the token path decides.
        return False, "No hCaptcha image challenge appeared"
    frame, owner, _ = found
    accepted = await solver.visual(page, frame, owner)
    return accepted, solver.last_reason
