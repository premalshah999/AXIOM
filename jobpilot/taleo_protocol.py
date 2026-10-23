"""The Taleo Enterprise form protocol: history rows named from the ids every career section posts.

Taleo prints only "Month" and "Year" under each experience date and gives the rows no caption the scan can tie to
them, so no answer source knew which row or which date a menu was: Textron's work experience (2026-10-23) left every
Month on its placeholder, the page answered "The field "Start Date" is mandatory." 28 times and the run stopped. The
ids say it all: `...-we-wei-<row>-frm-dv_cs_experience_BeginDate.month`, `..._EndDate.year`, `..._Employer`.
"""

import re

TALEO_FRAME = re.compile(r"^https://[^/]*\.taleo\.net/", re.I)
ROW_FIELD = re.compile(
    r"-(?P<row>\d+)-frm[-:]dv_cs_(?P<kind>experience|education)_(?P<name>[A-Za-z]+)(?:\.(?P<part>month|year|day))?$"
)
NAMES = {
    "experience": {
        "begindate": "Start Date",
        "enddate": "End Date",
        "employer": "Employer Name",
        "jobfunction": "Job Title",
        "title": "Job Title",
        "currentemployer": "I currently work here",
    },
    "education": {
        "institution": "School",
        "program": "Field of Study",
        "educationlevel": "Degree",
        "begindate": "Start Date",
        "graduationdate": "Graduation Date",
        "enddate": "End Date",
    },
}
PART_WORD = {"month": "Month", "year": "Year", "day": "Day"}


def squash(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def apply(fields, work_companies=(), schools=()):
    """Name Taleo history fields in place. A row whose employer (or school) the resume parser already filled is placed
    by that name in the profile's history, since the parser orders rows its own way; other rows keep their position."""
    rows = {}
    for f in fields:
        if not TALEO_FRAME.search(f.get("frame_url") or ""):
            continue
        match = ROW_FIELD.search(f.get("name") or f.get("html_id") or "")
        if not match:
            continue
        kind, name = match["kind"], match["name"].lower()
        rows.setdefault((f.get("frame_url"), kind, int(match["row"])), []).append((f, name, match["part"]))
    for (_, kind, row), members in rows.items():
        names = work_companies if kind == "experience" else schools
        anchor = next(
            (squash(f.get("value")) for f, name, _ in members if name in {"employer", "institution"} and f.get("value")),
            "",
        )
        placed = next((i for i, n in enumerate(names) if anchor and squash(n) == anchor), row)
        group = ("Work Experience " if kind == "experience" else "Education ") + str(placed + 1)
        dates = {}
        for f, name, part in members:
            caption = NAMES[kind].get(name)
            if not caption:
                continue
            f["group"] = group
            f["label"] = f"{caption} {PART_WORD[part]}" if part else caption
            if part:
                dates.setdefault(name, []).append((f, PART_WORD[part]))
        for parts in dates.values():
            present = sorted(word for _, word in parts)
            for f, _ in parts:
                f["date_parts"] = present
