"""What a month-period picker shows and what an answer says are compared as dates, never as text."""

import pytest

from jobpilot.widgets import period_dates, same_value

PICKER = {"label": "Start & end date", "type": "text", "widget": "period-picker"}


@pytest.mark.parametrize(
    "text,dates",
    [
        ("09/2020 - 06/2024", [(2020, 9), (2024, 6)]),
        ("2020 - 09 2024 - 06", [(2020, 9), (2024, 6)]),
        ("2020-09 - 2024-06", [(2020, 9), (2024, 6)]),
        ("2020年09 - 2024年6", [(2020, 9), (2024, 6)]),
        ("Sep 2020 - June 2024", [(2020, 9), (2024, 6)]),
        ("09/2020 - Present", [(2020, 9), "present"]),
        ("2020-09 至今", [(2020, 9), "present"]),
        # A year alone is no month: nothing is guessed.
        ("2020 - 2024", []),
        ("Start date - End date", []),
        ("", []),
    ],
)
def test_period_dates(text, dates):
    assert period_dates(text) == dates


def test_a_picker_showing_the_same_period_is_the_answer():
    assert same_value(PICKER, "2020 - 09 2024 - 06", "09/2020 - 06/2024")


@pytest.mark.parametrize("shown", ["2020 - 09 2024 - 07", "2019 - 09 2024 - 06", "2020 - 09", "", "Start date - End date"])
def test_a_picker_showing_anything_else_is_not(shown):
    assert not same_value(PICKER, shown, "09/2020 - 06/2024")


def test_other_fields_keep_their_own_comparison():
    assert not same_value({"label": "Notes", "type": "text"}, "2020 - 09 2024 - 06", "09/2020 - 06/2024")
