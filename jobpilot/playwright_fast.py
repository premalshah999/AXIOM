"""Bounded call-site capture for Playwright API calls.

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


def install():
    from playwright._impl import _connection

    if getattr(_connection.inspect, "_bounded", False):
        return
    shim = SimpleNamespace(**{k: getattr(inspect, k) for k in dir(inspect) if not k.startswith("__")})
    shim.stack = _stack
    shim._bounded = True
    _connection.inspect = shim
