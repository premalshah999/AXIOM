"""Questions the owner said were easy to infer (2026-10-24). Each went to the owner although the profile answers it, or
the question itself shows the job cannot be applied for. The applicant is synthetic."""

from datetime import date

from jobpilot.answers import Resolver
from jobpilot.answers.eligibility import residence_outside
from jobpilot.features.knowledge.writing import question_kind
from jobpilot.schemas import Profile

EDUCATION = [
    {"school": "State University", "degree": "Master of Science", "field_of_study": "Data Science", "end": "2026-05"},
    {"school": "Institute of Technology", "degree": "BE", "field_of_study": "Computer Engineering", "end": "2022-06"},
]
WORK = [{"company": "Acme Labs", "title": "AI Engineer", "start": "2025-12", "current": True}]


def ask(service, label, options=(), kind="select", **profile):
    resolver = Resolver(Profile.model_validate({"education": EDUCATION, "work": WORK, **profile}), service.db,
                        service.config)
    return resolver.local({"id": "q", "label": label, "type": kind, "required": True, "value": "", "valid": True,
                           "group": "", "section": "", "options": list(options)})


FED = "Please indicate all entities for which you are currently working or have worked."
FED_OPTIONS = ["Board of Governors", "Federal Reserve Bank of New York", "Consumer Financial Protection Bureau (CFPB)",
               "None of the above"]


def test_no_listed_organization_in_the_work_history_is_the_none_choice(service):
    answer = ask(service, FED, FED_OPTIONS, kind="checkboxgroup")
    assert answer.value == "None of the above" and answer.evidence_ids == ["profile:work"]


def test_a_listed_organization_the_applicant_worked_for_is_never_answered_none(service):
    worked = [{"company": "Federal Reserve Bank of New York", "title": "Analyst", "start": "2023-01", "end": "2024-01"}]
    answer = ask(service, FED, FED_OPTIONS, kind="checkboxgroup", work=worked)
    assert answer is None or answer.value != "None of the above"


def test_credit_hours_for_a_completed_degree_are_all_of_them(service):
    answer = ask(service, "How many credit hours towards your degree do you anticipate having completed by the time you "
                 "would start this position?", kind="textarea")
    assert "All credit hours required for my Master of Science in Data Science" in answer.value
    assert "May 2026" in answer.value and "profile:education.0.end" in answer.evidence_ids


def test_a_degree_still_in_progress_is_left_to_the_applicant(service):
    future = f"{date.today().year + 1}-05"
    answer = ask(service, "How many credit hours do you anticipate having completed by your start date?",
                 kind="textarea", education=[{**EDUCATION[0], "end": future}])
    assert answer is None or "All credit hours" not in str(answer.value)


def test_an_internship_cannot_be_compulsory_for_completed_degrees(service):
    answer = ask(service, "Do you confirm that your internship is compulsory to validate your year and/or diploma ?",
                 ["Yes", "No"])
    assert answer.value == "No"


def test_a_province_in_another_country_closes_the_job_for_a_us_resident():
    facts = {"state_of_residence": "Maryland"}
    assert residence_outside("Which province in South Africa do you currently live in?", ["Gauteng"], facts) == "Maryland"
    assert residence_outside("What province in Canada will you be working from?", [], facts) == "Maryland"
    assert residence_outside("Which state in the United States do you live in?", ["Maryland", "Texas"], facts) is None


def test_a_project_story_is_written_not_asked():
    assert question_kind("Tell us more about a project where you took human demonstration data and trained a policy") \
        == "behavioral"
    assert question_kind("Describe a project you are proud of") == "behavioral"
