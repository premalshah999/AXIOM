"""OmegaHires 2026-10-14: '(applicant confirmed)' leaked into a Work Authorization text answer."""

from jobpilot.answers import strip_fact_annotations


def test_provenance_notes_are_removed():
    assert (
        strip_fact_annotations("Yes, authorized to work for any employer in the USA (applicant confirmed)")
        == "Yes, authorized to work for any employer in the USA"
    )
    assert strip_fact_annotations("No convictions (confirmed by applicant)") == "No convictions"
    assert (
        strip_fact_annotations("No: never a citizen of restricted countries (applicant confirmed).")
        == "No: never a citizen of restricted countries"
    )
    assert strip_fact_annotations("Data Science (MS)") == "Data Science (MS)"
    assert strip_fact_annotations(True) is True
