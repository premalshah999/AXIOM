"""Amazon (2026-10-27): a reset's code page cannot be reopened; each continuation requested another reset and the
previous code was "Incorrect". The code that follows a reset request is awaited in the run (150 s, like Oracle's)."""

from types import SimpleNamespace

from jobpilot.adapters.base import RESET_CODE_INLINE_WAIT, Adapter


async def waited(reset):
    adapter = Adapter.__new__(Adapter)
    seen = {}

    async def wait(challenge, max_wait_seconds):
        seen["inline"] = max_wait_seconds
        return None

    adapter.challenge = {"id": "c1"}
    adapter.inbox = SimpleNamespace(live=lambda c: True, wait=wait)
    adapter.service = SimpleNamespace(mail=SimpleNamespace(narrow=lambda *a: None))
    adapter.emit = lambda *a, **k: None
    adapter.e = SimpleNamespace(extend=lambda seconds: None)
    adapter.config = SimpleNamespace(mail_wait_seconds=90)
    adapter.id = "custom"
    if reset:
        adapter.reset_in_progress = True
    await adapter.wait_email("code")
    return seen["inline"]


async def test_a_reset_code_is_awaited_in_the_run():
    assert await waited(reset=True) == RESET_CODE_INLINE_WAIT
    assert await waited(reset=False) == 30
