"""Qualcomm (Eightfold) 2026-10-14: a data-sharing preference picks the narrowest option."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

OPTIONS = [
    "Use my profile data for ANY ROLES GLOBALLY THAT MAY MATCH MY SKILLS, including those I haven't applied to.",
    "Use my profile data for ONLY THE ROLE I AM APPLYING FOR. I will apply separately for other roles.",
]
LABEL = "Your choice will guide how we manage your data across our systems and help us create a more personalized experience"


def test_narrowest_data_use_is_chosen(service):
    r = Resolver(Profile(), service.db, service.config)
    f = {
        "id": "q",
        "label": LABEL,
        "type": "radiogroup",
        "required": True,
        "options": OPTIONS,
        "value": "",
        "valid": True,
    }
    assert r.policy_choice(f, OPTIONS) == OPTIONS[1]


def test_local_answers_the_data_sharing_radiogroup(service):
    r = Resolver(Profile(), service.db, service.config)
    f = {
        "id": "q",
        "label": LABEL,
        "type": "radiogroup",
        "required": True,
        "options": OPTIONS,
        "value": "",
        "valid": True,
    }
    assert r.local(f).value == OPTIONS[1]
