"""Owner 2026-10-27: salary answers fall within 90,000-120,000. A range question gets the range; an amount gets the
recorded amount; an offered band holding it is chosen."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile


def resolver(service):
    profile = Profile(first_name="Alex", salary_expectation="105000",
                      answer_rules={"salary_stance": "$90,000 - $120,000"})
    return Resolver(profile, service.db, service.config, job={"ats": "smartrecruiters", "url": "https://x.example.test"})


def field(label, kind="text", options=()):
    return {"id": "s", "label": label, "type": kind, "required": True, "options": list(options), "value": "",
            "valid": True}


def test_a_range_question_gets_the_owners_range(service):
    a = resolver(service).local(field("Please indicate the yearly salary range you are targeting in USD:"))
    assert a.value == "$90,000 - $120,000"


def test_an_amount_question_gets_an_amount_in_the_range(service):
    a = resolver(service).local(field("What are your salary expectations?"))
    assert a is not None and 90000 <= int("".join(c for c in a.value if c.isdigit())) <= 120000
