"""Owner 2026-10-25: "only few of them are truly legit, everything else is just chaos, incomplete fields with incomplete
questions and wrong questions". The owner's open questions included fields the engine misread (a bare "Month", a
"From Date" without its employment row, a site's "Search for:" box, a list read as "No matches", an upload the page
showed as done), questions the profile answers, and roles the applicant cannot hold. Questions were also frozen when
asked: ones created before a rule existed waited for days. Only questions the owner can truly answer are asked."""

import pytest

from jobpilot import owner_questions as oq
from jobpilot.answers import Resolver, protected_status_missing
from jobpilot.db import Review, Run
from jobpilot.schemas import Profile

from test_owner_questions_20261023 import add_job, add_review

PROTECTED = [
    "A United States citizen or national",
    'A person lawfully admitted for permanent residence of the United States (i.e., "Green Card" holder)',
    "A person admitted as a refugee to the United States under 8 U.S.C. 1157",
    "A person admitted as an asylee to the United States under 8 U.S.C 1158",
]
PROTECTED_Q = "Are you any of the following protected individual(s) as defined in 8 U.S.C. 1324b(a)(3)?"
NOT_PROTECTED = {"us_citizen": False, "has_asylee_status": False, "current_immigration_status_US": "OPT"}


@pytest.mark.parametrize(
    "question,options,context,reason",
    [
        ("Month", [], {"type": "combobox"}, ""),
        ("From Date", [], {}, ""),
        ("* Division", [], {"type": "text"}, ""),
        ("Dates Employed:", [], {}, ""),
        ("Search for:", [], {}, ""),
        ("What kind of work environment are you open to?", ["No matches"], {"type": "combobox"}, ""),
        ("Please upload a cover letter. * Drop files here or cv.pdf successfully uploaded", [], {}, ""),
        ("End Date Month", ["Jan", "Feb"], {}, "Current role has no end date"),
        ("Please indicate all entities for which you have worked.", ["Acme Bank of Boston", "Acme Bank of Dallas"], {},
         ""),
    ],
)
def test_misread_fields_are_never_owner_questions(question, options, context, reason):
    assert oq.defect(question, options, context, reason)


@pytest.mark.parametrize(
    "question,options,context",
    [
        ("City of Birth", [], {}),
        ("Reference First Name", [], {}),
        ("End Date", [], {"section": "Work history: Globex · Analyst"}),
        ("Month", [], {"history_identity": {"company": "Globex"}}),
        ("Gender", ["Male", "Female"], {}),
        ("Highschool Name & Location:", [], {}),
    ],
)
def test_real_questions_are_still_asked(question, options, context):
    assert oq.defect(question, options, context) is None


def test_a_role_open_only_to_protected_individuals_is_closed_for_an_applicant_without_that_status():
    assert protected_status_missing(PROTECTED_Q, PROTECTED, NOT_PROTECTED)
    assert not protected_status_missing(PROTECTED_Q, [*PROTECTED, "None of the above"], NOT_PROTECTED)
    assert not protected_status_missing(PROTECTED_Q, PROTECTED, {"us_citizen": True})
    assert not protected_status_missing(PROTECTED_Q, PROTECTED, {})  # Unknown status is asked, never assumed.


def test_an_agency_question_is_answered_from_the_direct_application(service):
    profile = Profile(first_name="Alex", application_source="LinkedIn")
    resolver = Resolver(profile, service.db, service.config, job={"ats": "workday", "url": "https://example.test/j"})
    listed = resolver.local({"id": "l", "label": "Please list recruiter/agency you've spoken with and for which opportunity.",
                             "type": "textarea", "options": [], "required": True})
    assert listed.value == "N/A"
    asked = resolver.local({"id": "a", "type": "radio", "required": True, "options": ["Yes", "No"],
                            "label": "Have you spoken to any recruiters/agencies that have presented opportunities?"})
    assert asked.value == "No"
    without_policy = Resolver(Profile(first_name="Alex"), service.db, service.config,
                              job={"ats": "workday", "url": "https://example.test/j"})
    assert without_policy.local({"id": "l", "label": "Please list recruiter/agency you've spoken with.",
                                 "type": "textarea", "options": [], "required": True}) is None


async def test_open_questions_are_revalidated_against_the_current_engine(service):
    profile = Profile(first_name="Alex", application_source="LinkedIn", facts=NOT_PROTECTED).model_dump()
    jobs = {name: add_job(service, name) for name in ("misread", "restricted", "inferable", "legit")}
    with service.db.exclusive() as s:
        for run_id in jobs.values():
            s.get(Run, run_id).packet = {"profile": profile}
    add_review(service, "q-misread", jobs["misread"], "From Date")
    add_review(service, "q-restricted", jobs["restricted"], PROTECTED_Q, PROTECTED)
    add_review(service, "q-inferable", jobs["inferable"], "Please list recruiter/agency you've spoken with.",
               context={"type": "textarea"})
    add_review(service, "q-legit", jobs["legit"], "City of Birth")
    outcome = await service.revalidate_open_questions()
    assert outcome == {"closed": 1, "recovered": 1, "answered": 1}
    with service.db.session() as s:
        assert s.get(Run, jobs["misread"]).state == "waiting_browser"
        assert s.get(Run, jobs["restricted"]).state == "closed"
        assert s.get(Review, "q-inferable").answer == "N/A"
        assert s.get(Review, "q-legit").answer is None and s.get(Run, jobs["legit"]).state == "waiting_answer"


@pytest.mark.parametrize(
    "item,kind",
    [
        ({"question": "Please upload a cover letter. cv.pdf successfully uploaded", "reason": "Required document upload",
          "blocker_type": "missing_fact"}, "control_failure"),
        ({"question": "Country", "reason": "The answer is not in the saved profile", "blocker_type": "missing_fact"},
         "control_failure"),
        ({"question": "End Date Month", "options": ["Jan"], "reason": "Current role has no end date",
          "blocker_type": "missing_fact", "scope": {"group": "Work Experience 1"}}, "control_failure"),
        ({"question": "City of Birth", "reason": "Not recorded", "blocker_type": "missing_fact"}, "missing_fact"),
        ({"question": "Country", "reason": "x", "blocker_type": "missing_fact", "scope": {"group": "Citizenship"}},
         "missing_fact"),
        ({"question": "Month", "reason": "x", "blocker_type": "account"}, "account"),
    ],
)
def test_the_engines_own_missing_fact_questions_pass_the_gate(item, kind):
    """The engine tags its pending questions missing_fact; the gate ran only for untagged ones, so misreads from a run
    (an upload the page shows as done, a bare Country) still reached the owner (2026-10-25)."""
    from jobpilot.service.reviews import ReviewsMixin

    assert ReviewsMixin.review_kind(item) == kind
