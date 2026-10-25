"""A field that formats the typed amount still holds the answer. Workable showed the typed salary "80000" as
"80,000" and the read-back called it refused until the run stalled (Funnel Leasing, 2026-10-25)."""

import pytest

from jobpilot.widgets import same_value

FIELD = {"type": "text", "label": "What is your expected annual base salary in U.S. dollars (USD)?"}


@pytest.mark.parametrize("shown", ["80,000", "$80,000", "80 000", "80000.00", "$ 80,000.00"])
def test_a_formatted_amount_is_the_typed_amount(shown):
    assert same_value(FIELD, shown, "80000")


@pytest.mark.parametrize("shown,typed", [("8,000", "80000"), ("80,000.50", "80000"), ("01234", "1234")])
def test_a_different_amount_is_still_refused(shown, typed):
    assert not same_value(FIELD, shown, typed)
