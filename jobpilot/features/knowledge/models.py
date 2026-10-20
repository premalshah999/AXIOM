"""Public types of the knowledge feature."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Layer = Literal["confirmed", "narrative", "draft"]
Scope = Literal["global", "employer"]
CATEGORIES = (
    "personal",
    "education",
    "experience",
    "projects",
    "skills",
    "demographics",
    "work_authorization",
    "agreements",
    "clearance",
    "employer_relationship",
    "additional",
)


class EmployerIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str  # emp:<slug>
    display: str
    matched_by: Literal["tenant", "name", "created", "none"] = "none"
    aliases: tuple[str, ...] = ()


class QuestionIdentity(BaseModel):
    """Meaning-preserving identity of one form question."""

    model_config = ConfigDict(frozen=True)
    key: str
    text: str  # normalized; employer names replaced by <employer>; comparators as symbols
    category: str
    comparator: str | None = None
    quantities: tuple[str, ...] = ()
    negated: bool = False
    temporal: Literal["current", "past", "future", "none"] = "none"
    modality: Literal["willingness", "ability", "experience", "status", "none"] = "none"
    employer_specific: bool = False
    history_row: str | None = None


class Provenance(BaseModel):
    source: str  # app | telegram | review | profile_migration | resume | import
    actor: str = "applicant"
    reference: str = ""  # review id, evidence id, migration id …
    at: str = ""


class FactView(BaseModel):
    id: str
    category: str
    key: str
    question: str
    value: Any
    options: list[str] = Field(default_factory=list)
    layer: Layer
    scope: Scope
    employer_id: str | None = None
    row_id: str | None = None
    provenance: dict = Field(default_factory=dict)
    valid_from: str | None = None
    valid_until: str | None = None
    version: int = 1


class EvidenceItem(BaseModel):
    """One retrieved candidate. `match` says how it was found; only `exact`
    confirmed items may answer without a model."""

    fact_id: str
    kind: Literal["fact", "narrative", "history_row", "variant_row"]
    match: Literal["exact", "category", "row", "lexical", "variant"]
    category: str
    question: str
    value: Any
    scope: Scope
    employer_id: str | None = None
    row_id: str | None = None
    version: int = 1
    source: str = ""


class EvidencePacket(BaseModel):
    question: QuestionIdentity
    employer: EmployerIdentity | None
    store_version: int
    profile_digest: str
    exact_answer: Any | None = None  # set only for one unconflicted, current, option-compatible fact
    exact_option: str | None = None
    items: list[EvidenceItem] = Field(default_factory=list)
    conflicts: list[list[str]] = Field(default_factory=list)  # groups of contradicting fact ids
    expired: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)  # topics with no supporting fact
    truncated: bool = False
    bytes: int = 0
    cache_hit: bool = False


class Budget(BaseModel):
    max_items: int = Field(default=12, ge=1, le=60)
    max_bytes: int = Field(default=6000, ge=256, le=64000)
    narratives: int = Field(default=3, ge=0, le=10)
    # Newest native same-category answers considered as paraphrase candidates.
    candidates: int = Field(default=20, ge=0, le=50)


class FactSaved(BaseModel):
    fact_id: str
    version: int
    store_version: int
    superseded: list[str] = Field(default_factory=list)
    event_id: str


class SaveAnswer(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    value: Any
    options: list[str] = Field(default_factory=list, max_length=250)
    scope: Scope = "global"
    employer: str = Field(default="", max_length=300)
    employer_url: str = Field(default="", max_length=2048)
    category: str | None = None
    layer: Layer = "confirmed"
    history_row: str | None = None
    valid_until: str | None = None
    expected_version: int | None = None
    correlation: dict = Field(default_factory=dict)  # review_id, run_id, job_id
    source: str = "app"


class VersionConflict(Exception):
    def __init__(self, expected, current):
        super().__init__(
            f"Knowledge changed (expected version {expected}, current {current}). Reload and retry."
        )
        self.expected, self.current = expected, current
