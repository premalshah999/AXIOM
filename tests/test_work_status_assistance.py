"""John Deere (Eightfold) 2026-10-16: a work-status assistance question is the sponsorship question."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

FACTS = {"work_authorization_US": "Yes, authorized to work for any employer in the USA (applicant confirmed)",
         "requires_future_sponsorship_US": "False"}
ASSIST = ("Do you now, or will you in the future, require assistance in obtaining work status in the United States (e.g. H-1B "
          "(new or change of employer), TN, Permanent Residency, etc.)? (If you are authorized to work under the DACA program, "
          "you should answer “No”.)")
ELIGIBLE = ("Are you legally eligible to work for John Deere and other employers in the United States on a regular or full-time "
            "basis? (You are legally eligible if you are a U.S. citizen or if you have a valid work authorization.)")


def ask(service, label):
    r = Resolver(Profile(facts=FACTS), service.db, service.config, job={"ats": "eightfold", "url": "https://example.test/job/1"})
    f = {"id": "x", "label": label, "type": "radiogroup", "required": True, "value": "", "valid": True,
         "group": "", "section": "", "options": ["Yes", "No"]}
    return r.local(f)


def test_work_status_assistance_uses_the_sponsorship_fact(service):
    a = ask(service, ASSIST)
    assert a.value == "No" and a.evidence_ids == ["fact:requires_future_sponsorship_us"]


def test_eligibility_still_uses_the_authorization_fact(service):
    assert ask(service, ELIGIBLE).value == "Yes"
