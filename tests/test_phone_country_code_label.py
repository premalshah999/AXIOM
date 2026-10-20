"""Boehringer (SuccessFactors) 2026-10-16: "Main Phone (+ Country Code)" refused the national number."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile


def phone(service, profile, label="Main Phone (+ Country Code)"):
    resolver = Resolver(profile, service.db, service.config, job={"ats": "successfactors", "url": "https://example.test/job"})
    field = {"id": "p", "label": label, "type": "text", "required": True, "value": "", "valid": True,
             "group": "", "section": "", "options": []}
    return resolver.local(field)


def test_phone_asking_for_the_country_code_gets_the_saved_calling_code(service):
    answer = phone(service, Profile(phone="(240) 555-0100", phone_country_code="+1"))
    assert answer.value == "+1 2405550100" and answer.evidence_ids == ["profile:phone", "profile:phone_country_code"]


def test_international_number_and_us_residence_need_no_separate_code(service):
    profile = Profile(phone="+1 240-555-0100", address={"country": "United States"})
    assert phone(service, profile).value == "+1 2405550100"


def test_no_saved_code_outside_the_us_is_never_guessed(service):
    answer = phone(service, Profile(phone="2405550100", address={"country": "India"}))
    assert answer is None or not str(answer.value or "").startswith("+")
