"""The setup guide asks every background fact docs/KNOWLEDGE.md tells a new applicant to confirm.

New installation, 2026-10-21: non-solicitation, "worked here before" for other employers, company relationships,
export-control history and asylee status were never asked by the guide, so a friend had to type them as JSON in
Setup > Profile > Facts or be asked about them on every application."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WIZARD = {
    "has_non_solicitation": False,
    "previously_employed_here": False,
    "company_relationships": False,
    "export_control_restricted_country_history": False,
    "has_asylee_status": False,
    "has_refugee_status": False,
}


def background_step():
    text = (ROOT / "frontend" / "src" / "onboarding.tsx").read_text()
    # Background and self-identification are one Screening step since the v1 setup guide.
    return text.split("function ScreeningStep", 1)[1].split("\nfunction ", 1)[0]


@pytest.mark.parametrize("key", sorted(WIZARD))
def test_the_background_step_writes_the_key_the_resolver_reads(key):
    assert re.search(rf'setFact\("{key}"|yn\("{key}"[,)]', background_step())


def test_no_background_question_is_defaulted():
    step = background_step()
    assert "defaultValue" not in step and "?? false" not in step and "|| false" not in step


def test_the_saved_facts_reach_the_knowledge_base_and_unanswered_ones_stay_absent(client):
    client.put(
        "/api/profile",
        json={"name": "Alex Doe", "email": "applicant@example.com", "facts": {**WIZARD, "has_non_compete": True}},
    )
    items = client.get("/api/v2/knowledge/facts?size=100").json()["items"]
    stored = {row["key"]: row for row in items}
    for key, value in WIZARD.items():
        assert stored[f"kf:{key}"]["value"] is value, key
        assert stored[f"kf:{key}"]["layer"] == "confirmed"
    assert stored["kf:has_non_compete"]["value"] is True
    assert "kf:criminal_conviction" not in stored, "a fact nobody answered is not stored as No"
