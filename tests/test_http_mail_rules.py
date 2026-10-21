"""HTTP contract of the mail routes the app-password tests do not cover: rules, challenges, OAuth start and callback."""

from jobpilot.db import MailRule
from support_mail import seed


def rule_body(box):
    return {
        "mailbox_id": box["id"],
        "employer_origin": "https://careers.example.test",
        "sender_domains": ["notify.example.test"],
    }


def test_a_rule_is_added_and_removed_through_the_api(client, service):
    box, _ = seed(service)
    saved = client.post("/api/mail-rules", json=rule_body(box))
    assert saved.status_code == 200, saved.text
    rule = saved.json()
    assert client.get("/api/mail").json()["rules"], "the rule appears in the mail summary"
    assert client.delete(f"/api/mail-rules/{rule['id']}").json() == {"removed": True}
    with service.db.session() as s:
        assert s.get(MailRule, rule["id"]) is None
    assert client.delete(f"/api/mail-rules/{rule['id']}").status_code == 400


def test_a_rule_needs_a_connected_mailbox_and_exact_sender_domains(client, service):
    box, _ = seed(service)
    assert client.post("/api/mail-rules", json={**rule_body(box), "mailbox_id": "unknown"}).status_code == 400
    bad = client.post("/api/mail-rules", json={**rule_body(box), "sender_domains": ["*.example.test"]})
    assert bad.status_code == 400 and "exact sender domains" in bad.json()["detail"]
    assert client.post("/api/mail-rules", json={**rule_body(box), "sender_domains": []}).status_code == 422


def test_cancelling_a_challenge_that_is_not_waiting_is_a_400(client):
    assert client.post("/api/mail-challenges/unknown/cancel").status_code == 400


def test_starting_google_sign_in_without_a_client_id_is_a_400(client):
    answer = client.post("/api/mail/connect")
    assert answer.status_code == 400 and "GOOGLE_CLIENT_ID" in answer.json()["detail"]


def test_the_oauth_callback_with_no_state_redirects_to_the_dashboard_with_a_failure(client, config):
    answer = client.get("/oauth/gmail/callback", follow_redirects=False)
    assert answer.status_code == 303
    assert answer.headers["location"] == config.base_url.rstrip("/") + "/?gmail=connection_failed"
    assert answer.headers["cache-control"] == "no-store"


def test_the_oauth_callback_is_public_because_the_browser_returns_from_google_without_the_token(app):
    from fastapi.testclient import TestClient

    answer = TestClient(app).get("/oauth/gmail/callback", follow_redirects=False)
    assert answer.status_code == 303 and answer.headers["location"].endswith("/?gmail=connection_failed")


def test_the_inbox_check_needs_the_env_mailbox(client):
    answer = client.post("/api/inbox/check")
    assert answer.status_code == 400 and "GMAIL_ADDRESS" in answer.json()["detail"]
