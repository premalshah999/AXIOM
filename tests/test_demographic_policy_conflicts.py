"""Roblox 2026-10-14: conflicting saved race answers no longer block the owner's race preference."""

from types import SimpleNamespace

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

OPTIONS = ["East Asian", "South Asian", "Southeast Asian", "White or European", "I prefer to self-describe"]
LABEL = "How would you describe your racial/ethnic background? (mark all that apply)"


def resolver(service, options=OPTIONS):
    r = Resolver(
        Profile(facts={"race_preference": ["Asian", "South Asian"]}, decline_demographics=True),
        service.db,
        service.config,
    )
    field = {
        "id": "q",
        "label": LABEL,
        "type": "combobox",
        "required": True,
        "options": options,
        "value": "",
        "valid": True,
    }
    packet = SimpleNamespace(conflicts=[{"a": 1}], items=[], exact_answer=None)
    return r, field, packet


def test_race_preference_wins_over_conflicts(service):
    r, field, packet = resolver(service)
    answer = r.structured_answer(field, packet)
    assert answer.value == "South Asian" and answer.disposition == "answer"


def test_decline_option_wins_when_offered(service):
    r, field, packet = resolver(service, OPTIONS + ["I don't wish to answer"])
    assert r.structured_answer(field, packet).value == "I don't wish to answer"


def test_selected_option_missing_from_list_still_counts(service):
    r, field, packet = resolver(service, [o for o in OPTIONS if o != "South Asian"])
    field["value"] = "South Asian"
    assert r.structured_answer(field, packet).value == "South Asian"
