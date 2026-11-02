"""An earlier run's workspace with no drafts holds no choice, so the next run's drafts take it (2026-11-02: OpenAI's posting showed
no drafts after its length repair fixed both, because the empty workspace of a failed run was reused). One holding drafts is
still shared, so what the person chose survives. Synthetic data only."""

import asyncio

from jobpilot.features.resumes.aggressive import store
from jobpilot.features.resumes.aggressive.schema import Brief
from test_aggressive_pipeline import JOB, ORIGINALS, PROFILE, context, proposals


def brief_for(tmp_path, service):
    source, ctx = context(tmp_path, db=service.db)
    return source, asyncio.run(ctx.model(Brief, "", "{}"))


def test_an_empty_workspace_takes_the_next_runs_drafts(service, tmp_path):
    source, brief = brief_for(tmp_path, service)
    empty = store.create(service.db, JOB, source, PROFILE, [], brief)
    assert empty["drafts"] == []
    filled = store.create(service.db, JOB, source, PROFILE, proposals(), brief, replaces=list(ORIGINALS))
    assert filled["id"] == empty["id"] and filled["revision"] == empty["revision"] + 1
    assert [d["title"] for d in filled["drafts"]] == [p.title for p in proposals()]
    assert all(d["id"].startswith(empty["id"] + ":p") for d in filled["drafts"])
    assert filled["selected_drafts"] == [d["id"] for d in filled["drafts"]] and filled["removed_projects"] == list(ORIGINALS)
    assert store.get(service.db, empty["id"])["drafts"] == filled["drafts"]


def test_a_workspace_holding_drafts_is_shared_as_before(service, tmp_path):
    source, brief = brief_for(tmp_path, service)
    first = store.create(service.db, JOB, source, PROFILE, proposals(), brief, replaces=list(ORIGINALS))
    other = [p.model_copy(update={"title": p.title + " Variant"}) for p in proposals()]
    again = store.create(service.db, JOB, source, PROFILE, other, brief, replaces=list(ORIGINALS))
    assert again["id"] == first["id"] and again["revision"] == first["revision"]
    assert [d["title"] for d in again["drafts"]] == [p.title for p in proposals()]
    # And a run with no drafts never empties one that has them.
    assert store.create(service.db, JOB, source, PROFILE, [], brief)["drafts"] == first["drafts"]
