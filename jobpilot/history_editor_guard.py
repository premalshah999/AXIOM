"""Verify an explicitly offered education Save without gating optional empty fields."""

import re
from urllib.parse import urlsplit

from . import widgets


def education_save_context(form, observation, button):
    location = urlsplit(observation.get("url", ""))
    if not (
        location.scheme == "https"
        and location.hostname == "jobs.smartrecruiters.com"
        and location.port in {None, 443}
        and not location.username
        and not location.password
        and re.fullmatch(r"save", button["label"], re.I)
        and re.search(r"\beducation\b", button.get("section", ""), re.I)
    ):
        return None
    fields = [f for f in observation["fields"] if re.search(r"\beducation\s*\d+\b", f.get("group", ""), re.I)]
    groups = {f["group"] for f in fields}
    if len(groups) != 1:
        raise ValueError("Education Save has no unique observed editor row")
    for field in fields:
        actual = field.get("value", "")
        if not field.get("required") and actual in {"", None}:
            continue
        expected = form.expected.get(field["id"])
        if field.get("required") and (
            actual in {"", None} or not field.get("valid", True) or expected is None
        ):
            raise ValueError("Education editor has an unverified required field")
        if expected is not None and not widgets.same_value(field, actual, expected):
            raise ValueError("Education editor has an uncommitted supported answer")
    return groups.pop()


def education_saved(context, observation):
    if context and any(f.get("group") == context for f in observation["fields"]):
        raise ValueError("Education Save did not close its observed editor row")


def history_answer(resolver, field):
    """Narrow history questions, before broad School/Location/name rules."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from .schemas import Answer

    match = re.fullmatch(r"Education\s+(\d+)", field.get("group", ""), re.I)
    if not match:
        return None
    index = int(match[1]) - 1
    if not 0 <= index < len(resolver.profile.education):
        return None
    label = field["label"].strip()
    if re.fullmatch(r"School location", label, re.I):
        return Answer(
            field_id=field["id"],
            disposition="review" if field.get("required") else "skip",
            reason="Institution location is not the applicant home address or institution name",
        )
    if re.fullmatch(r"I currently attend", label, re.I):
        row = resolver.profile.education[index]
        end = re.fullmatch(r"(\d{4})-(\d{2})(?:-\d{2})?", row.end or "")
        start = re.fullmatch(r"(\d{4})-(\d{2})(?:-\d{2})?", row.start or "")
        current = datetime.now(ZoneInfo(resolver.config.timezone))
        today = current.year, current.month
        if end and 1 <= int(end[2]) <= 12 and (int(end[1]), int(end[2])) < today:
            if start and (int(start[1]), int(start[2])) > today:
                return None  # Contradictory history cannot establish attendance.
            ref = f"profile:education.{index}.end"
            return Answer(
                field_id=field["id"],
                value="false",
                disposition="answer",
                evidence_ids=[ref],
                supporting_facts=[ref],
                reason="Recorded degree ended before this month",
            )
    return None
