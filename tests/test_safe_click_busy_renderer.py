import asyncio
import pytest

from jobpilot import widgets


class BusyHandle:
    """A node whose first attribute read stalls like a busy renderer."""

    def __init__(self):
        self.calls, self.clicked = 0, False

    async def evaluate(self, script, *args):
        if "jpOriginalType" in script and "delete" not in script:
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(5)
            return None
        return False

    async def click(self, timeout=None):
        self.clicked = True


async def test_safe_click_survives_one_busy_renderer_stall(monkeypatch):
    async def uncovered(el):
        return None

    monkeypatch.setattr(widgets, "ensure_uncovered", uncovered)
    handle = BusyHandle()
    await widgets.safe_click(handle, timeout=1000, field_control=True)
    assert handle.calls == 2 and handle.clicked


class NavigatingHandle:
    def __init__(self, phase):
        self.phase, self.clicks, self.fallbacks = phase, 0, 0

    async def evaluate(self, script, *args):
        if "addEventListener" in script:
            return None
        if "jpOriginalType" in script:
            if self.phase == "before":
                raise RuntimeError("Execution context was destroyed, most likely because of a navigation")
            return None
        self.fallbacks += 1
        raise RuntimeError("Execution context was destroyed, most likely because of a navigation")

    async def click(self, **kwargs):
        self.clicks += 1
        raise TimeoutError("Click was dispatched but navigation exceeded the deadline")


async def test_navigation_during_timeout_fallback_is_observed_without_reclick(monkeypatch):
    async def uncovered(el):
        pass
    async def approach(el):
        return None
    monkeypatch.setattr(widgets, "ensure_uncovered", uncovered)
    handle = NavigatingHandle("after")
    await widgets.safe_click(handle)
    assert handle.clicks == 1
    assert handle.fallbacks == 1


async def test_navigation_before_any_click_is_not_treated_as_a_click(monkeypatch):
    async def uncovered(el):
        pass
    monkeypatch.setattr(widgets, "ensure_uncovered", uncovered)
    handle = NavigatingHandle("before")
    with pytest.raises(RuntimeError, match="Execution context"):
        await widgets.safe_click(handle)
    assert handle.clicks == 0


async def test_timeout_after_native_dispatch_never_replays_click(monkeypatch):
    class Dispatched(NavigatingHandle):
        async def evaluate(self, script, *args):
            if 'e=>e.__jpClickObserved===true' in script:
                return True
            if 'addEventListener' in script or 'jpOriginalType' in script:
                return None
            raise AssertionError('No fallback dispatch after observed native click')
    async def uncovered(el): pass
    async def approach(el): return None
    monkeypatch.setattr(widgets, 'ensure_uncovered', uncovered)
    handle=Dispatched('after')
    await widgets.safe_click(handle)
    assert handle.clicks==1
