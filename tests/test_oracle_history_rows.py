"""Oracle's Work and Education History (2026-10-23): "Employer Country"/"Employer City" took the employer's name, an
education row captioned "Education at University of Maryland" was not a history row, and an uncaptioned education
block's "Start Date Month" took the availability answer "Immediately". Each section was refused."""

from jobpilot.answers import Resolver
from jobpilot.forms import oracle_education_block
from jobpilot.schemas import EducationEntry, Profile, WorkEntry

PROFILE = Profile(
    work=[WorkEntry(company="Acme Labs", title="AI Engineer", location="College Park, MD", start="2024-08", end="")],
    education=[EducationEntry(school="University of Maryland", degree="Master of Science", field_of_study="Data Science",
                              start="2023-08", end="2025-05")],
    availability="Immediately",
    facts={"availability": "Immediately"},
)
MONTHS = ["", *[f"{m:02d}" for m in range(1, 13)]]


def ask(service, label, group="", **extra):
    return Resolver(PROFILE, service.db, service.config).local(
        {"id": "x", "label": label, "type": extra.pop("type", "text"), "group": group, "required": True,
         "options": extra.pop("options", []), **extra})


def test_employer_city_and_country_are_the_rows_location(service):
    city = ask(service, "Employer City", "Work Experience 1")
    assert city is not None and city.value == "College Park"
    country = ask(service, "Employer Country", "Work Experience 1", type="select",
                  options=["Canada", "United States", "Mexico"])
    assert country is None or country.value != "Acme Labs"


def test_a_row_named_by_its_school_is_that_education_entry(service):
    month = ask(service, "Start Date Month", "Education at University of Maryland", type="select", options=MONTHS,
                date_parts=["Month", "Year"])
    assert month is not None and month.value == "08"


def test_availability_never_fills_a_history_date_part(service):
    answer = ask(service, "Start Date Month", "", type="select", options=MONTHS)
    assert answer is None or "Immediately" not in str(answer.value)


def test_one_uncaptioned_oracle_education_block_is_education_1():
    frame = "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/1/apply/section/3"
    fields = [{"label": label, "frame_url": frame, "group": ""} for label in
              ("School Name", "Degree", "Start Date Month", "Start Date Year")]
    fields.append({"label": "Start Date Month", "frame_url": frame, "group": "Work Experience 1"})
    oracle_education_block(fields)
    assert [f["group"] for f in fields] == ["Education 1"] * 4 + ["Work Experience 1"]


def test_a_start_date_in_month_day_year_menus_is_today(service):
    """Legrand (Oracle, 2026-10-23): "earliest possible start date" as three menus; "0 days" went into Day."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo(service.config.timezone)).date()  # the engine's day, not the test machine's
    months = ["", *[f"{m:02d}" for m in range(1, 13)]]
    days = ["", *[str(d) for d in range(1, 32)]]
    years = ["", str(today.year), str(today.year + 1)]
    caption = "What is your earliest possible start date?"
    assert ask(service, caption + " Month", caption, type="select", options=months).value == f"{today.month:02d}"
    assert ask(service, caption + " Day", caption, type="select", options=days).value == str(today.day)
    assert ask(service, caption + " Year", caption, type="select", options=years).value == str(today.year)


def test_a_notice_period_in_days_is_a_number():
    from jobpilot.forms import numeric_year_counts

    fields = [{"label": "Notice Period in Days", "type": "text"}]
    numeric_year_counts(fields)
    assert fields[0]["type"] == "number"


def test_a_gpa_box_takes_the_number(service):
    """INTEGRIS (Oracle, 2026-10-23, REA): "3.9/4.0" was refused with "Enter a valid number"."""
    from jobpilot.schemas import EducationEntry, Profile

    saved = Profile(education=[EducationEntry(school="University of Maryland", degree="Master of Science", gpa="3.9/4.0")])
    resolver = Resolver(saved, service.db, service.config)
    gpa = resolver.local({"id": "g", "label": "GPA", "type": "text", "group": "Education 1", "options": []})
    assert gpa.value == "3.9"


def test_successfactors_rows_with_a_final_job_title_are_history_rows():
    """HF Sinclair (2026-10-23): "* Employer Name / Start Date / End Date / * Final Job Title" rows had no caption;
    the bare Start Date took the availability answer (today) and the page refused every row."""
    from jobpilot.forms import mark_history_rows

    fields = []
    for _ in range(2):
        fields += [{"label": "* Employer Name", "type": "text"}, {"label": "Start Date", "type": "text"},
                   {"label": "End Date", "type": "text"}, {"label": "* Final Job Title", "type": "text"}]
    mark_history_rows(fields)
    assert [f.get("history_row") for f in fields[:4]] == [{"kind": "work", "index": 0}] * 4
    assert [f.get("history_row") for f in fields[4:]] == [{"kind": "work", "index": 1}] * 4


def test_rows_that_open_with_their_dates_are_placed():
    """Sonepar (SuccessFactors 2026-10-23): "From Date, End Date, * Company, * Title" then "From Date, End Date,
    * School, Major, Degree"; the education dates went to the work row and nothing was placed."""
    from jobpilot.forms import mark_history_rows

    labels = ["From Date", "End Date", "* Company", "* Title", "From Date", "End Date", "* School"]
    fields = [{"label": label, "type": "text"} for label in labels]
    fields += [{"label": "Major", "type": "combobox"}, {"label": "Degree", "type": "combobox"}]
    mark_history_rows(fields)
    rows = [f.get("history_row") for f in fields]
    assert rows[:4] == [{"kind": "work", "index": 0}] * 4
    assert rows[4:6] == [{"kind": "education", "index": "highest"}] * 2


def test_any_dates_month_part_is_a_month():
    """DTCC (Oracle 2026-10-23, REA): "Projected Completion Date Month" got "5" typed into its search; every month was
    filtered out and the date refused."""
    from jobpilot.widgets_ats import component_value, month_component

    assert component_value("Projected Completion Date Month", "5") == "May"
    assert month_component("Date Available Month") and month_component("Month")
    assert not month_component("Number of months") and not month_component("Months of experience")


def test_a_row_named_by_the_catalogs_other_is_identified_by_its_degree_level(service):
    """HEXAWARE (Oracle, 2026-10-26): the unlisted school was saved as "Other", so the row read "Education at Other" and
    its End Date Month went to the owner. The row's degree level names the entry."""
    profile = Profile(education=[
        EducationEntry(school="State University", degree="Master of Science", field_of_study="Data Science",
                       start="2024-08", end="2026-05"),
        EducationEntry(school="Institute of Technology", degree="BE", field_of_study="Computer Engineering",
                       start="2020-06", end="2024-06"),
    ])
    names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"]

    def end_month(degree):
        return Resolver(profile, service.db, service.config).local(
            {"id": "x", "label": "End Date Month", "type": "combobox", "group": "Education at Other", "required": True,
             "options": names, "history_identity": {"school": "Other", "degree": degree}})

    assert end_month("M.PHIL - Master of Philosophy").value == "May"
    assert end_month("Bachelor of Engineering").value == "June"
    unknown = end_month("")
    assert unknown is None or unknown.disposition != "answer"


def test_a_refused_optional_end_date_of_the_current_role_is_today(service):
    """Dallas County (Oracle, 2026-10-25): the current role's optional End Date Month kept a parsed date and the page
    refused it ("Enter a date after Start Date."); skipping it sent the month to the owner."""
    from datetime import date

    months = ["", *[f"{m:02d}" for m in range(1, 13)]]
    field = {"id": "x", "label": "End Date Month", "type": "combobox", "group": "Work Experience 1",
             "required": False, "options": months}
    profile = Profile(work=[WorkEntry(company="Acme Labs", title="AI Engineer", start="2025-12", current=True)])
    resolver = Resolver(profile, service.db, service.config, job={"ats": "oracle", "url": "https://example.test/j"})
    assert resolver.local(field).disposition == "skip"
    refused = resolver.local({**field, "error": "Enter a date after Start Date."})
    assert refused.disposition == "answer" and refused.value == f"{date.today().month:02d}"
