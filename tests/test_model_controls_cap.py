"""Lenovo's Avature form (2026-11-01, run 2eedceda): 9 header links, then 71 skill-chip "x" buttons, then the page's own
Continue. The model saw the first 80 controls, never Continue, and clicked "Skip to content" until recovery stopped."""

from jobpilot.browser import model_controls, navigation_observation


def controls(*labels):
    return [{"id": f"c{i}", "label": label} for i, label in enumerate(labels)]


def test_repeated_remove_buttons_do_not_push_continue_past_the_cap():
    page = controls(*[f"Link {i}" for i in range(9)], *["×"] * 71, "Add Another", "Continue", "Cancel")
    shown = [c["label"] for c in model_controls(page)]
    assert "Continue" in shown and "Cancel" in shown
    assert shown.count("×") == 3 and len(shown) <= 80


def test_same_label_in_different_sections_is_not_collapsed():
    page = [{"id": f"r{i}", "label": "Remove", "section": f"Job {i}"} for i in range(6)]
    assert len(model_controls(page)) == 6


def test_navigation_observation_lists_the_continue_button():
    obs = {"url": "https://example.test/apply", "fields": [], "text": "",
           "controls": controls(*["×"] * 90, "Continue")}
    assert "Continue" in [c["label"] for c in navigation_observation(obs)["controls"]]
