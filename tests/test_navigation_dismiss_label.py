import pytest

from jobpilot.browser import is_dismiss_control


@pytest.mark.parametrize(
    "label",
    [
        "Close menu", " close dialog ", "Close modal", "Close popup", "Close", "Cancel", "×",
        # Named after what it closes: Meta's stood over the application, Microsoft's over its sign-in (2026-10-27, 11-01).
        "Close Sign up for Career Profile dialog",
        "Close candidate authentication modal",
    ],
)
def test_explicit_dismiss_labels(label):
    assert is_dismiss_control({"label": label})


@pytest.mark.parametrize("label", ["Close account", "Close application", "Save and close", "Open menu", "", "Close my account dialogue", "Close this job application"])
def test_unrelated_actions_are_not_dismiss_controls(label):
    assert not is_dismiss_control({"label": label})
