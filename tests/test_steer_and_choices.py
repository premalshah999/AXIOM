"""Take over's refusals and holds, one answer for a question on several applications, and the wording of pickers (2026-11-01).
No browser: the browser paths are tests/test_take_over.py and tests/test_choice_reading.py."""

import pytest

from jobpilot import owner_questions as oq, steer
from jobpilot.admission import QueueBlocked
from jobpilot.db import Job, Review, Run, Setting, now, record
from jobpilot.job_control import require_running


def make(service, state, *, status="needs_review", url="https://jobs.example.test/1"):
    with service.db.exclusive() as s:
        job = Job(identity=url, url=url, ats="greenhouse", company="ExampleSoft", title="Analyst", demo=False, status=status)
        s.add(job)
        s.flush()
        run = Run(job_id=job.id, state=state, mode="submit", started_at=now(), packet={"match_approved": True})
        s.add(run)
        s.flush()
        return job.id, run.id


async def test_take_over_is_refused_where_it_could_cause_harm(service):
    service.config.browser_cdp_url = "http://127.0.0.1:9"  # Never contacted: every refusal comes first.
    running, _ = make(service, "running", status="running", url="https://jobs.example.test/running")
    sent, _ = make(service, "confirmed", status="confirmed", url="https://jobs.example.test/sent")
    unsure, _ = make(service, "submission_unknown", status="submission_unknown", url="https://jobs.example.test/unsure")
    for job_id, words in ((running, "running"), (sent, "sent"), (unsure, "sent")):
        with pytest.raises(ValueError, match=words):
            await steer.start(service, job_id)
        assert not steer.record(service.db, job_id)  # Nothing was held, nothing paused.
    with pytest.raises(ValueError, match="not found|Job not found"):
        await steer.start(service, "0" * 32)


async def test_take_over_needs_the_apps_own_chrome(service):
    job_id, _ = make(service, "needs_review")
    service.config.browser_cdp_url = ""
    with pytest.raises(ValueError, match="own Chrome"):
        await steer.start(service, job_id)
    assert not steer.record(service.db, job_id)


def hold(service, job_id, phase):
    with service.db.exclusive() as s:
        s.add(Setting(key="job_control:" + job_id, value={"state": "paused", "at": now(), "previous_status": "needs_review", "steering": phase}))
        s.add(Setting(key=steer.KEY + job_id, value={"phase": phase, "at": now(), "previous_status": "needs_review", "approved": True}))


def test_a_held_job_lets_through_only_the_one_dry_run_that_rebuilds_its_page(service):
    job_id, _ = make(service, "needs_review")
    hold(service, job_id, "opening")
    with service.db.session() as s:
        require_running(s, job_id, "dry_run")  # The rebuilding run.
        for refused in ("submit", None):
            with pytest.raises(QueueBlocked):
                require_running(s, job_id, refused)
        assert steer.opening(s, job_id) and steer.holding(s, job_id)
    with service.db.exclusive() as s:
        s.get(Setting, "job_control:" + job_id).value = {"state": "paused", "steering": "yours"}
        s.get(Setting, steer.KEY + job_id).value = {"phase": "yours"}
    with service.db.session() as s:
        with pytest.raises(QueueBlocked):
            require_running(s, job_id, "dry_run")  # Once the page is the owner's, nothing starts.
        assert steer.holding(s, job_id) and not steer.opening(s, job_id)
    assert set(steer.steered_jobs(service.db)) == {job_id}


async def test_nothing_is_taken_back_unless_the_owner_holds_it(service):
    job_id, _ = make(service, "needs_review")
    for how in ("done", "back"):
        with pytest.raises(ValueError, match="don't have"):
            await steer.finish(service, job_id, how)
    with pytest.raises(ValueError, match="don't have"):
        steer.submitted(service, job_id)
    hold(service, job_id, "opening")
    with pytest.raises(ValueError, match="still opening"):
        await steer.finish(service, job_id, "done")


def test_the_hand_back_is_used_by_one_run_only(service):
    job_id, _ = make(service, "paused", status="paused")
    hold(service, job_id, "yours")
    steer.release(service, job_id, hands=True)
    with service.db.exclusive() as s:
        assert steer.hands_back(s, job_id) is True
        assert steer.hands_back(s, job_id) is False
    with service.db.session() as s:
        assert s.get(Job, job_id).status == "ready"  # The application was approved before it stopped.
        assert s.get(Setting, "job_control:" + job_id).value["state"] == "active"
        assert not steer.holding(s, job_id)


def question_on(service, text, jobs):
    ids = []
    with service.db.exclusive() as s:
        for n in range(jobs):
            job = Job(identity=f"https://jobs.example.test/q{n}", url=f"https://jobs.example.test/q{n}", ats="greenhouse",
                      company="ExampleSoft", title=f"Analyst {n}", demo=False, status="needs_review")
            s.add(job)
            s.flush()
            run = Run(job_id=job.id, state="needs_review", mode="submit", started_at=now(), packet={})
            s.add(run)
            s.flush()
            review = Review(run_id=run.id, question=text, options=[], key=f"field-{n}", reason="", context={"type": "text"})
            s.add(review)
            s.flush()
            ids.append(review.id)
    return ids


def test_one_request_answers_a_question_on_every_application_that_asks_it(client, service):
    ids = question_on(service, "What is your favourite colour?", 4)
    answered = client.post("/api/reviews/batch", json={"ids": ids, "answer": "Green"})
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["sent"] == 4 and body["of"] == 4 and "failed" not in body
    with service.db.session() as s:
        assert [r.answer for r in s.scalars(__import__("sqlalchemy").select(Review).where(Review.id.in_(ids)))] == ["Green"] * 4
    # Sent again (a second tab, a double click): every row is already answered, which is a success, not an error.
    again = client.post("/api/reviews/batch", json={"ids": ids, "answer": "Green"})
    assert again.status_code == 200 and again.json()["sent"] == 4
    nothing = client.post("/api/reviews/batch", json={"ids": ["0" * 32], "answer": "Green"})
    assert nothing.status_code == 200 and nothing.json()["sent"] == 1  # A question that no longer exists needs no answer.


def test_a_list_marked_partial_but_holding_yes_and_no_is_asked_as_yes_and_no():
    ctx = {"type": "combobox", "options_partial": True}
    assert oq.free_text_allowed(["Yes", "No"], ctx) is False
    assert oq.present({"question": "Are you a collector?", "options": ["Yes", "No"], "context": ctx})["allow_other"] is False
    # A real search list stays typeable.
    assert oq.free_text_allowed(["Springfield", "Shelbyville"], ctx) is True


def test_a_search_box_says_what_to_type_and_is_never_an_empty_dropdown():
    ctx = {"type": "combobox", "options_partial": True, "search_only": True}
    shown = oq.present({"question": "Which city do you live in?", "options": [], "context": ctx})
    assert shown["kind"] == "text" and "type the start of your answer" in shown["requirement"]
    assert oq.context_from({"type": "combobox", "context": {"search_only": True}}).get("search_only") is True
