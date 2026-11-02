"""Which pages and receipts belong to a job. Each stop sits beside the closest legitimate case it must let through. Names are invented."""

import pytest

from jobpilot.posting_match import (
    PostingWatch,
    clearly_other_role,
    looks_like_listing,
    other_posting_address,
    posting_ids,
    receipt_conflict,
    role_in_receipt,
    role_on_form,
)

JOB = "https://careers.example.com/careers/JobDetail/New-York-Software-Engineer/13045"


@pytest.mark.parametrize(
    "url, numbers",
    [
        (JOB, {"13045"}),
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
        (JOB, "https://careers.example.com/careers/JobDetail/New-York-Data-Analyst/14102", "path"),
        ("https://boards.example.io/acme/jobs/8296161", "https://boards.example.io/acme/jobs/8282426/confirmation", "path"),
        ("https://boards.example.io/acme/jobs/8296161", "https://job-boards.example.io/acme/jobs/8282426", "path"),
        ("https://acme.com/apply?jobId=13045", "https://acme.com/apply/step2?jobId=14102", "query"),
        ("https://acme.wd1.myworkdayjobs.com/x/job/NY/Analyst_R100", "https://acme.wd1.myworkdayjobs.com/x/job/NY/Engineer_R200", "path"),
        ("https://acme.com/careers/data-analyst", "https://acme.com/careers/senior-engineer", "slug"),
        # The same posting on its way through, however the address reads.
        ("https://boards.example.io/acme/jobs/8296161", "https://boards.example.io/acme/jobs/8296161/confirmation", None),
        ("https://boards.example.io/acme/jobs/8296161", "https://job-boards.example.io/acme/jobs/8296161", None),
        ("https://boards.example.io/acme/jobs/8296161?gh_jid=8296161", "https://boards.example.io/acme/jobs/8296161", None),
        (JOB, "https://careers.example.com/careers/ApplicationForm?jobId=13045", None),
        (JOB, "https://careers.example.com/careers/Login?jobId=13045", None),
        ("https://acme.wd1.myworkdayjobs.com/x/job/NY/Analyst_R100", "https://acme.wd1.myworkdayjobs.com/x/job/NY/Analyst_R100/apply/applyManually", None),
        # A hand-off to another site numbers the posting its own way.
        ("https://acme.com/careers/job/123", "https://boards.example.io/acme/jobs/456", None),
        ("https://acme.com/careers?gh_jid=123", "https://boards.example.io/acme/jobs/456", None),
        # A form that adds a number the board address lacked, or numbers the posting its own way, isn't a difference of address.
        ("https://acme.com/careers/software-engineer", "https://acme.com/careers/ApplicationForm?jobId=14102", None),
        ("https://acme.com/job/Software-Engineer/900100200", "https://acme.com/talentcommunity/apply/45678", None),
        # A page of the site beside a slug address.
        ("https://acme.com/careers/data-analyst", "https://acme.com/careers/create-account", None),
        ("https://acme.com/careers/data-analyst", "https://acme.com/careers/job-search", None),
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
        ("Quantitative Software Engineer", "AI Solutions Developer", "Two Sigma Example"),
        ("Software Engineer I", "Software Engineer II", ""),
        ("Software Engineer 1", "Software Engineer II", ""),
        ("Nurse", "Data Analyst", ""),
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
        ("Untitled role", "Data Analyst", ""),  # The job states no title to compare.
        ("", "Data Analyst", ""),
        ("Staff Engineer", "Senior Engineer", ""),  # Can't be told from a title alone; an id decides those.
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


GIVEN = {"title": "Quantitative Software Engineer", "company": "Two Sigma Example", "url": JOB}


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
        {"confirmation": "Thank you for applying to Two Sigma Example."},
        # The form's own number, walked to from this job's posting.
        {"confirmation": "Thanks", "url": "https://careers.example.com/careers/JobDetail/x/14102/confirmation", "posting_numbers": ["13045", "14102"]},
        {"confirmation": "Thanks. Requisition ID: 14102", "posting_numbers": ["13045", "14102"]},
        {},
    ],
)
def test_a_receipt_that_names_nothing_else_confirms(receipt):
    assert receipt_conflict(GIVEN, receipt) is None


@pytest.mark.parametrize(
    "receipt",
    [
        {"confirmation": "Thank you for applying.", "url": "https://careers.example.com/careers/JobDetail/New-York-Data-Analyst/14102/confirmation"},
        {"confirmation": "Thank you for applying. Requisition ID: 14102"},
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
    assert not looks_like_listing({"fields": SEARCH, "controls": roles}, HOME + "?jobId=13045", own)
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
    assert watch.judge("https://careers.example.com/careers/JobDetail/New-York-Data-Analyst/14102", {}).kind == "other_posting"


def test_a_page_reached_without_that_walk_does_not_own_its_number():
    saved = PostingWatch(GIVEN, opened_fresh=False)
    form = "https://careers.example.com/careers/ApplicationForm?jobId=14102"
    assert saved.judge(form, {"fields": [{"label": "Email", "type": "email"}]}).kind == "other_posting"
    # The route naming this job's own number is this job.
    resumed = PostingWatch(GIVEN, opened_fresh=False)
    assert resumed.judge(form.replace("14102", "13045"), {"fields": [{"label": "Email", "type": "email"}]}).kind == "ok"
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
