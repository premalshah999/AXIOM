"""Applicant attestations: broad statements the applicant confirms once, in their own words.

An attestation is a confirmed global fact with a fixed question, so every consumer
reads it through one key. Its provenance keeps the exact statement the applicant
confirmed. It never invents an answer by itself: a consumer combines it with other
confirmed evidence (for example, an employer absent from a complete work history).
"""

from datetime import UTC, datetime

from sqlalchemy import select

from .models import SaveAnswer
from .questions import identify
from .retrieval import _expired
from .store import normalized, readable, save_confirmed_answer
from .tables import KnFact

EMPLOYMENT_HISTORY = {
    "id": "employment_history_complete",
    "question": (
        "Is the employment history in your profile complete, including internships, contract "
        "and temporary-agency work?"
    ),
    "statement": (
        "My employment history in my profile is complete, including internships, contract and "
        "temporary-agency work. I have never worked for any employer not listed there."
    ),
    "category": "employer_relationship",
}
ATTESTATIONS = {EMPLOYMENT_HISTORY["id"]: EMPLOYMENT_HISTORY}


def attest(db, attestation_id: str, statement: str, source: str = "app"):
    """Record the applicant's confirmation. The statement must be the exact wording shown."""
    spec = ATTESTATIONS.get(attestation_id)
    if spec is None:
        raise ValueError("Unknown attestation")
    if " ".join(statement.split()) != spec["statement"]:
        raise ValueError("Confirm the statement exactly as written")
    return save_confirmed_answer(
        db,
        SaveAnswer(
            question=spec["question"],
            value="Yes",
            options=["Yes", "No"],
            category=spec["category"],
            source=source,
            correlation={"statement": spec["statement"]},
        ),
        legacy=False,
    )


def attestation(db, attestation_id: str, profile_id: str = "default") -> dict | None:
    """The live confirmation, or None (never confirmed, withdrawn, expired, or answered No)."""
    spec = ATTESTATIONS[attestation_id]
    key = identify(spec["question"]).key
    at = datetime.now(UTC).isoformat()
    with db.session() as s:
        rows = s.scalars(
            readable(select(KnFact)).where(
                KnFact.profile_id == profile_id,
                KnFact.key == key,
                KnFact.layer == "confirmed",
                KnFact.scope == "global",
            )
        )
        live = [f for f in rows if not _expired(f, at)]
    if len(live) != 1 or normalized((live[0].value or {}).get("v")) is not True:
        return None
    f = live[0]
    return {
        "id": attestation_id,
        "fact_id": f.id,
        "reference": "knowledge:" + f.id,
        "version": f.version,
        "statement": (f.provenance or {}).get("statement") or spec["statement"],
        "confirmed_at": (f.provenance or {}).get("at"),
    }


def employment_history_complete(db, profile_id: str = "default") -> dict | None:
    return attestation(db, EMPLOYMENT_HISTORY["id"], profile_id)
