"""A newcomer who has not added a model key must be told which setting to add and where.

New installation, 2026-10-21: the setup guide's resume step answered "The selected model provider has no API key
configured." and a queued application "Configure the selected answer and navigation provider keys before live browser
runs". Neither said which key, which file or what to run afterwards."""

import pytest

from jobpilot.agent_settings import current, missing_key_message
from jobpilot.discovery import add_job
from jobpilot.schemas import JobInput


@pytest.mark.parametrize(
    "provider,setting", [("deepseek", "DEEPSEEK_API_KEY"), ("haiku", "HAIKU_API_KEY"), ("mimo", "MIMO_API_KEY")]
)
def test_the_message_sends_the_owner_to_setup_connections(provider, setting):
    """Keys are pasted in the dashboard now; editing .env stays possible (owner 2026-10-26)."""
    message = missing_key_message(provider)
    assert "Settings > Connections" in message and setting in message


def test_selecting_a_provider_without_a_key_says_how_to_add_it(client, service):
    value = current(service.db, service.config).model_dump()
    value["models"]["answers"] = "haiku"
    response = client.put("/api/agents", json=value)
    assert response.status_code >= 400
    assert "HAIKU_API_KEY" in response.text


async def test_queueing_a_live_application_without_a_key_says_how_to_add_it(service, prepared):
    service.config.mimo_api_key = ""
    job, _ = add_job(service.db, JobInput(url="https://jobs.example.com/roles/1", title="Engineer"))
    with pytest.raises(ValueError) as caught:
        await service.queue(job["id"], "dry_run", prepared["id"])
    assert "MIMO_API_KEY" in str(caught.value) and ".env" in str(caught.value)
