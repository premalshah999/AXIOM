"""Answers the recorded history decides: prior employment (contractor, intern, agency wordings), enrolment, residence."""
import pytest

from jobpilot.answers import Resolver
from jobpilot.answers.text import FROM_RECORD
from jobpilot.features.knowledge import attest
from jobpilot.features.knowledge.attestations import EMPLOYMENT_HISTORY
from jobpilot.schemas import Answer, Profile

JOB = {"company": "Acme", "url": "https://acme.wd5.myworkdayjobs.com/job/42"}
NO_FACTS = {"previously_employed_here": False, "previously_employed_at_applying_company": False}

# Contractor, contingent, third-party and intern wordings seen on confirmed applications (2026-10-14..27), with the
# employer renamed. Each was answered Yes from the complete-history attestation.
WORDINGS = [
    "Have you previously or are you currently working as a contractor for Acme?",
    "Are you currently, or have you been a contractor with Acme in the past 12 months?",
    "Are you currently working, or have you worked, as third party labor or an independent contractor to Acme?",
    "Are you currently or have you ever been a contractor or temporary worker with Acme? If yes, enter your dates below",
    "Have you ever been a Contingent Worker at Acme or one of the Initech family of companies? This is someone not on payroll.",
    "Were you previously a contractor (contingent worker) with Acme?",
    "Are you currently or have you previously been a Contractor with Acme before?",
    "Please answer yes if you are currently or have ever been a contractor for The Acme Group or any subsidiaries "
    "including but not limited to the following: Acme, Initech.",
    "Have you ever been a contractor of Acme's affiliated companies or subsidiaries? (Acme Service Company)",
    "Did you work previously at Acme?",
    "Have you previously completed an internship at Acme?",
    "Are you currently contracting or previously contracted with Acme or any of its subsidiaries?",
    "Have you ever provided any contract work for Acme, Inc. or any of its subsidiaries or affiliates?",
    "Have you worked for Acme before?",
]


def field(label, options=("Yes", "No"), kind="dropdown"):
    return {"id": "q", "label": label, "type": kind, "required": True, "options": list(options), "value": "", "valid": True}


def resolver(service, *, attested=False, facts=None, work=None, **profile):
    if attested:
        attest(service.db, "employment_history_complete", EMPLOYMENT_HISTORY["statement"])
    return Resolver(
        Profile(facts=facts or {}, work=work or [{"company": "Globex", "title": "Developer"}], **profile),
        service.db, service.config, employer="Acme", job=JOB,
    )


@pytest.mark.parametrize("label", WORDINGS)
@pytest.mark.parametrize("how", ["attested", "fact"])
def test_worked_here_before_is_no_when_the_employer_is_not_in_the_history(service, label, how):
    r = resolver(service, attested=how == "attested", facts=NO_FACTS if how == "fact" else None)
    answer = r.local(field(label))
    assert answer is not None and answer.disposition == "answer" and answer.value == "No"


@pytest.mark.parametrize("label", WORDINGS)
def test_a_work_row_naming_the_employer_withholds_the_no(service, label):
    r = resolver(service, attested=True, facts=NO_FACTS, work=[{"company": "Acme Corp", "title": "Contractor"}])
    answer = r.local(field(label))
    assert answer is None or answer.value != "No"


def test_the_history_decides_menu_answers_over_a_stored_one(service):
    # forms.py keeps a menu's earlier value unless the new answer says it came from the record.
    r = resolver(service, attested=True)
    assert FROM_RECORD in r.local(field(WORDINGS[0])).reason


def test_yes_needs_a_work_row_that_names_the_employer(service):
    r = resolver(service, attested=True, work=[{"company": "Acme Corp", "title": "Contractor"}, {"company": "Globex"}])
    f = field("Did you work previously at Acme?")

    def yes(*evidence):
        return Answer(field_id="q", disposition="answer", value="Yes", evidence_ids=list(evidence))

    # The attestation as a packaged fact, with every row cited, still cannot back a Yes about another employer's row.
    assert r.prior_yes_unsupported(f, yes("fact:employment_history_complete", "profile:work.1"))
    assert r.prior_yes_unsupported(f, yes("fact:employment_history_complete"))
    assert not r.prior_yes_unsupported(f, yes("profile:work.0"))
    assert not r.prior_yes_unsupported(f, Answer(field_id="q", disposition="answer", value="No", evidence_ids=[]))
    assert not r.prior_yes_unsupported(field("Do you use Duolingo?"), yes("profile:work.1"))
