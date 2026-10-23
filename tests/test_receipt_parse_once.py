"""The receipt poller compares every message with every run at the employer. Re-reading each message for every run
held one core at 100% with ~1,900 confirmed runs and the host gate stopped new applications (2026-10-23)."""

import jobpilot.receipts as receipts


def test_a_message_is_read_once_however_many_runs_it_is_compared_with(monkeypatch):
    calls = []
    real = receipts._read_message
    monkeypatch.setattr(receipts, "_read_message", lambda m: calls.append(m["id"]) or real(m))
    message = {"id": "m-parse-once", "internalDate": "1760000000000", "payload": {"headers": [
        {"name": "From", "value": "Jobs <jobs@example.test>"}, {"name": "Subject", "value": "Hello"}]}}
    run = {"id": "r", "created_at": "2026-01-01T00:00:00+00:00", "packet": {"profile": {"email": "a@example.test"}}}
    for i in range(200):
        job = {"id": f"j{i}", "company": f"Company {i}", "title": "Data Engineer", "url": f"https://example.test/{i}"}
        assert receipts.acknowledgement(message, job, run) is None
    assert calls == ["m-parse-once"]
