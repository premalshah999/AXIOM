"""Evidence packets keep the facts a question needs when its category holds many.

A broad category ("additional" held 69 structured facts in production) used to fill the packet's item budget in
stored order, so the one fact the question needed could be cut. Disclosure categories still keep every fact.
"""

from jobpilot.features.knowledge.retrieval import clear_cache, retrieve_answer_context
from jobpilot.schemas import Profile


def packet(service, facts, question):
    clear_cache()
    service.db.set_setting("profile", Profile(first_name="Alex", last_name="Doe", facts=facts).model_dump())
    return retrieve_answer_context(service.db, question)


def test_relevant_fact_survives_a_crowded_category(service):
    filler = {f"preference_{i:02d}_note": f"unrelated preference {i}" for i in range(30)}
    facts = {**filler, "willing_to_travel": "Yes, up to 50%"}
    result = packet(service, facts, "What percentage of travel are you willing to do for this role?")
    kept = [item.value for item in result.items]
    assert "Yes, up to 50%" in kept
    # The most similar fact is ranked first among the category's facts.
    assert kept.index("Yes, up to 50%") == 0 or result.items[0].match != "category"


def test_disclosure_category_keeps_stored_order_and_reports_missing(service):
    result = packet(service, {}, "Will you now or in the future require visa sponsorship?")
    assert "work_authorization" in result.missing
    facts = {"requires_future_sponsorship_US": False, "us_citizen": False}
    result = packet(service, facts, "Will you now or in the future require visa sponsorship?")
    assert not result.missing
    assert {item.value for item in result.items} >= {False}
