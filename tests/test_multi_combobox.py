"""An Oracle CX multiselect was given the whole answer ("Unit Testing\\nArtificial Intelligence\\nJava") as one search
and refused it, while two of the values already showed as pills (2026-10-27)."""

import pytest

from jobpilot import widgets


class Select:
    def __init__(self, pills):
        self.pills = list(pills)

    async def evaluate(self, script, arg=None):
        return "\n".join(self.pills)


async def test_each_missing_value_is_picked_once_and_held_pills_are_kept(monkeypatch):
    el, searched = Select(["Unit Testing", "Artificial Intelligence"]), []

    async def combobox(frame, owner, value, multi=False, **options):
        assert multi
        searched.append(value)
        owner.pills.append(value)
        return "\n".join(owner.pills)

    monkeypatch.setattr(widgets, "combobox", combobox)
    result = await widgets.multi_combobox(None, el, "Unit Testing\nArtificial Intelligence\nJava")
    assert searched == ["Java"]
    assert result.splitlines() == ["Unit Testing", "Artificial Intelligence", "Java"]


async def test_a_value_that_does_not_stick_is_refused(monkeypatch):
    el = Select([])

    async def combobox(frame, owner, value, multi=False, **options):
        return ""

    monkeypatch.setattr(widgets, "combobox", combobox)
    with pytest.raises(ValueError, match="did not retain"):
        await widgets.multi_combobox(None, el, "Python; SQL")
