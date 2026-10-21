from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from jobpilot.admission import unknown_resubmit_allowed

NOW = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)


def run(state, minutes_ago=60, receipt=None):
    at = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return SimpleNamespace(state=state, receipt=receipt or {}, finished_at=at, created_at=at)


class Session:
    def __init__(self, policy=None):
        self.policy = policy

    def get(self, model, key):
        return SimpleNamespace(value=self.policy) if self.policy is not None else None


def test_uncertain_submission_is_retried_after_receipt_window():
    assert unknown_resubmit_allowed(
        Session({"enabled": True}), [run("waiting_browser"), run("submission_unknown")], NOW
    )


def test_retry_waits_for_receipt_window_and_is_bounded():
    assert not unknown_resubmit_allowed(Session({"enabled": True}), [run("submission_unknown", minutes_ago=5)], NOW)
    three = [run("submission_unknown")] * 3
    assert not unknown_resubmit_allowed(Session({"enabled": True}), three, NOW)
    assert unknown_resubmit_allowed(Session({"enabled": True, "max_resubmits": 5}), three, NOW)


def test_screenshot_confirmed_jobs_are_never_resubmitted():
    marked = run("submission_unknown")
    marked.job_id = "job1"
    assert not unknown_resubmit_allowed(Session({"enabled": True, "verdict": "submitted"}), [marked], NOW)


def test_confirmed_or_receipted_jobs_are_never_resubmitted():
    assert not unknown_resubmit_allowed(Session({"enabled": True}), [run("submission_unknown"), run("confirmed")], NOW)
    assert not unknown_resubmit_allowed(
        Session(), [run("submission_unknown", receipt={"email_confirmed": True})], NOW
    )
    assert not unknown_resubmit_allowed(Session({"enabled": False}), [run("submission_unknown")], NOW)


def test_resubmission_is_off_unless_the_installation_enables_it():
    unknown = [run("submission_unknown")]
    assert not unknown_resubmit_allowed(Session(), unknown, NOW)
    assert not unknown_resubmit_allowed(Session({"max_resubmits": 2}), unknown, NOW)
    assert unknown_resubmit_allowed(Session({"enabled": True}), unknown, NOW)
