"""Playwright call-count and call-site-cost reductions.

Single-round-trip Locator.evaluate
----------------------------------
Playwright runs Locator.evaluate as waitForSelector + evaluate + dispose: three protocol calls. The form drivers call
it about 150 times in their hot paths (one Workday dropdown write issued 42 protocol calls, 27 from this pattern;
measured 2026-10-20), and on the shared worker Chrome each call waits for a busy renderer. The fast path asks the page
for the one matching element with Frame.eval_on_selector(strict=True) in a single call. Only when the page reports no
element yet, or several, does it hand over to Playwright's own implementation, so waiting for a late element, the
strict-mode error and the timeout error are unchanged. Any other error came from the user's function, which has run
once already, and propagates as before.

Bounded call-site capture
-------------------------
Playwright records the caller stack of every API call (inspect.stack(0)),
walking and resolving every frame of a deep asyncio stack. With many
application owners on one event loop this was a top CPU cost (py-spy,
Oct 12). Its parser only needs the innermost frames: the Playwright frames and
the first caller frame that names the API. Twelve frames cover them.
"""

import inspect
import sys
from types import SimpleNamespace

DEPTH = 12


def _stack(context=1):
    frame = sys._getframe(1)
    frames = []
    while frame is not None and len(frames) < DEPTH:
        frames.append(
            inspect.FrameInfo(
                frame, frame.f_code.co_filename, frame.f_lineno, frame.f_code.co_name, None, None
            )
        )
        frame = frame.f_back
    return frames


# Messages of the two selector-level errors Frame.eval_on_selector raises before it runs the user's function.
_NOT_YET = ("Failed to find element matching selector", "strict mode violation")


def _install_single_roundtrip_evaluate():
    from playwright._impl._locator import Locator

    if getattr(Locator.evaluate, "_single_roundtrip", False):
        return
    slow = Locator.evaluate

    async def evaluate(self, expression, arg=None, timeout=None):
        try:
            return await self._frame.eval_on_selector(self._selector, expression, arg, strict=True)
        except Exception as exc:  # noqa: BLE001 - Playwright's Error, TimeoutError and driver errors all land here
            if not any(text in str(exc) for text in _NOT_YET):
                raise
        return await slow(self, expression, arg, timeout)

    evaluate._single_roundtrip = True
    Locator.evaluate = evaluate


def install():
    from playwright._impl import _connection

    _install_single_roundtrip_evaluate()
    if getattr(_connection.inspect, "_bounded", False):
        return
    shim = SimpleNamespace(**{k: getattr(inspect, k) for k in dir(inspect) if not k.startswith("__")})
    shim.stack = _stack
    shim._bounded = True
    _connection.inspect = shim
