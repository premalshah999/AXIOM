"""What a resume read gives the setup guide's boxes (frontend/src/resumeread.ts), run in node: the real code, without a browser."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1] / "frontend" / "src" / "resumeread.ts"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")

JOB_A = {"company": "Acme Example", "title": "Engineer", "location": "", "start": "2022-06", "end": "", "current": True, "description": ""}
JOB_B = {"company": "Birch Labs", "title": "Intern", "location": "", "start": "2021-06", "end": "2021-08", "current": False, "description": ""}
JOB_C = {"company": "Fir Systems", "title": "Backend Engineer", "location": "", "start": "2023-02", "end": "2024-11", "current": False, "description": ""}
SCHOOL = {"school": "Example State University", "degree": "BS", "field_of_study": "CS", "start": "2018", "end": "2022", "gpa": ""}
EMPTY = {"name": "", "first_name": "", "last_name": "", "email": "", "phone": "", "location": "", "linkedin": "", "github": "", "website": "", "phone_country_code": "+1",
         "address": {"line1": "", "line2": "", "city": "", "state": "", "postal_code": "", "county": "", "country": "United States"}, "work": [], "education": [], "skills": []}
FIRST = {"first_name": "Alex", "last_name": "Example", "phone": "+1 (555) 010-0100", "location": "Austin, TX", "work": [JOB_A, JOB_B], "education": [SCHOOL], "skills": ["Python"]}
SECOND = {"first_name": "Alex", "last_name": "Example", "phone": "+1-555-010-0199", "location": "Denver, Colorado 80202", "work": [JOB_C], "education": [], "skills": ["Go"]}


def run(call):
    script = f"import * as r from {json.dumps(CODE.as_uri())}; console.log(JSON.stringify({call}));"
    done = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    if "Unknown file extension" in done.stderr:
        pytest.skip("this node does not run TypeScript")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def merge(draft, read, last=None):
    return run(f"r.mergeExtract({json.dumps(draft)}, {json.dumps(read)}, {json.dumps(last)})")


def test_a_resume_phone_comes_apart_from_its_country_code():
    cases = {
        "+1 (555) 010-0100": ("(555) 010-0100", "+1"),
        "+1-555-010-0199": ("555-010-0199", "+1"),
        "+15550100100": ("5550100100", "+1"),
        "(+1) 555 010 0100": ("555 010 0100", "+1"),
        "1 (555) 010-0100": ("(555) 010-0100", "+1"),
        "555-010-0100": ("555-010-0100", ""),
        "+44 20 7946 0958": ("20 7946 0958", "+44"),
        "+91 98765 43210": ("98765 43210", "+91"),
    }
    got = run(f"{json.dumps(list(cases))}.map((raw) => r.splitPhone(raw))")
    assert [(g["phone"], g["code"]) for g in got] == list(cases.values())


def test_a_us_location_reaches_city_and_state_and_anywhere_else_is_left_to_type():
    cases = {
        "Austin, TX": ("Austin", "TX", ""),
        "Austin, TX 78701": ("Austin", "TX", "78701"),
        "Austin, Texas, USA": ("Austin", "Texas", ""),
        "Austin TX": ("Austin", "TX", ""),
        "Austin, TX (Remote)": ("Austin", "TX", ""),
        "Washington, D.C.": ("Washington", "DC", ""),
        "New York, New York 10001": ("New York", "New York", "10001"),
        "St. Louis, mo": ("St. Louis", "MO", ""),
        "123 Main St, Austin, TX 78701": ("Austin", "TX", "78701"),
        "Remote": None,
        "London, United Kingdom": None,
        "Toronto, ON": None,
        "": None,
        "United States": None,
    }
    got = run(f"{json.dumps(list(cases))}.map((text) => r.cityState(text))")
    assert [g and (g["city"], g["state"], g["postal_code"]) for g in got] == list(cases.values())


def test_the_first_read_fills_the_boxes_without_doubling_the_code():
    draft = merge(EMPTY, FIRST)
    assert (draft["phone_country_code"], draft["phone"]) == ("+1", "(555) 010-0100")
    assert (draft["address"]["city"], draft["address"]["state"]) == ("Austin", "TX")
    assert draft["work"] == [JOB_A, JOB_B] and draft["education"] == [SCHOOL] and draft["name"] == "Alex Example"


def test_a_second_resume_replaces_the_first_also_after_a_remove():
    draft = merge(EMPTY, FIRST)
    draft["work"] = [JOB_A]  # Remove pressed on the other job
    again = merge(draft, SECOND, FIRST)
    assert again["work"] == [JOB_C] and again["education"] == []
    assert again["phone"] == "555-010-0199" and again["skills"] == ["Go"]
    assert (again["address"]["city"], again["address"]["state"], again["address"]["postal_code"]) == ("Denver", "Colorado", "80202")


def test_what_the_person_typed_stays():
    draft = merge(EMPTY, FIRST)
    draft["address"]["city"] = "Round Rock"
    draft["phone"] = "555-010-0123"
    draft["work"] = [JOB_A, {**JOB_B, "title": "Senior Intern"}]
    again = merge(draft, SECOND, FIRST)
    assert again["address"]["city"] == "Round Rock" and again["address"]["state"] == "TX"
    assert again["phone"] == "555-010-0123"
    assert len(again["work"]) == 2 and again["work"][1]["title"] == "Senior Intern"


def test_a_profile_from_before_any_read_is_not_replaced():
    draft = {**EMPTY, "work": [JOB_C], "phone": "555-010-0123"}
    again = merge(draft, FIRST)
    assert again["work"] == [JOB_C] and again["phone"] == "555-010-0123"


def test_a_place_outside_the_us_clears_the_city_the_last_read_filled_and_the_code_follows_the_number():
    draft = merge(EMPTY, FIRST)
    again = merge(draft, {"phone": "+44 20 7946 0958", "location": "London, United Kingdom", "work": [], "education": [], "skills": []}, FIRST)
    assert (again["phone_country_code"], again["phone"]) == ("+44", "20 7946 0958")
    assert (again["address"]["city"], again["address"]["state"]) == ("", "") and again["work"] == []
