"""Vertiv (Oracle, 2026-10-27): the Nationality menu offered "Indian" for the citizenship fact "India". The answer
check read it as a confirmed-fact mismatch, its repair needed a fresh observation every pass, and the run stalled."""

from jobpilot.answer_quality import demonym_of, equivalent

NATIONALITY = {"label": "Nationality", "type": "combobox", "options": ["Indian", "Indonesian", "American"]}


def test_a_demonym_matches_its_country():
    assert equivalent(NATIONALITY, "Indian", "India")
    assert demonym_of("Canadian", "Canada") and demonym_of("Chinese", "China") and demonym_of("German", "Germany")
    assert demonym_of("American", "United States")


def test_another_country_is_still_a_mismatch():
    assert not equivalent(NATIONALITY, "Indonesian", "India")
    assert not demonym_of("American", "India")
    assert not demonym_of("Indian Ocean Territory", "India")


def test_only_citizenship_questions_compare_demonyms():
    assert not equivalent({"label": "Country", "type": "combobox"}, "Indian", "India")
