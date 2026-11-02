"""The production routes, source choices, immutable artifacts and final send gate."""

import asyncio
import hashlib

import pytest

from jobpilot.db import Job
from jobpilot.features.resumes import choice, pipeline
from jobpilot.features.resumes.aggressive import store
from jobpilot.features.resumes.aggressive.integration import refresh
from jobpilot.features.resumes.aggressive.schema import Choice, Confirmation
from jobpilot.features.resumes.compiler import compile_pdf
from jobpilot.features.resumes.tables import RsArtifact
from test_aggressive_pipeline import add_job, needs_tex, proposals, stub


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
            removed_projects=["original"],
        ),
    )
    replaced = asyncio.run(refresh(service, "ag-job", ws["id"], ws["revision"]))
    assert replaced["rules"]["ok"] and replaced["removed_projects"] == ["original"]
    assert not next(e for s in replaced["doc"]["sections"] for e in s["entries"] if e["id"] == "original")[
        "include"
    ]
    # The registered model path also forwards this explicit removal.
    result = asyncio.run(
        pipeline.prepare_resume(
            service.db, service.config, "ag-job", model=stub(), mode="aggressive", fresh=True
        )
    )
    assert result.status == "ready", result
    assert result.artifact.validation["removed_projects"] == ["original"]


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
