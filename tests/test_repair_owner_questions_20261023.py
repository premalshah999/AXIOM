"""The repair agent's owner questions (owner 2026-10-23: "a lot of error and debugging texts from telegram").

In three days it sent 20 questions: 10 asked for an employer-account password (8 for one SuccessFactors tenant, each
worded differently, so none was recognised as already asked), and one described the app ("The dropdown options
couldn't be read, so I can't map your saved answers to exact choices"). An answered repair question is not a form
field: no later field has its key, so the answer now reaches the navigator as the job's instruction.
"""

import pytest

from jobpilot.db import Review, Run
from jobpilot.repair_agent import OwnerQuestion, RepairPlan, repair_run
from test_repair_agent import seed


async def plan_with(monkeypatch, question, options=()):
    plan = RepairPlan(
        diagnosis="A required field is empty.",
        category="missing_fact",
        owner_question=OwnerQuestion(question=question, options=list(options)),
    )

    async def fake_structured(*args, **kwargs):
        return plan

    monkeypatch.setattr("jobpilot.models.structured", fake_structured)


@pytest.mark.parametrize(
    "question, options",
    [
        ("What should the SuccessFactors credential be for this employer?", []),
        ("Which option applies to you?", ["I will provide my sign-in details", "Skip this job"]),
        ("The dropdown options couldn't be read, so I can't map your saved answers to exact choices. Confirm them.", []),
    ],
)
async def test_credential_and_app_questions_never_reach_the_owner(service, tmp_path, monkeypatch, question, options):
    run_id, _, _ = seed(service, tmp_path)
    await plan_with(monkeypatch, question, options)
    summary = await repair_run(service, run_id)
    assert summary["outcome"] != "asked_owner"
    with service.db.session() as s:
        assert s.query(Review).filter(Review.run_id == run_id).count() == 0


async def test_the_same_question_in_other_words_is_not_asked_twice_for_a_job(service, tmp_path, monkeypatch):
    run_id, job_id, _ = seed(service, tmp_path)
    await plan_with(monkeypatch, "Are you a beneficiary of the Mandatory Employment of Disabled Workers scheme?",
                    ["Yes", "No"])
    assert (await repair_run(service, run_id))["outcome"] == "asked_owner"
    with service.db.exclusive() as s:
        s.add(Run(job_id=job_id, state="waiting_browser", mode="submit", packet={}, reason="Still blocked",
                  created_at="2999-01-01T00:00:00"))
        later = s.query(Run).filter(Run.job_id == job_id, Run.reason == "Still blocked").one().id
    (service.config.data_dir / "runs" / later / "steps").mkdir(parents=True)
    service.db.set_setting("repair_attempts:" + job_id, {})
    await plan_with(monkeypatch, "Are you a beneficiary of the Mandatory Employment of Disabled Workers scheme? "
                                 "(This is required.)", ["Yes", "No"])
    assert (await repair_run(service, later))["outcome"] != "asked_owner"


async def test_an_answered_repair_question_reaches_the_navigator(service, tmp_path, monkeypatch):
    run_id, job_id, _ = seed(service, tmp_path)
    await plan_with(monkeypatch, "Do you identify as a military spouse?", ["Yes", "No", "I prefer not to answer"])
    await repair_run(service, run_id)
    with service.db.session() as s:
        review = s.query(Review).filter(Review.run_id == run_id).one()
        assert review.context == {"source": "repair"} and s.get(Run, run_id).state == "needs_review"
    result = await service.resolve_review(review.id, "No")
    assert result["message"].startswith("Saved.")
    directive = service.db.get_setting("application_directive:" + job_id)
    assert "military spouse" in directive["text"] and directive["text"].endswith(": No")


async def test_a_question_the_profile_answers_is_never_put_to_the_owner(service, tmp_path, monkeypatch):
    """Flatirons (2026-10-29): the agent's own diagnosis said the saved profile holds the U.S.-citizen answer, yet it asked.
    The radio group not committing is a control failure; the saved fact answers the question."""
    run_id, _, _ = seed(service, tmp_path)
    stored = service.db.get_setting("profile", {}) or {}
    service.db.set_setting("profile", {**stored, "facts": {**(stored.get("facts") or {}), "us_citizen": False}})
    await plan_with(monkeypatch, "Are you a U.S. Citizen? Being a U.S. citizen is a requirement for this role.",
                    ["Yes", "No"])
    summary = await repair_run(service, run_id)
    assert summary["outcome"] != "asked_owner"
    assert any("saved profile answers it" in why for _, why in summary["rejected"])
    with service.db.session() as s:
        assert s.query(Review).filter(Review.run_id == run_id).count() == 0


async def test_a_bare_caption_from_the_repair_agent_is_not_asked(service, tmp_path, monkeypatch):
    run_id, _, _ = seed(service, tmp_path)
    await plan_with(monkeypatch, "None of the Above")
    assert (await repair_run(service, run_id))["outcome"] != "asked_owner"
