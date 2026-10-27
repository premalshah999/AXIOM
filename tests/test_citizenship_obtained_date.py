"""Westinghouse's SuccessFactors citizenship table (2026-10-27) repeats "Country N Name", "Type of Status N" and "Date
Obtained N". "Date Obtained 1" went to the owner three times although the applicant's citizenship was acquired at birth
and the profile holds the date of birth."""

from jobpilot.answers import Resolver
from jobpilot.forms import label_status_dates
from jobpilot.schemas import Profile

FACTS = {"citizenship_acquired": "At birth", "country_of_citizenship": "Freedonia", "date_of_birth": "01/02/1990",
         "us_citizen": False}


def table(rows=3):
    fields = []
    for i in range(1, rows + 1):
        fields += [
            {"id": f"c{i}", "label": f"Country {i} Name", "type": "combobox"},
            {"id": f"s{i}", "label": f"Type of Status {i}", "type": "combobox"},
            {"id": f"d{i}", "label": f"Date Obtained {i}", "type": "text", "widget": "date-text", "date_format": "mdy"},
        ]
    for f in fields:
        f.update({"required": f["id"].endswith("1"), "options": [], "value": "", "valid": True, "group": "",
                  "section": ""})
    return fields


def test_a_status_table_date_is_labelled_as_the_status_date_and_a_certificate_date_is_not():
    fields = table()
    certificate = {"id": "x", "label": "Date Obtained 1", "type": "text"}
    label_status_dates(fields)
    assert {f["id"]: f["label"] for f in fields}["d1"] == "Date Obtained 1 (citizenship or residency status)"
    label_status_dates([certificate, {"id": "n", "label": "Certification Name 1", "type": "text"}])
    assert certificate["label"] == "Date Obtained 1"


def resolver(service, facts):
    return Resolver(Profile(first_name="Alex", facts=facts), service.db, service.config,
                    job={"ats": "successfactors", "url": "https://example.test/j"})


def test_citizenship_since_birth_was_obtained_on_the_date_of_birth(service):
    fields = {f["id"]: f for f in table()}
    label_status_dates(fields.values())
    answer = resolver(service, FACTS).local(fields["d1"])
    assert answer is not None and answer.disposition == "answer"
    assert answer.value == "01/02/1990"
    assert answer.evidence_ids == ["fact:citizenship_acquired", "fact:date_of_birth"]
    # A second row is another status; an applicant with permanent residency elsewhere is not decided by birth alone.
    assert getattr(resolver(service, FACTS).local(fields["d2"]), "value", "") != "01/02/1990"
    resident = resolver(service, {**FACTS, "permanent_resident": True}).local(fields["d1"])
    assert getattr(resident, "value", "") != "01/02/1990"
    naturalized = resolver(service, {**FACTS, "citizenship_acquired": "Naturalization"}).local(fields["d1"])
    assert getattr(naturalized, "value", "") != "01/02/1990"
