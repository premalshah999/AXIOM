"""The iCIMS form protocol: field meaning from the names every portal posts, not from the label each portal prints.

Every iCIMS employer runs the same server-rendered forms. Across 247 form posts to 15 employers (2026-10-20/21) the
profile step was always `/candidate` with `PersonProfileFields.*` and `PortalProfileFields.Resume*`, custom
questions `Q<n>`/`rcf<n>`, and the EEO step `CandProfileFields.*`/`icims_f_*`; repeating rows prefix the name with
their row id (`1745957_PersonProfileFields.AddressCity`, `-1_` for a new row). The printed labels vary ("Year" or
"Graduation Date Year" for `CandProfileFields.GraduationDate_Year`). Reading labels put "05/2026" into Schwab's
four-character year box, and read Schwab's saved login and masked password as a sign-in to perform.
"""

import re

ICIMS_FRAME = re.compile(r"^https://[^/]*\.icims\.com/", re.I)
ROW = re.compile(r"^-?\d+_")
# iCIMS date fields are three controls: <base>_Month and <base>_Date menus and a four-character <base>_Year box.
DATE_PART = re.compile(r"^(?P<base>(?:[A-Za-z]+Fields\.)?(?:rcf\d+)|[A-Za-z]+Fields\.[A-Za-z]+)_(?P<part>Month|Date|Year)$")
PART_WORD = {"Month": "Month", "Date": "Day", "Year": "Year"}
ACCOUNT = {"PersonProfileFields.Login", "PersonProfileFields.Password", "PersonProfileFields.Password_Confirm"}
SIGNED_IN = re.compile(r"log ?out|sign out", re.I)
# The candidate acknowledgement packet form signs with required checkboxes named icims_f_signature and
# icims_f_Signature_<n>, printed only as "xSignature" and "Signature 1" (SAS 2026-10-21): nobody checked them, the
# browser held the final Submit, and the run ended submission_unknown with nothing sent.
SIGNATURE = re.compile(r"^icims_f_signature(?:_\d+)?$", re.I)
SIGNATURE_LABEL = "I acknowledge and sign this form electronically"
# The Forms module's employment application (Cooper Health's "OFCCP_Pre", 2026-10-22) prints its captions in table
# cells the scan cannot tie to a control: every label read as the bare name ("Emp1SupName", "ICertify"), the
# employer rows were not history rows, and the run stopped on facts it held. Questions are named here; employer and
# school rows join the profile's work and education history.
FORM_FIELD = re.compile(r"^icims_f_(?P<name>[A-Za-z][\w]*)$")
FORM_LABELS = {
    "icertify": "I certify that the information in this application is true and complete",
    "work_in_us": "Are you legally authorized to work in the United States?",
    "over_18": "Are you age 18 or over?",
    "howyouhearaboutus": "How did you hear about us?",
    "previouslyemployedheresync": "Have you previously been employed by this organization?",
    "any_relatives_employed_here": "Do you have any relatives employed by this organization?",
    "yesrelatives": "If yes, names of relatives employed by this organization",
    "sourcereferral": "Referral source name",
    "need_sponsorship": "Will you now or in the future require sponsorship for employment visa status?",
    "hs_graduate": "Did you graduate?",
    "desiredsalary": "Desired Salary",
    "desiredsalary_currency": "Desired Salary Currency",
    "desiredsalary_timeframe": "Desired Salary Time Frame",
    "cellphone": "Mobile Phone",
    "zip": "Zip Code",
    "streetaddress2": "Address Line 2",
    "full_time": "Desired employment type: Full Time",
    "part_time": "Desired employment type: Part Time",
    "temporary": "Desired employment type: Temporary",
    "pool": "Desired employment type: Pool (per diem)",
}
# (pattern, label) for employer rows "EmpName1", "Emp1SupName", "JobTitle2", "mayWeContact3"; the row number is the
# first group. Labels avoid the word "employer" where the history rules would read it as the company name.
FORM_WORK = (
    (re.compile(r"^EmpName(\d)$", re.I), "Employer Name"),
    (re.compile(r"^EmpAdd(\d)$", re.I), "Street Address"),
    (re.compile(r"^EmpCity(\d)$", re.I), "City"),
    (re.compile(r"^EmpState(\d)$", re.I), "State"),
    (re.compile(r"^EmpZip(\d)$", re.I), "Zip Code"),
    (re.compile(r"^EmpCountry(\d)$", re.I), "Country"),
    (re.compile(r"^EmpPhone(\d)$", re.I), "Phone"),
    (re.compile(r"^JobTitle(\d)$", re.I), "Job Title"),
    (re.compile(r"^Emp(\d)SupName$", re.I), "Supervisor Name"),
    (re.compile(r"^Emp(\d)SupTitle$", re.I), "Supervisor Title"),
    (re.compile(r"^Emp(\d)Duties$", re.I), "Duties"),
    (re.compile(r"^Emp(\d)ReasonLeaving$", re.I), "Reason for Leaving"),
    (re.compile(r"^mayWeContact(\d?)$", re.I), "May we contact this reference?"),
)
FORM_SCHOOL = {
    "schoolname": "School Name",
    "schoolcity": "City",
    "schoolstate": "State",
    "schoolcountry": "Country",
    "schoolmajor": "Major",
    "schooldegree": "Degree",
}
# Form-module dates: icims_f_<base>_Month / _Date / _Year ("Emp1From", "DateAvailableToStart").
FORM_DATE = re.compile(r"^icims_f_(?P<base>[A-Za-z][A-Za-z0-9]*)_(?P<part>Month|Date|Year)$")
FORM_DATE_ROW = re.compile(r"^Emp(?P<row>\d)(?P<side>From|To)$", re.I)


def protocol_name(field):
    """The field's iCIMS name without its row prefix. iCIMS search menus are scanned as their visible
    `<a id="<select id>_icimsDropdown">` anchor, which has no name of its own."""
    name = field.get("name") or ""
    html_id = field.get("html_id") or ""
    if not name and html_id.endswith("_icimsDropdown"):
        name = html_id.removesuffix("_icimsDropdown")
    return ROW.sub("", name)


def words(identifier):
    """'GraduationDate' -> 'Graduation Date'."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", identifier)


def server_limits(own, text):
    """iCIMS checks some answer lengths only on the server and names the question in its refusal ("...authorization
    to work in Canada?: Your answer is over the maximum length of 100 characters", Quest 2026-10-21, no maxlength
    attribute). The named field takes that limit, so the answer is rewritten within it instead of resent."""
    if "maximum length" not in (text or ""):
        return
    for f in own:
        label = re.sub(r"[\s?:*]+$", "", str(f.get("label") or "")).strip()
        if f.get("type") not in {"text", "textarea"} or len(label) < 3:
            continue
        found = re.search(
            re.escape(label) + r"\s*\??\s*:\s*your answer is over the maximum length of (\d+) characters", text, re.I
        )
        if found and not 0 < f.get("maxlength", -1) <= int(found[1]):
            f["maxlength"] = int(found[1])


def squash(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


# Profile sub-fields printed only as "Type" beside the phone number and the address (Berkley 2026-10-23: two "Type"
# menus went to the owner as questions).
PROFILE_LABELS = {
    "PersonProfileFields.PhoneType": "Phone Type",
    "PersonProfileFields.AddressType": "Address Type",
}


def name_form_field(f, name):
    if name in PROFILE_LABELS and squash(f.get("label")) in {"", "type"}:
        f["label"] = PROFILE_LABELS[name]
        return
    """Name one Forms-module field whose printed label is only its own name."""
    if f.get("type") == "checkbox" and SIGNATURE.match(name):
        f["label"] = SIGNATURE_LABEL
        return
    match = FORM_FIELD.match(name)
    if not match or FORM_DATE.match(name):
        return
    bare = match["name"]
    if squash(f.get("label")) not in {"", squash(bare), squash(bare) + "status"}:
        return  # The scan found the employer's own caption.
    key = bare.lower()
    if key in FORM_LABELS:
        f["label"] = FORM_LABELS[key]
    elif key in FORM_SCHOOL:
        f["label"], f["group"] = FORM_SCHOOL[key], "Education 1"
    elif row := next(((label, m) for pattern, label in FORM_WORK if (m := pattern.match(bare))), None):
        label, m = row
        f["label"], f["group"] = label, f"Work Experience {m[1] or 1}"
    elif squash(f.get("label")) in {"", squash(bare)}:
        f["label"] = words(bare.replace("_", " ")).strip()  # "LastName" -> "Last Name"; "Veteran status" is kept.


def form_date_caption(base):
    """(caption, group) for a Forms-module date base: "Emp1From" -> ("Start Date", "Work Experience 1")."""
    if row := FORM_DATE_ROW.match(base):
        return ("Start Date" if row["side"].lower() == "from" else "End Date"), f"Work Experience {row['row']}"
    if base.lower() in {"emphere" + "from", "emphere" + "to"}:
        return "Previous Employment Here " + ("Start Date" if base.lower().endswith("from") else "End Date"), ""
    return words(base), ""


def apply(fields, controls, text=""):
    """Name standard iCIMS fields by protocol, in place. Other platforms' fields are untouched."""
    own = [f for f in fields if ICIMS_FRAME.search(f.get("frame_url") or "")]
    server_limits(own, text)
    dates, form_dates = {}, {}
    for f in own:
        f["icims_name"] = name = protocol_name(f)
        name_form_field(f, name)
        if form := FORM_DATE.match(name):
            form_dates.setdefault((f.get("frame_url"), f.get("group"), form["base"]), []).append((f, form["part"]))
            continue
        if match := DATE_PART.match(name):
            raw = (f.get("name") or f.get("html_id") or name).removesuffix("_icimsDropdown")
            key = (f.get("frame_url"), f.get("group"), raw.rsplit("_", 1)[0], match["base"])
            dates.setdefault(key, []).append((f, match["part"]))
    for (*_, base), parts in dates.items():
        if re.search(r"(?:^|\.)rcf\d+$", base):
            # Custom question IDs have no semantic meaning. Keep their employer caption, while protocol
            # suffixes determine the date parts (ICE's PersonProfileFields.rcf2023/rcf2024).
            captions = [re.sub(r"\s*\b(?:Month|Day|Date|Year)\s*$", "", f["label"], flags=re.I).strip()
                        for f, _ in parts]
            caption = next((label for label in captions if label), "")
        else:
            caption = words(base.split(".", 1)[1])
        present = sorted(PART_WORD[part] for _, part in parts)
        for f, part in parts:
            # One date in three controls: each answers only its part, and iCIMS refuses a date missing any part
            # ("Please fill in all date fields"), so the parts are answered together or not at all.
            if caption:
                f["label"] = f"{caption} {PART_WORD[part]}"
            f["date_parts"] = present
    for (*_, base), parts in form_dates.items():
        caption, group = form_date_caption(base)
        present = sorted(PART_WORD[part] for _, part in parts)
        for f, part in parts:
            f["label"] = f"{caption} {PART_WORD[part]}"
            f["date_parts"] = present
            if group:
                f["group"] = group
    if any(SIGNED_IN.fullmatch(c.get("label", "").strip()) for c in controls):
        for f in own:
            if f["icims_name"] in ACCOUNT and f.get("value"):
                # A signed-in profile shows the account's saved login and a masked password. They are the account,
                # not questions: never retyped, and never a sign-in still to perform.
                f["site_account"] = True
