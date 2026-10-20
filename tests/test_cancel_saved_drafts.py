import pytest
import httpx

from jobpilot.db import Job, Run, Setting, Outbox
from jobpilot.operations.cancel import cancel_saved_drafts
from jobpilot.operations.close_tabs import close_inactive_tabs


@pytest.fixture(autouse=True)
def synthetic_tab_snapshot(monkeypatch):
    def snapshot(endpoint, client):
        response = client.get(endpoint + "/json/list")
        return {t["id"]: t for t in response.json() if t.get("type") == "page"}

    monkeypatch.setattr("jobpilot.operations.close_tabs._targets", snapshot)


def test_cancel_requires_pause_and_drained_owners(service):
    with pytest.raises(RuntimeError, match="Pause workers"):
        cancel_saved_drafts(service.db)
    service.db.set_setting("control", {"paused": True, "auto_submit": False})
    with service.db.session() as s:
        s.add(Run(id="owner", job_id="job", state="submitting"))
    with pytest.raises(RuntimeError, match="must drain"):
        cancel_saved_drafts(service.db)


def test_cancel_preserves_history_proof_and_blocks_recovery(service):
    service.db.set_setting("control", {"paused": True, "auto_submit": False})
    with service.db.session() as s:
        s.add_all(
            [
                Job(id="draft", identity="draft", url="https://example.test/1", status="waiting_answer"),
                Job(id="sent", identity="sent", url="https://example.test/2", status="confirmed"),
                Run(id="unsent", job_id="draft", state="waiting_answer", packet={"checkpoint": "kept"}),
                Run(id="old", job_id="sent", state="paused"),
                Run(id="proof", job_id="sent", state="confirmed", receipt={"website_confirmed": True}),
                Run(
                    id="reserved", job_id="draft", state="waiting_browser", packet={"verification_only": True}
                ),
                Outbox(id="wake", kind="fact_resume", run_id="unsent"),
                Setting(key="draft_recovery:draft", value={"scheduled": True}),
            ]
        )
    result = cancel_saved_drafts(service.db)
    assert result["cancelled_runs"] == 2 and result["preserved_reserved_drafts"] == 1
    with service.db.session() as s:
        assert s.get(Run, "unsent").packet == {"checkpoint": "kept"}
        assert s.get(Run, "proof").state == "confirmed"
        assert s.get(Run, "reserved").state == "waiting_browser"
        assert s.get(Job, "sent").status == "confirmed"
        assert s.get(Outbox, "wake").state == "cancelled"
    assert not service.db.get_setting("draft_recovery:draft")["scheduled"]
    assert service.db.get_setting("job_control:draft")["state"] == "paused"
    assert cancel_saved_drafts(service.db)["cancelled_runs"] == 0


@pytest.mark.parametrize("state", ["queued", "running", "submitting"])
def test_tab_cleanup_protects_every_active_owner(service, state):
    with service.db.session() as s:
        s.add_all(
            [
                Run(id="owner", job_id="job", state=state),
                Run(id="previous", job_id="older", state="paused"),
                Setting(key="desktop_job:job", value={"target_id": "active"}),
                Setting(key="desktop_job:older", value={"target_id": "parent"}),
            ]
        )
    requests = []

    def chrome(request):
        requests.append(request.url.path)
        return httpx.Response(
            200,
            json=[
                {"id": "active", "type": "page", "openerId": "parent"},
                {"id": "parent", "type": "page"},
                {"id": "sibling", "type": "page", "openerId": "parent"},
            ],
        )

    with httpx.Client(transport=httpx.MockTransport(chrome)) as client:
        assert close_inactive_tabs(service.db, "http://chrome.test", client) == {"closed": 0, "pending": 0}
    assert requests == ["/json/list"]
    assert service.db.get_setting("desktop_job:job") == {"target_id": "active"}
    assert service.db.get_setting("desktop_job:older") == {"target_id": "parent"}


def test_inactive_tab_cleanup_keeps_checkpoints_unknown_guards_and_user_tabs(service):
    saved = {
        "target_id": "draft",
        "ledger": {"field": {"verified": True}},
        "armed": True,
        "proof": "retained",
    }
    with service.db.session() as s:
        s.add_all(
            [
                Run(id="unknown", job_id="job", state="submission_unknown", packet={"reservation": "kept"}),
                Setting(key="desktop_job:job", value=saved),
            ]
        )
    closed = []

    def chrome(request):
        if request.url.path == "/json/list":
            return httpx.Response(
                200,
                json=[
                    {"id": "draft", "type": "page"},
                    {"id": "popup", "openerId": "draft", "type": "page"},
                    {"id": "child", "openerId": "popup", "type": "page"},
                    {"id": "user", "type": "page"},
                ],
            )
        # Ownership must be detached before Chrome receives a close command.
        assert service.db.get_setting("desktop_job:job")["target_id"] is None
        closed.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, text="Target is closing")

    with httpx.Client(transport=httpx.MockTransport(chrome)) as client:
        assert close_inactive_tabs(service.db, "http://chrome.test", client) == {"closed": 3, "pending": 0}
    assert set(closed) == {"draft", "popup", "child"}
    stored = service.db.get_setting("desktop_job:job")
    assert stored["ledger"] == saved["ledger"] and stored["armed"] and stored["proof"] == "retained"
    with service.db.session() as s:
        assert s.get(Run, "unknown").state == "submission_unknown"
        assert s.get(Run, "unknown").packet == {"reservation": "kept"}


def test_failed_popup_close_retries_after_opener_disappears(service):
    with service.db.session() as s:
        s.add_all(
            [
                Run(id="done", job_id="job", state="confirmed"),
                Setting(key="desktop_job:job", value={"target_id": "draft"}),
            ]
        )
    remaining = {"draft", "popup"}
    fail = True

    def chrome(request):
        if request.url.path == "/json/list":
            return httpx.Response(
                200, json=[{"id": tid, "type": "page", "openerId": "draft"} for tid in remaining]
            )
        tid = request.url.path.rsplit("/", 1)[-1]
        if tid == "popup" and fail:
            return httpx.Response(503)
        remaining.remove(tid)
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(chrome)) as client:
        assert close_inactive_tabs(service.db, "http://chrome.test", client) == {"closed": 1, "pending": 1}
        assert service.db.get_setting("desktop_job:job")["inactive_target_ids"] == ["popup"]
        fail = False
        assert close_inactive_tabs(service.db, "http://chrome.test", client) == {"closed": 1, "pending": 0}
    assert remaining == set()
