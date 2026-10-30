"""Apptronik (Greenhouse, 2026-10-30, nine runs): "a. If NO, please indicate your country of citizenship and, if
different, the country in which you have most recently obtained citizenship or permanent residency status. This
information will be used to determine whether it would be possible to obtain any required license from the U.S.
government." was classed as a government disclosure. The model proposed the saved country, the scope check refused it
on every run, and the repair agent stopped on "identical failure repeated". The saved citizenship facts state the
answer, so it resolves locally, with no model."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

QUESTION = (
    "a. If NO, please indicate your country of citizenship and, if different, the country in which you have most "
    "recently obtained citizenship or permanent residency status. This information will be used to determine whether "
    "it would be possible to obtain any required license from the U.S. government. "
    '(Enter in "N/A" if marked Yes above)'
)
FACTS = {"us_citizen": False, "country_of_citizenship": "Freedonia", "citizenship": "Freedonia"}


def field(label=QUESTION, **extra):
    return {"id": "q1", "label": label, "type": "text", "required": True, "options": [], "group": "", "section": "",
            **extra}


def local(service, facts, f=None):
    resolver = Resolver(Profile(first_name="Alex", facts=facts), service.db, service.config,
                        job={"ats": "greenhouse", "url": "https://example.test/j"})
    return resolver.local(f or field())


def test_the_conditional_government_worded_question_resolves_from_the_saved_country(service):
    answer = local(service, FACTS)
    assert answer is not None and answer.disposition == "answer"
    # The one country is the full answer when the profile holds no different one.
    assert answer.value == "Freedonia"
    assert answer.evidence_ids == ["fact:country_of_citizenship"]


def test_a_further_country_is_added_only_when_asked_and_held(service):
    assert local(service, {**FACTS, "second_citizenship": "Ruritania"}).value == "Freedonia; Ruritania"
    # Not asked for: the plain question names one country.
    assert local(service, {**FACTS, "second_citizenship": "Ruritania"}, field("Country of citizenship")).value == "Freedonia"
    # The same country twice is one country.
    assert local(service, {**FACTS, "second_citizenship": "freedonia"}).value == "Freedonia"
    # Permanent residency is not a citizenship; it joins only when the question asks for residency.
    resident = {**FACTS, "country_of_permanent_residence": "Ruritania"}
    assert local(service, resident).value == "Freedonia; Ruritania"
    assert local(service, resident, field("Please list all countries of citizenship")).value == "Freedonia"


def test_a_status_description_is_never_taken_for_a_country(service):
    assert getattr(local(service, {"citizenship": "US Citizen"}), "value", "") != "US Citizen"
    assert getattr(local(service, {"citizenship": "Permanent resident", "us_citizen": False}), "value", "") != (
        "Permanent resident"
    )


def test_a_us_citizen_follows_the_na_instruction_and_a_dropdown_takes_an_offered_country(service):
    answer = local(service, {**FACTS, "us_citizen": True, "country_of_citizenship": "United States"})
    assert answer is not None and answer.value == "N/A" and answer.evidence_ids == ["fact:us_citizen"]
    menu = field("Country of citizenship", type="select", options=["Select", "Ruritania", "Freedonia"])
    assert local(service, FACTS, menu).value == "Freedonia"
    # A country the page does not offer is not guessed.
    assert getattr(local(service, FACTS, field("Country of citizenship", type="select", options=["Ruritania"])),
                   "value", "") != "Freedonia"


def test_a_yes_no_question_about_citizenship_is_not_a_country_answer(service):
    yes_no = field("Are you a citizen of the country where this role is located?", options=["Yes", "No"])
    assert getattr(local(service, FACTS, yes_no), "value", "") != "Freedonia"
