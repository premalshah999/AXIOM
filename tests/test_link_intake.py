"""What a pasted link is before it becomes a job (owner, 2026-11-01: "there is a lot you can fix on the apply through the
link pipeline"; "why is it still failing so much, especially on the custom link urls I'm sending").

A LinkedIn "leaving LinkedIn" page around an ad click tracker around the employer's posting became a job on the wrong page
and was archived without a run (2026-10-26). A job board's own page, or a company's list of jobs, became a job whose run
opened the wrong page. Each now unwraps, follows the board once, or stops with a sentence that says what to do.
"""

import pytest

from jobpilot.db import Job
from jobpilot.link_intake import board_name, employer_link, listing_kind, prepare, unwrap

POSTING = "https://careers.example.test/job/ABC123/Data-Engineer?utm_source=board&ss=paid"
WRAPPED = (
    "https://www.linkedin.com/safety/go?isSdui=true&mt=opaque"
    "&url=https%3A%2F%2Fad.doubleclick.net%2Fddm%2Fclk%2F111%3B222%3Bm%3Fhttps%3A%2F%2Fcareers.example.test%2Fjob%2FABC123"
    "%2FData-Engineer%3Futm_source%3Dboard%26ss%3Dpaid&urlhash=x1"
)


def test_a_wrapper_around_a_click_tracker_around_the_posting_is_the_posting():
    assert unwrap(WRAPPED) == POSTING
    assert unwrap("https://www.google.com/url?q=https%3A%2F%2Fboards.example.test%2Facme%2Fjobs%2F7&sa=U") == (
        "https://boards.example.test/acme/jobs/7"
    )
    assert unwrap("https://l.facebook.com/l.php?u=https%3A%2F%2Fboards.example.test%2Facme%2Fjobs%2F7") == (
        "https://boards.example.test/acme/jobs/7"
    )
    # A link that wraps nothing is left alone, and so is an employer's own page that merely has a url parameter.
    assert unwrap(POSTING) == POSTING
    assert unwrap("https://careers.example.test/apply?url=https%3A%2F%2Fother.example.test") == (
        "https://careers.example.test/apply?url=https%3A%2F%2Fother.example.test"
    )


def test_boards_and_google_jobs_are_told_apart_from_an_employers_own_google_page():
    assert board_name("https://www.linkedin.com/jobs/view/3912345678/") == "LinkedIn"
    assert board_name("https://smartapply.indeed.com/beta/indeedapply/form/resume") == "Indeed"
    assert board_name("https://www.ziprecruiter.com/c/Example/Job/Data-Engineer/-in-Austin,TX?jid=1") == "ZipRecruiter"
    assert board_name("https://app.joinhandshake.com/jobs/9012345") == "Handshake"
    assert board_name("https://www.google.com/search?q=data+engineer&ibp=htl;jobs") == "Google Jobs"
    assert board_name("https://www.google.com/about/careers/applications/jobs/results/12345") == ""
    assert board_name(POSTING) == ""


def test_the_employer_apply_link_a_board_page_carries_is_found_and_a_board_link_is_not():
    page = (
        '<html><body><code id="applyUrl"><!--"https://www.linkedin.com/jobs/view/externalApply/391?url='
        'https%3A%2F%2Fboards.example.test%2Facme%2Fjobs%2F7%3Ftrk%3Dli"--></code></body></html>'
    )
    assert employer_link(page, "https://www.linkedin.com/jobs/view/391") == "https://boards.example.test/acme/jobs/7?trk=li"
    easy = '<a href="https://www.linkedin.com/jobs/view/391/apply">Easy Apply</a>'
    assert employer_link(easy, "https://www.linkedin.com/jobs/view/391") == ""
    indeed = '<a href="https://careers.example.test/job/1?x=1&amp;y=2">Apply on company site</a>'
    assert employer_link(indeed, "https://www.indeed.com/viewjob?jk=1") == "https://careers.example.test/job/1?x=1&y=2"


@pytest.mark.parametrize(
    "url, listed",
    [
        ("https://boards.greenhouse.io/acme", True),
        ("https://job-boards.greenhouse.io/acme/jobs/4001", False),
        ("https://boards.greenhouse.io/embed/job_board?for=acme", True),
        ("https://jobs.lever.co/acme", True),
        ("https://jobs.lever.co/acme/0b2c4c5a-1111-2222-3333-444455556666", False),
        ("https://jobs.ashbyhq.com/acme", True),
        ("https://jobs.ashbyhq.com/acme/0b2c4c5a-1111-2222-3333-444455556666", False),
        ("https://apply.workable.com/acme/", True),
        ("https://apply.workable.com/acme/j/ABCDEF1234/", False),
        ("https://jobs.smartrecruiters.com/Acme", True),
        ("https://jobs.smartrecruiters.com/Acme/744000123456-data-engineer", False),
        ("https://careers-acme.icims.com/jobs/search?ss=1", True),
        ("https://careers-acme.icims.com/jobs/1234/data-engineer/job", False),
        ("https://acme.wd5.myworkdayjobs.com/en-US/External", True),
        ("https://acme.wd5.myworkdayjobs.com/en-US/External/job/Austin/Data-Engineer_R100", False),
        ("https://acme.wd5.myworkdayjobs.com/External/details/Data-Engineer_R100", False),
        ("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs", True),
        ("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/4455", False),
        ("https://careers.example.test/job/ABC123", False),
    ],
)
def test_a_list_of_jobs_is_told_apart_from_one_posting(url, listed):
    assert bool(listing_kind(url)) is listed


async def test_prepare_follows_a_board_to_the_employer_or_says_what_to_do(monkeypatch):
    async def board(url, allow_private=False):
        return (
            '<code id="applyUrl"><!--"https://www.linkedin.com/jobs/view/externalApply/9?url='
            'https%3A%2F%2Fboards.example.test%2Facme%2Fjobs%2F7"--></code>',
            url,
        )

    monkeypatch.setattr("jobpilot.network.fetch", board)
    assert await prepare("https://www.linkedin.com/jobs/view/9") == ("https://boards.example.test/acme/jobs/7", "")

    async def closed(url, allow_private=False):
        raise RuntimeError("HTTP 999")

    monkeypatch.setattr("jobpilot.network.fetch", closed)
    link, problem = await prepare("https://www.linkedin.com/jobs/view/9")
    assert "LinkedIn" in problem and "paste the link" in problem

    link, problem = await prepare("https://jobs.lever.co/acme")
    assert "list of jobs" in problem and "one job posting" in problem
    assert await prepare(WRAPPED) == (POSTING, "")


def test_the_pasted_box_unwraps_and_refuses_with_a_sentence_and_still_queues_the_rest(client, service, monkeypatch):
    service.dispatch = True
    picked = []

    async def fake_apply(service, job_id):
        picked.append(job_id)
        return {"job_id": job_id, "state": "queued", "run_id": "r1", "message": "Application queued."}

    async def nothing(*a):
        return None

    async def board(url, allow_private=False):
        raise RuntimeError("blocked")

    monkeypatch.setattr("jobpilot.api.routes.queue.apply", fake_apply)
    monkeypatch.setattr("jobpilot.api.routes.queue.posting_api_text", nothing)
    monkeypatch.setattr("jobpilot.network.fetch", board)
    results = client.post(
        "/api/queue/links",
        json={"urls": [WRAPPED, "https://www.indeed.com/viewjob?jk=abc", "https://jobs.lever.co/acme"]},
    ).json()["results"]
    assert [r["state"] for r in results] == ["queued", "refused", "refused"]
    assert results[0]["url"] == WRAPPED and results[0]["resolved_url"].startswith("https://careers.example.test/job/ABC123")
    assert "Indeed" in results[1]["message"] and "list of jobs" in results[2]["message"]
    with service.db.session() as s:
        assert s.get(Job, picked[0]).url.startswith("https://careers.example.test/job/ABC123")
        assert len(picked) == 1  # No job exists for the two refused links.


async def test_a_link_sent_on_telegram_is_the_owners_pick_not_a_fit_ranking(service, monkeypatch):
    from jobpilot import telegram
    from test_telegram_owner_questions_20261023 import Calls, enable, update

    enable(service)
    asked = []

    async def fake_apply(svc, job_id):
        asked.append(job_id)
        return {"job_id": job_id, "state": "queued", "message": "Application queued."}

    async def nothing(*a):
        return None

    async def forbidden(*a, **k):
        raise AssertionError("the owner's link must not be ranked against the fit filters")

    monkeypatch.setattr("jobpilot.owner_apply.apply", fake_apply)
    monkeypatch.setattr("jobpilot.discovery.posting_api_text", nothing)
    monkeypatch.setattr("jobpilot.discovery.rank", forbidden)
    calls = Calls(monkeypatch)
    await telegram._handle_update(service, update("/apply https://careers.example.test/job/ABC123"))
    assert len(asked) == 1
    assert "Application queued." in calls.of("sendMessage")[-1]["text"]
    await telegram._handle_update(service, update("/apply https://jobs.lever.co/acme"))
    assert len(asked) == 1 and "list of jobs" in calls.of("sendMessage")[-1]["text"]



def test_a_career_sites_page_label_is_not_a_job_title():
    from jobpilot.posting_identity import placeholder_title
    from jobpilot.schemas import job_title

    for junk in ("Job Details", "Career Portal", "Jobs - ExampleSoft", "Data Engineer Job Details | ExampleSoft", "Careers | Join Us | ExampleSoft", "Data Engineer | ExampleSoft Careers"):
        assert placeholder_title(junk), junk
    for real in ("Data Engineer", "Software Engineer - Early Career", "Engineer, Early Careers - EMEA", "Analyst - Work From Home", "Careers Advisor"):
        assert not placeholder_title(real), real
    # iCIMS puts the field label before the title in its listing links and on its job pages.
    assert job_title("Job Posting Title Quantitative Engineer") == "Quantitative Engineer"


async def test_a_page_without_structured_data_does_not_give_the_job_its_page_title(service, monkeypatch):
    from support_http import seed_job

    from jobpilot import discovery

    job_id = seed_job(service, 1)
    with service.db.exclusive() as s:
        row = s.get(Job, job_id)
        row.title, row.company, row.description = "", "", ""

    async def no_api(*args):
        return None

    async def page(url, **kwargs):
        return "<html><head><title>Job Details | ExampleSoft</title></head><body><p>" + "Build data pipelines. " * 30 + "</p></body></html>", url

    monkeypatch.setattr(discovery, "posting_api_text", no_api)
    monkeypatch.setattr(discovery, "fetch", page)
    await discovery.enrich(service.db, service.config, job_id)
    with service.db.session() as s:
        row = s.get(Job, job_id)
        assert row.title == "" and "data pipelines" in row.description
