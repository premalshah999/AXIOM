"""Owner 2026-10-29: ~80% of the questions in "Needs you" were incomplete, irrelevant or inferable. One question once,
an optional field never, a question with no question not at all, and a row's dates from the row's own profile entry."""

from jobpilot.db import Review, Run
from jobpilot.schemas import EducationEntry, Profile, WorkEntry

from test_owner_questions_20261023 import add_job, add_review
from test_waiting_email_state import seed


def profile():
    return Profile(
        first_name="Alex",
        work=[WorkEntry(company="ExampleSoft", title="Engineer", location="Ann Arbor, MI", start="2024-01", current=True),
              WorkEntry(company="Globex Labs", title="Analyst", start="2021-06", end="2022-05")],
        education=[EducationEntry(school="Example University", degree="MS", start="2022-08", end="2024-05")],
    ).model_dump()


def item(question, key, **more):
    return {"question": question, "options": [], "key": key, "reason": "x", "blocker_type": "missing_fact",
            "type": "text", "required": True, "field_id": more.pop("field_id", key), "scope": {}, **more}


def test_completion_asks_each_question_once_and_never_an_optional_one(service):
    run_id = seed(service, "once")
    reviews = [
        item("Other (free text) about your fit for the role", "k1", field_id="a"),
        item("Other (free text) about your fit for the role", "k1", field_id="b"),
        item("Which of these tools do you use daily?", "k2", required=False),
        item("What is the name of your high school?", "k3"),
    ]
    service.complete(run_id, {"state": "needs_review", "reason": "Required profile information is missing",
                              "reviews": reviews}, 30.0)
    with service.db.session() as s:
        asked = sorted(r.question for r in s.query(Review).where(Review.run_id == run_id))
    assert asked == ["Other (free text) about your fit for the role", "What is the name of your high school?"]


def test_a_second_employers_rows_stay_separate_questions(service):
    run_id = seed(service, "rows")
    ctx = lambda company: {"history_kind": "work", "history_identity": {"company": company}}  # noqa: E731
    reviews = [
        item("Supervisor Name", "k", field_id="a", context=ctx("ExampleSoft")),
        item("Supervisor Name", "k", field_id="b", context=ctx("Globex Labs")),
        item("Supervisor Name", "k", field_id="c", context=ctx("Globex Labs")),
    ]
    service.complete(run_id, {"state": "needs_review", "reviews": reviews}, 30.0)
    with service.db.session() as s:
        assert s.query(Review).where(Review.run_id == run_id).count() == 2


async def test_the_sweep_merges_duplicates_and_answers_a_rows_dates_from_the_row(service):
    jobs = {name: add_job(service, name) for name in ("dups", "row", "nocaption", "caption")}
    with service.db.exclusive() as s:
        for run_id in jobs.values():
            s.get(Run, run_id).packet = {"profile": profile()}
    add_review(service, "d1", jobs["dups"], "Why do you want this job?", key="same")
    add_review(service, "d2", jobs["dups"], "Why do you want this job?", key="same")
    row = {"type": "text", "history_kind": "work", "history_identity": {"company": "Globex Labs"},
           "widget": "date-text", "date_format": "mdy", "required": True}
    add_review(service, "r1", jobs["row"], "Start & end date", key="k-start", context={**row, "slot": 0})
    add_review(service, "r2", jobs["row"], "Start & end date", key="k-end", context={**row, "slot": 1})
    add_review(service, "n1", jobs["nocaption"], "None of the Above", context={"type": "checkbox"})
    add_review(service, "c1", jobs["caption"], "None of the Above", context={
        "type": "checkbox", "caption": "Have you received any of the following benefits in the past year?"})
    outcome = await service.revalidate_open_questions()
    assert outcome["merged"] == 1 and outcome["recovered"] == 1
    with service.db.session() as s:
        assert sorted(r.id for r in s.query(Review).where(Review.run_id == jobs["dups"])) in (["d1"], ["d2"])
        assert s.get(Run, jobs["nocaption"]).state == "waiting_browser"
        assert s.get(Run, jobs["caption"]).state == "waiting_answer"  # a real question now, shown with its caption
        answered = (s.get(Review, "r1").answer, s.get(Review, "r2").answer)
    # Both boxes read "Start & end date": the first is the row's start, the second its end (TikTok, 2026-10-29).
    assert answered == ("2021-06-01", "2022-05-01")  # the widget formats it
