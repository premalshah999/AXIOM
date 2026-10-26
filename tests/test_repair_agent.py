"""Repair agent (2026-10-17): a failed run's evidence goes to the navigator model, and its plan is applied as checked
data (answer overrides, a navigator instruction, an owner question, an uncertain submission's outcome), then the job
is retried. The owner asked for this so production failures fix themselves instead of waiting for a person."""

import json

import pytest

from jobpilot import repair_agent
from jobpilot.answers import Resolver
from jobpilot.db import Event, Job, Review, Run
from jobpilot.repair_agent import FieldFix, OwnerQuestion, RepairPlan, check_fixes, repair_run
from jobpilot.schemas import Profile

PROFILE = {
    "first_name": "Alex",
    "last_name": "Rivera",
    "address": {"city": "Springfield", "state": "MD", "country": "United States"},
    "education": [{"school": "University of Michigan", "degree": "Master of Science", "gpa": "3.9/4.0"}],
    "facts": {"metro_region": "Towson"},
}
STEP = {
    "url": "https://acme.taleo.net/careersection/careersection/candidateacquisition/flow.jsf",
    "headings": ["Errors occurred on this page", "Education"],
    "errors": ['The field "GPA" contains an invalid value.'],
    "fields": [
        {"label": "GPA", "type": "text", "required": False, "filled": True, "error": "", "options": []},
        {"label": "Region", "type": "select", "required": False, "filled": False, "error": "",
         "options": ["Not Specified", "Towson", "Rockville"]},
        {"label": "Have you ever been convicted of a felony?", "type": "select", "required": True, "filled": False,
         "error": "", "options": ["Yes", "No"]},
    ],
    "controls": ["Save and Continue", "Save as Draft"],
}


def seed(service, tmp_path, state="waiting_browser", reason="The browser did not advance within its recovery budget"):
    service.db.set_setting("profile", PROFILE)
    with service.db.exclusive() as s:
        job = Job(identity=f"rp-{state}-{reason[:8]}", url="https://acme.taleo.net/careersection/ex/jobdetail.ftl?job=1",
                  company="Acme", title="AI Specialist", ats="taleo")
        s.add(job)
        s.flush()
        run = Run(job_id=job.id, state=state, mode="submit", packet={}, reason=reason)
        s.add(run)
        s.flush()
        s.add(Event(run_id=run.id, kind="agent_step", message="Browser decision 1", data={"action": "fill"}))
        run_id, job_id = run.id, job.id
    folder = service.config.data_dir / "runs" / run_id
    (folder / "steps").mkdir(parents=True)
    (folder / "steps" / "01-form.json").write_text(json.dumps(STEP))
    (folder / "answers.json").write_text(json.dumps({"a": {"label": "GPA", "answer": "3.9/4.0"}}))
    return run_id, job_id, folder


def test_fixes_are_checked_against_the_page_and_the_applicants_own_values(service, tmp_path):
    run_id, _, _ = seed(service, tmp_path)
    profile = Profile.model_validate(PROFILE)
    ev = repair_agent.evidence(service.db, service.config.data_dir, run_id, profile)
    plan = RepairPlan(
        diagnosis="GPA must be numeric; Region needs a value",
        field_fixes=[
            FieldFix(label="GPA", value="3.9"),
            FieldFix(label="Region", value="Towson"),
            FieldFix(label="Region", value="Rockville"),
            FieldFix(label="Have you ever been convicted of a felony?", value="No"),
            FieldFix(label="Password", value="hunter2"),
            FieldFix(label="Favorite color", value="Blue"),
        ],
    )
    accepted, rejected = check_fixes(plan, ev, profile)
    assert [(f.label, f.value) for f in accepted] == [("GPA", "3.9"), ("Region", "Towson")]
    assert {label for label, _ in rejected} == {
        "Region",
        "Have you ever been convicted of a felony?",
        "Password",
        "Favorite color",
    }


async def test_a_failed_run_is_repaired_and_retried(service, tmp_path, monkeypatch):
    run_id, job_id, _ = seed(service, tmp_path)
    plan = RepairPlan(
        diagnosis='Taleo rejects "3.9/4.0" in GPA; it takes the number.',
        category="field_value",
        field_fixes=[FieldFix(label="GPA", value="3.9")],
        navigator_instruction="Type 3.9 in GPA, then Save and Continue.",
        retry=True,
    )

    async def fake_structured(config, db, output_type, instructions, prompt, *args, **kwargs):
        evidence = json.loads(prompt)
        assert evidence["page"]["errors"] == ['The field "GPA" contains an invalid value.']
        assert output_type is RepairPlan
        return plan

    resumed = []

    async def fake_manage(service, job, action, **kwargs):
        resumed.append((job, action))
        return {"run_id": "next-run"}

    monkeypatch.setattr("jobpilot.models.structured", fake_structured)
    monkeypatch.setattr("jobpilot.job_control.manage", fake_manage)
    summary = await repair_run(service, run_id)
    assert summary["outcome"] == "retried" and resumed == [(job_id, "resume")]
    assert service.db.get_setting("repair_directive:" + job_id)["text"].startswith("Type 3.9")
    resolver = Resolver(Profile.model_validate(PROFILE), service.db, service.config, job={"id": job_id})
    assert resolver.local({"id": "g", "label": "GPA", "type": "text", "options": []}).value == "3.9"
    with service.db.session() as s:
        assert s.query(Event).filter(Event.run_id == run_id, Event.kind == "repair_agent").count() == 1


async def test_a_missing_personal_fact_becomes_an_owner_question(service, tmp_path, monkeypatch):
    run_id, job_id, _ = seed(service, tmp_path)
    plan = RepairPlan(
        diagnosis="The page requires a conviction answer that is not saved.",
        category="missing_fact",
        owner_question=OwnerQuestion(question="Have you ever been convicted of a felony?", options=["Yes", "No"]),
    )

    async def fake_structured(*args, **kwargs):
        return plan

    monkeypatch.setattr("jobpilot.models.structured", fake_structured)
    summary = await repair_run(service, run_id)
    assert summary["outcome"] == "asked_owner"
    with service.db.session() as s:
        review = s.query(Review).filter(Review.run_id == run_id).one()
        assert review.options == ["Yes", "No"] and s.get(Run, run_id).state == "needs_review"


async def test_an_uncertain_submission_is_confirmed_from_its_final_page(service, tmp_path, monkeypatch):
    run_id, job_id, folder = seed(service, tmp_path, state="submission_unknown", reason="Submission attempted")
    with service.db.exclusive() as s:
        s.add(Event(run_id=run_id, kind="submitting", message="Commit step started"))
    (folder / "final_outline.txt").write_text("div.empty-title: Thank you for your application, Alex!\nspan: Track your application")
    plan = RepairPlan(
        diagnosis="The final page acknowledges the application.",
        category="submission",
        submission="confirmed",
        evidence_quote="Thank you for your application, Alex!",
    )

    async def fake_structured(*args, **kwargs):
        return plan

    monkeypatch.setattr("jobpilot.models.structured", fake_structured)
    summary = await repair_run(service, run_id)
    assert summary["outcome"] == "confirmed"
    with service.db.session() as s:
        assert s.get(Run, run_id).state == "confirmed"


async def test_unrepairable_failures_and_the_daily_budget_are_respected(service, tmp_path, monkeypatch):
    calls = []

    async def fake_structured(*args, **kwargs):
        calls.append(1)
        return RepairPlan(diagnosis="nothing to change")

    monkeypatch.setattr("jobpilot.models.structured", fake_structured)
    captcha_run, _, _ = seed(service, tmp_path, reason="Website security check (CAPTCHA) was not accepted")
    assert (await repair_run(service, captcha_run))["skipped"] == "not repairable"
    run_id, job_id, _ = seed(service, tmp_path, reason="The browser did not advance within its recovery budget twice")
    await repair_run(service, run_id)
    await repair_run(service, run_id)
    assert (await repair_run(service, run_id))["skipped"] == "daily repair budget used"
    assert len(calls) == 2


@pytest.mark.parametrize("state", ["confirmed", "closed"])
async def test_successful_or_final_runs_are_never_touched(service, tmp_path, state):
    run_id, _, _ = seed(service, tmp_path, state=state, reason="done " + state)
    assert (await repair_run(service, run_id))["skipped"] == "not repairable"


async def test_a_run_already_waiting_on_owner_answers_is_left_alone(service, tmp_path):
    run_id, _, _ = seed(service, tmp_path, state="needs_review", reason="Needs your answer")
    with service.db.exclusive() as s:
        s.add(Review(run_id=run_id, question="Region", options=["Towson"], key="k", reason=""))
    assert (await repair_run(service, run_id))["skipped"] == "waiting on the owner"


async def test_no_retry_storms(service, tmp_path, monkeypatch):
    plan = RepairPlan(diagnosis="Click Next instead", navigator_instruction="Click Next.", retry=True)

    async def fake_structured(*args, **kwargs):
        return plan

    resumed = []

    async def fake_manage(service, job, action, **kwargs):
        resumed.append(job)
        return {"run_id": "next"}

    monkeypatch.setattr("jobpilot.models.structured", fake_structured)
    monkeypatch.setattr("jobpilot.job_control.manage", fake_manage)
    run_id, job_id, _ = seed(service, tmp_path, reason="Browser control failed at forms.py:3919; tab retained")
    service.db.set_setting("draft_recovery:" + job_id, {"scheduled": True, "source_run_id": run_id})
    assert (await repair_run(service, run_id))["outcome"] == "fix_applied" and not resumed

    run2, job2, _ = seed(service, tmp_path, reason="Control failed at forms.py:3919; tab retained again")
    with service.db.exclusive() as s:
        older = Run(job_id=job2, state="waiting_browser", mode="submit", packet={},
                    reason="Control failed at forms.py:3919; tab retained again", created_at="2000-01-01T00:00:00")
        s.add(older)
    assert (await repair_run(service, run2))["outcome"] == "stopped" and not resumed


async def test_a_posting_the_platform_no_longer_lists_is_closed_without_a_model_call(service, tmp_path, monkeypatch):
    # Gas South and Coherent Solutions (2026-10-26): the agent saw the role missing from the board but could only
    # decline to retry, so each sat in Repairing. The public jobs API settles it.
    run_id, job_id, _ = seed(service, tmp_path)
    monkeypatch.setattr(service.config, "allow_private_urls", False, raising=False)

    async def gone(url, company=""):
        return "The posting is closed: Greenhouse no longer lists it, so nothing was submitted"

    async def no_model(*args, **kwargs):
        raise AssertionError("no model call for a closed posting")

    monkeypatch.setattr("jobpilot.browser.posting_gone", gone)
    monkeypatch.setattr("jobpilot.models.structured", no_model)
    assert (await repair_run(service, run_id))["outcome"] == "closed"
    with service.db.session() as s:
        assert s.get(Run, run_id).state == "closed" and s.get(Job, job_id).status == "closed"
