"""America's Tire (iCIMS, 2026-10-27): row 3 of five required experience rows still held row 5's employer and title, both
rows were read as entry 5, and the third job was never written; every Submit Profile came back "Invalid Data Error"."""

from types import SimpleNamespace

from jobpilot.forms import regroup_history_rows

PROFILE = SimpleNamespace(
    work=[
        SimpleNamespace(company="Initech", title="AI Engineer"),
        SimpleNamespace(company="Globex", title="Research Engineer"),
        SimpleNamespace(company="Hooli", title="Research Engineer"),
        SimpleNamespace(company="Vandelay", title="Data Intern"),
        SimpleNamespace(company="Acme", title="Software Intern"),
    ],
    education=[],
)


def row(position, company="", title=""):
    """An Employer/Title/Description triple of one repeated row, as the scanner reports it."""
    identity = {"company": company, "title": title}
    return [
        {"id": f"r{position}-{label}", "label": label, "group": f"Work Experience {position}", "frame_url": "https://x",
         "history_kind": "work", "history_identity": identity}
        for label in ("Employer", "Title", "Description")
    ]


def groups(fields):
    return [f["group"] for f in fields if f["label"] == "Employer"]


def test_two_rows_holding_one_job_keep_one_entry_each():
    fields = (
        row(1, "Initech", "AI Engineer")
        + row(2, "Globex", "Research Engineer")
        + row(3, "Acme", "Software Intern")  # a stale copy of row 5
        + row(4, "Vandelay", "Data Intern")
        + row(5, "Acme", "Software Intern")
    )
    regroup_history_rows(fields, PROFILE)
    assert groups(fields) == [f"Work Experience {n}" for n in range(1, 6)]
    # Every member of a row moves with it.
    assert {f["group"] for f in fields if f["id"].startswith("r3-")} == {"Work Experience 3"}


def test_a_row_prefilled_with_another_entry_still_takes_that_entry():
    # A parsed resume put the jobs in another order; each row keeps the job it shows, and the blank rows take the rest.
    fields = row(1, "Globex", "Research Engineer") + row(2, "Initech", "AI Engineer") + row(3) + row(4) + row(5)
    regroup_history_rows(fields, PROFILE)
    assert groups(fields) == [f"Work Experience {n}" for n in (2, 1, 3, 4, 5)]


def test_a_displaced_duplicate_takes_the_first_entry_no_row_holds():
    # Row 1 shows entry 3's job, row 3 shows it too: row 3 keeps it (its own place), row 1 is answered by position.
    fields = row(1, "Hooli", "Research Engineer") + row(2) + row(3, "Hooli", "Research Engineer")
    regroup_history_rows(fields, PROFILE)
    assert groups(fields) == ["Work Experience 1", "Work Experience 2", "Work Experience 3"]
    # Row 2 shows entry 1's job and row 1 shows entry 3's, which row 3 holds: row 1 takes the first free entry.
    fields = row(1, "Hooli", "Research Engineer") + row(2, "Initech", "AI Engineer") + row(3, "Hooli", "Research Engineer")
    regroup_history_rows(fields, PROFILE)
    assert groups(fields) == ["Work Experience 2", "Work Experience 1", "Work Experience 3"]


def test_an_unknown_or_blank_row_keeps_its_place():
    fields = row(1, "Unlisted Co", "Clerk") + row(2)
    regroup_history_rows(fields, PROFILE)
    assert groups(fields) == ["Work Experience 1", "Work Experience 2"]
