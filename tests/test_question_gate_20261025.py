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
    search = resolver.local({"id": "s", "type": "radio", "required": True, "options": ["Yes", "No"],
                             "label": "Has a search agency submitted your application for consideration within the "
                                      "last 12 months?"})
    assert search.value == "No"  # Vanguard 2026-10-27
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
    assert outcome == {"closed": 1, "recovered": 1, "answered": 1, "merged": 0}
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


def test_a_consent_for_employment_purposes_is_dated_today(service):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    label = ("In connection with my application for employment, I authorize the Company to obtain a Consumer Report "
             "for employment purposes. I understand that the Consumer Report may contain information about my "
             "background.")
    resolver = Resolver(Profile(first_name="Alex", accept_all_application_terms=True), service.db, service.config,
                        job={"ats": "workday", "url": "https://example.test/j"})
    answer = resolver.local({"id": "d", "label": label, "type": "date_parts", "options": [], "required": True})
    today = datetime.now(ZoneInfo(service.config.timezone)).date()
    assert answer is not None and answer.value == f"{today.month:02d}/{today.day:02d}/{today.year}"
    dated = resolver.local({"id": "s", "label": "Employment start date", "type": "date_parts", "options": [],
                            "required": True})
    assert dated is None or dated.evidence_ids != ["policy:today"]


def test_two_spellings_of_one_date_are_one_answer():
    from jobpilot.answers.validate import same_date

    assert same_date("10/25/2026", "2026-10-25") and not same_date("10/25/2026", "2026-10-24")


def test_an_end_date_the_page_demands_for_the_current_role_is_never_an_owner_question(service):
    """Dallas County's Oracle row kept asking "End Date Month" for the current job after "Current Job" was answered;
    the navigator sent it to the owner, who has no end date to give (2026-10-25). Since 2026-10-26 a required end date
    on the current role is today's date ("ongoing") on every platform, as on SuccessFactors and iCIMS."""
    from datetime import date

    from jobpilot.browser import current_role_end_date
    from jobpilot.schemas import WorkEntry

    profile = Profile(first_name="Alex", work=[WorkEntry(company="Globex", title="Analyst", start="2025-12", current=True),
                                               WorkEntry(company="Initech", title="Intern", start="2024-06", end="2024-08")])
    resolver = Resolver(profile, service.db, service.config, job={"ats": "oracle", "url": "https://example.test/j"})
    months = [date(2026, m, 1).strftime("%B") for m in range(1, 13)]
    current = {"id": "e1", "label": "End Date Month", "type": "combobox", "required": True, "options": months,
               "group": "Work Experience 1"}
    past = {**current, "id": "e2", "group": "Work Experience 2"}
    answer = resolver.local(current)
    assert answer.disposition == "answer" and answer.value == date.today().strftime("%B")
    assert not current_role_end_date(resolver, current)
    assert not current_role_end_date(resolver, past)


async def test_identifier_and_foreign_posting_questions_close_and_field_ids_reopen(service):
    # 2026-10-27: Lowe's asked for the last 4 digits of an SSN, NerdWallet for a Canadian province and Rippling showed
    # "field-44" as a question; none is the owner's to answer.
    jobs = {name: add_job(service, name) for name in ("ssn", "canada", "fieldid")}
    with service.db.exclusive() as s:
        for run_id in jobs.values():
            s.get(Run, run_id).packet = {"profile": {"first_name": "Alex"}}
    add_review(service, "q-ssn", jobs["ssn"], "What are the last 4 digits of your Social Security Number (SSN)?")
    add_review(service, "q-canada", jobs["canada"], "What province in Canada will you be working from?")
    add_review(service, "q-field", jobs["fieldid"], "field-44")
    await service.revalidate_open_questions()
    with service.db.session() as s:
        assert s.get(Run, jobs["ssn"]).state == "closed" and "dropped" in s.get(Run, jobs["ssn"]).reason
        assert s.get(Run, jobs["canada"]).state == "closed"
        assert s.get(Run, jobs["fieldid"]).state == "waiting_browser"


async def test_open_questions_of_a_german_posting_close(service):
    # Sopra Steria (SmartRecruiters, "Bundesweit, DE") asked "Deutschkenntnisse" (2026-10-27).
    from jobpilot.db import Job

    job = add_job(service, "german")
    with service.db.exclusive() as s:
        s.get(Run, job).packet = {"profile": {"first_name": "Alex"}}
        s.get(Job, "german").title = "AI Backend Developer - m/w/d"
    add_review(service, "q-german", job, "Deutschkenntnisse")
    await service.revalidate_open_questions()
    with service.db.session() as s:
        run = s.get(Run, job)
        assert run.state == "closed" and "another country" in run.reason
