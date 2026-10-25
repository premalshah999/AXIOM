"""A required cover letter is refused only for a claim its evidence does not support. Style findings left after the
writer's repair ("too technical: names 10 technologies", length, a cliche) refused every required letter for two
employers, and the owner was asked to supply one (2026-10-25)."""

from types import SimpleNamespace

import pytest

from jobpilot import letters
from jobpilot.features.knowledge import writing
from jobpilot.schemas import Profile


def resolver(service, tmp_path, run_id):
    service.db.set_setting("execution_services", {"enabled": True})
    service.config.data_dir = tmp_path
    return SimpleNamespace(
        db=service.db,
        config=service.config,
        run_id=run_id,
        job={"title": "Data Engineer", "company": "Acme", "description": "Build pipelines."},
        resume_text="Built data pipelines.",
        profile=Profile(first_name="Alex", last_name="Rivera"),
        resume_artifact_id=None,
        variant_rows=lambda: [],
    )


@pytest.mark.parametrize(
    "problems,accepted",
    [
        (["too technical: names 10 technologies"], True),
        (["letter length 340 words (aim for 220-300)", "cliché: passionate"], True),
        (["unsupported number: 47%"], False),
        (["unsupported tool or name: Kubernetes"], False),
    ],
)
async def test_only_unsupported_claims_refuse_a_letter(service, tmp_path, monkeypatch, problems, accepted):
    async def write(*args, **kwargs):
        return writing.Written(text="I built the pipelines Acme needs. " * 8, kind="cover_letter",
                               evidence_ids=["resume:selected"], problems=problems, needs_review=True)

    monkeypatch.setattr(writing, "write_cover_letter", write)
    monkeypatch.setattr(writing, "configured_model", lambda *a, **k: None)
    target = resolver(service, tmp_path, "run-" + str(abs(hash(tuple(problems)))))
    if accepted:
        path = await letters.cover_letter(target)
        assert path.exists() and path.suffix == ".pdf"
    else:
        with pytest.raises(ValueError, match="writing repair"):
            await letters.cover_letter(target)


async def test_a_letter_citing_evidence_it_was_not_given_is_refused(service, tmp_path, monkeypatch):
    async def write(*args, **kwargs):
        return writing.Written(text="I built the pipelines Acme needs. " * 8, kind="cover_letter",
                               evidence_ids=["evidence:invented"], problems=[])

    monkeypatch.setattr(writing, "write_cover_letter", write)
    monkeypatch.setattr(writing, "configured_model", lambda *a, **k: None)
    with pytest.raises(ValueError):
        await letters.cover_letter(resolver(service, tmp_path, "run-invented"))
