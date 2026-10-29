"""Prompt layout (2026-10-29): the provider caches the longest shared prefix, so stable content leads. Every change here
reorders or drops only empty/default values; these tests pin that each fact still reaches the model."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot import models
from jobpilot.answers import Resolver
from jobpilot.pipeline_comparison import worker_context
from jobpilot.prompt_layout import canonical, compact_scope, navigation_payload, without_defaults
from jobpilot.schemas import Answer, AnswerBatch, Profile


def leaves(value, path=""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from leaves(v, f"{path}/{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from leaves(v, f"{path}[{i}]")
    else:
        yield path, value


def test_without_defaults_drops_only_empty_metadata():
    record = {"value": False, "row_id": None, "options": [], "scope": "global", "version": 0, "flag": False, "note": ""}
    assert without_defaults(record) == {"value": False, "scope": "global", "version": 0}
    # A False fact value is the answer ("requires sponsorship: no") and is never dropped when named.
    assert without_defaults({"answer": "", "x": None}, keep=("answer",)) == {"answer": ""}


def test_canonical_orders_by_key_without_changing_values():
    facts = {"fact:b": False, "fact:a": {"z": 1, "y": [2]}, "profile:c": "x"}
    assert list(canonical(facts)) == ["fact:a", "fact:b", "profile:c"]
    assert dict(canonical(facts)) == facts


def test_compact_scope_keeps_every_reference_and_the_store_version():
    meta = {
        "question": {"key": "k", "text": "t", "comparator": None, "quantities": [], "negated": False},
        "employer_id": None,
        "store_version": 0,
        "references": [
            {"id": "knowledge:f1", "kind": "fact", "match": "exact", "scope": "global", "row_id": None, "version": 1},
            {"id": "knowledge:f2", "kind": "narrative", "match": "lexical", "scope": "employer", "row_id": "r1", "version": 2},
        ],
        "conflicts": [],
        "missing": ["authorization"],
        "expired": [],
    }
    out = compact_scope(meta)
    assert [r["id"] for r in out["references"]] == ["knowledge:f1", "knowledge:f2"]
    assert out["references"][1]["row_id"] == "r1" and "row_id" not in out["references"][0]
    assert out["store_version"] == 0 and out["missing"] == ["authorization"]
    assert "conflicts" not in out and "expired" not in out and "employer_id" not in out
    # Nothing present in the original with a real value is lost.
    kept = dict(leaves(out))
    for path, value in leaves(meta):
        if value not in (None, "", [], False):
            assert kept.get(path) == value or path.startswith("/question")


def observation(revision):
    return {"url": "https://example.test/apply", "revision": revision, "fields": [{"id": "f1"}], "text": "Page"}


def payload(revision, recent):
    return navigation_payload(
        company="ExampleSoft", role="Analyst", profile_history={"work_rows": 1}, owner_instruction="",
        repair_instruction="", specialist_handoff={}, observation=observation(revision), blocked_step={},
        applicant_context={"facts": {"fact:a": 1}}, supported_answers=[], visual_observation=None,
        recent_actions=recent,
    )


def test_navigation_scan_counter_no_longer_breaks_the_cached_prefix():
    one, two = json.dumps(payload(7, [])), json.dumps(payload(8, [{"action": "click"}]))
    shared = len(__import__("os").path.commonprefix([one, two]))
    # Everything up to the page's trailing per-step parts is byte-identical between consecutive steps.
    assert shared > one.index('"blocked_step"')
    assert one.index('"applicant_context"') < one.index('"observation"') < one.index('"recent_actions"')
    assert '"revision"' not in json.dumps(payload(7, [])["observation"])


def test_navigation_payload_carries_every_value_of_the_old_layout():
    new = payload(7, [{"action": "click"}])
    old_keys = {"company", "role", "profile_history", "owner_instruction", "repair_instruction", "specialist_handoff",
                "observation", "blocked_step", "applicant_context", "supported_answers", "visual_observation",
                "recent_actions"}
    assert old_keys <= set(new)
    assert new["observation_revision"] == 7 and new["observation"]["fields"] == [{"id": "f1"}]


@pytest.fixture
def resolver(service, monkeypatch):
    monkeypatch.setattr(type(service.config), "llm_api_key", property(lambda self: "test"), raising=False)
    return Resolver(
        Profile(facts={"zeta_fact": "last", "alpha_fact": False, "willing_to_relocate": True}),
        service.db,
        service.config,
    )


def question(i, label):
    return {"id": f"q{i}", "label": label, "type": "text", "required": True, "options": [], "value": "", "valid": True}


async def test_answer_batch_prompt_is_sorted_and_loses_no_fact(resolver, monkeypatch):
    seen = []

    async def structured(config, db, output_type, instructions, prompt, *a, **k):
        seen.append((instructions, json.loads(prompt)))
        return AnswerBatch(answers=[Answer(field_id=q["id"], disposition="skip", reason="fixture") for q in json.loads(prompt)["questions"]])

    monkeypatch.setattr("jobpilot.answers.model_batch.structured", structured)
    resolver.job = {"title": "Analyst", "ats": "greenhouse"}
    facts, _, _ = resolver.answer_sources()
    await resolver.resolve([question(1, "Describe your favorite tool")], use_local=False)
    instructions, sent = seen[0]
    sent_facts = sent["confirmed_facts"]
    assert list(sent_facts) == sorted(sent_facts)
    # Every fact the selector kept still reaches the model with the same value (False included).
    assert sent_facts["fact:alpha_fact"] is False and sent_facts["fact:zeta_fact"] == "last"
    assert all(facts[k] == v for k, v in sent_facts.items() if k in facts)
    # The platform brief is last, so different ATSs share the whole policy text as a cached prefix.
    assert instructions.endswith(worker_context("greenhouse"))
    assert instructions.startswith("You are the applicant's autonomous application agent.")


async def test_answer_batch_instruction_prefix_is_shared_across_ats(resolver, monkeypatch):
    seen = []

    async def structured(config, db, output_type, instructions, prompt, *a, **k):
        seen.append(instructions)
        return AnswerBatch(answers=[Answer(field_id="q1", disposition="skip", reason="fixture")])

    monkeypatch.setattr("jobpilot.answers.model_batch.structured", structured)
    for ats in ("greenhouse", "workday"):
        resolver.job = {"title": "Analyst", "ats": ats}
        resolver.answer_cache.clear()
        await resolver.resolve([question(1, "Describe your favorite tool")], use_local=False)
    import os

    shared = len(os.path.commonprefix(seen))
    assert shared > 0.9 * min(len(s) for s in seen)


def test_navigation_context_keeps_every_retrieved_value_and_drops_only_defaults(service, monkeypatch):
    from jobpilot.continuous_agent import navigation_context

    item = {
        "fact_id": "m:fact:relocate", "reference": "knowledge:m:fact:relocate", "kind": "fact", "match": "exact",
        "category": "personal", "question": "Will you relocate?", "value": False, "scope": "global",
        "employer_id": None, "row_id": None, "version": 1, "source": "",
    }

    def retrieve(_, ids):
        return {
            fid: {"question": {"key": "k", "negated": False, "comparator": None}, "conflicts": [], "truncated": False,
                  "items": [dict(item)]}
            for fid in ids
        }

    monkeypatch.setattr("jobpilot.continuous_agent.retrieve", retrieve)
    resolver = Resolver(Profile(facts={"zeta": 1, "alpha": True}), service.db, service.config)
    field = {"id": "1", "label": "Will you relocate?", "type": "radio", "required": True, "options": ["Yes", "No"],
             "value": "", "valid": True}
    out = navigation_context(SimpleNamespace(resolver=resolver, fields={"1": field}), {"fields": [field]})
    kept = out["knowledge"]["knowledge:m:fact:relocate"]
    assert kept["value"] is False and kept["question"] == "Will you relocate?" and kept["match"] == "exact"
    assert "row_id" not in kept and "employer_id" not in kept and "fact_id" not in kept
    assert out["questions"]["1"] == {"question": {"key": "k"}, "references": ["knowledge:m:fact:relocate"]}
    assert list(out["facts"]) == sorted(out["facts"])
