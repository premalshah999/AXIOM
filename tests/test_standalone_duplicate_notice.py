"""A standalone "You have already applied to this position" is the whole submit error (Qualcomm, 2026-10-29): the rerun
stopped rather than submitting a duplicate, but was parked as a generic browser failure. Only a statement about this
posting counts, never a sign-in prompt, a question or a notice about another job."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot.forms import FormSession


class FellThrough(Exception):
    """The text was not a standalone duplicate notice; rejection() went on to its other checks."""


@pytest.mark.parametrize("text,duplicate", [
    ("You have already applied to this position.", True),
    ("Application form\nYou’ve already applied for this job!\nSubmit application", True),
    ("Already applied? Sign in", False),
    ("Have you already applied to this position?", False),
    ("If you have already applied to this position, sign in.", False),
    ("You have already applied to another position.", False),
])
async def test_standalone_current_job_duplicate_is_a_rejection(text, duplicate):
    form = SimpleNamespace(_page_text=AsyncMock(return_value=text), page=SimpleNamespace(url="https://careers.example/apply?pid=42"),
                           refusal_notes=[], fields={}, scan=AsyncMock(side_effect=FellThrough),
                           inline_field_errors=AsyncMock(side_effect=FellThrough))
    try:
        result = await FormSession.rejection(form)
    except (FellThrough, AttributeError):
        result = None
    assert bool(result and result.get("category") == "already_applied") is duplicate
