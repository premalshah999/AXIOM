"""Work history rows on iCIMS (Atlassian, 2026-10-19) carry the employer's Address, City, State, Country and Zip. iCIMS's
résumé parser put the company name into "Address" and the applicant's home zip into "Zip", and the resolver agreed:
the row's "Company…" pattern answered the address, and the home contact facts answered Zip/State/Country. The profile
records no employer street address, zip or phone: those stay blank (cleared if parsed), and a row's State/Country come
only from that row's own location."""

from jobpilot.answers import HISTORY_UNKNOWN, Resolver, history_region
from jobpilot.schemas import Address, Profile, WorkEntry


def profile():
    return Profile(
        name="Alex Example",
        address=Address(line1="1 Home St", city="Springfield", state="Maryland", postal_code="00000",
                        country="United States"),
        work=[
            WorkEntry(company="Robert H. Smith School of Business", title="AI Engineer", location="College Park, MD",
                      start="2025-12", current=True),
            WorkEntry(company="Example AI", title="Data Science Intern", location="Ahmedabad, India",
                      start="2023-08", end="2024-06"),
        ],
    )


def row(label, n, kind="text", options=(), required=False):
    return {"id": f"{label}-{n}", "label": label, "type": kind, "required": required, "options": list(options),
            "value": "", "valid": False, "group": f"Work Experience {n}", "section": "Professional Experience"}


def local(service, f):
    return Resolver(profile(), service.db, service.config).local(f)


def test_employer_address_zip_and_phone_stay_blank(service):
    for label in ("Address", "Company Address", "Zip", "Zip Code", "Postal Code", "Phone"):
        answer = local(service, row(label, 2))
        assert answer.disposition == "skip" and HISTORY_UNKNOWN in answer.evidence_ids, label
    assert local(service, row("Zip", 2, required=True)).disposition == "review"


def test_a_rows_region_comes_from_its_own_location(service):
    states = ["Maryland", "Virginia"]
    countries = ["United States", "India"]
    assert local(service, row("State", 1, "combobox", states)).value == "Maryland"
    assert local(service, row("Country", 1, "combobox", countries)).value == "United States"
    assert local(service, row("Country", 2, "combobox", countries)).value == "India"
    # Ahmedabad's state is not recorded: never the home state, never a guess.
    assert local(service, row("State", 2, "combobox", states)).disposition == "skip"
    # A row the profile does not have never borrows the home address.
    assert local(service, row("State", 5, "combobox", states)).disposition == "skip"


def test_a_city_box_gets_the_city(service):
    assert local(service, row("City", 2)).value == "Ahmedabad"


def test_region_parsing():
    assert history_region("College Park, MD", False) == "Maryland"
    assert history_region("College Park, Maryland", True) == "United States"
    assert history_region("Ahmedabad, Gujarat, India", False) == "Gujarat"
    assert history_region("Ahmedabad, India", False) == ""
    assert history_region("Remote", True) == ""
