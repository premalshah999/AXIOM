"""Liberty Mutual (iCIMS) 2026-10-21: the whole candidate profile is one group, so each field's "context" (the group's
other values) carried "Phone Country Code: (+1) United States". The resolver read that as the question's scope, and
Email, City and Address were answered with the address country."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Address, Profile

NEIGHBOURS = {"First Name": "Alex", "Last Name": "Doe", "Type": "Mobile", "Phone Country Code": "(+1) United States",
              "Number": "(555) 555-0100", "Country": "United States"}


def resolver(service):
    profile = Profile(name="Alex Doe", first_name="Alex", last_name="Doe", email="applicant@example.com",
                      phone="5555550100", address=Address(line1="100 Main Street", city="Springfield", state="Maryland",
                                                          postal_code="00000", country="United States of America"))
    return Resolver(profile, service.db, service.config,
                    job={"id": "j", "ats": "icims", "company": "Acme",
                         "url": "https://careers-acme.icims.com/jobs/1/engineer/job"})


def field(fid, label, typ, section=""):
    return {"id": fid, "label": label, "type": typ, "required": True, "options": [], "value": "", "valid": True,
            "section": section, "group": "Enter your information", "context": dict(NEIGHBOURS)}


def test_neighbouring_values_do_not_turn_email_city_or_address_into_the_country(service):
    r = resolver(service)
    fields = [field("e", "Email", "email"), field("a", "Address", "text", "Addresses (1) * required."),
              field("c", "City", "text", "Addresses (1) * required.")]
    expected = {"e": "applicant@example.com", "a": "100 Main Street", "c": "Springfield"}
    for f in fields:
        r.question_context(f)
        assert r.context_labels[f["id"]] == f["label"]
        assert r.local({**f, "label": r.context_labels[f["id"]]}).value == expected[f["id"]]


def test_a_text_context_still_scopes_a_label(service):
    r = resolver(service)
    f = {**field("n", "Country", "text"), "context": "Country of citizenship"}
    r.question_context(f)
    assert "citizenship" in r.context_labels["n"]
