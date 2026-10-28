"""TikTok (2026-10-28): each history row dates itself with one "Start & end date" box. The scanner no longer reads it as
a single date, and the row's period answers it: "MM/YYYY - MM/YYYY", or "- Present" for a current role."""

from jobpilot.answers.history import HISTORY_PERIOD


def test_period_labels():
    for label in ("Start & end date", "Start and end dates", "Dates of employment", "From - To", "Period"):
        assert HISTORY_PERIOD.fullmatch(label), label
    for label in ("Start date", "End date", "Graduation date"):
        assert not HISTORY_PERIOD.fullmatch(label), label



def test_a_period_box_takes_the_rows_two_dates(service):
    from jobpilot.answers import Resolver
    from jobpilot.schemas import EducationEntry, Profile, WorkEntry

    profile = Profile(
        work=[WorkEntry(company="Acme Labs", title="AI Engineer", start="2024-08", end="", current=True)],
        education=[EducationEntry(school="Example University", degree="Master of Science", start="2023-08", end="2025-05")],
    )

    def ask(label, group):
        return Resolver(profile, service.db, service.config).local(
            {"id": "x", "label": label, "type": "text", "group": group, "required": True, "options": []})

    assert ask("Start & end date", "Work Experience 1").value == "08/2024 - Present"
    assert ask("Start & end date *", "Education 1").value == "08/2023 - 05/2025"
