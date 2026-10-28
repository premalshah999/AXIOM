"""Taleo (Bealls 2026-10-17): a resumed run reopened candidateacquisition/flow.jsf by URL and Taleo answered with its
"career section unavailable" page. JSF flow routes restart from the posting instead."""

from types import SimpleNamespace

from jobpilot.browser import BrowserEngine

JOB = "https://beallsinc.taleo.net/careersection/bealls+outlet+mobile/jobdetail.ftl?job=260000GI"


def route_for(url, ats="taleo"):
    engine = SimpleNamespace(
        job={"ats": ats, "url": JOB}, run_record={"packet": {}}, desktop_resume_state=lambda saved: {"url": url}
    )
    return BrowserEngine.desktop_resume_route(engine, {})


def test_taleo_flow_pages_are_not_reopened_by_url():
    assert route_for("https://beallsinc.taleo.net/careersection/careersection/candidateacquisition/flow.jsf") is None
    assert route_for("https://beallsinc.taleo.net/careersection/iam/accessmanagement/login.jsf") is None


def test_other_saved_routes_still_reopen():
    assert route_for(JOB) == JOB
    flow = "https://beallsinc.taleo.net/careersection/careersection/candidateacquisition/flow.jsf"
    assert route_for(flow, ats="custom") == flow


def test_a_parked_error_page_is_never_reopened():
    """Hearst (Oracle, 2026-10-27): the tab was parked on /errors/404 while the posting was live; reopened, the 404
    became the entry and "return to job" went back to it."""
    assert route_for("https://acme.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/errors/404", ats="oracle") is None
    assert route_for("https://jobs.example.test/apply/404", ats="custom") is None
    assert route_for("https://jobs.example.test/error", ats="custom") is None
    assert route_for("https://jobs.example.test/apply/errors-and-omissions-analyst", ats="custom") is not None


def test_a_smartrecruiters_one_click_step_is_never_reopened():
    """AbbVie, Versant Media, SBT Global (2026-10-26..28): reopened at /screening after tab cleanup, the one-click form
    had lost its applicant step and no Submit ever posted the application."""
    screening = "https://jobs.smartrecruiters.com/oneclick-ui/company/Acme/publication/0000-1111/screening?dcr_ci=Acme"
    assert route_for(screening, ats="smartrecruiters") is None
    posting = "https://jobs.smartrecruiters.com/Acme/744000000000001-data-engineer"
    assert route_for(posting, ats="smartrecruiters") == posting
