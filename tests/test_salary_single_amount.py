"""CVS Health 2026-10-14: a recorded single desired salary ("80000") sits in an offered range."""

from jobpilot.answer_quality import money_range, ranges_touch


def test_single_amount_is_a_point_range():
    assert money_range("80000") == (80000.0, 80000.0)
    assert ranges_touch("$80,000 - $100,000", "80000") and ranges_touch("$60,000 - $80,000", "80000")


def test_far_ranges_still_mismatch():
    assert not ranges_touch("$140,000 - $160,000", "80000")
    assert not ranges_touch("Decline to Answer", "80000")


def test_recorded_salary_picks_the_range_holding_it():
    from jobpilot.answers import Resolver
    from jobpilot.schemas import Profile

    opts = [
        "Under $20,000",
        "$60,000 - $80,000",
        "$80,000 - $100,000",
        "$140,000 - $160,000",
        "Decline to Answer",
    ]
    field = {"id": "x", "label": "What is your base pay expectation for this position?"}
    assert (
        Resolver(Profile(salary_expectation="80000"), None, None).policy_choice(field, opts)
        == "$80,000 - $100,000"
    )
    assert Resolver(Profile(), None, None).policy_choice(field, opts) is None


def test_compensation_textarea_takes_the_recorded_amount(service):
    # TransUnion (Workday) 2026-10-15: "What are your base compensation expectations for this role?" as a textarea.
    from jobpilot.answers import Resolver
    from jobpilot.schemas import Profile

    r = Resolver(Profile(salary_expectation="80000"), service.db, service.config)
    for kind in ("text", "textarea"):
        a = r.local(
            {
                "id": "c",
                "label": "What are your base compensation expectations for this role?",
                "type": kind,
                "required": True,
                "options": [],
                "value": "",
                "valid": True,
            }
        )
        assert a is not None and a.value == "80000"
    prose = r.local(
        {
            "id": "w",
            "label": "Why are you willing to relocate for this role?",
            "type": "textarea",
            "required": True,
            "options": [],
            "value": "",
            "valid": True,
        }
    )
    assert prose is None or prose.value != "Yes"


def test_compensation_question_asking_for_currency_gets_it(service):
    from jobpilot.answers import Resolver
    from jobpilot.schemas import Profile

    r = Resolver(Profile(salary_expectation="80000"), service.db, service.config)
    label = (
        "What are your base compensation expectations for this role? (Please provide your expectations only.) "
        "Please specify the amount and currency."
    )
    a = r.local(
        {
            "id": "c",
            "label": label,
            "type": "textarea",
            "required": True,
            "options": [],
            "value": "",
            "valid": True,
        }
    )
    assert a is not None and a.value == "80,000 USD"
