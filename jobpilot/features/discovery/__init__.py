"""Discovery policy: freshness, dedup, classification cache, readiness, backpressure.

Public interface (decisions only; Codex's DBOS workers execute them):

- DiscoveryPolicy, Capacity, DueWork, ReadyApplication
- discover_due(db, policy, capacity, at) -> DueWork
- ready_applications(db, policy, limit, at) -> [ReadyApplication]   (queue via service.queue)
- classify(db, jobs, classify_fn, policy, profile) -> report; existing_rank_adapter(db, config)
- register(session, job, source_id, posted, basis) -> (canonical_job_id, duplicate)
- gate(job, policy) -> reason | None;  fresh(posted, basis, first_seen, policy) -> (bool, basis)
- graduation_gate(job, profile, today) -> "" | reason  (posting's stated graduation window vs degree dates; a required PhD vs recorded degrees)
- defer(db, job_id, reason, seconds);  record_source(db, source_id, ok, error)
- status(db, policy, capacity) -> counts and bottleneck
- router(service) -> APIRouter mounted at /api/v2/discovery
"""

from .classify import classify, existing_rank_adapter
from .graduation import graduation_gate
from .policy import POLICY_VERSION, DiscoveryPolicy, fresh, gate, register, requisition_key
from .readiness import (
    Capacity,
    DueWork,
    ReadyApplication,
    capacity,
    defer,
    discover_due,
    ready_applications,
    record_source,
    status,
)
from .router import policy_for, router

__all__ = [
    "POLICY_VERSION",
    "Capacity",
    "DiscoveryPolicy",
    "DueWork",
    "ReadyApplication",
    "capacity",
    "classify",
    "defer",
    "discover_due",
    "existing_rank_adapter",
    "fresh",
    "gate",
    "graduation_gate",
    "policy_for",
    "ready_applications",
    "record_source",
    "register",
    "requisition_key",
    "router",
    "status",
]
