from sqlalchemy import JSON, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .._store import FeatureBase, now


class RsArtifact(FeatureBase):
    """One tailored resume. Immutable once `ready`; only `ready` can be queued.

    cache_key = H(source resume sha, profile digest, job identity, description sha,
    policy version). A duplicate request reuses the validated artifact.
    """

    __tablename__ = "rs_artifacts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String, index=True)  # building | ready | failed | cancelled
    source_resume_id: Mapped[str] = mapped_column(String)
    source_resume_sha: Mapped[str] = mapped_column(String)
    profile_digest: Mapped[str] = mapped_column(String)
    job_identity: Mapped[str] = mapped_column(String, index=True)
    job_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    employer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    description_sha: Mapped[str] = mapped_column(String)
    policy_version: Mapped[str] = mapped_column(String)
    pdf_sha: Mapped[str | None] = mapped_column(String, nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String, nullable=True)  # relative to data_dir
    text: Mapped[str] = mapped_column(Text, default="")
    selection: Mapped[list] = mapped_column(
        JSON, default=list
    )  # [{unit_id, kind, source_row, text, source_text}]
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[dict] = mapped_column(JSON, default=dict)  # {code, retryable, detail}
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)


class RsMaster(FeatureBase):
    """The applicant's master resume as an editable document.

    Tailoring edits only allowed sections of an *approved* master; everything
    else is rendered from it verbatim. `verification` records how faithfully the
    document reproduces the source PDF's words.
    """

    __tablename__ = "rs_masters"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_resume_id: Mapped[str] = mapped_column(String, index=True)
    source_resume_sha: Mapped[str] = mapped_column(String)
    origin: Mapped[str] = mapped_column(String, default="import")  # import | upload | edited
    status: Mapped[str] = mapped_column(String, default="draft")  # draft | approved | superseded
    doc: Mapped[dict] = mapped_column(JSON, default=dict)
    style: Mapped[dict] = mapped_column(JSON, default=dict)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String, default=now)
    approved_at: Mapped[str | None] = mapped_column(String, nullable=True)
