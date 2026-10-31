"""`jobpilot verify` only works on a new installation, and it must say so before it does anything.

New installation, 2026-10-21: after the account and profile were created, `jobpilot verify` queued its demo
applications anyway. The demo form refused the real profile's values with HTTP 422 and the command ended with
"submission_unknown: Submission attempted, but no explicit receipt was captured", which says nothing about the cause,
and the demo jobs it left behind closed the open sign-up."""

import pytest
from fastapi.testclient import TestClient

from jobpilot import verify


@pytest.fixture
def against_the_app(app, config, monkeypatch):
    config.enable_demo = True
    monkeypatch.setattr(
        verify.httpx, "Client", lambda **kwargs: TestClient(app, headers=kwargs["headers"])
    )
    return config


def test_an_existing_profile_is_refused_before_any_demo_job_is_queued(against_the_app, client):
    client.put("/api/profile", json={"name": "Alex Doe", "email": "applicant@example.com"})
    with pytest.raises(verify.VerificationFailed) as caught:
        verify.verify_deployment(against_the_app, timeout=5)
    assert "new installation" in str(caught.value) and "applicant@example.com" in str(caught.value)
    assert client.get("/api/snapshot").json()["jobs"] == []


def test_a_new_installation_gets_past_the_profile_check(against_the_app):
    with pytest.raises(verify.VerificationFailed) as caught:
        verify.verify_deployment(against_the_app, timeout=5)
    # The profile check passed: the next check stops it. Without a built dashboard that is the dashboard itself (503);
    # in a checkout where `npm run build` has run, GET / is served and the next check is the disabled workers (the full
    # suite after a landing build failed on this, 2026-10-31).
    message = str(caught.value)
    assert "new installation" not in message
    assert message == "GET / returned HTTP 503" or message == "Workers are disabled"
