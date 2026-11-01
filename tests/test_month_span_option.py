"""A month answer picks the one offered span that contains it.

Oracle (2026-10-31): "What is your expected graduation date:" offers "April - June 2026" ... "October - December 2028", and
the saved answer "05/2026" was refused as 'supports the offered option "05/2026" instead' on five runs, ending each in a stall.
"""

import pytest

from jobpilot.widgets import best_option

SPANS = [
    "April - June 2026", "April - June 2027", "January - March 2026", "January - March 2027",
    "July - September 2026", "October - December 2026", "I am currently not attending school", "Not Listed",
]


@pytest.mark.parametrize("value", ["05/2026", "5/2026", "May 2026", "2026-05", "05/20/2026", "may, 2026"])
def test_a_month_is_the_span_that_contains_it(value):
    assert best_option(SPANS, value) == "April - June 2026"


def test_the_year_must_match_and_edges_belong_to_their_span():
    assert best_option(SPANS, "12/2026") == "October - December 2026"
    assert best_option(SPANS, "01/2027") == "January - March 2027"
    assert best_option(SPANS, "05/2025") is None, "no offered span holds May 2025"


def test_a_menu_that_is_not_made_of_spans_is_left_alone():
    assert best_option(["May 2026", "June 2026"], "05/2026") is None or best_option(["May 2026", "June 2026"], "05/2026") == "May 2026"
    assert best_option(["Yes", "No", "Maybe"], "05/2026") is None


def test_overlapping_spans_are_not_guessed_between():
    assert best_option(["January - June 2026", "April - June 2026", "July - December 2026"], "05/2026") is None
