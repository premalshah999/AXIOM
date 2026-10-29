"""Montenson's WOTC questionnaire (2026-10-29): "received assistance at least 18 months between ..." matched the
over-18 age intent, and the answer auditor blocked a truthful No as contradicting the applicant's age fact."""

import pytest

from jobpilot.answers.intents import INTENTS

AGE = next(pattern for pattern, keys in INTENTS if "over_18" in keys)


@pytest.mark.parametrize("question,age", [
    ("Are you at least 18 years of age?", True),
    ("Are you 18 years or older?", True),
    ("Are you over 18?", True),
    ("Are you at least 18?", True),
    ("Are you of legal working age?", True),
    ("Have you or someone in your household received assistance at least 18 months between 2024 and now?", False),
    ("Were you unemployed and received unemployment compensation for at least 18 weeks?", False),
    ("Have you completed at least 18 credits?", False),
])
def test_only_an_age_question_is_the_age_intent(question, age):
    assert bool(AGE.search(question)) is age
