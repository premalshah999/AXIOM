from unittest.mock import AsyncMock

from jobpilot.answers import Resolver
from jobpilot.schemas import Answer, Profile


async def test_repeated_prime_packet_inference_reuses_the_answer_with_current_field_id(service, monkeypatch):
    """Prime, run 0ef849663776443aa0c0100f89cd690f, 2026-10-21: cache hit returned two tuple items, not three."""
    # Name, label and required flag from 06-form.form.json; synthetic answer only.
    field = {"id": "old", "name": "icims_f_Any_Relatives_Employed_Here", "label": "Any relatives employed here?",
             "type": "radio", "required": True, "options": ["Yes", "No"], "value": ""}
    resolver = Resolver(Profile(), service.db, service.config)
    inferred = Answer(field_id="old", value="No", disposition="answer", evidence_ids=["fact:relatives"])
    infer = AsyncMock(return_value=(inferred, "answered"))
    monkeypatch.setattr("jobpilot.inference_agent.infer", infer)
    for field_id in ("old", "rerendered"):
        current = {**field, "id": field_id}
        answers = [Answer(field_id=field_id, disposition="review", blocker_type="missing_fact")]
        await resolver.infer_before_asking(answers, {field_id: current})
        assert answers[0].field_id == field_id
        assert answers[0].value == "No"
        assert answers[0].evidence_ids == ["fact:relatives"]
    infer.assert_awaited_once()
    assert inferred.field_id == "old"
