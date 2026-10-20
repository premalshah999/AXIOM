"""The in-browser sender-lease wait: bounded, non-blocking, and deferred with SENDER_BUSY after the cap."""

from types import SimpleNamespace

import pytest

from jobpilot import inbox as inbox_module
from jobpilot.inbox import LEASE_BUSY, LEASE_WAIT_CAP_SECONDS, Inbox


class Clock:
    """Virtual time: asyncio.sleep advances it, so the tests never really wait."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


class FakeMail:
    """A sibling run holds the sender until release_at (None = never released)."""

    def __init__(self, clock, release_at):
        self.clock = clock
        self.release_at = release_at
        self.begin_calls = 0

    def rule_for(self, url):
        return {"id": "rule-1", "mailbox_id": 1}

    def begin(self, run_id, rule, recipient, kind="auto", *, resume_challenge_id=None):
        self.begin_calls += 1
        if self.release_at is None or self.clock.now < self.release_at:
            raise ValueError("Another application is verifying with this sender; retry after it finishes")
        return {"id": "challenge-1", "since": self.clock.now, "expires": self.clock.now + 120}


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(
        inbox_module, "time", SimpleNamespace(monotonic=clock.monotonic, time=lambda: clock.now)
    )
    monkeypatch.setattr(inbox_module, "asyncio", SimpleNamespace(sleep=clock.sleep))
    return clock


def make_inbox(clock, release_at):
    mail = FakeMail(clock, release_at)
    return Inbox(SimpleNamespace(config=None, mail=mail)), mail


@pytest.mark.parametrize("released_after", [1, 4])
async def test_holder_released_within_cap_is_acquired(clock, released_after):
    inbox, mail = make_inbox(clock, release_at=clock.now + released_after)
    start = clock.now
    challenge = await inbox.arm("run-1", {"url": "https://x.test"}, "alex@example.test", patience=120)
    assert challenge["id"] == "challenge-1"
    assert challenge["rule"] == {"id": "rule-1", "mailbox_id": 1}
    assert released_after <= clock.now - start < LEASE_WAIT_CAP_SECONDS
    assert mail.begin_calls > 1


async def test_unreleased_holder_defers_with_sender_busy_after_cap(clock):
    inbox, mail = make_inbox(clock, release_at=None)
    start = clock.now
    with pytest.raises(ValueError, match=LEASE_BUSY):
        await inbox.arm("run-1", {"url": "https://x.test"}, "alex@example.test", patience=500)
    # The deadline is checked between one-second polls, so the wait ends within one poll of the cap.
    assert LEASE_WAIT_CAP_SECONDS <= clock.now - start <= LEASE_WAIT_CAP_SECONDS + 1
    assert mail.begin_calls > 1


async def test_patience_below_cap_is_respected(clock):
    inbox, _ = make_inbox(clock, release_at=None)
    start = clock.now
    with pytest.raises(ValueError, match=LEASE_BUSY):
        await inbox.arm("run-1", {"url": "https://x.test"}, "alex@example.test", patience=2)
    assert 2 <= clock.now - start <= 3


async def test_wait_polls_with_async_sleep_and_never_blocks_the_loop(clock, monkeypatch):
    # Every wait step must go through asyncio.sleep (awaited), not time.sleep.
    calls = []
    real_sleep = clock.sleep

    async def recording_sleep(seconds):
        calls.append(seconds)
        await real_sleep(seconds)

    monkeypatch.setattr(inbox_module, "asyncio", SimpleNamespace(sleep=recording_sleep))
    inbox, _ = make_inbox(clock, release_at=clock.now + 4)
    await inbox.arm("run-1", {"url": "https://x.test"}, "alex@example.test", patience=90)
    assert calls and all(s == 1 for s in calls)


async def test_a_shared_sender_platform_waits_in_the_browser_for_the_holder(clock):
    # Oracle (2026-10-18): four "email sender busy" collisions in 15 minutes when each run gave up after 5 s.
    events = []
    mail = FakeMail(clock, release_at=clock.now + 90)
    inbox = Inbox(SimpleNamespace(config=None, mail=mail, db=SimpleNamespace(event=lambda *a: events.append(a))))
    start = clock.now
    challenge = await inbox.arm("run-1", {"url": "https://x.test", "ats": "oracle"}, "alex@example.test", patience=180)
    assert challenge["id"] == "challenge-1" and 90 <= clock.now - start < 92
    assert [e[1] for e in events] == ["mail_wait"]
    with pytest.raises(ValueError, match=LEASE_BUSY):
        await make_inbox(clock, release_at=None)[0].arm(
            "run-2", {"url": "https://x.test", "ats": "oracle"}, "alex@example.test", patience=500
        )
