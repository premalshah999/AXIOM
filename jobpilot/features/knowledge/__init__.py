"""Applicant knowledge: confirmed facts, employer memory and evidence packets.

Public interface (everything else in this package is internal):

- retrieve_answer_context(db, question, options, employer, employer_url, history_row,
  resume_artifact_id, budget) -> EvidencePacket
- save_confirmed_answer(db, SaveAnswer) -> FactSaved   (raises VersionConflict)
- retire_fact(db, fact_id, expected_version) -> int
- list_facts(db, ...) -> {items, total, page, size, store_version}
- pending_events(db, limit) / ack_event(db, event_id)   (executor resumes waiting jobs)
- migrate_profile(db, dry_run) / rollback(db) / sync(db)
- resolve_employer(db, name, url) -> EmployerIdentity
- identify(question, employer_aliases, history_row) -> QuestionIdentity
- role_brief / write_answer / write_cover_letter / review: job-specific writing (see writing.py)
- employment_history_complete(db) -> {fact_id, reference, version, statement, confirmed_at} | None
  attest(db, attestation_id, statement): applicant-confirmed broad statements (see attestations.py)
- router(service) -> APIRouter mounted at /api/v2/knowledge
"""

from .attestations import attest, attestation, employment_history_complete
from .migration import migrate_profile, rollback, sync
from .models import (
    Budget,
    EmployerIdentity,
    EvidenceItem,
    EvidencePacket,
    FactSaved,
    QuestionIdentity,
    SaveAnswer,
    VersionConflict,
)
from .questions import identify
from .retrieval import clear_cache, retrieve_answer_context
from .router import router
from .store import ack_event, list_facts, pending_events, retire_fact, save_confirmed_answer
from .writing import (
    RoleBrief,
    Written,
    configured_model,
    question_kind,
    review,
    role_brief,
    write_answer,
    write_cover_letter,
)


def resolve_employer(db, name="", url="", create=True) -> EmployerIdentity | None:
    from . import employers

    with db.exclusive() if create else db.session() as s:
        return employers.resolve(s, name, url, create=create)


__all__ = [
    "attest",
    "attestation",
    "employment_history_complete",
    "RoleBrief",
    "Written",
    "configured_model",
    "question_kind",
    "review",
    "role_brief",
    "write_answer",
    "write_cover_letter",
    "Budget",
    "EmployerIdentity",
    "EvidenceItem",
    "EvidencePacket",
    "FactSaved",
    "QuestionIdentity",
    "SaveAnswer",
    "VersionConflict",
    "ack_event",
    "clear_cache",
    "identify",
    "list_facts",
    "migrate_profile",
    "pending_events",
    "resolve_employer",
    "retire_fact",
    "retrieve_answer_context",
    "rollback",
    "router",
    "save_confirmed_answer",
    "sync",
]
