"""Oracle's "Total Experience In Years" is a text box that accepts only a number (HEXAWARE 2026-10-23: a paragraph,
"Enter a valid number. Use a dot as a separator.", run stalled). Year-count questions are numeric fields."""

from jobpilot.forms import numeric_year_counts


def test_year_count_questions_are_numeric():
    fields = [
        {"label": "Total Experience In Years", "type": "text"},
        {"label": "Relevant Experience In Years", "type": "text"},
        {"label": "How many years of Python experience do you have?", "type": "text"},
        {"label": "Years of experience", "type": "text"},
        {"label": "Describe your experience", "type": "textarea"},
        {"label": "Years of experience", "type": "select", "options": ["0-1", "2-3"]},
        {"label": "Why do you want this job?", "type": "text"},
    ]
    numeric_year_counts(fields)
    assert [f["type"] for f in fields] == ["number", "number", "number", "number", "textarea", "select", "text"]
