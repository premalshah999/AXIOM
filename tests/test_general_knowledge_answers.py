"""Interco's conduct quiz (2026-10-30): trivia ("Which of the following is the smallest storage unit listed?") was
refused as unsupported_evidence, and "a company sponsored happy hour" was read as an immigration-sponsorship question.
General knowledge needs no profile evidence; a question about the applicant, a sensitive subject or a sanctions
disclosure still needs a saved fact."""

import pytest

from jobpilot.answers import Resolver, critical_question
from jobpilot.knowledge import topic
from jobpilot.schemas import Answer, Profile

HAPPY_HOUR = (
    "You are at a company sponsored happy hour or dinner. You notice that your colleagues have ordered alcoholic "
    "beverages and you would like a glass of wine. What is a good rule of thumb to remember at work events?"
)
SANCTIONS = (
    "Have you ever been excluded, sanctioned, disciplined, or had a license or certification revoked by any federal "
    "entity, state entity, or licensing board?"
)


def field(label, options, kind="radio"):
    return {"id": "q", "label": label, "type": kind, "required": True, "options": list(options), "value": "", "valid": True}


def check(service, f, value, evidence=()):
    r = Resolver(Profile(), service.db, service.config, job={"ats": "custom", "url": "https://x.test"})
    facts, sources, learned = r.answer_sources()
    proposal = Answer(field_id="q", value=value, disposition="answer", evidence_ids=list(evidence))
    return r._validate_answer(f, proposal, facts, sources, learned)


def test_trivia_needs_no_profile_evidence(service):
    done = check(service, field("Which of the following is the smallest storage unit listed?",
                                ["Megabyte", "Terabyte", "Gigabyte", "Gyrobyte", "None of the Above"]), "Megabyte")
    assert done.disposition == "answer" and done.value == "Megabyte"


@pytest.mark.parametrize("label, options", [
    (SANCTIONS, ["Yes", "No"]),  # a disclosure: only a saved fact answers it
    ("Have you ever been convicted of a crime?", ["Yes", "No"]),
    ("Which of the following is your preferred pronoun?", ["He/him", "She/her", "They/them"]),  # about the applicant
    ("Does our company offer remote work?", ["Yes", "No"]),  # the employer's, not general knowledge
    ("Do you require visa sponsorship?", ["Yes", "No"]),
    ("From Date Year", ["2024", "2025", "2026"]),  # a field label, not a quiz question
])
def test_a_question_about_the_applicant_or_a_disclosure_still_needs_a_fact(service, label, options):
    assert check(service, field(label, options), options[-1]).disposition != "answer"


def test_a_sponsored_social_event_is_not_an_immigration_question():
    assert not critical_question(HAPPY_HOUR) and topic(HAPPY_HOUR) == ""
    assert critical_question("Will you require visa sponsorship?") and topic("Will you require sponsorship?") == "sponsorship"
    assert topic("Do you require employer-sponsored visa support?") == "sponsorship"


async def test_a_refused_cover_letter_says_which_check_refused_it(service, monkeypatch):
    """MetOx (2026-10-29): the letter was drafted and repaired twice and refused; nothing recorded why."""
    from types import SimpleNamespace

    from jobpilot import letters

    async def written(*args, **kwargs):
        return SimpleNamespace(text="A letter.", evidence_ids=["evidence:x"], problems=["unsupported claim: 40% faster"])

    monkeypatch.setattr("jobpilot.execution_services.enabled", lambda db: True)
    monkeypatch.setattr("jobpilot.features.knowledge.writing.configured_model", lambda *a, **k: None)
    monkeypatch.setattr("jobpilot.features.knowledge.writing.write_cover_letter", written)
    r = Resolver(Profile(), service.db, service.config, job={"title": "Analyst", "company": "Acme", "description": "x"})
    monkeypatch.setattr(r, "variant_rows", lambda: [])
    with pytest.raises(ValueError, match="unsupported claim: 40% faster"):
        await letters.cover_letter(r)
