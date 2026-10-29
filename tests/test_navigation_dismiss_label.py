import pytest

from jobpilot.browser import is_dismiss_control


@pytest.mark.parametrize("label", ["Close menu", " close dialog ", "Close modal", "Close popup", "Close", "Cancel", "×"])
def test_explicit_dismiss_labels(label):
    assert is_dismiss_control({"label": label})


@pytest.mark.parametrize("label", ["Close account", "Close application", "Save and close", "Open menu", ""])
def test_unrelated_actions_are_not_dismiss_controls(label):
    assert not is_dismiss_control({"label": label})
