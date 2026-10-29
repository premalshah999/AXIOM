"""Prompt layout: stable content first, per-call content last.

The provider caches the longest prefix a request shares with an earlier one (Sail Research reports it in 256-token
blocks), so a model call is cheap and fast only as far as its prompt matches the previous prompt byte for byte. Two
things broke that on 2026-10-28: the navigator put the page's scan counter (`revision`) at the top of the observation, so
every re-scan cut the shared prefix at ~1.8k of 20-40k tokens (cache hit 1792 tokens on NavigationDecision calls whose
page had not changed in content), and the profile facts were ordered by the questions on the page, so no two calls shared
them. Layout changes here never drop or rewrite a fact: tests pin that every value still reaches the model.
"""

import json


def canonical(mapping):
    """The same entries in one fixed order: facts sorted by reference, so every call renders them identically."""
    return {key: mapping[key] for key in sorted(mapping)}


def _unset(value):
    return value is None or value is False or value == "none" or (isinstance(value, str | list | dict | tuple) and not value)


def without_defaults(record, keep=("value",)):
    """A dumped record minus its unset/default metadata (None, "", [], {}, False, "none"): same facts, fewer tokens.

    `keep` names are never dropped: an applicant fact whose value is False ("sponsorship needed: no") is the answer."""
    return {k: v for k, v in record.items() if k in keep or not _unset(v)}


def compact_scope(meta):
    """packet_metadata without its empty entries (row_id None, no conflicts/missing/expired): same references, fewer tokens."""
    return {
        **without_defaults({k: v for k, v in meta.items() if k != "references"}, keep=("store_version",)),
        "references": [without_defaults(r, keep=("id",)) for r in meta.get("references", [])],
    }


def navigation_payload(
    *,
    applicant_context,
    supported_answers,
    company,
    role,
    profile_history,
    owner_instruction,
    repair_instruction,
    specialist_handoff,
    observation,
    blocked_step,
    visual_observation,
    recent_actions,
):
    """The navigator's user message. Order is the cache: what stays the same across steps leads.

    The run's own constants lead, then applicant evidence (it changes only when the page's questions do), then the
    observation without its scan counter; what changes every step (blocked step, picture, recent actions, and the
    counter itself) trails."""
    page = dict(observation)
    revision = page.pop("revision", None)
    return {
        "company": company,
        "role": role,
        "profile_history": profile_history,
        "owner_instruction": owner_instruction,
        "repair_instruction": repair_instruction,
        "specialist_handoff": specialist_handoff,
        "applicant_context": applicant_context,
        "supported_answers": supported_answers,
        "observation": page,
        "blocked_step": blocked_step,
        "visual_observation": visual_observation,
        "recent_actions": recent_actions,
        "observation_revision": revision,
    }


def dumps(payload):
    return json.dumps(payload, default=str)
