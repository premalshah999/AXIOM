"""Structured outputs. Proposed work and applicant-attested work have different schemas."""

from typing import Literal

from pydantic import BaseModel, Field

Archetype = Literal[
    "shipped product", "open-source tool", "messy data analysis", "benchmark", "internal tool"
]


class Brief(BaseModel):
    product: str = Field(max_length=800)
    proof_points: list[str] = Field(min_length=3, max_length=3)
    real_stack: list[str] = Field(default_factory=list, max_length=20)
    seniority: str = Field(max_length=400)
    hard_keywords: list[str] = Field(default_factory=list, max_length=10)


class Proposal(BaseModel):
    title: str = Field(min_length=6, max_length=160)
    archetype: Archetype
    proof_point: int = Field(ge=0, le=2)
    descriptor: str = Field(max_length=40)
    data_source: str = Field(min_length=3, max_length=160)
    tools: list[str] = Field(min_length=1, max_length=10)
    bullets: list[str] = Field(min_length=1, max_length=8)
    build_plan: list[str] = Field(min_length=1, max_length=8)
    metric_shape: Literal["none", "count", "range", "before/after", "latency", "coverage"] = "none"


class Rewrite(BaseModel):
    id: str
    text: str = Field(max_length=1200)


class Category(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    skills: list[str] = Field(max_length=60)


class Plan(BaseModel):
    bullets: list[Rewrite] = Field(default_factory=list, max_length=100)
    project_order: list[str] = Field(default_factory=list, max_length=30)
    categories: list[Category] = Field(default_factory=list, max_length=5)
    proposals: list[Proposal] = Field(min_length=3, max_length=3)


class Revision(BaseModel):
    bullets: list[Rewrite] = Field(default_factory=list, max_length=100)
    project_order: list[str] = Field(default_factory=list, max_length=30)
    categories: list[Category] = Field(default_factory=list, max_length=5)
    proposals: list[Proposal] = Field(default_factory=list, max_length=3)


class Critique(BaseModel):
    proof_points: list[str] = Field(min_length=3, max_length=3)
    evidence: list[str] = Field(min_length=3, max_length=3)
    missing_requirements: list[str] = Field(default_factory=list, max_length=30)
    distinctness: str
    issues: list[str] = Field(default_factory=list, max_length=30)
    line_fixes: list[str] = Field(default_factory=list, max_length=20)
    grade: Literal["A", "B", "C"]
    passed: bool
    what_would_change_the_verdict: list[str] = Field(default_factory=list, max_length=10)


class Confirmation(BaseModel):
    # Approval is specific to the stored project revision. The owner explicitly
    # permits projects which haven't been built yet; don't impose a built gate.
    approved: Literal[True]
    revision: int = Field(ge=0)


class Choice(BaseModel):
    selected_drafts: list[str] = Field(default_factory=list, max_length=30)
    removed_projects: list[str] = Field(default_factory=list, max_length=30)
    revision: int = Field(ge=0)
