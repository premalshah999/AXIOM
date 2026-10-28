"""Koniag (Dayforce, 2026-10-27): reopened on Candidate Info, the Personal Information section stood in edit mode with
Update / Cancel, and the page's Next did nothing until the section was saved. The stall breaker saves the one open
section editor before the step action repeats."""

from jobpilot.browser import open_editor_commit


def controls(*labels):
    return [{"id": f"c{i}", "label": label} for i, label in enumerate(labels)]


def test_the_open_section_editors_update_is_its_commit():
    page = controls("Candidate Info", "Questionnaire", "Import Resume", "Update", "Cancel", "Add Cover Letter",
                    "Cancel", "Next")
    assert open_editor_commit(page, "c7")["label"] == "Update"


def test_a_page_without_an_open_editor_has_no_commit():
    assert open_editor_commit(controls("Import Resume", "Cancel", "Next"), "c2") is None
    assert open_editor_commit(controls("Save as draft", "Cancel", "Next"), "c2") is None


def test_two_open_editors_are_ambiguous_and_the_clicked_control_is_never_its_own_commit():
    assert open_editor_commit(controls("Save", "Cancel", "Update", "Cancel", "Next"), "c4") is None
    assert open_editor_commit(controls("Save", "Cancel"), "c0") is None
