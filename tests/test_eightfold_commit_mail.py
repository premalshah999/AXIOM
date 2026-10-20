from types import SimpleNamespace

from jobpilot.adapters.base import Adapter
from jobpilot.adapters.others import Eightfold


async def test_request_window_precedes_eightfold_commit(monkeypatch):
    calls = []

    async def arm():
        calls.append("arm")
        return True

    async def commit(self, final):
        calls.append("commit")
        return {"state": "submission_unknown"}

    monkeypatch.setattr(Adapter, "commit", commit)
    adapter = object.__new__(Eightfold)
    adapter.committed = False
    adapter.arm_mail = arm
    assert await adapter.commit({"id": "submit"}) == {"state": "submission_unknown"}
    assert calls == ["arm", "commit"]


async def test_busy_sender_prevents_eightfold_submission(monkeypatch):
    async def arm():
        return False

    async def commit(self, final):
        raise AssertionError("Must not submit without the request window")

    monkeypatch.setattr(Adapter, "commit", commit)
    adapter = object.__new__(Eightfold)
    adapter.committed = False
    adapter.arm_mail = arm
    adapter.lease_busy = lambda: {"state": "timed_out", "recovery_cause": "sender_busy"}
    assert (await adapter.commit({"id": "submit"}))["recovery_cause"] == "sender_busy"
