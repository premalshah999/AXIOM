"""Tailoring modes: one async function each, behind one contract. The screen, the ATS check, approval and the send path work for any mode here.

A mode gets the job, the user's source resume and profile, and returns the tailored LaTeX, the compiled PDF's path and the drafts it wrote
that the user hasn't confirmed:

    async def run(job: dict, source: SourceResume, profile: dict, ctx: ModeContext) -> ModeResult

    register(ModeSpec("mine", "My mode", "What it does, in a sentence.", run))

A package at features/resumes/<mode>/ registers itself when imported (`AUTOLOAD`), so adding a mode needs no edit here. Until then the
picker shows it as coming soon.

What the pipeline does with a result, whichever mode made it, before anything can be approved or sent:
- check_rules() on `doc` against the source and on the compiled PDF. Preview-only project sources allow review; send checks still require confirmation.
- `drafts` are stored on the artifact. One still unconfirmed anywhere in the PDF's text stops the send, approval on or off.
"""

import importlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from .editor import Entry, ResumeDoc, Style
from .rules import check_rules, draft_leaks  # noqa: F401  (the shared check; import it from here)

log = logging.getLogger(__name__)

POLICY_PREFIX = "tailor-"
REVIEW_POLICY = "tailor-review-v1"  # The applicant's own edit of a tailored resume, saved from the review screen.
AUTOLOAD = ("aggressive",)
KNOWN_POLICIES = ("tailor-normal-v1", "tailor-aggressive-v1", REVIEW_POLICY)
PLANNED = {
    "aggressive": {
        "label": "Aggressive",
        "note": "A rewrite aimed at this job's proof points, with project ideas you could build.",
    }
}
Model = Callable[[type, str, str], Awaitable[BaseModel]]


class Draft(BaseModel):
    """Something a mode wrote that the applicant hasn't confirmed, such as a project they could build. It can never reach a sent PDF while `confirmed` is False."""

    id: str
    title: str
    bullets: list[str] = Field(default_factory=list)
    confirmed: bool = False


@dataclass
class SourceResume:
    """The resume everything traces to. `doc` is the editable document: the approved master, a verified import, or the profile's history."""

    id: str
    sha: str
    text: str
    doc: ResumeDoc
    style: Style
    pdf: bytes | None = None
    origin: str = "master"  # master | import | profile


@dataclass
class ModeContext:
    db: Any
    config: Any
    model: Model
    compile_fn: Any
    work_dir: Path  # Write the PDF here; the pipeline moves it to its permanent place.
    evidence: Any  # evidence.Inventory: the applicant's supported material.
    ats_terms: list = field(default_factory=list)  # ats.terms() for this posting
    ats_source: dict = field(default_factory=dict)  # ats.score() of the source: says which terms are `needed` and must never be added


@dataclass
class ModeResult:
    latex: str
    pdf_path: Path
    drafts: list[Draft] = field(default_factory=list)  # Unconfirmed: allowed in review, blocked at approval/send.
    doc: ResumeDoc | None = None  # Required: the rules are checked on it, and the review screen edits it.
    style: Style | None = None
    allowed_skills: list[str] = field(default_factory=list)  # Skills an added item may be, beyond the source's own.
    added_projects: list[Entry] = field(default_factory=list)  # Projects the applicant confirmed that joined this resume.
    report: dict = field(default_factory=dict)  # Shown on the review screen.
    model_calls: int = 0
    preview_projects: list[Entry] = field(default_factory=list)  # Pending project sources, for preview checks only.
    removed_projects: list[str] = field(default_factory=list)  # Explicit applicant choices, never model omissions.


def review_checks(doc, source, *, added_projects=(), preview_projects=(), unconfirmed=(), **kwargs):
    """One shared rules implementation, with separate preview and send decisions.

    A pending source must be exactly the declared project draft. It cannot grant
    permission to change an employer, degree, date, or any non-project section.
    """
    drafts = [{**(d if isinstance(d, dict) else d.model_dump()), "confirmed": False} for d in unconfirmed]
    pending = {d["id"]: d for d in drafts}
    preview_projects = list(preview_projects)
    problems = []
    for entry in preview_projects:
        draft = pending.get(entry.id)
        if not draft or entry.heading != draft["title"] or [b.text for b in entry.bullets if b.include] != draft["bullets"] or entry.dates or entry.location:
            problems.append({"code": "invalid_preview_project", "where": entry.id, "message": "A preview project must match its saved, unconfirmed draft."})
    preview = check_rules(doc, source, added_projects=[*added_projects, *preview_projects], **kwargs)
    if problems:
        preview["problems"].extend(problems)
        preview["ok"] = False
    send = check_rules(doc, source, added_projects=added_projects, unconfirmed=drafts, **kwargs)
    return preview, send


ModeFn = Callable[[dict, SourceResume, dict, ModeContext], Awaitable[ModeResult]]


@dataclass
class ModeSpec:
    id: str
    label: str
    note: str
    run: ModeFn
    # (prompt tokens, completion tokens) before the resume's own size, for the estimate shown before tailoring.
    estimate_tokens: Callable[[int, int], tuple[int, int]] | None = None
    timeout_seconds: float = 180


_REGISTRY: dict[str, ModeSpec] = {}
_loaded = False


def register(spec: ModeSpec) -> None:
    _REGISTRY[spec.id] = spec


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    from . import normal

    register(normal.SPEC)
    for name in AUTOLOAD:
        try:
            importlib.import_module(f"{__package__}.{name}")
        except ModuleNotFoundError as exc:
            # Only its own absence means "not here yet"; a missing dependency inside it is a real fault.
            if exc.name != f"{__package__}.{name}":
                log.exception("tailoring mode %s failed to load", name)
        except Exception:
            log.exception("tailoring mode %s failed to load", name)


def get(mode: str) -> ModeSpec | None:
    _load()
    return _REGISTRY.get(mode)


def listing() -> list[dict]:
    """For the picker: what's ready and what's coming."""
    _load()
    rows = [{"id": s.id, "label": s.label, "note": s.note, "available": True} for s in _REGISTRY.values()]
    rows += [{"id": k, **v, "available": False} for k, v in PLANNED.items() if k not in _REGISTRY]
    return rows


def policy_for(mode: str) -> str:
    return f"{POLICY_PREFIX}{mode}-v1"


def is_tailored_policy(policy_version: str | None) -> bool:
    return bool(policy_version) and policy_version.startswith(POLICY_PREFIX) and policy_version != "tailor-master-v2"
