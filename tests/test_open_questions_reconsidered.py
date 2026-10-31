"""Saving something the owner can answer from frees the open questions it answers (2026-10-31).

"A lot of needs answer applications are just stuck", "never ask me what you can answer". A saved questionnaire reached the
next run but the waiting question stayed open until the app restarted, and the check read the run's own copy of the
profile, taken before the owner saved it. The answer resumes the application with a fresh try, as an Apply click does."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from jobpilot.db import Event, Job, Review, Run, Setting


def seed_waiting(service, question, options, starts=3, packet_profile=None):
    """A job whose newest run waits on one question, with `starts` runs already spent."""
    old = datetime.now(UTC) - timedelta(hours=3)
    with service.db.exclusive() as s:
        s.get(Setting, "control").value = {"paused": False, "auto_submit": True}
        s.add(Setting(key="application_budget", value={"max_seconds": None}))
        job = Job(identity="reuse-1", url="https://acme.example/jobs/1", company="Acme", title="Data Analyst",
                  ats="custom", status="waiting_answer")
        s.add(job)
        s.flush()
        for i in range(starts):
            asking = i == starts - 1
            stamp = (old + timedelta(minutes=i)).isoformat()
            packet = {"match_approved": True, **({"profile": packet_profile} if packet_profile is not None else {})}
            run = Run(job_id=job.id, state="waiting_answer" if asking else "waiting_browser", mode="submit",
                      packet=packet, started_at=stamp, created_at=stamp, reason="Required profile information is missing")
            s.add(run)
        s.flush()
        review = Review(id="open-q", run_id=run.id, question=question, key="answers-v9:open-q", options=options,
                        context={}, reason="Required profile information is missing")
        s.add(review)
        s.flush()
        return job.id, run.id


def stub_queue(service, monkeypatch):
    from jobpilot.application_budget import require_allowance

    calls = []

    async def queue(job_id, mode, resume_id=None, **kwargs):
        calls.append(job_id)
        with service.db.session() as s:
            require_allowance(s, job_id)
        return {"id": "fresh-run"}

    monkeypatch.setattr(service, "queue", queue)
    return calls


def events(service, run_id, kind):
    with service.db.session() as s:
        return list(s.scalars(select(Event).where(Event.run_id == run_id, Event.kind == kind)))


def test_saving_a_questionnaire_frees_the_question_it_answers_and_resumes_the_application(client, service, monkeypatch):
    """The saved fact reached the next run but the waiting question stayed open until the app restarted."""
    job_id, run_id = seed_waiting(service, "Are you willing to relocate?", ["Yes", "No"],
                                  packet_profile={"first_name": "Alex"})  # the run's own copy lacks the fact
    service.db.set_setting("profile", {"first_name": "Alex", "facts": {}})
    calls = stub_queue(service, monkeypatch)
    saved = client.put("/api/knowledge/questionnaires/faq", json={"answers": {"willing_to_relocate": False}})
    assert saved.status_code == 200, saved.text
    with service.db.session() as s:
        assert s.get(Review, "open-q").answer == "No"
    # The three starts were spent: the answer earned a fresh try, as an Apply click does.
    assert calls and len(events(service, run_id, "fresh_try")) == 1
    assert service.db.get_setting("application_budget_reset:" + job_id)["answer_grants"] == 1


async def test_an_unrelated_save_leaves_questions_open_and_runs_waiting(service, monkeypatch):
    seed_waiting(service, "Do you hold a valid passport from a country on our restricted list?", ["Yes", "No"])
    service.db.set_setting("profile", {"first_name": "Alex", "facts": {"willing_to_relocate": False}})
    calls = stub_queue(service, monkeypatch)
    outcome = await service.reconsider_open_questions()
    assert outcome["answered"] == 0 and not calls
    with service.db.session() as s:
        assert s.get(Review, "open-q").answer is None


async def test_a_saved_expectation_answers_a_waiting_compensation_question(service, monkeypatch):
    """Flagged "unsupported" while the profile held a salary expectation (2026-10-12..27); the rule is in place, so a
    waiting number box takes the saved expectation and the application resumes."""
    seed_waiting(service, "What are your current compensation requirements? (Enter a number, e.g. annual salary)", [],
                 packet_profile={"first_name": "Alex"})
    with service.db.exclusive() as s:
        s.get(Review, "open-q").context = {"type": "number"}
    service.db.set_setting("profile", {"first_name": "Alex", "salary_expectation": "105000"})
    calls = stub_queue(service, monkeypatch)
    await service.reconsider_open_questions()
    with service.db.session() as s:
        assert s.get(Review, "open-q").answer == "105000"
    assert calls


async def test_a_pass_requested_during_a_pass_runs_once_more_and_passes_never_overlap(service, monkeypatch):
    seen = []

    async def settle():
        seen.append("settle")
        if len(seen) == 1:
            await service.reconsider_open_questions()  # a save arrives mid-pass: it only asks for another pass
        return 0

    async def revalidate():
        seen.append("revalidate")
        return {"closed": 0, "recovered": 0, "answered": 0, "merged": 0}

    monkeypatch.setattr(service, "settle_open_questions", settle)
    monkeypatch.setattr(service, "revalidate_open_questions", revalidate)
    await service.reconsider_open_questions()
    assert seen == ["settle", "revalidate", "settle", "revalidate"]
    assert service._reconsidering is False
