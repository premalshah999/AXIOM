"""The flows around a tailored resume, on synthetic data: what a failure says, what the Settings switch moves, and when a resume can no longer change."""

import asyncio

import pytest
from test_tailoring_normal import POSTING, add_job, needs_tex, plan_of, source_doc, stub, tailored

from jobpilot.admission import QueueBlocked
from jobpilot.db import Job, Resume
from jobpilot.features.resumes import choice, editor, modes, normal, pipeline
from jobpilot.features.resumes.compiler import compile_pdf
from jobpilot.features.resumes.editor import Bullet, Entry, Section, render
from jobpilot.features.resumes.tables import RsTailorChoice
from jobpilot.features.resumes.validate import parse_check

RAW_WORDS = ("project_quality", "_", "Exceeded", "status_code", "pdflatex", "Error", "validation", "None")


@pytest.mark.parametrize(
    "error, fix",
    [
        ({"code": "no_source_resume"}, "resumes"),
        ({"code": "no_model", "detail": "x"}, "connections"),
        ({"code": "no_source"}, "profile"),
        ({"code": "missing_description", "detail": "The posting text has not been fetched yet"}, None),
        ({"code": "timeout", "detail": "Tailoring exceeded its time limit"}, None),
        ({"code": "compiler_missing", "detail": "pdflatex is not installed on this host"}, None),
        ({"code": "compile_error", "detail": "compile_error: Undefined control sequence"}, None),
        ({"code": "tailoring_error", "detail": "project_quality: Use 2,2,2 or 2,2,3 proposal bullets; A: name an actual posting tool"}, None),
        ({"code": "tailoring_error", "detail": "Exceeded maximum retries (1) for output validation"}, None),
        ({"code": "tailoring_error", "detail": "no_model: Add your AI key in Settings > Connections"}, "connections"),
        ({"code": "tailoring_error", "detail": "status_code: 401, model_name: x, body: {}"}, "connections"),
        ({"code": "tailoring_error", "detail": "status_code: 402, model_name: x, body: {}"}, "connections"),
        ({"code": "tailoring_error", "detail": "Daily model budget reached"}, "applying"),
        ({"code": "tailoring_error", "detail": "AnswerModelTimeout"}, None),
        ({"code": "tailoring_error", "detail": "AnswerModelConnectionError"}, None),
        ({"code": "tailoring_error", "detail": "KeyError"}, None),
        ({}, None),
    ],
)
def test_a_failure_says_what_to_do_and_never_the_raw_error(error, fix):
    said = choice.plain_failure(error)
    assert said["fix"] == fix
    assert said["message"].endswith((".",)) and len(said["message"]) > 20
    for raw in RAW_WORDS:
        assert raw not in said["message"], said


def test_a_broken_rule_is_named_in_the_problems_own_words():
    said = choice.plain_failure({"code": "rules", "detail": "A; B"}, [{"message": 'A bullet under "Data Analyst" has 148 characters; it can have at most 99.'}])
    assert 'has 148 characters' in said["message"] and said["fix"] is None


def test_no_resume_yet_points_to_resumes_and_trying_again_afterwards_works(client, service):
    with service.db.exclusive() as s:
        s.add(Job(id="j1", identity="j1", url="https://jobs.example.test/j1", title="Data Engineer", company="Northwind", status="ready", score=90, description=POSTING))
    choice.choose(service.db, "j1", "normal")
    assert asyncio.run(choice.run(service.db, service.config, "j1", model=stub(plan_of())))["state"] == "failed"
    view = client.get("/api/v2/resumes/tailor/job/j1").json()
    assert view["choice"]["state"] == "failed" and view["failure"]["fix"] == "resumes"
    assert "Add yours in Resumes" in view["failure"]["message"]
    assert view["resume_name"] is None


def test_a_queue_row_waiting_on_a_failure_gets_the_same_words(service):
    add_job(service)
    choice.choose(service.db, "j1", "normal")
    choice._record(service.db, "j1", "normal", state="failed", error={"code": "tailoring_error", "detail": "project_quality: Use 2,2,2"})
    with service.db.exclusive() as s:
        with pytest.raises(QueueBlocked) as caught:
            choice.artifact_for_queue(s, s.get(Job, "j1"), "submit")
    assert "project_quality" not in str(caught.value) and "didn't pass" in str(caught.value)


@needs_tex
def test_the_settings_switch_moves_what_is_waiting(service):
    add_job(service)
    tailored(service)
    assert choice.get_choice(service.db, "j1").state == "review"
    assert choice.waiting_words(service.db, ["j1"]) == {"j1": choice.WAITING_WORDS["review"]}
    choice.set_approval(service.db, False)
    row = choice.get_choice(service.db, "j1")
    assert (row.state, row.approved_how, row.approved_artifact_id) == ("approved", "setting_off", row.artifact_id)
    assert choice.waiting_words(service.db, ["j1"]) == {}
    choice.set_approval(service.db, True)
    row = choice.get_choice(service.db, "j1")
    assert (row.state, row.approved_how, row.approved_artifact_id, row.approved_sha) == ("review", None, None, None)
    # What the person approved themselves is theirs: the switch doesn't take it back.
    choice.approve(service.db, service.config, "j1", row.artifact_id)
    choice.set_approval(service.db, False)
    choice.set_approval(service.db, True)
    row = choice.get_choice(service.db, "j1")
    assert (row.state, row.approved_how) == ("approved", "reviewed")


@needs_tex
def test_the_switch_never_approves_what_the_checks_refuse(service):
    add_job(service)
    tailored(service)
    with service.db.exclusive() as s:
        row = s.get(RsTailorChoice, "j1")
        from jobpilot.features.resumes.tables import RsArtifact

        artifact = s.get(RsArtifact, row.artifact_id)
        artifact.validation = {**artifact.validation, "rules": {"ok": False, "problems": [{"message": "nope"}]}}
    choice.set_approval(service.db, False)
    assert choice.get_choice(service.db, "j1").state == "review"


@needs_tex
@pytest.mark.parametrize("status", ["queued", "running", "confirmed", "submission_unknown"])
def test_a_started_application_can_be_read_but_not_changed(client, service, status):
    add_job(service)
    tailored(service)
    view = client.get("/api/v2/resumes/tailor/job/j1").json()
    artifact = view["artifact"]["id"]
    with service.db.exclusive() as s:
        s.get(Job, "j1").status = status
    view = client.get("/api/v2/resumes/tailor/job/j1").json()
    assert view["started"] is True
    for answer in (
        client.post("/api/v2/resumes/tailor/job/j1/retry"),
        client.post("/api/v2/resumes/tailor/job/j1/save", json={"doc": view["doc"]}),
        client.post("/api/v2/resumes/tailor/job/j1/approve", json={"artifact_id": artifact, "apply": False}),
    ):
        assert answer.status_code == 409 and "already started" in answer.json()["detail"]
    assert choice.get_choice(service.db, "j1").artifact_id == artifact


def test_a_build_doesnt_land_under_the_mode_chosen_after_it_began(service):
    add_job(service)
    choice.choose(service.db, "j1", "normal")
    base = stub(plan_of())

    async def model(output_type, system, user):
        if output_type is normal.NormalPlan:
            choice.choose(service.db, "j1", "aggressive")
        return await base(output_type, system, user)

    asyncio.run(choice.run(service.db, service.config, "j1", model=model))
    row = choice.get_choice(service.db, "j1")
    assert (row.mode, row.state, row.artifact_id) == ("aggressive", "preparing", None)


def test_the_mode_chosen_during_a_build_gets_its_turn(service, monkeypatch):
    add_job(service)
    choice.choose(service.db, "j1", "normal")
    built = []

    async def fake_run(db, config, job_id, *, fresh=False):
        built.append((choice.get_choice(db, job_id).mode, fresh))
        if len(built) == 1:
            choice.choose(db, job_id, "aggressive")
        else:
            choice._record(db, job_id, "aggressive", state="review")

    monkeypatch.setattr(choice, "run", fake_run)

    async def scenario():
        choice.start(service.db, service.config, "j1")
        await choice._TASKS["j1"]

    asyncio.run(scenario())
    assert built == [("normal", False), ("aggressive", True)]
    assert choice.get_choice(service.db, "j1").state == "review"


@needs_tex
def test_a_newer_resume_makes_a_version_out_of_date_in_words(client, service):
    add_job(service)
    tailored(service)
    row = choice.get_choice(service.db, "j1")
    choice.approve(service.db, service.config, "j1", row.artifact_id)
    assert client.get("/api/v2/resumes/tailor/job/j1").json()["stale"] is False
    with service.db.exclusive() as s:
        s.get(Job, "j1").resume_id = None
        s.add(Resume(id="r2", name="newer", filename="r2.pdf", sha256="sha2", roles=[], text="Alex Example newer", created_at="2999-01-01T00:00:00+00:00"))
    assert client.get("/api/v2/resumes/tailor/job/j1").json()["stale"] is True
    with service.db.exclusive() as s:
        with pytest.raises(QueueBlocked) as caught:
            choice.artifact_for_queue(s, s.get(Job, "j1"), "submit")
    assert caught.value.code == choice.STALE and "Your resume" in str(caught.value)


@needs_tex
def test_an_editor_save_on_the_jobs_own_version_becomes_its_resume_and_needs_approval(client, service):
    add_job(service)
    tailored(service)
    row = choice.get_choice(service.db, "j1")
    first = row.artifact_id
    choice.approve(service.db, service.config, "j1", first)
    view = client.get("/api/v2/resumes/tailor/job/j1").json()
    saved = client.post("/api/v2/resumes/editor/save", json={"doc": view["doc"], "style": editor.Style().model_dump(), "job_id": "j1", "base_artifact_id": first})
    assert saved.status_code == 200, saved.text
    answer = saved.json()
    assert answer["choice"] == {"mode": "normal", "state": "review"}
    row = choice.get_choice(service.db, "j1")
    assert row.artifact_id == answer["artifact_id"] != first
    assert row.state == "review" and row.approved_artifact_id is None and row.approved_sha is None
    # Editing a version that isn't the job's current one leaves the job alone.
    again = client.post("/api/v2/resumes/editor/save", json={"doc": view["doc"], "style": editor.Style().model_dump(), "job_id": "j1", "base_artifact_id": first}).json()
    assert again["choice"] is None and choice.get_choice(service.db, "j1").artifact_id == answer["artifact_id"]
    listed = client.get("/api/v2/resumes/artifacts?job_id=j1&size=10").json()["items"]
    held = {a["artifact_id"]: a["choice"] for a in listed}
    assert held[answer["artifact_id"]] == {"mode": "normal", "state": "review"}
    assert held[first] is None and held[again["artifact_id"]] is None


@needs_tex
def test_a_section_with_every_entry_taken_off_needs_no_heading():
    doc = source_doc().model_copy(deep=True)
    doc.sections.append(Section(id="projects", title="Projects", kind="projects", entries=[
        Entry(id="p1", heading="Report Review", include=False, bullets=[Bullet(id="p1b", text="Built a report for local review of public records")]),
    ]))
    compiled = asyncio.run(compile_pdf(render(doc, editor.Style())))
    assert compiled.ok
    parsed = parse_check(compiled.pdf, doc)
    assert parsed["ok"], [i for i in parsed["items"] if not i["ok"]]
    assert not any("Projects" in i["label"] for i in parsed["items"])


def test_the_plain_mode_is_offered_first():
    assert modes.listing()[0]["id"] == "normal"


def test_tailoring_asks_for_the_key_of_the_provider_chosen_for_writing(service):
    add_job(service)
    config = service.config
    config.llm_provider, config.deepseek_api_key, config.openai_api_key = "deepseek", "", "writing-key"
    service.db.set_setting("agent_settings", {"models": {"answers": "openai", "navigator": "deepseek"}})
    assert pipeline.answers_key(service.db, config) == "writing-key"
    service.db.set_setting("agent_settings", {"models": {"answers": "deepseek", "navigator": "openai"}})
    assert pipeline.answers_key(service.db, config) == ""
    result = asyncio.run(pipeline.prepare_resume(service.db, config, "j1", mode="normal"))
    assert result.status == "failed" and result.error.code == "no_model"
