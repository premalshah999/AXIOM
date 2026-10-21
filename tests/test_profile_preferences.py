"""Applicant preferences live in the profile (answer_rules), not in code: a blank profile claims nothing."""

from jobpilot.answer_rules import delegated_facts, rules_for
from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

SALARY = {
    "id": "q",
    "label": "Desired salary",
    "type": "text",
    "required": True,
    "options": [],
    "value": "",
    "valid": True,
}


def facts_for(service, **profile):
    r = Resolver(Profile(**profile), service.db, service.config)
    return r.answer_sources()[0]


def test_blank_profile_claims_no_preference(service):
    for autonomous in (True, False):
        assert delegated_facts(rules_for(Profile()), autonomous) == {}
        facts = facts_for(service, autonomous=autonomous)
        assert not [k for k in facts if k.endswith("_policy")]


def test_stated_preferences_become_facts_only_when_autonomous(service):
    rules = {
        "salary_stance": "Open to discussion",
        "travel_willingness": "willing to travel up to 25%",
        "employment_type_sought": "a full-time role",
    }
    assert delegated_facts(rules_for(Profile(answer_rules=rules)), False) == {}
    facts = facts_for(service, autonomous=True, answer_rules=rules)
    assert "stating: Open to discussion" in facts["fact:compensation_choice_policy"]
    assert "Applicant is willing to travel up to 25%" in facts["fact:travel_percentage_choice_policy"]
    assert "pursuing a full-time role" in facts["fact:application_role_type_policy"]
    # Only what is stated: a profile with just a salary stance has no travel or employment-type claim.
    only = facts_for(service, autonomous=True, answer_rules={"salary_stance": "Negotiable"})
    assert "fact:compensation_choice_policy" in only
    assert "fact:travel_percentage_choice_policy" not in only and "fact:application_role_type_policy" not in only


def test_salary_answer_uses_the_profiles_own_stance(service):
    blank = Resolver(Profile(autonomous=True), service.db, service.config)
    assert blank.local(SALARY) is None
    stated = Resolver(
        Profile(autonomous=True, answer_rules={"salary_stance": "Flexible"}), service.db, service.config
    )
    assert stated.local(SALARY).value == "Flexible"
    # A saved salary expectation always wins over a stance.
    saved = Resolver(
        Profile(autonomous=True, salary_expectation="100000", answer_rules={"salary_stance": "Flexible"}),
        service.db,
        service.config,
    )
    assert not saved.local(SALARY) or saved.local(SALARY).value != "Flexible"


def test_salary_prompt_follows_the_stance(service):
    stated = Resolver(
        Profile(autonomous=True, answer_rules={"salary_stance": "Flexible"}), service.db, service.config
    )
    assert "answer Flexible" in stated.salary_prompt()
    blank = Resolver(Profile(autonomous=True), service.db, service.config)
    assert "never invent" in blank.salary_prompt() and "Negotiable" not in blank.salary_prompt()
