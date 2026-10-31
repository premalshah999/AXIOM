"""Answers the recorded history decides: current enrolment from the education rows, residence over willingness."""
import pytest

from jobpilot.answers import Resolver
from jobpilot.schemas import Answer, Profile

JOB = {"company": "Acme", "url": "https://acme.wd5.myworkdayjobs.com/job/42"}


def field(label, options=("Yes", "No"), kind="dropdown"):
    return {"id": "q", "label": label, "type": kind, "required": True, "options": list(options), "value": "", "valid": True}


def resolver(service, **profile):
    return Resolver(Profile(**profile), service.db, service.config, employer="Acme", job=JOB)


@pytest.mark.parametrize("label,expected", [
    ("Are you currently enrolled in a degree seeking program?", "No"),
    ("Are you currently enrolled in an accredited degree program?", "No"),
    ("Are you currently pursuing a Master's degree?", "No"),
    ("Are you in school?", "No"),
    # Also asks about a completed degree, a plan or a different subject: not decided by "enrolled now".
    ("Are you currently enrolled OR received a Bachelor's degree?", None),
    ("Are you currently enrolled in a program that will be completed by June 2027?", None),
    ("Are you currently enrolled in a master's program with anticipated graduation in 2028?", None),
])
def test_current_enrolment_follows_the_education_rows(service, label, expected):
    education = [{"school": "Fixture University", "degree": "MS", "start": "2022-08", "end": "2024-05"}]
    answer = resolver(service, education=education).local(field(label))
    assert (answer.value if answer is not None and answer.disposition == "answer" else None) == expected


def test_an_unfinished_degree_is_not_answered_no(service):
    education = [{"school": "Fixture University", "degree": "MS", "start": "2025-08", "end": "2099-05"}]
    answer = resolver(service, education=education).local(field("Are you currently enrolled in a degree seeking program?"))
    assert answer is None or answer.value != "No"


@pytest.mark.parametrize("label,evidence,misused", [
    ("Are you currently within commuting distance to our Boise, ID office (within 50 miles)? Please answer based on "
     "where you currently reside, not your willingness to relocate.", ["fact:willing_to_relocate"], True),
    ("Do you currently meet the worksite location requirements for being On-site or Hybrid?",
     ["fact:willing_to_relocate", "fact:relocation_locations"], True),
    # Located facts, or an offered way out, make willingness relevant.
    ("Are you currently within commuting distance to our Boise, ID office?",
     ["fact:willing_to_relocate", "fact:city", "fact:state"], False),
    ("Do you currently live within commuting distance of Boise, or are you willing to relocate?",
     ["fact:willing_to_relocate"], True and False),
    ("Are you willing to relocate?", ["fact:willing_to_relocate"], False),
])
def test_relocation_willingness_does_not_answer_where_the_applicant_lives_now(service, label, evidence, misused):
    r = resolver(service)
    answer = Answer(field_id="q", disposition="answer", value="Yes", evidence_ids=evidence)
    assert r.willingness_misused(field(label), answer) is misused
