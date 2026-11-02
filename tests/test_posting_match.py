"""Which pages and receipts belong to a job. Each stop sits beside the closest legitimate case it must let through. Names are invented."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from jobpilot.admission import unknown_resubmit_allowed
from jobpilot.db import Job, Run
from jobpilot.posting_match import (
    PostingWatch,
    clearly_other_role,
    looks_like_listing,
    other_posting_address,
    posting_ids,
    receipt_conflict,
    role_in_receipt,
    role_heading,
    role_on_form,
    acknowledges_application,
    could_carry_application,
)

JOB = "https://careers.example.com/careers/JobDetail/New-York-Software-Engineer/61001"


@pytest.mark.parametrize(
    "url, numbers",
    [
        (JOB, {"61001"}),
        # A code that's the employer's own, in all its postings, isn't the posting's.
        ("https://eihu.fa.example.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/2617302", {"2617302"}),
        ("https://eihu.fa.example.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/2617302/apply/section/1", {"2617302"}),
        ("https://eihu.fa.example.com/hcmUI/CandidateExperience/en/sites/CX_1001/my-profile", set()),
        ("https://recruiting.example.com/ABC1000/JobBoard/3682c16a-648b-40c6-b3c2-3f99df29628f/OpportunityDetail?opportunityId=54b8d7c2-b726-4114-8f26-e8d8a5d96d2a", {"54b8d7c2b72641148f26e8d8a5d96d2a"}),
        ("https://apply.example.com/acme/j/ABC123DEF0/", {"abc123def0"}),
        ("https://careers.example.com/jobs/25997/security-engineer%2c-2027-graduate-u.s./job", {"25997"}),
        ("https://recruiting.example.com/recruiting/jobs/Apply/3701235/Acme/Software-Engineer", {"3701235"}),
        ("https://acme.wd5.myworkdayjobs.com/en-US/ext/job/Austin/Data-Scientist_R0076343-1", {"r0076343"}),
        ("https://acme.wd5.myworkdayjobs.com/en-US/ext/job/Austin/Analyst_10079702-WD-1/apply/applyManually", {"10079702wd"}),
        ("https://acme.wd5.myworkdayjobs.com/x/job/Austin/Backend-Level-4_Q326SWEB2/apply", {"q326sweb2"}),
        ("https://acme.breezy.hr/p/e59f8d41d06d01-senior-engineer-remote/apply", {"e59f8d41d06d01"}),
        ("https://jobs.example.com/acme/7440000-senior-engineer", {"7440000"}),
        ("https://acme.com/careers?gh_jid=5269938007", {"5269938007"}),
        ("https://jobs.example.com/acme/3682c16a-648b-40c6-b3c2-3f99df29628f/apply", {"3682c16a-648b-40c6-b3c2-3f99df29628f"}),
        ("https://acme.com/careers/Software-Engineer-New-Grad-2027", set()),  # A year ends many titles.
        ("https://acme.com/careers/software-engineer", set()),
        ("https://acme.com/apply?job=apply", set()),
    ],
)
def test_the_numbers_an_address_states(url, numbers):
    assert {n.replace("-", "") for n in posting_ids(url)} == {n.replace("-", "") for n in numbers}


@pytest.mark.parametrize(
    "own, url, expected",
    [
        (JOB, "https://careers.example.com/careers/JobDetail/New-York-Data-Analyst/62002", "path"),
        ("https://boards.example.io/acme/jobs/8296161", "https://boards.example.io/acme/jobs/8282426/confirmation", "path"),
        ("https://boards.example.io/acme/jobs/8296161", "https://job-boards.example.io/acme/jobs/8282426", "path"),
        ("https://acme.com/apply?jobId=61001", "https://acme.com/apply/step2?jobId=62002", "query"),
        ("https://acme.wd1.myworkdayjobs.com/x/job/NY/Analyst_R100", "https://acme.wd1.myworkdayjobs.com/x/job/NY/Engineer_R200", "path"),
        ("https://acme.com/careers/data-analyst", "https://acme.com/careers/senior-engineer", "slug"),
        # Another place beside the number; another posting under the employer's code, on a form step or not; another board row.
        ("https://acme.wd1.myworkdayjobs.com/x/job/Austin/Analyst_R100", "https://acme.wd1.myworkdayjobs.com/x/job/Remote/Engineer_R200", "path"),
        ("https://a.fa.example.com/hcm/sites/CX_1001/job/2617302", "https://a.fa.example.com/hcm/sites/CX_1001/job/2617999", "path"),
        ("https://a.fa.example.com/hcm/sites/CX_1001/job/2617302", "https://a.fa.example.com/hcm/sites/CX_1001/job/2617999/apply/section/1", "path"),
        ("https://u.example.com/ABC1000/JobBoard/3682c16a-648b-40c6-b3c2-3f99df29628f/OpportunityDetail?opportunityId=54b8d7c2-b726-4114-8f26-e8d8a5d96d2a",
         "https://u.example.com/ABC1000/JobBoard/3682c16a-648b-40c6-b3c2-3f99df29628f/OpportunityDetail?opportunityId=11b8d7c2-b726-4114-8f26-e8d8a5d96d2a", "query"),
        ("https://apply.example.com/acme/j/ABC123DEF0/", "https://apply.example.com/acme/j/XYZ789GHI1/apply", "path"),
        # The same posting on its way through, however the address reads.
        ("https://boards.example.io/acme/jobs/8296161", "https://boards.example.io/acme/jobs/8296161/confirmation", None),
        ("https://boards.example.io/acme/jobs/8296161", "https://job-boards.example.io/acme/jobs/8296161", None),
        ("https://boards.example.io/acme/jobs/8296161?gh_jid=8296161", "https://boards.example.io/acme/jobs/8296161", None),
        (JOB, "https://careers.example.com/careers/ApplicationForm?jobId=61001", None),
        (JOB, "https://careers.example.com/careers/Login?jobId=61001", None),
        ("https://acme.wd1.myworkdayjobs.com/x/job/NY/Analyst_R100", "https://acme.wd1.myworkdayjobs.com/x/job/NY/Analyst_R100/apply/applyManually", None),
        # A hand-off to another site numbers the posting its own way.
        ("https://acme.com/careers/job/123", "https://boards.example.io/acme/jobs/456", None),
        ("https://acme.com/careers?gh_jid=123", "https://boards.example.io/acme/jobs/456", None),
        # A form that adds a number the board address lacked, or numbers the posting its own way, isn't a difference of address.
        ("https://acme.com/careers/software-engineer", "https://acme.com/careers/ApplicationForm?jobId=62002", None),
        ("https://acme.com/job/Software-Engineer/900100200", "https://acme.com/talentcommunity/apply/45678", None),
        ("https://acme.com/job/900100200", "https://acme.com/apply/45678", None),
        ("https://acme.com/careers/JobDetail/61001", "https://acme.com/careers/ApplicationForm/45678", None),
        ("https://a.fa.example.com/hcm/sites/CX_1001/job/2617302", "https://a.fa.example.com/hcm/sites/CX_1002/job/2617302", None),
        ("https://a.fa.example.com/hcm/sites/CX_1001/job/2617302", "https://a.fa.example.com/hcm/sites/CX_1001/my-profile", None),
        # A page of the site beside a slug address.
        ("https://acme.com/careers/data-analyst", "https://acme.com/careers/create-account", None),
        ("https://acme.com/careers/data-analyst", "https://acme.com/careers/job-search", None),
        # A bare ?id= or ?pid= number: on one page of the site it names the record that page shows.
        ("https://acme.com/job?id=1234", "https://acme.com/job?id=5678", "query"),
        ("https://acme.com/careers?pid=11122", "https://acme.com/careers?pid=33344", "query"),
        ("https://acme.com/careers?pid=11122", "https://acme.com/careers/apply?pid=33344", "query"),
        ("https://acme.com/job?id=1234", "https://acme.com/job?id=1234&step=2", None),
        ("https://acme.com/careers?pid=11122", "https://acme.com/careers/apply?pid=11122", None),
        # A form that numbers the application its own way, a word that isn't a number, a page that adds one.
        ("https://acme.com/job?id=1234", "https://acme.com/apply?id=99887", None),
        ("https://acme.com/jobs?id=eng", "https://acme.com/jobs?id=ops", None),
        ("https://acme.com/job?id=1234", "https://acme.com/job", None),
        ("https://acme.com/jobs/data-analyst", "https://acme.com/jobs/data-analyst?id=5678", None),
        # The path's own number says it is the same posting, whatever the id beside it is.
        ("https://acme.com/job/12345?id=999", "https://acme.com/job/12345?id=111", None),
        # Nothing to compare.
        ("", JOB, None),
        (JOB, "", None),
    ],
)
def test_another_postings_address(own, url, expected):
    assert other_posting_address(own, url) == expected


@pytest.mark.parametrize(
    "own, stated, company",
    [
        ("Quantitative Software Engineer", "AI Solutions Developer", "Northwind Example"),
        ("Software Engineer I", "Software Engineer II", ""),
        ("Software Engineer 1", "Software Engineer II", ""),
        ("Nurse", "Data Analyst", ""),
        # A different seniority word is a different requisition, and "Senior" would otherwise read as confirmed for this job.
        ("Software Engineer", "Senior Software Engineer", ""),
        ("Senior Software Engineer", "Software Engineer", ""),
        ("Staff Engineer", "Senior Engineer", ""),
        ("Associate", "Senior Associate", ""),
        ("Software Engineer", "Software Engineer Intern", ""),
        ("Sr. Software Engineer", "Principal Software Engineer", ""),
    ],
)
def test_clearly_another_role(own, stated, company):
    assert clearly_other_role(own, stated, company)


@pytest.mark.parametrize(
    "own, stated, company",
    [
        ("Software Engineer", "Software Engineer, Platform (Remote)", ""),
        ("Sr. Software Engineer", "Senior Software Engineer", ""),
        ("SWE", "Software Engineer", ""),
        ("Software Engineer I", "Software Engineer", ""),
        ("Software Developer", "Softwareentwickler (m/w/d)", ""),  # A translation is the same job.
        ("Software Engineer", "ExampleSoft", "ExampleSoft Inc"),  # The employer's name is no role.
        ("Software Engineer", "ExampleSoft Inc", "ExampleSoft"),
        ("Software Engineer", "this position", ""),
        ("Software Engineer", "", ""),
        ("Senior Software Engineer", "Software Engineer, Senior", ""),  # The same word, where the employer puts it.
        ("Sr Software Engineer", "Senior Software Engineer (Remote)", ""),
        ("Jr. Analyst", "Junior Analyst", ""),
        ("Senior Analyst", "Senior Engineer", ""),  # The same seniority: the words decide, as before.
        ("Untitled role", "Data Analyst", ""),  # The job states no title to compare.
        ("", "Data Analyst", ""),
    ],
)
def test_not_clearly_another_role(own, stated, company):
    assert not clearly_other_role(own, stated, company)


@pytest.mark.parametrize(
    "text, role",
    [
        ("Your application for AI Solutions Developer has been submitted.", "AI Solutions Developer"),
        ("Thank you for applying for Data Analyst at ExampleSoft!", "Data Analyst"),
        ("Thank you for your application for the role of Data Analyst.", "Data Analyst"),
        ("Application Submitted. Application for Data Analyst was received.", "Data Analyst"),
        ("Thank you for applying.", ""),
        ("Thank you for applying for this position.", ""),
        ("Thank you for applying to NCR Example! You have no more tasks.", ""),  # "To" names the employer.
        ("We have successfully received your application for potential employment.", ""),
        ("You must have applied for a Social Security number from the U.S.", ""),
        ("You've already applied for this job.", ""),
        ("Thank you for your application to ExampleSoft.", ""),
    ],
)
def test_the_role_a_receipt_names(text, role):
    assert role_in_receipt(text, "ExampleSoft") == role


def test_the_role_a_form_heading_names():
    assert role_on_form(["Apply for Data Analyst"]) == "Data Analyst"
    assert role_on_form(["Careers", "Apply: Data Analyst"]) == "Data Analyst"
    assert role_on_form(["Application Form", "Apply", "Personal Information", "Apply for this job"]) == ""
    assert role_on_form(["Apply to Join Our Team", "Apply for Jobs at Acme", "Application for Employment"]) == ""


@pytest.mark.parametrize(
    "headings, role",
    [
        (["Data Analyst"], "Data Analyst"),
        (["Application", "Data Analyst"], "Data Analyst"),
        (["Software Engineer, Platform"], ""),  # This job in other words.
        (["Data Analyst", "Software Engineer"], ""),  # A heading reads as this job: the other may be a list beside it.
        (["Personal Information"], ""),
        (["Application Form"], ""),
        (["Talk to your recruiter"], ""),
        (["Career Portal"], ""),
        ([], ""),
    ],
)
def test_a_main_heading_that_names_another_role(headings, role):
    assert role_heading("Software Engineer", headings, "ExampleSoft") == role


GIVEN = {"title": "Quantitative Software Engineer", "company": "Northwind Example", "url": JOB}


def test_the_jobs_own_number_outranks_a_stale_title():
    renamed = "https://careers.example.com/careers/JobDetail/New-York-Platform-Developer/61001"
    # The employer renamed the posting: its receipt and form at the job's own number name a role the saved title doesn't.
    assert receipt_conflict(GIVEN, {"url": JOB + "/confirmation", "confirmation": "Your application for Platform Developer has been submitted."}) is None
    watch = PostingWatch(GIVEN)
    assert watch.other_posting(renamed, {"headings": ["Apply for Platform Developer"]}, "Platform Developer") == ""
    # The same words at any other address are another posting.
    assert watch.other_posting("https://careers.example.com/careers/ApplicationForm", {"headings": ["Apply for Platform Developer"]})


def test_the_incident_receipt_is_no_confirmation_and_names_both_roles():
    why = receipt_conflict(GIVEN, {"url": "https://careers.example.com/careers/Success", "confirmation": "Your application for AI Solutions Developer has been submitted."})
    assert "AI Solutions Developer" in why and "Quantitative Software Engineer" in why


@pytest.mark.parametrize(
    "receipt",
    [
        {"confirmation": "Thank you for applying for this position."},
        {"confirmation": "Thank you for applying.", "url": "https://careers.example.com/careers/Success"},
        {"confirmation": "Your application for Quantitative Software Engineer has been submitted."},
        {"confirmation": "Application Submitted", "url": JOB + "/confirmation"},
        {"confirmation": "Application status: Application Received", "url": "https://careers.example.com/my-profile"},
        {"confirmation": "Thank you for applying to Northwind Example."},
        # The form's own number, walked to from this job's posting.
        {"confirmation": "Thanks", "url": "https://careers.example.com/careers/JobDetail/x/62002/confirmation", "posting_numbers": ["61001", "62002"]},
        {"confirmation": "Thanks. Requisition ID: 62002", "posting_numbers": ["61001", "62002"]},
        {},
    ],
)
def test_a_receipt_that_names_nothing_else_confirms(receipt):
    assert receipt_conflict(GIVEN, receipt) is None


@pytest.mark.parametrize(
    "receipt",
    [
        {"confirmation": "Thank you for applying.", "url": "https://careers.example.com/careers/JobDetail/New-York-Data-Analyst/62002/confirmation"},
        {"confirmation": "Thank you for applying. Requisition ID: 62002"},
    ],
)
def test_a_receipt_carrying_another_number_is_no_confirmation(receipt):
    assert "another posting" in receipt_conflict(GIVEN, receipt)


def controls(*labels, href="/x"):
    return [{"label": label, "href": href} for label in labels]


HOME = "https://careers.example.com/careers"
SEARCH = [{"label": "Keywords", "type": "combobox"}, {"label": "Location", "type": "combobox"}, {"label": "Experience Level", "type": "combobox"}]


def test_a_career_home_is_a_listing_and_the_jobs_own_page_is_not():
    roles = controls("Structured Credit Analyst", "Data Operations Analyst", "Kubernetes Tech Lead", "Open Roles", "About Us")
    own = [JOB]
    assert looks_like_listing({"fields": SEARCH, "controls": roles}, HOME, own)
    # The same page content at the job's own address, or one stating its number, stays.
    assert not looks_like_listing({"fields": SEARCH, "controls": roles}, JOB, own)
    assert not looks_like_listing({"fields": SEARCH, "controls": roles}, HOME + "?jobId=61001", own)
    # A page with one Apply is a posting, whatever its "similar jobs" list says.
    assert not looks_like_listing({"fields": [], "controls": roles + controls("Apply")}, HOME + "/x-y", own)
    # A real form or a sign-in isn't a list.
    assert not looks_like_listing({"fields": [{"label": "Email", "type": "email"}], "controls": roles}, HOME + "/Login", own)
    # A list's own rows, each with its Apply, and one posting's Apply at its top and foot.
    rows = [{"label": "Apply", "href": "/apply/1"}, {"label": "Apply", "href": "/apply/2"}]
    assert looks_like_listing({"fields": [], "controls": rows}, HOME + "/x-y", own)
    assert not looks_like_listing({"fields": [], "controls": controls("Apply Now", "Apply Now", "Careers at Example")}, HOME + "/x-y", own)
    # A plain page at a listing's address counts, unless it has a step to go on with.
    plain = controls("About Us", "Contact", "Log in", href="")
    assert looks_like_listing({"fields": [], "controls": plain}, HOME, own)
    assert looks_like_listing({"fields": [], "controls": plain}, "https://corporate.example.org/careers/", own)  # A closed board's bounce.
    assert not looks_like_listing({"fields": [], "controls": plain + controls("Continue", href="")}, HOME, own)


def test_a_walk_from_the_own_posting_owns_what_it_reaches():
    watch = PostingWatch(GIVEN)
    assert watch.judge(JOB, {"fields": [], "controls": controls("Apply")}).kind == "ok"
    form = "https://careers.example.com/talentcommunity/apply/45678"
    assert watch.judge(form, {"fields": [{"label": "Email", "type": "email"}], "controls": []}).kind == "ok"
    assert "45678" in watch.numbers()
    # Another posting's page is still another posting's after the walk.
    assert watch.judge("https://careers.example.com/careers/JobDetail/New-York-Data-Analyst/62002", {}).kind == "other_posting"


def test_a_posting_the_employer_moved_is_the_job_and_a_closed_jobs_redirect_to_another_role_is_not():
    moved = "https://careers.example.com/careers/JobDetail/New-York-Quantitative-Software-Engineer/19999"
    other = "https://careers.example.com/careers/JobDetail/New-York-Data-Analyst/62002"
    watch = PostingWatch(GIVEN, landing=True)
    assert watch.judge(moved, {"headings": ["Quantitative Software Engineer"]}).kind == "ok"
    assert "19999" in watch.numbers()
    assert PostingWatch(GIVEN, landing=True).judge(other, {"headings": []}).kind == "other_posting"
    unread = "https://careers.example.com/careers/job/19999"
    bare = PostingWatch({**GIVEN, "url": "https://careers.example.com/careers/job/61001"}, landing=True)
    assert bare.judge(unread, {"headings": ["Data Analyst"]}).kind == "other_posting"
    bare = PostingWatch({**GIVEN, "url": "https://careers.example.com/careers/job/61001"}, landing=True)
    assert bare.judge(unread, {"headings": []}).kind == "ok"
    # Only what the job's own address opened: once the run has clicked, another number is another posting.
    walked = PostingWatch({**GIVEN, "url": "https://careers.example.com/careers/job/61001"})
    assert walked.judge(unread, {"headings": []}).kind == "other_posting"


def test_a_page_reached_without_that_walk_does_not_own_its_number():
    saved = PostingWatch(GIVEN, opened_fresh=False)
    form = "https://careers.example.com/careers/ApplicationForm?jobId=62002"
    assert saved.judge(form, {"fields": [{"label": "Email", "type": "email"}]}).kind == "other_posting"
    # The route naming this job's own number is this job.
    resumed = PostingWatch(GIVEN, opened_fresh=False)
    assert resumed.judge(form.replace("62002", "61001"), {"fields": [{"label": "Email", "type": "email"}]}).kind == "ok"
    assert resumed.trail


def test_the_identity_that_cant_be_read_blocks_nothing():
    watch = PostingWatch({"title": "Untitled role", "company": "", "url": "https://go.example.net/x1"})
    assert watch.judge("https://boards.example.io/acme/jobs/456", {"fields": [{"label": "Email", "type": "email"}]}).kind == "ok"
    assert watch.other_posting("https://boards.example.io/acme/jobs/456", {"headings": ["Application"]}, "") == ""
    # A hand-off from a short link becomes this job, and another posting on that board is then another posting.
    assert watch.judge("https://boards.example.io/acme/jobs/789", {}).kind == "other_posting"
    assert watch.other_posting_link("/acme/jobs/789", "https://boards.example.io/acme/jobs/456")
    assert not watch.other_posting_link("/acme/jobs/456/apply", "https://boards.example.io/acme/jobs/456")
    assert not watch.other_posting_link("", "https://boards.example.io/acme/jobs/456")


BASE = "https://careers.example.com/careers/JobDetail/New-York-Software-Engineer/61001"


@pytest.mark.parametrize(
    "href, label, other",
    [
        ("/careers/JobDetail/New-York-Data-Analyst/62002", "Data Analyst", True),  # Same shape, another number.
        ("/careers/JobDetail/New-York-Data-Analyst/62002", "View role", True),
        ("/careers/roles/62002", "AI Solutions Developer", True),  # Another shape, but it reads as another role.
        ("/careers/roles/62002", "View role", True),  # Another shape, another number, a link to read.
        ("/careers/OpenRoles", "AI Solutions Developer", True),
        # This job's own way on: its Apply may carry the form's own number.
        ("/talentcommunity/apply/45678", "Apply", False),
        ("/talentcommunity/apply/45678", "Continue", False),
        ("/careers/ApplicationForm?jobId=61001", "Apply", False),
        ("/careers/my-applications/8812", "View application", False),
        # A page that isn't a posting.
        ("/careers/OpenRoles", "See All Openings", False),
        ("/careers/privacy", "Privacy Policy", False),
        ("https://elsewhere.example.org/x/999", "Read more", False),
        ("#top", "Data Analyst", False),
        ("mailto:jobs@example.com", "Contact a Recruiter", False),
        # A link naming this very job.
        ("/careers/JobDetail/New-York-Quantitative-Software-Engineer/61001", "Quantitative Software Engineer", False),
    ],
)
def test_the_links_a_run_never_opens(href, label, other):
    assert PostingWatch(GIVEN).other_posting_link(href, BASE, label) is other


# ----- where a run's result is written ---------------------------------------------------------------------------------------


def seed(service, *, state="submitting", title="Quantitative Software Engineer"):
    with service.db.exclusive() as s:
        job = Job(identity="wp-1", url=JOB, company="Northwind Example", title=title, ats="custom", status="running")
        s.add(job)
        s.flush()
        run = Run(job_id=job.id, state=state, mode="submit", packet={}, reason="")
        s.add(run)
        s.flush()
        return run.id, job.id


def written(service, run_id, job_id):
    with service.db.session() as s:
        return s.get(Run, run_id), s.get(Job, job_id).status


def page_receipt(ids, words, url="https://careers.example.com/careers/Success"):
    return {"type": "explicit_confirmation_page", "url": url, "confirmation": words, "evidence_version": 2, "evidence_selector": "p",
            "status_region": "application_acknowledgement", "run_id": ids[0], "job_id": ids[1]}  # fmt: skip


def test_a_receipt_for_another_role_is_written_as_unknown_never_confirmed(service):
    run_id, job_id = seed(service)
    service.complete(
        run_id, {"state": "confirmed", "reason": "Website confirmation captured",
                 "receipt": page_receipt((run_id, job_id), "Your application for AI Solutions Developer has been submitted.")}, 1.0,
    )  # fmt: skip
    run, status = written(service, run_id, job_id)
    assert (run.state, status) == ("submission_unknown", "submission_unknown")
    assert "AI Solutions Developer" in run.reason and "Quantitative Software Engineer" in run.reason
    assert run.receipt["other_posting"] is True and run.receipt["website_confirmed"] is False


@pytest.mark.parametrize(
    "words",
    ["Thank you for applying for this position.", "Your application has been received.", "Thank you for applying to Northwind Example."],
)
def test_a_generic_receipt_is_written_as_confirmed(service, words):
    run_id, job_id = seed(service)
    service.complete(
        run_id, {"state": "confirmed", "reason": "Website confirmation captured", "receipt": page_receipt((run_id, job_id), words)}, 1.0
    )
    run, status = written(service, run_id, job_id)
    assert (run.state, status) == ("confirmed", "confirmed")
    assert run.receipt["website_confirmed"] is True and "other_posting" not in run.receipt


def test_a_final_page_naming_another_role_cant_be_booked_as_this_job(service):
    run_id, job_id = seed(service, state="submission_unknown")
    with pytest.raises(ValueError, match="AI Solutions Developer"):
        service.reconcile(run_id, True, "Your application for AI Solutions Developer has been submitted.", final_page=True)
    assert written(service, run_id, job_id)[0].state == "submission_unknown"
    service.reconcile(run_id, True, "Thank you for applying. Your application has been submitted.", final_page=True)
    assert written(service, run_id, job_id)[0].state == "confirmed"


def test_an_application_that_reached_another_posting_is_never_resent():
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    at = (now - timedelta(hours=2)).isoformat()
    session = SimpleNamespace(get=lambda *_a: SimpleNamespace(value={"enabled": True}))
    run = SimpleNamespace(state="submission_unknown", receipt={}, finished_at=at, created_at=at)
    assert unknown_resubmit_allowed(session, [run], now, checked=False)
    run.receipt = {"other_posting": True}
    assert not unknown_resubmit_allowed(session, [run], now, checked=False)


# ----- the guard never sends an application twice --------------------------------------------------------------------------------


SIMILAR = controls("Data Analyst Lead", "Support Associate", "Product Designer Manager", "Kubernetes Tech Lead", "About Us")


@pytest.mark.parametrize(
    "words",
    [
        "Thank you for applying! Your application has been received.",
        "Thank you for applying.",
        "Your application for Software Engineer has been submitted.",
        "We have received your application.",
        "Application Submitted",
        "You have successfully applied.",
        "Bewerbung eingereicht! Die Bewerbung wurde erfolgreich eingereicht.",
    ],
)
def test_a_receipt_listing_similar_jobs_is_never_a_listing(words):
    page_seen = {"fields": [], "controls": SIMILAR, "text": f"Thank you\n{words}\nSimilar jobs\nData Analyst Lead\nSupport Associate"}
    assert acknowledges_application(page_seen["text"])
    assert not looks_like_listing(page_seen, HOME + "/Success", [JOB])
    assert PostingWatch(GIVEN).judge(HOME + "/Success", page_seen).kind == "ok"


@pytest.mark.parametrize(
    "words",
    [
        "",
        "Find your next role. Open roles below.",
        "Thank you for your interest in careers at Example.",
        "How do I know my application was received?",
        "After you apply, your application will be received by our team.",
        "If your application is submitted before Friday, we will read it.",
        "Your application was not submitted.",
    ],
)
def test_a_real_listing_with_no_acknowledgement_is_still_a_listing(words):
    page_seen = {"fields": [], "controls": SIMILAR, "text": f"Careers\n{words}\nData Analyst Lead\nSupport Associate"}
    assert not acknowledges_application(page_seen["text"])
    assert looks_like_listing(page_seen, HOME, [JOB])
    assert PostingWatch(GIVEN).judge(HOME, page_seen).kind == "listing"


def test_another_postings_page_stays_another_postings_even_when_it_thanks_the_applicant():
    """The engine reads the page's proof first and never goes back from it; the verdict itself must not make that number this job's."""
    watch = PostingWatch(GIVEN)
    seen = {"fields": [], "controls": [], "text": "Thank you for applying."}
    assert watch.judge("https://careers.example.com/careers/JobDetail/New-York-Data-Analyst/62002/confirmation", seen).kind == "other_posting"
    assert "62002" not in watch.numbers()


@pytest.mark.parametrize(
    "url, body, could",
    [
        ("https://acme.com/apply/submit", b"first_name=Alex", True),
        ("https://acme.com/careers/ApplicationForm?jobId=61001", b"", True),  # A body with a file is withheld.
        ("https://acme.com/api/applications", b'{"email":"alex@example.test"}', True),
        ("https://acme.com/careers/Login?jobId=61001", b"username=a&password=b", False),
        ("https://acme.com/careers/x", b"username=a&password=b", False),  # A password in it makes it a sign-in, wherever it goes.
        ("https://acme.com/account/register", b"email=a", False),
        ("https://acme.com/search", b"q=engineer", False),
        ("https://acme.com/api/collect", b"e=1", False),
    ],
)
def test_which_writes_could_carry_an_application(url, body, could):
    assert could_carry_application(url, body) is could
