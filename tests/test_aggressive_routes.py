"""Aggressive-owned routes mounted on a fresh, authenticated AXIOM test app."""

import asyncio

from jobpilot.features.resumes.aggressive import store
from jobpilot.features.resumes.aggressive.schema import Brief
from test_aggressive_pipeline import JOB, ORIGINALS, PROFILE, context, needs_tex, proposals


def setup_workspace(app, service, tmp_path):
    # Use the production factory's mount, including its security middleware.
    source, ctx = context(tmp_path, db=service.db)
    brief = asyncio.run(ctx.model(Brief, "", "{}"))
    return store.create(service.db, JOB, source, PROFILE, proposals(), brief, replaces=list(ORIGINALS))


@needs_tex
def test_preview_contains_drafts_then_switches_to_originals(client, app, service, tmp_path):
    from pypdf import PdfReader
    import io

    ws = setup_workspace(app, service, tmp_path)
    root = "/api/v2/resumes/aggressive/" + ws["id"]
    preview = client.get(root + "/preview.pdf")
    assert preview.status_code == 200, preview.text
    assert preview.headers["x-project-approval-required"] == "true"
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(preview.content)).pages)
    assert all(p.title in text for p in proposals())
    assert all(heading not in text for heading, _ in ORIGINALS.values())
    chosen = client.put(
        root + "/choices", json={"revision": 0, "selected_drafts": [], "removed_projects": []}
    )
    assert chosen.status_code == 200
    original = client.get(root + "/preview.pdf")
    assert original.status_code == 200, original.text
    assert original.headers["x-project-approval-required"] == "false"
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(original.content)).pages)
    assert all(heading in text for heading, _ in ORIGINALS.values())
    assert all(p.title not in text for p in proposals())
    stale = client.put(root + "/choices", json={"revision": 0})
    assert stale.status_code == 409


def test_only_explicit_revision_approval_confirms_a_project(client, app, service, tmp_path):
    ws = setup_workspace(app, service, tmp_path)
    root = "/api/v2/resumes/aggressive/" + ws["id"]
    endpoint = root + "/drafts/" + ws["drafts"][0]["id"] + "/approve"
    assert client.post(endpoint, json={"revision": 0, "approved": False}).status_code == 422
    result = client.post(endpoint, json={"revision": 0, "approved": True})
    assert result.status_code == 200
    data = client.get(root).json()
    assert data["drafts"][0]["confirmed"]
    assert not data["drafts"][1]["confirmed"]
    assert client.post(endpoint, json={"revision": 0, "approved": True}).status_code == 409
    assert client.get("/api/v2/resumes/aggressive/missing").status_code == 404
