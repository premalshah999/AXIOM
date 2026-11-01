"""What a person meets on a first run, from nothing: every rule here came from walking the app as a new user (2026-11-01)."""

import asyncio
import io

import httpx
import pytest
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from jobpilot import connections, link_intake
from jobpilot.config import Settings
from jobpilot.db import Job, Resume
from jobpilot.execution_services import master_policy
from jobpilot.owner_apply import _approve


def resume_pdf(text="Alex Example, alex@example.test, Software Engineer at ExampleSoft since 2023"):
    out = io.BytesIO()
    page = canvas.Canvas(out)
    page.drawString(72, 720, text)
    page.save()
    return out.getvalue()


def scanned_pdf():
    out = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    writer.write(out)
    return out.getvalue()


def upload(client, content, name="resume", query=""):
    return client.post("/api/resumes" + query, data={"name": name, "roles": ""}, files={"file": ("r.pdf", content, "application/pdf")})


def add_resume(service, name, created_at):
    with service.db.exclusive() as s:
        s.add(Resume(id=name, name=name, filename=name + ".pdf", sha256=name, roles=[], text="x" * 40, created_at=created_at))


def test_a_new_install_sends_the_newest_resume_rather_than_waiting_for_an_approved_copy(service):
    # A fresh database has no policy. The old default was "tailored": every job waited for a version nothing would ever build.
    assert master_policy(service.db) is None  # no resume, nothing to send
    add_resume(service, "older", "2026-10-01T00:00:00+00:00")
    add_resume(service, "newer", "2026-10-02T00:00:00+00:00")
    assert master_policy(service.db)["resume_id"] == "newer"


def test_a_choice_the_person_made_stands(service):
    add_resume(service, "older", "2026-10-01T00:00:00+00:00")
    add_resume(service, "newer", "2026-10-02T00:00:00+00:00")
    service.db.set_setting("application_resume_policy", {"mode": "master", "resume_id": "older"})
    assert master_policy(service.db)["resume_id"] == "older"
    service.db.set_setting("application_resume_policy", {"mode": "tailored"})
    assert master_policy(service.db) is None


def test_a_pasted_job_takes_the_newest_resume_not_the_first_one_ever_uploaded(service):
    add_resume(service, "mistake", "2026-10-01T00:00:00+00:00")
    add_resume(service, "right", "2026-10-02T00:00:00+00:00")
    with service.db.exclusive() as s:
        s.add(Job(id="j1", identity="j1", url="https://jobs.example.test/1", title="Analyst", company="ExampleSoft", status="new"))
    assert _approve(service, "j1") is None
    with service.db.session() as s:
        assert s.get(Job, "j1").resume_id == "right"


def test_the_guide_can_prefer_an_older_resume(client, service):
    add_resume(service, "older", "2026-10-01T00:00:00+00:00")
    add_resume(service, "newer", "2026-10-02T00:00:00+00:00")
    assert client.post("/api/resumes/older/prefer", json={}).status_code == 200
    assert master_policy(service.db)["resume_id"] == "older"
    assert client.post("/api/resumes/none/prefer", json={}).status_code == 404


def test_the_same_file_again_in_the_guide_is_that_resume(client):
    pdf = resume_pdf()  # The PDF carries its own timestamp: the same file means the same bytes.
    first = upload(client, pdf, "first").json()
    again = upload(client, pdf, "again", "?reuse=1")
    assert again.status_code == 200 and again.json()["id"] == first["id"]
    assert "already" in upload(client, pdf, "again").json()["detail"]


@pytest.mark.parametrize(
    "content, words",
    [
        (scanned_pdf(), "picture of your resume"),
        (b"PK\x03\x04 a word file named resume.pdf", "isn't really a PDF"),
        (b"%PDF-1.4\n" + b"0" * (5 * 1024 * 1024 + 1), "over 5 MB"),
    ],
)
def test_a_resume_that_cannot_be_used_says_what_to_do_in_plain_words(client, content, words):
    answer = upload(client, content)
    assert answer.status_code == 400 and words in answer.json()["detail"]
    for developer in ("extractable", "classification", "Export a text-based"):
        assert developer not in answer.json()["detail"]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/careers",
        "https://example.test/jobs/",
        "https://careers.example.test/",
        "https://example.test/",
        "https://example.test/join-us",
    ],
)
def test_a_careers_page_or_home_page_is_not_one_job(url):
    assert listing(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/careers/1234-marketing-manager",
        "https://example.test/careers?gh_jid=1234",
        "https://example.test/jobs/1234",
        "https://careers.example.test/job/1234",
        "https://example.test/apply",
        "https://example.test/careers#job=12",
    ],
)
def test_a_link_with_more_to_it_may_be_a_posting(url):
    assert not listing(url)


def listing(url):
    return link_intake.listing_kind(url)


def _failing(exc):
    async def fetch(*a, **k):
        raise exc

    return fetch


def _status(code):
    request = httpx.Request("GET", "https://example.test/job/1")
    return httpx.HTTPStatusError("x", request=request, response=httpx.Response(code, request=request))


@pytest.mark.parametrize("code", [404, 410])
def test_a_dead_link_is_refused_with_a_sentence(monkeypatch, code):
    monkeypatch.setattr("jobpilot.network.fetch", _failing(_status(code)))
    link, problem = asyncio.run(link_intake.prepare("https://example.test/job/1"))
    assert link == "https://example.test/job/1" and "no longer exists" in problem


@pytest.mark.parametrize(
    "exc",
    [
        _status(403),  # A site's answer to a plain client says nothing about the job.
        _status(500),
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("slow"),
        ValueError("Source response exceeds its size limit"),
    ],
)
def test_a_site_that_will_not_answer_a_plain_request_is_left_to_the_browser(monkeypatch, exc):
    monkeypatch.setattr("jobpilot.network.fetch", _failing(exc))
    assert asyncio.run(link_intake.prepare("https://example.test/job/1")) == ("https://example.test/job/1", "")


def test_a_name_that_resolves_to_nothing_is_refused(monkeypatch):
    monkeypatch.setattr("jobpilot.network.fetch", _failing(httpx.ConnectError("[Errno 8] nodename nor servname provided")))
    assert "no website" in asyncio.run(link_intake.prepare("https://nothing.example.test/job/1"))[1]


class _Answer:
    def __init__(self, code, body=None):
        self.status_code, self.body = code, body or {}

    def json(self):
        return self.body


def _key_check(monkeypatch, answer):
    class Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a): ...
        async def get(self, *a, **k):
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr(connections.httpx, "AsyncClient", Client)


@pytest.mark.parametrize(
    "answer, words",
    [
        (_Answer(401), "OpenRouter didn't accept this key"),
        (_Answer(402), "OpenRouter account is out of credit"),
        (_Answer(503), "OpenRouter isn't answering"),
        (httpx.ConnectError("x"), "Couldn't reach OpenRouter"),
    ],
)
def test_a_refused_key_names_who_refused_it(monkeypatch, tmp_path, answer, words):
    _key_check(monkeypatch, answer)
    cfg = Settings(data_dir=tmp_path, openrouter_api_key="sk-or-synthetic", openrouter_base_url="https://or.example.test")
    result = asyncio.run(connections.test(cfg, "openrouter_api_key"))
    assert not result["ok"] and words in result["message"]
    assert "the service" not in result["message"] and "HTTP" not in result["message"] and "ConnectError" not in result["message"]


def test_the_failure_report_says_nothing_when_it_knows_nothing_more(service):
    from jobpilot.db import Run
    from jobpilot.failure_reports import create_report

    service.config.failure_reports_enabled = True
    with service.db.exclusive() as s:
        s.add(Job(id="j2", identity="j2", url="https://jobs.example.test/2", title="Analyst", company="ExampleSoft", status="failed"))
        s.add(Run(id="r2", job_id="j2", state="failed", mode="submit", packet={}, reason="The page did not load"))
    asyncio.run(create_report(service, "r2"))
    report = service.db.get_setting("failure_report:r2")
    assert report["summary"] == "The page did not load"
    assert report["likely_cause"] == "" and report["next_steps"] == []


@pytest.mark.parametrize(
    "reason, words",
    [
        ("Browser control failed at browser.py:441; application tab retained for recovery", "got stuck"),
        ("Worker interrupted before submission; draft recovery scheduled", "interrupted before anything was sent"),
        ("Worker error: KeyError", "problem of its own"),
        ("Open the ApplyPilot Chrome window: the browser is responding, but its automation connection is unavailable.", "use its browser"),
        ("Chromium did not expose a debugging endpoint", "use its browser"),
    ],
)
def test_the_engine_never_explains_itself_in_its_own_words(reason, words):
    from jobpilot.vocabulary import plain_sentence

    said = plain_sentence(reason)
    assert words in said and ".py" not in said and "Worker" not in said and "Error" not in said


def test_an_employers_words_are_left_alone():
    from jobpilot.vocabulary import plain_sentence

    assert plain_sentence("The posting is closed or no longer available") == "The posting is closed or no longer available."
