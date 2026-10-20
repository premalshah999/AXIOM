"""Evidence-backed resume tailoring.

Public interface:

- prepare_resume(db, config, job_id, source_resume_id=None, *, policy, drafter, compile_fn) -> TailoringResult
- cancel(db, artifact_id) -> bool
- current_artifact(db, job_id, data_dir) -> TailoredResume | None   (the only queueable variant: ready,
  matching current source/profile/posting, PDF intact; tailored or editor-saved)
- preparation_state(db, job_id, data_dir) -> ready | building | failed | stale | none
- ready_artifact(...) is kept as an alias of current_artifact
- variant_rows(db, artifact_id) -> [{unit_id, kind, source_row, text}]
- model_drafter(db, config) -> Drafter | None
- router(service) -> APIRouter mounted at /api/v2/resumes
"""

from .pipeline import (
    POLICY_VERSION,
    TailoredResume,
    TailoringError,
    TailoringResult,
    TailorPolicy,
    cancel,
    current_artifact,
    current_identity,
    file_intact,
    model_drafter,
    preparation_state,
    prepare_resume,
    ready_artifact,
    variant_rows,
)
from .router import router

__all__ = [
    "POLICY_VERSION",
    "TailorPolicy",
    "TailoredResume",
    "TailoringError",
    "TailoringResult",
    "cancel",
    "current_artifact",
    "current_identity",
    "file_intact",
    "preparation_state",
    "model_drafter",
    "prepare_resume",
    "ready_artifact",
    "router",
    "variant_rows",
]
