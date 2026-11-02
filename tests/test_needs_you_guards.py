"""Needs you and Applications, the server's side (2026-11-01 QA).

Every way of starting a job again is refused once it was sent or may have been; one answer clears every job asking the same thing; a
multi-pick and a Yes/No that says "multiple" are what they are; Take over hands back only the page the person looked at; and no screen
is handed a validator's report, a stack trace, a source file or an internal id. Synthetic data only."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select

from jobpilot import steer, vocabulary
from jobpilot.db import Event, Job, Review, Run, Setting
from jobpilot.owner_questions import employer_only, fits_form, present, same_question, takes_several

from support_http import seed_job, seed_run
from test_owner_questions_20261023 import add_job, add_review

pytestmark = pytest.mark.integration


def control(service, **values):
    with service.db.exclusive() as s:
        row = s.get(Setting, "control")
        row.value = {"paused": False, "auto_submit": True, **values}


# ---------- retry, restore, delete and bulk can't resend ----------
def sent_job(service, state, *, n=1, submitting_event=False, status=None):
    """A job whose newest run ended in `state`, with an earlier stop before it."""
    job_id = seed_job(service, n, status=status or state)
    seed_run(service, job_id, state="failed", reason="Stopped before anything was sent")
    run_id = seed_run(service, job_id, state=state)
    if submitting_event:
        service.db.event(run_id, "submitting", "Commit step started", {})
    return job_id


def runs_of(service, job_id):
    with service.db.session() as s:
        return list(s.scalars(select(Run.id).where(Run.job_id == job_id)))


HISTORIES = [
    ("confirmed", False, "confirmed"),
    ("already_applied", False, "already_applied"),
    ("submitting", False, "submitting"),
    ("submission_unknown", False, "submission_unknown"),
    # Parked after its commit step started: the employer may have it.
    ("waiting_browser", True, "waiting_browser"),
    ("failed", True, "failed"),
]
PRESSES = {
    "apply": lambda c, j: c.post(f"/api/jobs/{j}/apply", json={}),
    "resume": lambda c, j: c.post(f"/api/jobs/{j}/resume", json={}),
    "queue": lambda c, j: c.post(f"/api/jobs/{j}/queue", json={"mode": "submit"}),
    "apply_many": lambda c, j: c.post("/api/jobs/apply-many", json={"job_ids": [j]}),
    "queue_add": lambda c, j: c.post(f"/api/queue/{j}/add", json={}),
    "queue_add_many": lambda c, j: c.post("/api/queue/add-many", json={"job_ids": [j]}),
    "queue_back": lambda c, j: c.post(f"/api/queue/{j}/back", json={}),
    "take_over": lambda c, j: c.post(f"/api/jobs/{j}/take-over", json={}),
    "approve_over": lambda c, j: c.post(f"/api/jobs/{j}/approve-over-limit", json={}),
    "approve_reserved": lambda c, j: c.post(f"/api/jobs/{j}/approve-reserved", json={"apply": True}),
}


@pytest.mark.parametrize("state,committed,status", HISTORIES, ids=[f"{h[0]}{'+submit' if h[1] else ''}" for h in HISTORIES])
@pytest.mark.parametrize("press", list(PRESSES))
def test_no_control_starts_a_job_that_was_or_may_have_been_sent(client, service, config, monkeypatch, state, committed, status, press):
    control(service)
    config.browser_cdp_url = "http://127.0.0.1:9"
    service.dispatch = AsyncMock()
    job_id = sent_job(service, state, submitting_event=committed, status=status)
    before = runs_of(service, job_id)
    PRESSES[press](client, job_id)
    # A job in line would start by itself on the next tick: it may not be put there either.
    with service.db.session() as s:
        waiting = s.get(Setting, "owner_apply:" + job_id)
    assert runs_of(service, job_id) == before, press
    assert not waiting, (press, waiting.value)
    assert not service.dispatch.await_count


@pytest.mark.parametrize("state", ["confirmed", "already_applied", "submission_unknown", "submitting"])
def test_restore_refuses_what_may_have_reached_the_employer_and_delete_keeps_the_record(client, service, state):
    job_id = sent_job(service, state)
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    with service.db.session() as s:
        assert s.get(Job, job_id).status == "archived"
    answer = client.post(f"/api/jobs/{job_id}/restore", json={})
    assert answer.status_code == 409 and "reached the employer" in answer.json()["detail"]
    with service.db.session() as s:
        assert s.get(Job, job_id).status == "archived"
    # The run is what stops a second application: removal keeps it.
    assert len(runs_of(service, job_id)) == 2


def test_restore_brings_back_a_job_nothing_was_sent_for_and_only_then(client, service):
    job_id = sent_job(service, "failed")
    client.delete(f"/api/jobs/{job_id}")
    answer = client.post(f"/api/jobs/{job_id}/restore", json={})
    assert answer.status_code == 200 and answer.json()["state"] == "ready"
    assert client.get("/api/archive").json()["applications"] == []


def test_a_parked_run_past_its_commit_step_stays_removed(client, service):
    job_id = sent_job(service, "failed", submitting_event=True, status="failed")
    client.delete(f"/api/jobs/{job_id}")
    assert client.post(f"/api/jobs/{job_id}/restore", json={}).status_code == 409


def test_apply_many_says_each_job_was_refused_and_starts_none(client, service):
    control(service)
    service.dispatch = AsyncMock()
    jobs = [sent_job(service, state, n=n) for n, state in enumerate(("confirmed", "already_applied", "submission_unknown"), 1)]
    results = client.post("/api/jobs/apply-many", json={"job_ids": jobs}).json()["results"]
    assert {r["outcome"] for r in results} <= {"already", "refused"}, results
    assert all(len(runs_of(service, j)) == 2 for j in jobs)


# ---------- one answer, every job asking ----------
def waiting(service, name, company, question, options=(), context=None, key=None, created="2026-10-30T00:00:00"):
    run_id = add_job(service, name, company=company, created=created)
    add_review(service, "q-" + name, run_id, question, options, key=key or "answers-v9:" + name, context=context or {})
    return run_id


def answer_of(service, review_id):
    with service.db.session() as s:
        return s.get(Review, review_id).answer


@pytest.mark.parametrize(
    "first,second",
    [
        # A tick box, which is never reused from saved answers.
        (("I agree to the terms of this employer's privacy notice", [], {"type": "checkbox", "caption": ""}),) * 2,
        # The same list in another order.
        (("Which shift do you prefer?", ["Day", "Night", "Either"], {}), ("Which shift do you prefer?", ["Either", "Day", "Night"], {})),
        # The same caption under the same printed question.
        (
            ("Please specify", [], {"caption": "What is your highest certification?"}),
            ("Please specify", [], {"caption": "What is your highest certification?"}),
        ),
        # The same words, dressed differently.
        (("Are you at least 18 years old?", ["Yes", "No"], {}), ("* Are you at least 18 years old? *", ["No", "Yes"], {})),
    ],
    ids=["tick box", "list in another order", "caption", "other dress"],
)
def test_one_answer_clears_every_job_asking_the_same_thing(client, service, first, second):
    control(service)
    waiting(service, "a", "Acme", first[0], first[1], first[2])
    waiting(service, "b", "Birch Labs", second[0], second[1], second[2])
    waiting(service, "c", "Cedar", second[0], second[1], second[2])
    reply = "Yes" if first[1] or first[2].get("type") == "checkbox" else "A truthful answer"
    if "Day" in first[1]:
        reply = "Day"
    assert client.post("/api/reviews/q-a", json={"answer": reply}).status_code == 200
    assert answer_of(service, "q-b") == reply
    assert answer_of(service, "q-c") == reply


def test_the_job_page_answering_one_job_clears_the_rest_past_the_hundred_needs_you_shows(client, service):
    control(service)
    for n in range(103):
        run_id = add_job(service, f"filler{n}", company=f"Filler {n}", created="2026-10-01T00:00:00")
        add_review(service, f"filler-q{n}", run_id, f"A question only filler {n} asks?", key=f"answers-v9:f{n}")
    waiting(service, "a", "Acme", "I agree to the terms", [], {"type": "checkbox"})
    waiting(service, "z", "Zed", "I agree to the terms", [], {"type": "checkbox"}, created="2026-10-31T00:00:00")
    assert client.post("/api/reviews/q-a", json={"answer": "Yes"}).status_code == 200
    assert answer_of(service, "q-z") == "Yes"
    # Nothing unrelated was touched.
    assert answer_of(service, "filler-q5") is None


def test_an_answer_tied_to_one_employer_stays_with_it(client, service):
    control(service)
    question = "Why do you want to join our team?"
    waiting(service, "a", "Acme", question)
    waiting(service, "b", "Birch Labs", question)
    assert client.post("/api/reviews/q-a", json={"answer": "Acme's work on maps."}).status_code == 200
    assert answer_of(service, "q-b") is None


def test_the_page_never_groups_an_employers_own_question_across_employers():
    for wording in ("Why do you want to join our team?", "Have you previously worked for this company?", "Describe your interest in this role."):
        assert employer_only(wording), wording
    assert not employer_only("Are you at least 18 years old?", ["Yes", "No"])
    assert present({"question": "Why do you want to join our team?", "options": [], "context": {}, "reason": ""})["employer_only"]


def test_a_question_no_one_could_read_is_not_the_same_as_another(client, service):
    control(service)
    waiting(service, "a", "Acme", "Three required Yes/No questions whose text wasn't captured", ["Yes", "No"])
    waiting(service, "b", "Birch Labs", "Three required Yes/No questions whose text wasn't captured", ["Yes", "No"])
    client.post("/api/reviews/q-a", json={"answer": "Yes"})
    assert answer_of(service, "q-b") is None


def test_several_picks_are_accepted_where_the_form_takes_several_and_refused_elsewhere(client, service):
    control(service)
    tools = ["Python", "SQL", "Tableau"]
    waiting(service, "a", "Acme", "Which tools have you used? Select all that apply.", tools)
    assert client.post("/api/reviews/q-a", json={"answer": "Python\nSQL"}).status_code == 200
    assert answer_of(service, "q-a") == "Python\nSQL"
    waiting(service, "b", "Birch Labs", "What is your favourite tool?", tools)
    refused = client.post("/api/reviews/q-b", json={"answer": "Python\nSQL"})
    assert refused.status_code == 400 and refused.json()["detail"] == "Choose one of the offered answers"
    waiting(service, "c", "Cedar", "Which tools have you used? Select all that apply.", tools, key="answers-v9:other")
    assert client.post("/api/reviews/q-c", json={"answer": "Python\nCobol"}).status_code == 400


def test_yes_or_no_that_says_multiple_takes_one_answer():
    question = "Do you speak multiple languages?"
    assert not takes_several(question, ["Yes", "No"], {})
    assert not present({"question": question, "options": ["Yes", "No"], "context": {}, "reason": ""})["multiple"]
    assert takes_several("Select all that apply", ["Red", "Blue"], {})
    assert takes_several("Colours", ["Yes", "No"], {"type": "checkboxgroup"})
    assert not fits_form("Yes\nNo", question, ["Yes", "No"], {})


def test_same_question_is_the_words_and_the_choices_in_any_order():
    assert same_question("Q?", ["Yes", "No"], {}) == same_question("Q?", ["No", "Yes"], {})
    assert same_question("Q?", ["Yes", "No"], {}) != same_question("Q?", ["Yes", "No", "Maybe"], {})
    assert same_question("Other words?", ["Yes", "No"], {}) != same_question("Q?", ["Yes", "No"], {})


# ---------- Take over ----------
def held(service, job_id, phase="yours"):
    with service.db.exclusive() as s:
        s.add(Setting(key=steer.KEY + job_id, value={"phase": phase, "previous_status": "needs_review", "approved": True}))
        s.add(Setting(key="job_control:" + job_id, value={"state": "paused", "steering": phase}))
        s.add(Setting(key="desktop_job:" + job_id, value={"target_id": "tab-1"}))


def tabs(monkeypatch, **fields):
    found = {"tab-1": {"id": "tab-1", "title": "Apply: Analyst", "url": "https://jobs.example.test/apply", **fields}}
    monkeypatch.setattr(steer, "_targets", lambda config: found)


@pytest.mark.parametrize("how", ["done", "back"])
async def test_neither_way_of_handing_back_runs_on_a_page_that_says_it_was_received(service, monkeypatch, how):
    job_id = seed_job(service, status="paused")
    seed_run(service, job_id, state="paused")
    held(service, job_id)
    tabs(monkeypatch, title="Thank you for applying")
    service.queue = AsyncMock()
    with pytest.raises(ValueError, match="confirmation"):
        await steer.finish(service, job_id, how)
    assert not service.queue.await_count
    assert steer.record(service.db, job_id)["phase"] == "yours"


async def test_done_hands_back_the_tab_and_address_the_person_had_open(service, monkeypatch):
    job_id = seed_job(service, status="paused")
    seed_run(service, job_id, state="paused")
    held(service, job_id)
    tabs(monkeypatch)
    service.queue = AsyncMock(return_value={"id": "next"})
    await steer.finish(service, job_id, "done")
    assert steer.record(service.db, job_id)["hands"] == {"target_id": "tab-1", "url": "https://jobs.example.test/apply"}
    with service.db.exclusive() as s:
        assert steer.hands_back(s, job_id) == {"target_id": "tab-1", "url": "https://jobs.example.test/apply"}
    with service.db.exclusive() as s:
        assert steer.hands_back(s, job_id) is None  # Once.


@pytest.mark.parametrize("how", ["done", "back"])
async def test_a_closed_tab_or_give_it_back_hands_back_nothing(service, monkeypatch, how):
    job_id = seed_job(service, status="paused")
    seed_run(service, job_id, state="paused")
    held(service, job_id)
    if how == "done":
        monkeypatch.setattr(steer, "_targets", lambda config: {})  # The person closed the tab.
    else:
        tabs(monkeypatch)
    service.queue = AsyncMock(return_value={"id": "next"})
    await steer.finish(service, job_id, how)
    with service.db.exclusive() as s:
        assert steer.hands_back(s, job_id) is None


async def test_an_opening_that_never_finished_has_a_way_out_and_one_still_going_does_not(service, monkeypatch):
    job_id = seed_job(service, status="paused")
    run_id = seed_run(service, job_id, state="running", mode="dry_run")
    held(service, job_id, phase="opening")
    with service.db.exclusive() as s:
        row = s.get(Setting, steer.KEY + job_id)
        row.value = {**row.value, "run_id": run_id, "at": steer.now()}
    assert steer.status(service, job_id)["stuck"] is False
    with pytest.raises(ValueError, match="still opening"):
        await steer.finish(service, job_id, "back")
    # A restart ended the run without handing it over.
    with service.db.exclusive() as s:
        s.get(Run, run_id).state = "waiting_browser"
    assert steer.status(service, job_id)["stuck"] is True
    with pytest.raises(ValueError, match="still opening"):
        await steer.finish(service, job_id, "done")  # Nothing was on the screen to keep.
    monkeypatch.setattr(steer, "_targets", lambda config: {})
    service.queue = AsyncMock(return_value={"id": "next"})
    assert (await steer.finish(service, job_id, "back"))["state"] == "queued"
    assert steer.record(service.db, job_id)["phase"] == "released"


def test_only_the_very_page_handed_back_counts_as_what_the_person_saw():
    handed = {"target_id": "tab-1", "url": "https://jobs.example.test/apply"}
    assert steer.owner_saw(handed, True, "tab-1", handed["url"])
    assert not steer.owner_saw(handed, False, "tab-1", handed["url"])  # Reopened from the job page or the saved route.
    assert not steer.owner_saw(handed, True, "tab-2", handed["url"])  # Another tab.
    assert not steer.owner_saw(handed, True, "tab-1", "https://jobs.example.test/apply/step2")  # The page moved on.
    assert not steer.owner_saw(True, True, "tab-1", handed["url"])  # A run queued before this rule named no page.
    assert not steer.owner_saw(None, True, "tab-1", handed["url"])


# ---------- plain words on every screen ----------
RAW = [
    "1 validation error for Profile\nemail\n  Input should be a valid string [type=string_type, input_value=1, input_type=int]\n    For further information visit https://errors.pydantic.dev/2.11/v/string_type",
    'Traceback (most recent call last):\n  File "jobpilot/forms.py", line 12, in fill\nKeyError: \'x\'',
    "Browser control failed at browser.py:441",
    "Worker error: TypeError",
    "Required field f0-ib9l29f2-jp30 repeatedly fails with failure_category=unsupported",
    "The application form was filled, but the latest fill results report auth_required=true.",
    "Repair agent: The run failed while uploading the resume: Locator.set_input_files timed out (15s) at upload_greenhouse",
    "Technical blocker on the sign-in dialog of example.test",
    "Page.goto: Timeout 30000ms exceeded.",
    "The Email field repeatedly rejects the profile email ([email omitted]) on [URL omitted].",
]


@pytest.mark.parametrize("raw", RAW)
def test_no_stored_reason_reads_as_code(raw):
    said = vocabulary.plain_sentence(raw)
    assert said and not vocabulary.is_developer_text(said), said
    assert "pydantic" not in said and "Traceback" not in said and ".py" not in said


def test_an_ordinary_sentence_stands():
    for said in (
        "The employer's verification email didn't arrive.",
        "The posting is closed or no longer available.",
        "This application requires a video recording of you, and the app can't record or upload video.",
    ):
        assert vocabulary.plain_sentence(said) == said
    assert vocabulary.plain_sentence("The application asks for a social security number; jobs that ask are dropped (owner rule)") == "The application asks for a social security number."
    # An id in brackets goes; the sentence stays.
    assert vocabulary.plain_sentence("The School menu (f0-sfuavz6t-jp22) has no options") == "The School menu has no options."
    assert vocabulary.plain_sentence("The '* Education' combobox (field f0-9eit1jxf-jp70) offers only Yes.") == "The '* Education' menu offers only Yes."


class Picky(BaseModel):
    salary: int


def test_a_validator_report_never_leaves_the_api(client, service, monkeypatch):
    async def refuses(review_id, answer):
        Picky.model_validate({"salary": "a lot"})

    monkeypatch.setattr(service, "resolve_review", refuses)
    answer = client.post("/api/reviews/any", json={"answer": "x"})
    assert answer.status_code == 400
    text = answer.json()["detail"]
    assert isinstance(text, str) and "pydantic" not in text and "validation error" not in text.lower()
    assert text == "Salary must be a whole number."


def test_a_refused_request_is_a_sentence_with_the_same_status(client):
    answer = client.post("/api/reviews/any", json={"answer": ""})
    assert answer.status_code == 422
    assert answer.json()["detail"] == "Answer is too short."


def test_library_errors_and_crashes_become_plain_words(app, config, service, monkeypatch):
    plain = TestClient(app, headers={"Authorization": "Bearer " + config.app_token}, raise_server_exceptions=False)
    for error, status in ((RuntimeError("Page.goto failed at browser.py:12"), 409), (ValueError("KeyError: 'x' in forms.py"), 400), (KeyError("packet"), 500)):
        async def fails(review_id, answer, error=error):
            raise error

        monkeypatch.setattr(service, "resolve_review", fails)
        answer = plain.post("/api/reviews/any", json={"answer": "x"})
        assert answer.status_code == status
        assert answer.json()["detail"] == vocabulary.PROBLEM
    # A sentence the app wrote stands, without the code in front of it, and a validator's report as text names its field.
    assert vocabulary.plain_error("Choose one of the offered answers") == "Choose one of the offered answers"
    assert vocabulary.plain_error("no_model: Add your AI key in Settings > Connections") == "Add your AI key in Settings > Connections"
    assert vocabulary.plain_error(RAW[0]) == "Email isn't valid."


def test_the_job_page_gets_plain_reports_and_a_plain_activity_and_the_record_keeps_the_rest(client, service):
    job_id = seed_job(service, status="failed")
    run_id = seed_run(service, job_id, state="failed", reason="Worker error: TypeError")
    service.db.set_setting(
        "failure_report:" + run_id,
        {
            "summary": "The run failed with a RuntimeError before any model calls.",
            "likely_cause": "Execution stopped at the browser startup step (browser.py:346:run).",
            "next_steps": ["Open the Chrome window by running: python -m jobpilot.cli browser", "AXIOM tries this again by itself."],
            "evidence": {"exception": "KeyError", "code_locations": ["forms.py:12"]},
        },
    )
    for kind, message in (
        ("model_call", "Model request did not complete"),
        ("settle_timing", "Application readiness wait"),
        ("queued", "Application queued"),
        ("submitting", "Commit step started; automatic retries are disabled"),
        ("finished", "Required field f0-abcd1234-jp3 stayed empty; control_failure"),
        ("field_error", "Could not commit ZIP Code"),
    ):
        service.db.event(run_id, kind, message, {})
    body = client.get(f"/api/runs/{run_id}").json()
    report = body["failure_report"]
    assert "evidence" not in report and report["likely_cause"] == ""
    assert report["next_steps"] == ["AXIOM tries this again by itself."]
    assert report["summary"] == ""  # Plainly, the same as the reason above it.
    said = [a["message"] for a in body["activity"]]
    assert said == ["Queued to start.", "Pressed Submit.", "Required field stayed empty."]
    record = client.get(f"/api/runs/{run_id}/record")
    assert record.status_code == 200 and "attachment" in record.headers["content-disposition"]
    assert "Model request did not complete" in record.text and "forms.py:12" in record.text


def test_a_video_the_form_requires_says_so_and_offers_no_answer_box():
    shown = present({"question": "Please upload a video introducing yourself", "options": [], "context": {"type": "file", "required": True}, "reason": ""})
    assert shown["kind"] == "cannot_automate" and "video" in shown["cannot_automate"]
