"""A full date followed by a time or a note is still that date. Paylocity's "Available to Start" raised "Unsupported date
format" on every write and the run stalled (Lightology, Space Tango 2026-10-26)."""

import pytest

from jobpilot.widgets import parse_date


@pytest.mark.parametrize("value,parts", [
    ("2026-10-23T00:00:00", ("10", "23", "2026")),
    ("2026-10-23 (today)", ("10", "23", "2026")),
    ("10/23/2026 (today)", ("10", "23", "2026")),
    ("2026-10-23", ("10", "23", "2026")),
    ("05/2026", ("05", "", "2026")),
])
def test_a_date_with_trailing_text_is_read(value, parts):
    assert parse_date(value) == parts


def test_other_text_is_still_refused():
    # "Immediately" stays no date here: the audit must not read it as today's (answer_quality.incomparable_date).
    for text in ("Immediately", "Two weeks after an offer"):
        with pytest.raises(ValueError):
            parse_date(text)
