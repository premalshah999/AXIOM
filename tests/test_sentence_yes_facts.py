"""Target 2026-10-14: a saved 'Yes, authorized to work ...' sentence answers the authorization question."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

FACTS = {
    "work_authorization_US": "Yes, authorized to work for any employer in the USA (applicant confirmed)",
    "requires_future_sponsorship_US": False,
}
LABEL = "Are you legally authorized for employment in the United States?"


def field(options):
    return {
        "id": "q",
        "label": LABEL,
        "type": "dropdown",
        "required": True,
        "options": options,
        "value": "",
        "valid": True,
    }


def test_sentence_fact_picks_the_long_yes_option(service):
    r = Resolver(Profile(facts=FACTS), service.db, service.config)
    opts = [
        "Yes, I am legally authorized for employment in the United States.",
        "No, I am not legally authorized for employment in the United States.",
    ]
    a = r.local(field(opts))
    assert a is not None and a.value == opts[0]


def test_sentence_fact_picks_plain_yes(service):
    r = Resolver(Profile(facts=FACTS), service.db, service.config)
    assert r.local(field(["Yes", "No"])).value == "Yes"
