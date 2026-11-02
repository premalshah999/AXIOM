"""Greenhouse's education block (no school box): Degree, Discipline, "End date month", "End date year". The dates are the
highest degree's and come from the saved profile instead of going to the applicant."""

from jobpilot.answers import Resolver
from jobpilot.forms import mark_history_rows
from jobpilot.schemas import EducationEntry, Profile

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def block():
    base = {"value": "", "valid": True, "group": "", "section": "", "required": True, "options": []}
    return [
        {"id": "d", "label": "Degree", "type": "combobox", **base},
        {"id": "m", "label": "Discipline", "type": "combobox", **base},
        {"id": "em", "label": "End date month", "type": "combobox", **{**base, "options": MONTHS}},
        {"id": "ey", "label": "End date year", "type": "number", **base},
    ]


def test_end_date_parts_come_from_the_highest_degree(service):
    profile = Profile(education=[
        EducationEntry(school="Example University", degree="Master's Degree", field_of_study="Computer Science", end="2026-05"),
        EducationEntry(school="Sample College", degree="Bachelor's Degree", field_of_study="Physics", end="2023-05"),
    ])
    resolver = Resolver(profile, service.db, service.config, job={"ats": "greenhouse", "url": "https://example.test/job"})
    fields = block()
    mark_history_rows(fields)
    by_id = {f["id"]: f for f in fields}
    assert by_id["em"]["history_row"] == {"kind": "education", "index": "highest"}
    assert resolver.local(by_id["em"]).value == "May"
    assert resolver.local(by_id["ey"]).value == "2026"


def test_a_form_with_a_work_block_nearer_keeps_its_own_dates(service):
    fields = block()
    base = {"value": "", "valid": True, "group": "", "section": "", "required": True, "options": []}
    fields += [
        {"id": "c", "label": "Company name", "type": "text", **base},
        {"id": "t", "label": "Title", "type": "text", **base},
        {"id": "wm", "label": "End date month", "type": "combobox", **base},
    ]
    mark_history_rows(fields)
    assert "history_row" not in {f["id"]: f for f in fields}["wm"] or {f["id"]: f for f in fields}["wm"]["history_row"]["kind"] != "education"
