"""The form leaves an optional picker empty when it cannot commit the applicant's fact (offered choices do not
support it, or the write budget runs out). The answer-quality gate then flagged the blank against the saved fact as a
"confirmed fact mismatch" and blocked submission: Atlassian's Work Experience Country/State (India, Maharashtra) held a
complete application back on 2026-10-19."""

from types import SimpleNamespace

from jobpilot import answer_quality
from jobpilot.schemas import Answer


def test_an_unrepairable_optional_picker_never_blocks_submission():
    by_id = {
        "work-country": {"id": "work-country", "type": "combobox", "required": False},
        "address-state": {"id": "address-state", "type": "combobox", "required": True},
        "cover": {"id": "cover", "type": "textarea", "required": False},
    }
    pending = [{"field_id": key, "question": key, "repair_cause": "confirmed_fact_mismatch"} for key in by_id]
    kept, left = answer_quality.optional_left(pending, by_id)
    assert [item["field_id"] for item in kept] == ["address-state", "cover"]
    assert left == [{"field_id": "work-country", "cause": "confirmed_fact_mismatch", "advisory": True, "optional_left": True}]


def test_an_omitted_optional_picker_is_not_a_fact_mismatch(monkeypatch):
    monkeypatch.setattr(
        answer_quality,
        "supported_local",
        lambda form, field: Answer(field_id=field["id"], disposition="answer", value="India",
                                   evidence_ids=["profile:work.0.location"]),
    )
    field = {"id": "f1", "label": "Country", "type": "combobox", "required": False, "value": "", "options": []}
    form = SimpleNamespace(optional_picker_omissions={"k"}, field_key=lambda f: "k")
    assert answer_quality.local_corrections(form, [field]) == {}
    # Without the omission the blank still contradicts the saved fact.
    form.optional_picker_omissions = set()
    assert answer_quality.local_corrections(form, [field])["f1"][1] == "confirmed_fact_mismatch"
    # A required field is never excused by an omission record.
    form.optional_picker_omissions = {"k"}
    assert answer_quality.local_corrections(form, [{**field, "required": True}])["f1"][1] == "confirmed_fact_mismatch"
