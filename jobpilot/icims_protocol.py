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


def apply(fields, controls, text=""):
    """Name standard iCIMS fields by protocol, in place. Other platforms' fields are untouched."""
    own = [f for f in fields if ICIMS_FRAME.search(f.get("frame_url") or "")]
    server_limits(own, text)
    dates = {}
    for f in own:
        f["icims_name"] = name = protocol_name(f)
        if f.get("type") == "checkbox" and SIGNATURE.match(name):
            f["label"] = SIGNATURE_LABEL
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
    if any(SIGNED_IN.fullmatch(c.get("label", "").strip()) for c in controls):
        for f in own:
            if f["icims_name"] in ACCOUNT and f.get("value"):
                # A signed-in profile shows the account's saved login and a masked password. They are the account,
                # not questions: never retyped, and never a sign-in still to perform.
                f["site_account"] = True
