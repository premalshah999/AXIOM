"""Shared browser observations and verified action feedback.

Revisions include DOM identities; progress fingerprints deliberately do not.
Neither contract carries credentials, codes, tokens or applicant field values.
"""

import hashlib
import json
import re
from collections import Counter
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field


def missing_posting_notice(obs, job_url):
    """Prominent employer error, even when a results page contains search controls."""
    current, expected = urlsplit(obs.get("url", "")), urlsplit(job_url)
    if not (current.hostname and current.hostname == expected.hostname):
        return False
    if obs.get("busy") or obs.get("dialog") or any(
        dialog.get("field_ids") or dialog.get("control_ids") for dialog in obs.get("dialogs", [])
    ):
        return False
    # Headings/errors describe the current page; body prose can quote a closed
    # job or mention one among recommended postings and cannot prove this one gone.
    pattern = re.compile(
        r"^(?:error\s*:\s*)?(?:(?:the|this)\s+)?(?:requested\s+)?"
        r"(?:job|position|posting|requisition)(?:\s+(?:posting|listing))?\s+"
        r"(?:(?:could not|cannot|can't) be found|(?:was |is )?not found|"
        r"(?:is |has been )?(?:no longer available|closed|removed|expired))\s*[.!]?$", re.I
    )
    return any(pattern.fullmatch(str(text).strip()) for text in [*obs.get("headings", []), *obs.get("errors", [])])


FailureCategory = Literal[
    "stale",
    "covered",
    "disabled",
    "missing",
    "timeout",
    "invalid",
    "unsupported",
    "no_progress",
    "missing_fact",
    "model_unavailable",
    "invalid_model_output",
    "unavailable",
]


class ActionResult(BaseModel):
    action: str
    executed: bool = False
    verified: bool = False
    before_revision: int = 0
    after_revision: int = 0
    changes: dict[str, int | bool] = Field(default_factory=dict)
    failure_category: FailureCategory | None = None
    detail: str = Field(default="", max_length=240)


def field_identity(field):
    return (
        field.get("label", ""),
        field.get("type", ""),
        field.get("section", ""),
        field.get("group", ""),
        field.get("name", ""),
        field.get("automation_id", ""),
        field.get("slot", 0),
    )


def semantic_state(obs):
    parsed = urlsplit(obs.get("url", ""))
    return {
        "page": (parsed.hostname, parsed.path),
        "step": obs.get("headings", [])[:12],
        "busy": bool(obs.get("busy")),
        "errors": sorted(set(obs.get("errors", []))),
        "fields": sorted(
            (
                field_identity(f),
                str(f.get("value", "")),
                bool(f.get("valid", True)),
                bool(f.get("required")),
                f.get("error", ""),
            )
            for f in obs.get("fields", [])
        ),
        "controls": sorted((field_identity(b), bool(b.get("disabled"))) for b in obs.get("controls", [])),
        "dialogs": [(d.get("title", ""), d.get("text", "")[:800]) for d in obs.get("dialogs", [])],
        "menu": sorted((o.get("text", ""), bool(o.get("leaf", True))) for o in obs.get("options", [])),
    }


def fingerprint(obs, *, revision=False):
    state = semantic_state(obs)
    if revision:
        state["nodes"] = [f.get("id") for f in obs.get("fields", [])] + [
            b.get("id") for b in obs.get("controls", [])
        ]
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def action_result(action, before, after, *, executed=True, failure=None, detail=""):
    left = {field_identity(f): f for f in before.get("fields", [])}
    right = {field_identity(f): f for f in after.get("fields", [])}
    changes = {
        "page_changed": semantic_state(before)["page"] != semantic_state(after)["page"],
        "fields_added": len(right.keys() - left.keys()),
        "fields_removed": len(left.keys() - right.keys()),
        "values_changed": sum(
            str(left[k].get("value", "")) != str(right[k].get("value", ""))
            for k in left.keys() & right.keys()
        ),
        "validation_changed": Counter(before.get("errors", [])) != Counter(after.get("errors", []))
        or any(left[k].get("valid") != right[k].get("valid") for k in left.keys() & right.keys()),
        "dialog_changed": semantic_state(before)["dialogs"] != semantic_state(after)["dialogs"],
        "menu_changed": semantic_state(before)["menu"] != semantic_state(after)["menu"],
        "busy_changed": bool(before.get("busy")) != bool(after.get("busy")),
    }
    return ActionResult(
        action=action,
        executed=executed,
        verified=executed and not failure and any(changes.values()),
        before_revision=before.get("revision", 0),
        after_revision=after.get("revision", 0),
        changes=changes,
        failure_category=failure,
        detail=detail[:240],
    )


class ProgressTracker:
    """Consecutive unchanged outcomes, independent of unstable DOM IDs."""

    def __init__(self, limit=3):
        self.limit = limit
        self.last = None
        self.unchanged = 0

    def observe(self, obs):
        current = fingerprint(obs)
        self.unchanged = self.unchanged + 1 if current == self.last else 0
        self.last = current
        return self.unchanged < self.limit
