"""SmartRecruiters' German form names none of its drop zones: two read "Eine Datei auswählen oder hier ablegen." beside
"Profilbild hochladen", no resume was attached and "Weiter" never advanced (Sopra Steria, four jobs, 2026-10-25/26)."""

from jobpilot.forms import FormSession


def session(*labels):
    form = object.__new__(FormSession)
    form.fields = {f"f{i}": {"id": f"f{i}", "type": "file", "label": label, "group": ""} for i, label in enumerate(labels)}
    return form


def test_the_first_unnamed_drop_zone_is_the_resume_never_the_photo():
    form = session("Profilbild hochladen", "Eine Datei auswählen oder hier ablegen.", "Eine Datei auswählen oder hier ablegen.")
    assert [f["id"] for f in form.resume_fields()] == ["f1"]
    english = session("Upload profile picture", "Choose a file or drop it here", "Choose a file or drop it here")
    assert [f["id"] for f in english.resume_fields()] == ["f1"]


def test_named_and_differently_described_uploads_are_not_guessed():
    assert [f["id"] for f in session("Cover letter", "Resume/CV").resume_fields()] == ["f1"]
    assert session("Choose a file or drop it here", "Select a file for your transcript").resume_fields() == []
    assert session("Profile photo", "Avatar image").resume_fields() == []
