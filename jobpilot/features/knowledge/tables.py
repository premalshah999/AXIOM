from sqlalchemy import JSON, Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .._store import FeatureBase, now


class KnFact(FeatureBase):
    """One versioned applicant fact or narrative.

    layer: confirmed (applicant-confirmed fact), narrative (supported prose),
    draft (proposed, never used in applications).
    scope: global, or employer (employer_id set; never visible to other employers).
    """

    __tablename__ = "kn_facts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    profile_id: Mapped[str] = mapped_column(String, default="default", index=True)
    category: Mapped[str] = mapped_column(String, index=True)
    key: Mapped[str] = mapped_column(String, index=True)  # question key or structured fact key
    question: Mapped[str] = mapped_column(Text, default="")
    value: Mapped[dict] = mapped_column(JSON)  # {"v": <json value>}
    options: Mapped[list] = mapped_column(JSON, default=list)
    layer: Mapped[str] = mapped_column(String, default="confirmed", index=True)
    scope: Mapped[str] = mapped_column(String, default="global")
    employer_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    row_id: Mapped[str | None] = mapped_column(String, nullable=True)  # history-row identity
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    valid_from: Mapped[str | None] = mapped_column(String, nullable=True)
    valid_until: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    superseded_by: Mapped[str | None] = mapped_column(String, nullable=True)
    retired: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now)

    __table_args__ = (Index("kn_facts_lookup", "profile_id", "key", "scope", "employer_id"),)


class KnEmployer(FeatureBase):
    __tablename__ = "kn_employers"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # emp:<slug>
    display: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=now)


class KnAlias(FeatureBase):
    """Exact alias → employer. kind: name | tenant. Matching is exact on the
    normalized alias, never a substring."""

    __tablename__ = "kn_aliases"
    alias: Mapped[str] = mapped_column(String, primary_key=True)
    employer_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String, default="name")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class KnVersion(FeatureBase):
    """Monotonic store version per profile, for optimistic concurrency and cache keys."""

    __tablename__ = "kn_versions"
    profile_id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[str] = mapped_column(String, default=now)


class KnEvent(FeatureBase):
    """Durable, correlated notification that a fact was saved; the executor
    consumes it to resume a waiting application without holding a slot."""

    __tablename__ = "kn_events"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, default="fact_saved")
    fact_id: Mapped[str] = mapped_column(String)
    key: Mapped[str] = mapped_column(String)
    employer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    correlation: Mapped[dict] = mapped_column(JSON, default=dict)  # review_id, run_id, job_id
    state: Mapped[str] = mapped_column(String, default="pending", index=True)
    created_at: Mapped[str] = mapped_column(String, default=now)
    delivered_at: Mapped[str | None] = mapped_column(String, nullable=True)


class KnMigration(FeatureBase):
    __tablename__ = "kn_migrations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    profile_id: Mapped[str] = mapped_column(String, default="default")
    source_digest: Mapped[str] = mapped_column(String)
    fact_ids: Mapped[list] = mapped_column(JSON, default=list)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    rolled_back: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now)
