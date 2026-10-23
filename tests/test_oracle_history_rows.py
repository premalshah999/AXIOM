"""Oracle's Work and Education History (2026-10-23): "Employer Country"/"Employer City" took the employer's name, an
education row captioned "Education at University of Maryland" was not a history row, and an uncaptioned education
block's "Start Date Month" took the availability answer "Immediately". Each section was refused."""

from jobpilot.answers import Resolver
from jobpilot.forms import oracle_education_block
from jobpilot.schemas import EducationEntry, Profile, WorkEntry

PROFILE = Profile(
    work=[WorkEntry(company="Acme Labs", title="AI Engineer", location="College Park, MD", start="2024-08", end="")],
    education=[EducationEntry(school="University of Maryland", degree="Master of Science", field_of_study="Data Science",
                              start="2023-08", end="2025-05")],
    availability="Immediately",
    facts={"availability": "Immediately"},
)
MONTHS = ["", *[f"{m:02d}" for m in range(1, 13)]]


def ask(service, label, group="", **extra):
    return Resolver(PROFILE, service.db, service.config).local(
        {"id": "x", "label": label, "type": extra.pop("type", "text"), "group": group, "required": True,
         "options": extra.pop("options", []), **extra})


def test_employer_city_and_country_are_the_rows_location(service):
    city = ask(service, "Employer City", "Work Experience 1")
    assert city is not None and city.value == "College Park"
    country = ask(service, "Employer Country", "Work Experience 1", type="select",
                  options=["Canada", "United States", "Mexico"])
    assert country is None or country.value != "Acme Labs"


def test_a_row_named_by_its_school_is_that_education_entry(service):
    month = ask(service, "Start Date Month", "Education at University of Maryland", type="select", options=MONTHS,
                date_parts=["Month", "Year"])
    assert month is not None and month.value == "08"


def test_availability_never_fills_a_history_date_part(service):
    answer = ask(service, "Start Date Month", "", type="select", options=MONTHS)
    assert answer is None or "Immediately" not in str(answer.value)


def test_one_uncaptioned_oracle_education_block_is_education_1():
    frame = "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/1/apply/section/3"
    fields = [{"label": label, "frame_url": frame, "group": ""} for label in
              ("School Name", "Degree", "Start Date Month", "Start Date Year")]
    fields.append({"label": "Start Date Month", "frame_url": frame, "group": "Work Experience 1"})
    oracle_education_block(fields)
    assert [f["group"] for f in fields] == ["Education 1"] * 4 + ["Work Experience 1"]
