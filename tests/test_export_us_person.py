"""Intuitive 2026-10-14: an export assessment asking U.S.-person status is answered from citizenship facts."""

from jobpilot.knowledge import relevant_fact

LABEL = (
    "U.S. EXPORT COMPLIANCE ASSESSMENT ... Export controls do not apply to U.S. Persons. Are you a U.S. "
    "Citizen, a Lawful Permanent Resident (issued an alien registration card from the U.S. government), a "
    "Lawful Asylee, or a Lawful Refugee of the United States?"
)


def test_parenthetical_government_does_not_set_the_subject():
    from jobpilot.knowledge import topic

    assert topic(LABEL) == "citizenship"


def test_us_person_status_accepts_citizenship_and_immigration_facts():
    assert relevant_fact(LABEL, "us_citizen")
    assert not relevant_fact(LABEL, "willing_to_relocate")


def test_plain_export_question_keeps_export_facts_only():
    label = "Have you ever been subject to U.S. export control restrictions or sanctions?"
    assert relevant_fact(label, "export_control_restricted_country_history")
    assert not relevant_fact(label, "us_citizen")
