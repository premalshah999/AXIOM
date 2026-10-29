"""Owner 2026-10-29: employer-address fields were filled with the applicant's own address. A numbered history row was
already guarded (test_history_row_contact); a single "Current Employer" block, a "Supervisor" section or a "Reference"
group was not, so its bare Address / Zip / Phone took the applicant's own contact facts."""

import asyncio

from jobpilot.answers import HISTORY_UNKNOWN, Resolver
from jobpilot.answers.third_party import third_party
from jobpilot.schemas import Address, Profile, WorkEntry


def profile():
    return Profile(
        name="Alex Example",
        phone="+1 555 010 0000",
        address=Address(line1="1 Home St", city="Springfield", state="Ohio", postal_code="00000",
                        country="United States"),
        work=[WorkEntry(company="ExampleSoft", title="Engineer", location="Ann Arbor, MI", start="2024-01", current=True)],
    )


def field(label, section="", group="", required=True, kind="text", options=()):
    return {"id": label, "label": label, "type": kind, "required": required, "options": list(options), "value": "",
            "valid": False, "group": group, "section": section}


def test_a_third_partys_contact_detail_is_never_the_applicants(service):
    r = Resolver(profile(), service.db, service.config)
    for args in [("Employer Address",), ("Address", "Employer Information"), ("Zip Code", "Previous Employer"),
                 ("Phone", "Supervisor"), ("Supervisor Phone",), ("Company Address",), ("Employer's Address",),
                 ("Address", "Reference 1"), ("Phone Number", "Reference"), ("Employer Zip",), ("Company City",),
                 ("Postal Code", "Emergency Contact")]:
        answer = r.local(field(*args))
        assert answer.disposition == "review" and HISTORY_UNKNOWN in answer.evidence_ids, args
        assert answer.value is None, args
    # An optional one is simply left blank.
    assert r.local(field("Employer Phone", required=False)).disposition == "skip"


def test_the_batch_resolver_applies_the_guard_before_retrieval_and_the_model(service):
    r = Resolver(profile(), service.db, service.config)
    answers = asyncio.run(r._resolve([field("Zip Code", "Previous Employer"), field("Zip Code", "Contact")]))
    by_id = {a.field_id: a for a in answers}
    assert by_id["Zip Code"].disposition in {"review", "answer"}
    zip_fields = [field("Zip Code", "Previous Employer")]
    zip_fields[0]["id"] = "employer-zip"
    assert asyncio.run(r._resolve(zip_fields))[0].disposition == "review"


def test_the_current_employers_location_comes_from_its_work_row(service):
    r = Resolver(profile(), service.db, service.config)
    assert r.local(field("City", "Current Employer")).value == "Ann Arbor"
    state = r.local(field("State", "Current Employer", kind="combobox", options=["Ohio", "Michigan"]))
    assert state.value == "Michigan" and state.evidence_ids == ["profile:work.0.location"]
    # A previous employer is no recorded row: nothing is guessed.
    assert r.local(field("City", "Previous Employer")).disposition == "review"


def test_the_applicants_own_contact_fields_are_untouched(service):
    r = Resolver(profile(), service.db, service.config)
    assert r.local(field("Zip Code", "Contact Information")).value == "00000"
    assert r.local(field("Address", "Personal Information")).value == "1 Home St"
    assert third_party(field("Your Phone Number", "Reference")) == ""
    assert third_party(field("Country", "Employment Eligibility")) == ""
    assert third_party(field("Country of residence")) == ""


def test_a_placed_history_row_keeps_its_own_rules(service):
    f = field("Zip", group="Work Experience 1", section="Professional Experience")
    assert third_party(f) == ""
    g = field("Zip", section="Work Experience")
    assert third_party(g) == "work experience"
    g["history_kind"] = "work"
    assert third_party(g) == ""


def test_the_answer_audit_never_treats_the_applicants_address_as_an_employers_fact(service):
    """Two applications were blocked on "Employer City" (confirmed_fact_mismatch): the audit's known answer was the
    applicant's own city, so a correct employer city counted as the mismatch."""
    from types import SimpleNamespace

    from jobpilot import answer_quality

    form = SimpleNamespace(resolver=Resolver(profile(), service.db, service.config))
    for label, section in (("Employer City", ""), ("City", "Previous Employer"), ("Zip Code", "Supervisor")):
        known = answer_quality.supported_local(form, field(label, section))
        assert known.disposition != "answer" and known.value is None, label


def test_a_long_question_that_mentions_a_state_or_an_organization_is_not_a_contact_field():
    question = ("To the best of your knowledge, are you a current or former employee of an organization that is a state, "
                "local or non-U.S. government client?")
    assert third_party(field(question)) == ""
