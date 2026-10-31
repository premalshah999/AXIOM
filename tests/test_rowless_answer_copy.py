"""A rowless answer fills no history row's date (4fe4eac6, run 6d2635b1, 2026-10).

A rowless "End Date Month" answered one form's field and, sharing its key, was copied onto a work row of another form,
where it became that row's date. An answer with no row names no row, and a bare date caption is never carried to another
form's field; every other question is still copied to the same question elsewhere."""

from jobpilot.db import Review

from test_owner_questions_20261023 import add_job, add_review


def test_a_rowless_answer_does_not_fill_a_history_rows_date(service):
    """A rowless "End Date Month" answered one form's field and, sharing its key, was copied onto a work row (4fe4eac6,
    run 6d2635b1, 2026-10)."""
    first, second = add_job(service, "form-a"), add_job(service, "form-b", company="Initech")
    shared = "answers-v9:end-month"
    add_review(service, "rowless", first, "End Date Month", key=shared, options=["Jan", "Feb", "Nov"])
    add_review(service, "row", second, "End Date Month", key=shared, options=["Jan", "Feb", "Nov"],
               context={"history_kind": "work", "history_identity": {"company": "Globex"}})
    add_review(service, "other-rowless", second, "End Date Month", key=shared, options=["Jan", "Feb", "Nov"])
    service.answer_review("rowless", "Nov")
    with service.db.session() as s:
        assert s.get(Review, "row").answer is None
        assert s.get(Review, "other-rowless").answer is None  # a bare date caption names no field of another form


def test_a_rowless_question_is_still_copied_to_the_same_question_elsewhere(service):
    first, second = add_job(service, "form-a"), add_job(service, "form-b", company="Initech")
    shared = "answers-v9:relocate"
    add_review(service, "a", first, "Are you willing to relocate?", key=shared, options=["Yes", "No"])
    add_review(service, "b", second, "Are you willing to relocate?", key=shared, options=["Yes", "No"])
    service.answer_review("a", "No")
    with service.db.session() as s:
        assert s.get(Review, "b").answer == "No"
