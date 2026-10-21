"""SAP's SuccessFactors application page (careers.sap.com, 2026-10-16) repeats unnumbered work rows:
Start Date, End Date, * Employer, Industry, * Position Title. The model mixed the rows, read every Start
Date as availability (today), and Apply answered "Please correct the errors below."."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession, mark_history_rows
from jobpilot.schemas import Profile, WorkEntry

WORK = [
    WorkEntry(company="Acme Labs", title="AI Engineer", start="2025-12", current=True),
    WorkEntry(company="Beta University", title="Research Engineer", start="2025-07", end="2026-05"),
    WorkEntry(company="Gamma Center", title="ML Engineer", start="2024-07", end="2025-08"),
]


def sap_rows(rows, *, other_start_dates=0):
    date = {"type": "text", "widget": "date-text", "date_format": "mdy", "required": False}
    fields = []
    for i in range(rows):
        fields += [
            {"id": f"s{i}", "label": "Start Date", **date},
            {"id": f"e{i}", "label": "End Date", **date},
            {"id": f"c{i}", "label": "* Employer", "type": "text", "required": True},
            {"id": f"i{i}", "label": "Industry", "type": "combobox", "required": False},
            {"id": f"t{i}", "label": "* Position Title", "type": "text", "required": True},
        ]
    fields += [{"id": f"x{i}", "label": "Start Date", **date} for i in range(other_start_dates)]
    for f in fields:
        f.update({"value": "", "valid": True, "group": "", "section": "", "options": []})
    return fields


def rows_of(fields):
    return {f["id"]: f["history_row"]["index"] for f in fields if "history_row" in f}


def test_each_repeated_row_is_its_own_history_entry():
    fields = sap_rows(3)
    mark_history_rows(fields)
    assert rows_of(fields) == {f"{k}{i}": i for i in range(3) for k in "sect"}


def test_a_label_repeated_by_another_block_is_never_placed():
    fields = sap_rows(3, other_start_dates=2)
    mark_history_rows(fields)
    placed = rows_of(fields)
    assert not any(k.startswith(("s", "x")) for k in placed)
    assert placed["e2"] == 2 and placed["c2"] == 2 and placed["t2"] == 2


def test_single_row_needs_all_four_labels_and_stale_rows_are_cleared():
    single = sap_rows(1)
    mark_history_rows(single)
    assert rows_of(single) == {"s0": 0, "e0": 0, "c0": 0, "t0": 0}
    pair = [f for f in sap_rows(1) if f["id"] != "e0"]
    mark_history_rows(pair)
    assert rows_of(pair) == {}
    fields = sap_rows(2)
    mark_history_rows(fields)
    assert rows_of(fields)
    rescanned = [f for f in fields if f["id"] != "t1"]
    mark_history_rows(rescanned)
    assert rows_of(rescanned) == {}


def test_rows_answer_from_their_own_entry_never_from_availability(service):
    profile = Profile(work=WORK, availability="Immediately")
    resolver = Resolver(
        profile, service.db, service.config, job={"ats": "successfactors", "url": "https://example.test/job"}
    )
    fields = sap_rows(3)
    mark_history_rows(fields)
    by_id = {f["id"]: f for f in fields}
    assert resolver.local(by_id["c2"]).value == "Gamma Center"
    assert resolver.local(by_id["t2"]).value == "ML Engineer"
    start = resolver.local(by_id["s0"])
    assert start.value == "2025-12-01" and start.evidence_ids == ["profile:work.0.start"]
    assert resolver.local(by_id["e1"]).value == "2026-05-01"
    assert resolver.local(by_id["e0"]).disposition == "skip"  # The current role's optional end date stays blank.


async def rejection(page, tmp_path, body):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(body)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    return await form.rejection()


async def test_correct_the_errors_banner_is_a_validation_refusal_naming_observed_fields(page, tmp_path):
    body = """<div>Please correct the errors below.</div>
    <label for=s>Start Date</label><input id=s value="10/16/2026">
    <label for=e>End Date</label><input id=e value="08/31/2025">
    <div>End date must be after Start date.</div><button>Apply</button>"""
    r = await rejection(page, tmp_path, body)
    assert r["category"] == "validation" and r["missing_fields"] == ["End Date"]


async def test_correct_the_errors_banner_without_named_fields_is_still_not_a_submission(page, tmp_path):
    r = await rejection(page, tmp_path, "<div>Please correct the errors below.</div><button>Apply</button>")
    assert r["category"] == "validation" and r["missing_fields"] == []


def classic_rows(education=2, work=3):
    """Syncreon's classic SuccessFactors page (2026-10-21): education rows above the work rows, both with End Date."""
    date = {"type": "text", "widget": "date-text", "date_format": "mdy", "required": True}
    fields = []
    for i in range(education):
        fields += [
            {"id": f"lvl{i}", "label": "Education Level", "type": "combobox", "required": False},
            {"id": f"maj{i}", "label": "Major or Subject", "type": "combobox", "required": False},
            {"id": f"sch{i}", "label": "* School", "type": "text", "required": True},
            {"id": f"from{i}", "label": "From Date", **date},
            {"id": f"to{i}", "label": "End Date", **date},
        ]
    for i in range(work):
        fields += [
            {"id": f"s{i}", "label": "Start Date", **date},
            {"id": f"e{i}", "label": "End Date", **date},
            {"id": f"c{i}", "label": "* Company Name", "type": "text", "required": True},
            {"id": f"t{i}", "label": "* Job Title", "type": "text", "required": True},
        ]
    for f in fields:
        f.update({"value": "", "valid": True, "group": "", "section": "", "options": []})
    return fields


def test_education_rows_sharing_end_date_leave_each_work_row_its_own_dates():
    fields = classic_rows()
    mark_history_rows(fields)
    placed = {f["id"]: (f["history_row"]["kind"], f["history_row"]["index"]) for f in fields if "history_row" in f}
    assert all(placed[f"{k}{i}"] == ("work", i) for i in range(3) for k in ("s", "e", "c", "t"))
    assert all(placed[f"{k}{i}"] == ("education", i) for i in range(2) for k in ("lvl", "maj", "sch", "from", "to"))


def test_classic_rows_answer_from_their_own_entries(service):
    from jobpilot.schemas import EducationEntry

    education = [
        EducationEntry(school="State University", degree="Master of Science", field_of_study="Data Science",
                       start="2024-08", end="2026-05"),
        EducationEntry(school="Institute of Technology", degree="BE", field_of_study="Computer Engineering",
                       start="2020-06", end="2024-06"),
    ]
    profile = Profile(work=WORK, education=education)
    resolver = Resolver(
        profile, service.db, service.config, job={"ats": "successfactors", "url": "https://example.test/job"}
    )
    fields = classic_rows()
    mark_history_rows(fields)
    by_id = {f["id"]: f for f in fields}
    assert resolver.local(by_id["e1"]).value == "2026-05-01"
    assert resolver.local(by_id["e2"]).value == "2025-08-01"
    assert resolver.local(by_id["sch1"]).value == "Institute of Technology"
    second_start = resolver.local(by_id["from1"])
    assert second_start.value == "2020-06-01" and second_start.evidence_ids == ["profile:education.1.start"]


def test_a_single_school_is_still_answered_as_a_whole():
    fields = classic_rows(education=1)
    mark_history_rows(fields)
    assert not any(f["history_row"]["kind"] == "education" for f in fields if "history_row" in f)
    assert all(by["history_row"]["kind"] == "work" for by in fields if by["id"].startswith(("e", "s")) and "history_row" in by)
    assert {f["id"] for f in fields if "history_row" in f} >= {"e0", "e1", "e2", "s0", "c2"}
