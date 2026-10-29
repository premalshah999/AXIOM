"""Gates found wrong on 2026-10-29: unfetched postings refused for good, an unreadable HTTP 200 treated as an outage that
never cools down, and ranking's model calls recorded nowhere."""

import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select

from jobpilot import discovery, models, provider_health
from jobpilot.db import Event, Job, Resume, Setting
from jobpilot.features.discovery.policy import UNFETCHED_CHARS, DiscoveryPolicy, gate
from jobpilot.schemas import JobInput

POLICY = DiscoveryPolicy()


# --- the discovery gate ------------------------------------------------------------------------------------------------


def posting(description, url="https://go.example.test/s/Ab3x", title="Software Engineer I"):
    return {"title": title, "url": url, "description": description, "location": "Austin, TX", "country": "US"}


@pytest.mark.parametrize("description", ["", "Engineer", "x" * (UNFETCHED_CHARS - 1)])
def test_a_posting_whose_text_was_never_fetched_is_not_a_careers_page(description):
    """6-27 characters on short-link URLs were refused as generic_careers_page for good: the fetch ranking makes
    never ran for a refused job (2026-10-29)."""
    assert gate(posting(description), POLICY) is None


def test_a_fetched_page_without_a_job_id_is_still_a_careers_page():
    assert gate(posting("Our careers page lists open roles. " * 3), POLICY) == "generic_careers_page"
    assert gate(posting("Build services in Python. " * 20), POLICY) is None  # a real posting under any URL
    assert gate(posting("", title="Careers"), POLICY) == "generic_careers_page"


# --- ranking fetches what the gate no longer refuses ------------------------------------------------------------------


def seed(service, url, description, title="Software Engineer I"):
    with service.db.exclusive() as s:
        if not s.get(Resume, "r1"):
            s.add(Resume(id="r1", name="Main", filename="main.pdf", sha256="x" * 64, text="Python, SQL"))
    job, _ = discovery.add_job(
        service.db, JobInput(url=url, title=title, company="ExampleSoft", description=description), origin="manual"
    )
    return job["id"]


def offline(monkeypatch, code=503):
    async def fetch(url, **kwargs):
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("down", request=request, response=httpx.Response(code, request=request))

    async def no_api(db, job_id, url):
        return None

    monkeypatch.setattr(discovery, "fetch", fetch)
    monkeypatch.setattr(discovery, "posting_api_text", no_api)


def ranker(monkeypatch, seen):
    async def structured(config, db, output_type, instructions, prompt, *args, **kwargs):
        jobs = json.loads(prompt)["jobs"]
        seen.extend(j["job_id"] for j in jobs)
        return output_type(
            jobs=[{"job_id": j["job_id"], "score": 80, "eligible": True, "resume_id": "r1", "reason": "fit"} for j in jobs]
        )

    monkeypatch.setattr(discovery, "structured", structured)


def test_a_stub_that_cannot_be_fetched_and_names_no_job_is_deferred_not_ranked(service, monkeypatch):
    stub = seed(service, "https://go.example.test/s/Ab3x", "Engineer")
    offline(monkeypatch)
    seen = []
    ranker(monkeypatch, seen)
    out = {r["job_id"]: r for r in asyncio.run(discovery.rank_batch(service.db, service.config, [stub]))}
    assert "deferred" in out[stub] and not seen
    with service.db.session() as s:
        assert s.get(Job, stub).status == "new"


def test_a_stub_on_a_url_that_names_a_job_is_ranked_when_the_fetch_fails(service, monkeypatch):
    stub = seed(service, "https://job-boards.greenhouse.io/acme/jobs/801", "Engineer")
    offline(monkeypatch)
    seen = []
    ranker(monkeypatch, seen)
    out = {r["job_id"]: r for r in asyncio.run(discovery.rank_batch(service.db, service.config, [stub]))}
    assert seen == [stub] and out[stub]["score"] == 80


def test_a_stub_is_replaced_by_the_fetched_posting_before_it_is_ranked(service, monkeypatch):
    stub = seed(service, "https://go.example.test/s/Ab3x", "Engineer")
    text = "Build services in Python. " * 20

    async def enrich(db, config, job_id):
        with db.session() as s:
            s.get(Job, job_id).description = text

    monkeypatch.setattr(discovery, "enrich", enrich)
    prompts = []
    seen = []

    async def structured(config, db, output_type, instructions, prompt, *args, **kwargs):
        prompts.append(prompt)
        seen.append(stub)
        return output_type(jobs=[{"job_id": stub, "score": 80, "eligible": True, "resume_id": "r1", "reason": "fit"}])

    monkeypatch.setattr(discovery, "structured", structured)
    asyncio.run(discovery.rank_batch(service.db, service.config, [stub]))
    assert seen and "Build services in Python." in prompts[0]


# --- an HTTP 200 reply that is not a completion -------------------------------------------------------------------------


class Probe(BaseModel):
    ok: bool


UNREADABLE = {"id": "fixture"}  # no choices: HTTP 200, but not a completion


def completion(content='{"ok":true}'):
    return {
        "id": "fixture", "object": "chat.completion", "created": 0, "model": "fixture",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 10, "total_tokens": 1010, "cost": 0.00003},
    }


@pytest.fixture
def provider(service, monkeypatch):
    config = service.config.model_copy(update={"llm_provider": "openrouter", "openrouter_api_key": "fixture-only"})
    replies = []

    async def respond(request):
        reply = replies.pop(0)
        return httpx.Response(200, text=reply) if isinstance(reply, str) else httpx.Response(200, json=reply)

    def client(db, cfg, run_id=None, **kwargs):
        return httpx.AsyncClient(
            transport=models.MeteredTransport(
                db, cfg, run_id, inner=httpx.MockTransport(respond), thinking=kwargs["thinking"],
                purpose=kwargs.get("purpose", "unspecified"),
            )
        )

    monkeypatch.setattr(models, "http_client", client)
    provider_health._invalid.clear()
    yield config, replies
    provider_health._invalid.clear()


async def ask(config, service):
    return await models.structured(config, service.db, Probe, "fixture", "fixture", retries=0)


@pytest.mark.parametrize("bad", [UNREADABLE, "<html>Gateway page served with status 200</html>"])
async def test_an_unreadable_reply_is_its_own_error_and_a_repeat_opens_the_cooldown(service, provider, bad):
    """Sail Research's reply failed validation: parked runs retried every 1-2 minutes and the cooldown never opened."""
    config, replies = provider
    for n in range(provider_health.INVALID_AFTER):
        replies.append(bad)
        with pytest.raises(models.AnswerModelInvalidResponse):
            await ask(config, service)
        gate_ = provider_health.dispatch_gate(service.db, config)
        assert (gate_.limit == 0) == (n == provider_health.INVALID_AFTER - 1)
    assert gate_.code == "provider_unavailable" and "cannot be read" in gate_.reason
    assert provider_health.status(service.db, config)["last_error"] == provider_health.INVALID
    # It is still an AnswerModelUnavailable, so every existing handler parks the run as before.
    assert issubclass(models.AnswerModelInvalidResponse, models.AnswerModelUnavailable)


async def test_a_reply_that_reads_ends_the_count(service, provider):
    config, replies = provider
    for reply in (UNREADABLE, UNREADABLE, completion(), UNREADABLE, UNREADABLE):
        replies.append(reply)
        try:
            await ask(config, service)
        except models.AnswerModelInvalidResponse:
            pass
    assert provider_health.dispatch_gate(service.db, config).limit != 0


async def test_a_model_slip_in_the_output_is_not_an_unreadable_reply(service, provider):
    config, replies = provider
    for _ in range(provider_health.INVALID_AFTER + 1):
        replies.append(completion(content='{"wrong": 1}'))
        with pytest.raises(Exception) as caught:
            await ask(config, service)
        assert not isinstance(caught.value, models.AnswerModelInvalidResponse)
    assert provider_health.dispatch_gate(service.db, config).limit != 0


def test_a_probe_that_recovers_and_answers_unreadably_again_reopens_at_once(service):
    config = service.config
    service.db.set_setting(provider_health.key(config), {"generation": 4, "failures": 0, "recovered_at": provider_health.time.time()})
    assert provider_health.note_invalid_response(service.db, config) is True
    assert provider_health.status(service.db, config)["state"] == "cooldown"


def test_the_answer_batch_reason_keeps_the_class(service):
    from types import SimpleNamespace

    answer = SimpleNamespace(
        blocker_type="site_transient", reason="Answer model could not complete: AnswerModelInvalidResponse"
    )
    with pytest.raises(models.AnswerModelInvalidResponse):
        models.AnswerModelUnavailable.raise_for(answer)
    answer.reason = "Answer model could not complete: TimeoutError"
    with pytest.raises(models.AnswerModelUnavailable) as caught:
        models.AnswerModelUnavailable.raise_for(answer)
    assert type(caught.value) is models.AnswerModelUnavailable


# --- ranking's model calls are recorded ---------------------------------------------------------------------------------


async def test_a_call_with_no_run_records_one_event_with_its_cost(service, provider):
    config, replies = provider

    class RankedBatch(BaseModel):
        ok: bool

    replies.append(completion())
    assert (await models.structured(config, service.db, RankedBatch, "fixture", "fixture", retries=0)).ok
    with service.db.session() as s:
        events = list(s.scalars(select(Event).where(Event.kind == "model_call")))
    assert [e.run_id for e in events] == ["ranking"]
    data = events[0].data
    assert data["purpose"] == "RankedBatch" and data["prompt_tokens"] == 1000 and data["completion_tokens"] == 10
    assert data["billed_usd"] == 0.00003 and data["elapsed_seconds"] >= 0


def test_other_runless_calls_are_filed_apart_from_ranking():
    assert models.call_owner("run-1", "ClassifiedFit") == "run-1"
    assert models.call_owner(None, "ClassifiedFit") == "ranking"
    assert models.call_owner("", "AnswerBatch") == "background"
