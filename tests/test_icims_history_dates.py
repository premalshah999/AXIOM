"""iCIMS work rows split each date into Month (Jan..Dec), Day (1..31) and Year boxes, and offer no "currently work
here" checkbox. ICE refused a current job's blank End Date with "Invalid Data Error" although the field carries no
required marker (2026-10-19); Atlassian accepts either. A current role's end date is today, as on SuccessFactors."""

from datetime import date

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile, WorkEntry

MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def resolver(service, ats="icims"):
    profile = Profile(
        work=[
            WorkEntry(company="Example Research Lab", title="ML Research Engineer", start="2025-07", current=True),
            WorkEntry(company="Example AI", title="Data Science Intern", start="2023-07", end="2024-08"),
        ]
    )
    return Resolver(profile, service.db, service.config, job={"ats": ats, "url": "https://careers-acme.icims.com/jobs/1/x/job"})


def box(label, row, options=None):
    return {
        "id": f"{label}-{row}",
        "label": label,
        "type": "select" if options else "text",
        "required": False,
        "value": "",
        "valid": True,
        "group": f"Work Experience {row}",
        "options": options or [],
    }


def test_a_current_role_ends_today_in_icims_month_day_year_boxes(service):
    today, value = date.today(), resolver(service)
    month = value.local(box("End Date Month", 1, MONTHS))
    day = value.local(box("End Date Day", 1, [""] + [str(d) for d in range(1, 32)]))
    year = value.local(box("End Date Year", 1))
    assert (month.value, day.value, year.value) == (MONTHS[today.month], str(today.day), str(today.year))


def test_a_finished_role_keeps_its_own_dates_and_a_month_date_starts_on_the_first(service):
    value = resolver(service)
    assert value.local(box("End Date Month", 2, MONTHS)).value == "Aug"
    assert value.local(box("End Date Year", 2)).value == "2024"
    # ICE refused "May / (placeholder) / 2026" (2026-10-19); iCIMS's parser writes day 1 for month-precision dates.
    assert value.local(box("Start Date Day", 2, [""] + [str(d) for d in range(1, 32)])).value == "1"
    assert value.local(box("End Date Day", 2, [""] + [str(d) for d in range(1, 32)])).value == "1"


def test_other_ats_still_leave_a_current_end_date_blank(service):
    skipped = resolver(service, ats="workday").local(box("End Date Month", 1, MONTHS))
    assert skipped is None or skipped.disposition == "skip"


def test_a_required_current_end_date_ends_today_on_any_platform(service):
    # Dallas County and Community Health Systems (Oracle, 2026-10-26): rows without a currently-employed checkbox
    # require an end date, and the owner was asked for the month of a job that has not ended.
    today = date.today()
    answer = resolver(service, ats="oracle").local({**box("End Date Month", 1, MONTHS), "required": True})
    assert answer.disposition == "answer" and answer.value == MONTHS[today.month]
