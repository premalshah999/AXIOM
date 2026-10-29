"""A label inside a negative answer is not that answer (forms audit, 2026-10-29): bare checkbox/option labels such as
"Veteran" or "Disability" were chosen for "I am not a protected veteran" and "No, I do not have a disability"."""

import pytest

from jobpilot.widgets import best_option


@pytest.mark.parametrize("options,value,expected", [
    (["Veteran", "Disabled Veteran"], "I am not a protected veteran", None),
    (["Disability", "Veteran"], "No, I do not have a disability", None),
    (["I am a protected veteran", "I am not a protected veteran"], "I am not a protected veteran",
     "I am not a protected veteran"),
    (["Hispanic or Latino", "Not Hispanic or Latino"], "Not Hispanic or Latino", "Not Hispanic or Latino"),
    (["Mobile", "Home"], "Mobile Phone", "Mobile"),
    (["No", "Yes"], "No, I am not", "No"),
])
def test_a_negative_answer_never_takes_the_affirmative_label(options, value, expected):
    assert best_option(options, value) == expected
