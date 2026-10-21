"""DecisionPoint (iCIMS) 2026-10-21: Form CC-305 "Voluntary Self-Identification of Disability" dates itself with bare
Month, Day and Year controls under a "Date" caption. The run stopped: "Required application facts are missing: Month;
Day; Year". On a self-identification form they are today's date."""

from datetime import datetime
from zoneinfo import ZoneInfo

from jobpilot.answers import Resolver
from jobpilot.forms import label_signature_date
from jobpilot.schemas import Profile

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
DAYS = [str(n) for n in range(1, 32)]


def parts():
    return [
        {"id": "m", "label": "Month", "type": "select", "section": "", "group": "", "options": MONTHS},
        {"id": "d", "label": "Day", "type": "select", "section": "", "group": "", "options": DAYS},
        {"id": "y", "label": "Year", "type": "text", "section": "", "group": "", "options": []},
    ]


def test_bare_date_parts_on_a_self_identification_form_are_todays_date(service):
    fields = parts()
    label_signature_date(fields, "Form CC-305 Voluntary Self-Identification of Disability Name Date")
    assert [f["label"] for f in fields] == ["Today's date (Month)", "Today's date (Day)", "Today's date (Year)"]
    r = Resolver(Profile(name="Alex Doe"), service.db, service.config)
    today = datetime.now(ZoneInfo(service.config.timezone)).date()
    answers = {f["id"]: r.local({**f, "required": True, "value": "", "valid": True}).value for f in fields}
    assert answers == {"m": MONTHS[today.month - 1], "d": str(today.day), "y": str(today.year)}


def test_date_parts_elsewhere_keep_their_labels():
    fields = parts()
    label_signature_date(fields, "Work Experience 1 Start Date")
    assert [f["label"] for f in fields] == ["Month", "Day", "Year"]
    grouped = [{**f, "group": "Work Experience 1"} for f in parts()]
    label_signature_date(grouped, "Voluntary Self-Identification of Disability")
    assert [f["label"] for f in grouped] == ["Month", "Day", "Year"]
