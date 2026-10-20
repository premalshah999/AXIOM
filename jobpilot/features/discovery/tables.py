from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .._store import FeatureBase, now


class DcRequisition(FeatureBase):
    """Canonical requisition across search/source URLs, with posting-time provenance."""

    __tablename__ = "dc_requisitions"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # canonical requisition key
    job_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    employer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, default="")
    location: Mapped[str] = mapped_column(String, default="")
    posted_at: Mapped[str | None] = mapped_column(String, nullable=True)
    posted_basis: Mapped[str] = mapped_column(
        String, default="first_seen"
    )  # verified | inferred | first_seen
    first_seen: Mapped[str] = mapped_column(String, default=now)
    last_seen: Mapped[str] = mapped_column(String, default=now)
    sources: Mapped[list] = mapped_column(JSON, default=list)  # [{url, source_id, seen_at, posted, basis}]
    closed: Mapped[bool] = mapped_column(default=False)


class DcClassification(FeatureBase):
    """Cached fit outcome per (job description, profile, policy) version."""

    __tablename__ = "dc_classifications"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # H(identity, description, profile, policy)
    job_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String)  # ok | failed | rejected
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String, default="")
    retry_at: Mapped[str | None] = mapped_column(String, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String, default=now)


class DcSourceHealth(FeatureBase):
    __tablename__ = "dc_source_health"
    source_id: Mapped[str] = mapped_column(String, primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str] = mapped_column(String, default="")
    last_ok: Mapped[str | None] = mapped_column(String, nullable=True)


class DcDeferral(FeatureBase):
    """A temporary cooldown defers a job with a retry time; it never discards it."""

    __tablename__ = "dc_deferrals"
    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    reason: Mapped[str] = mapped_column(String)
    not_before: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=now)
