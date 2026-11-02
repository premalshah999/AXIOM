"""The production routes, source choices, immutable artifacts and final send gate."""

import asyncio
import hashlib

import pytest
from pypdf import PdfReader

from jobpilot.db import Job
from jobpilot.features.resumes import choice, pipeline
from jobpilot.features.resumes.aggressive import store
from jobpilot.features.resumes.aggressive.integration import refresh
from jobpilot.features.resumes.aggressive.schema import Choice, Confirmation
from jobpilot.features.resumes.compiler import compile_pdf
from jobpilot.features.resumes.tables import RsArtifact
from jobpilot.features.resumes.validate import squash
from test_aggressive_pipeline import ORIGINALS, add_job, needs_tex, proposals, stub


def pending(service):
    add_job(service)
    choice.choose(service.db, "ag-job", "aggressive")
    model = stub()
    result = asyncio.run(choice.run(service.db, service.config, "ag-job", model=model))
    assert result["state"] == "review", result
    with service.db.session() as s:
        artifact = s.get(RsArtifact, result["artifact_id"])
    return artifact, store.get(service.db, artifact.validation["report"]["workspace_id"]), model


@needs_tex
def test_project_approval_and_refresh_use_no_ai_and_invalidate_old_artifact(client, service):
    artifact, workspace, model = pending(service)
    root = "/api/v2/resumes/aggressive/" + workspace["id"]
    original_pdf = (service.config.data_dir / artifact.pdf_path).read_bytes()
    for d in workspace["drafts"]:
        approved = client.post(
            root + "/drafts/" + d["id"] + "/approve",
            json={"approved": True, "revision": workspace["revision"]},
        )
        assert approved.status_code == 200, approved.text
        workspace = approved.json()
    before = client.get("/api/v2/resumes/tailor/job/ag-job").json()
    assert before["project_changes_pending"] and before["approval_block"]["code"] == choice.STALE
    updated = client.post(root + "/refresh", json={"job_id": "ag-job", "revision": workspace["revision"]})
    assert updated.status_code == 200, updated.text
    view = updated.json()
    assert view["rules"]["ok"] and not view["project_changes_pending"]
    assert view["approval_block"] is None
    assert view["cost"]["calls"] == 0 and view["cost"]["usd"] == 0
    assert len(model.calls) == 4  # one import and three pipeline calls
    assert view["artifact"]["id"] != artifact.id
    assert (service.config.data_dir / artifact.pdf_path).read_bytes() == original_pdf
    with pytest.raises(ValueError, match="changed"):
        choice.approve(service.db, service.config, "ag-job", artifact.id)
    approved = choice.approve(service.db, service.config, "ag-job", view["artifact"]["id"])
    with service.db.session() as s:
        current = s.get(RsArtifact, view["artifact"]["id"])
        assert choice.send_problem(s, current) is None
        assert choice.artifact_for_queue(s, s.get(Job, "ag-job"), "submit") == current.id
        assert (
            hashlib.sha256((service.config.data_dir / current.pdf_path).read_bytes()).hexdigest()
            == approved["pdf_sha"]
        )
    cached_model = stub()
    cached = asyncio.run(
        pipeline.prepare_resume(service.db, service.config, "ag-job", model=cached_model, mode="aggressive")
    )
    assert cached.cached and cached.artifact.artifact_id == view["artifact"]["id"]
    assert not cached_model.calls


@needs_tex
def test_originals_only_and_explicit_replacement_pass_shared_rules(service):
    _, ws, _ = pending(service)
    ws = store.choose(service.db, ws["id"], Choice(revision=ws["revision"]))
    originals = asyncio.run(refresh(service, "ag-job", ws["id"], ws["revision"]))
    assert originals["rules"]["ok"] and originals["drafts"] == []
    assert "Public Report Review" in originals["doc"]["sections"][2]["entries"][0]["heading"]
    assert all(p.title not in str(originals["doc"]) for p in proposals())
    # "Use only my original projects" restores everything, on the document and in the file a recruiter gets.
    assert originals["removed_projects"] == []
    assert [e["id"] for e in originals["doc"]["sections"][2]["entries"] if e["include"]] == list(ORIGINALS)
    with service.db.session() as s:
        restored = s.get(RsArtifact, originals["artifact"]["id"])
    page = PdfReader(service.config.data_dir / restored.pdf_path).pages
    assert len(page) == 1
    printed = squash(page[0].extract_text())
    for _, texts in ORIGINALS.values():
        assert all(squash(t) in printed for t in texts)
    for d in ws["drafts"]:
        ws = store.confirm(
            service.db, ws["id"], d["id"], Confirmation(approved=True, revision=ws["revision"])
        )
    ws = store.choose(
        service.db,
        ws["id"],
        Choice(
            revision=ws["revision"],
            selected_drafts=[d["id"] for d in ws["drafts"]],
            removed_projects=list(ORIGINALS),
        ),
    )
    replaced = asyncio.run(refresh(service, "ag-job", ws["id"], ws["revision"]))
    assert replaced["rules"]["ok"] and replaced["removed_projects"] == list(ORIGINALS)
    for key in ORIGINALS:
        assert not next(e for s in replaced["doc"]["sections"] for e in s["entries"] if e["id"] == key)["include"]
    # The registered model path also forwards this explicit removal.
    result = asyncio.run(
        pipeline.prepare_resume(
            service.db, service.config, "ag-job", model=stub(), mode="aggressive", fresh=True
        )
    )
    assert result.status == "ready", result
    assert result.artifact.validation["removed_projects"] == list(ORIGINALS)


@needs_tex
def test_review_edits_with_drafts_in_place_keep_the_rule_for_real_bullets(client, service):
    _, ws, _ = pending(service)
    view = client.get("/api/v2/resumes/tailor/job/ag-job").json()
    assert view["removed_projects"] == list(ORIGINALS)
    doc = view["doc"]
    real = doc["sections"][0]["entries"][0]["bullets"][0]
    shorter = real["text"].replace("every adjustment", "each adjustment")
    edited = client.post(
        "/api/v2/resumes/tailor/job/ag-job/save", json={"doc": {**doc}, "removed_projects": view["removed_projects"]}
    )
    assert edited.status_code == 200, edited.text
    real["text"] = shorter
    edited = client.post("/api/v2/resumes/tailor/job/ag-job/save", json={"doc": doc, "removed_projects": view["removed_projects"]})
    assert edited.status_code == 200, edited.text
    assert edited.json()["removed_projects"] == list(ORIGINALS)
    assert shorter in str(edited.json()["doc"]) and proposals()[0].title in str(edited.json()["doc"])
    # One character past the source, or one bullet fewer, is refused by the shared rules, drafts or no drafts.
    source = view["source_doc"]["sections"][0]["entries"][0]["bullets"][0]["text"]
    real["text"] = source + "x"
    too_long = client.post("/api/v2/resumes/tailor/job/ag-job/save", json={"doc": doc, "removed_projects": view["removed_projects"]})
    assert too_long.status_code == 422 and too_long.json()["problems"][0]["code"] == "bullet_too_long"
    real["text"] = source
    doc["sections"][0]["entries"][0]["bullets"].pop()
    fewer = client.post("/api/v2/resumes/tailor/job/ag-job/save", json={"doc": doc, "removed_projects": view["removed_projects"]})
    assert fewer.status_code == 422 and fewer.json()["problems"][0]["code"] == "bullet_count"
    # The drafts are its choice to change above, not in the save.
    doc = view["doc"]
    moved = client.post("/api/v2/resumes/tailor/job/ag-job/save", json={"doc": doc, "removed_projects": []})
    assert moved.status_code == 409


@needs_tex
def test_server_invalidates_approval_in_another_tab_even_with_setting_off(service):
    _, ws, _ = pending(service)
    ws = store.choose(service.db, ws["id"], Choice(revision=ws["revision"]))
    view = asyncio.run(refresh(service, "ag-job", ws["id"], ws["revision"]))
    choice.approve(service.db, service.config, "ag-job", view["artifact"]["id"])
    choice.set_approval(service.db, False)
    store.choose(
        service.db, ws["id"], Choice(revision=ws["revision"], selected_drafts=[ws["drafts"][0]["id"]])
    )
    assert choice.get_choice(service.db, "ag-job").approved_sha is None
    with service.db.session() as s:
        old = s.get(RsArtifact, view["artifact"]["id"])
        assert choice.send_problem(s, old)[0] == choice.STALE
    assert choice.run_problem(service.db, {"resume_artifact_id": old.id})


@needs_tex
def test_started_application_cannot_change_projects_and_stale_refresh_cannot_publish(service):
    base, ws, _ = pending(service)
    with service.db.exclusive() as s:
        s.get(Job, "ag-job").status = "queued"
    with pytest.raises(ValueError, match="already started"):
        store.choose(service.db, ws["id"], Choice(revision=ws["revision"]))
    with service.db.exclusive() as s:
        s.get(Job, "ag-job").status = "ready"
    changed = False

    async def race(tex, **kwargs):
        nonlocal changed
        built = await compile_pdf(tex, **kwargs)
        if not changed:
            changed = True
            store.choose(service.db, ws["id"], Choice(revision=ws["revision"]))
        return built

    with pytest.raises(ValueError, match="changed while compiling"):
        asyncio.run(refresh(service, "ag-job", ws["id"], ws["revision"], compile_fn=race))
    assert choice.get_choice(service.db, "ag-job").artifact_id == base.id


@needs_tex
def test_full_editor_cannot_erase_project_provenance(client, service):
    _, ws, _ = pending(service)
    ws = store.choose(service.db, ws["id"], Choice(revision=ws["revision"]))
    view = asyncio.run(refresh(service, "ag-job", ws["id"], ws["revision"]))
    with service.db.session() as s:
        base = s.get(RsArtifact, view["artifact"]["id"])
    edited = client.post(
        "/api/v2/resumes/editor/save",
        json={
            "job_id": "ag-job",
            "base_artifact_id": base.id,
            "doc": view["doc"],
            "style": base.validation["editor"]["style"],
        },
    )
    assert edited.status_code == 200, edited.text
    editor_id = edited.json()["artifact_id"]
    choice.approve(service.db, service.config, "ag-job", editor_id)
    store.choose(
        service.db, ws["id"], Choice(revision=ws["revision"], selected_drafts=[ws["drafts"][0]["id"]])
    )
    with service.db.session() as s:
        derived = s.get(RsArtifact, editor_id)
        assert derived.validation["report"]["workspace_id"] == ws["id"]
        assert choice.send_problem(s, derived)[0] == choice.STALE
