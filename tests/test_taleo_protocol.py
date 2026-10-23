"""Taleo history rows (Textron 2026-10-23): "Month"/"Year" menus with no caption; every Month stayed on its placeholder
and "The field "Start Date" is mandatory." came back 28 times. The ids name the row and the date."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile, WorkEntry
from jobpilot.taleo_protocol import apply

FRAME = "https://acme.taleo.net/careersection/careersection/candidateacquisition/flow.jsf"
BASE = "et-ef-content-ftf-gp-j_id_id16pc9-page_2-we-wei-{row}-frm-dv_cs_experience_{name}"
MONTHS = ["Month", *[f"{m:02d}" for m in range(1, 13)]]


def field(row, name, label, value="", kind="select", options=None):
    return {"id": f"{row}-{name}", "name": BASE.format(row=row, name=name), "label": label, "type": kind,
            "value": value, "frame_url": FRAME, "options": options or [], "group": ""}


def test_experience_rows_are_named_and_placed_by_employer():
    fields = [
        field(0, "Employer", "Employer", value="Beta Labs", kind="text"),
        field(0, "BeginDate.month", "Month", options=MONTHS),
        field(0, "BeginDate.year", "Year"),
        field(1, "Employer", "Employer", value="", kind="text"),
        field(1, "EndDate.month", "Month", options=MONTHS),
        field(1, "CurrentEmployer", "Current Job", kind="checkbox"),
    ]
    apply(fields, ["Acme University", "Beta Labs"], [])
    named = {f["id"]: (f["label"], f["group"], f.get("date_parts")) for f in fields}
    # The parser put Beta Labs (the profile's second job) in row 0.
    assert named["0-BeginDate.month"] == ("Start Date Month", "Work Experience 2", ["Month", "Year"])
    assert named["0-Employer"][:2] == ("Employer Name", "Work Experience 2")
    assert named["1-EndDate.month"] == ("End Date Month", "Work Experience 2", ["Month"])
    assert named["1-CurrentEmployer"][:2] == ("I currently work here", "Work Experience 2")


def test_other_platforms_are_untouched():
    other = {**field(0, "BeginDate.month", "Month"), "frame_url": "https://acme.wd5.myworkdayjobs.com/x"}
    apply([other], ["Acme"], [])
    assert other["label"] == "Month" and other["group"] == ""


def test_the_start_month_is_answered_from_the_profile(service):
    profile = Profile(work=[WorkEntry(company="Acme University", title="AI Engineer", start="2024-08", end="")])
    month = field(0, "BeginDate.month", "Month", options=MONTHS)
    year = field(0, "BeginDate.year", "Year", options=["Year", "2026", "2025", "2024", "2023"])
    employer = field(0, "Employer", "Employer", value="Acme University", kind="text")
    apply([month, year, employer], ["Acme University"], [])
    resolver = Resolver(profile, service.db, service.config)
    assert resolver.local({**month, "id": "m"}).value == "08"
    assert resolver.local({**year, "id": "y"}).value == "2024"
