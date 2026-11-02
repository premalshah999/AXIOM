"""Structured outputs. Proposed work and applicant-attested work have different schemas."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Archetype = Literal[
    "shipped product", "open-source tool", "messy data analysis", "benchmark", "internal tool"
]


def _clip(text, most):
    text = text.strip()
    return text if len(text) <= most else (text[:most].rsplit(" ", 1)[0] or text[:most])


class Brief(BaseModel):
    product: str = Field(max_length=800)
    proof_points: list[str] = Field(min_length=3, max_length=3)
    real_stack: list[str] = Field(default_factory=list, max_length=20)
    seniority: str = Field(max_length=400)
    hard_keywords: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="before")
    @classmethod
    def fit(cls, data):
        """A brief only plans; none of it is printed. One a little over a limit is cut, not refused: refused, it was asked again,
        refused again, and the whole tailoring failed (an ML posting, 2026-11-02). Too few proof points still fail."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for name, most in (("product", 800), ("seniority", 400)):
            if isinstance(data.get(name), str):
                data[name] = _clip(data[name], most)
        for name, most in (("proof_points", 3), ("real_stack", 20), ("hard_keywords", 10)):
            if isinstance(data.get(name), list):
                data[name] = data[name][:most]
        return data


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
    # One per project_shapes item, or three for a resume with no projects. The gates check the count.
    proposals: list[Proposal] = Field(min_length=1, max_length=3)


class Revision(BaseModel):
    bullets: list[Rewrite] = Field(default_factory=list, max_length=100)
    project_order: list[str] = Field(default_factory=list, max_length=30)
    categories: list[Category] = Field(default_factory=list, max_length=5)
    proposals: list[Proposal] = Field(default_factory=list, max_length=3)


class BulletFix(BaseModel):
    draft: int = Field(ge=0, le=2)
    bullet: int = Field(ge=0, le=7)
    wordings: list[str] = Field(min_length=1, max_length=3)


class ShapeFix(BaseModel):
    """The one bounded length repair: a short reply under the default token cap, holding only the bullets that missed."""

    bullets: list[BulletFix] = Field(default_factory=list, max_length=24)


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
