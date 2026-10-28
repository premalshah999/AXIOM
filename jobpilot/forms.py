"""Browser controls are referenced from observations, never model-generated selectors."""

import asyncio
import hashlib
import json
import re
import time
import traceback
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from . import icims_protocol, phone_fields, taleo_protocol, widgets
from .answers import HISTORY_UNKNOWN, answer_key, job_catalog_search_box, normalize
from .knowledge import topic
from .browser_contracts import ActionResult, action_result, field_identity, fingerprint
from .models import AnswerModelUnavailable
from .playwright_fast import install as install_playwright_fast
from .privacy import dismiss_cookies

# Every route to a page (the host Chrome over CDP and a private browser) reaches this module, so the single-call
# Locator.evaluate is installed here as well as in desktop_cdp.
install_playwright_fast()

SCAN = (Path(__file__).parent / "js" / "scan.js").read_text()
FIELD_WRITE_SECONDS = 12
# A school search that finds nothing falls back to shorter searches and then "Other"; each empty result can wait out
# its option wait. On slow Workday tenants the chain outlasted 12 s and the field never committed (Lennar's
# "Nanyang Technological University", 27 deadline failures on school fields since 10-16).
SCHOOL_LABEL = re.compile(r"\bschool\b|\buniversity\b|\binstitution\b|\bcollege\b", re.I)
# A picker of employers to name the applicant's own (not "Have you worked for this employer?").
EMPLOYER_PICKER = re.compile(
    r"\b(?:latest|current|most recent|previous|former|last|present) employer\b|\bname of (?:your )?(?:latest |current |"
    r"most recent |previous |former )?employer\b|^\s*employer(?: name)?\s*\*?$",
    re.I,
)
SCHOOL_WRITE_SECONDS = 30
CONTROL_RECOVERY_SECONDS = 30

# Unnumbered rows that repeat per work-history entry. SAP's SuccessFactors application page
# (careers.sap.com, 2026-10-16) shows "Start Date, End Date, * Employer, * Position Title" five
# times with no row heading; the decision model then mixed rows and a date read as availability.
HISTORY_ROW_LABELS = {
    "company": re.compile(r"(?:employer|company)(?: name)?", re.I),
    # HF Sinclair (SuccessFactors 2026-10-23): "* Final Job Title"; the rows were not placed and the bare Start Date
    # took the availability answer (today), "Start date must be before End date".
    "title": re.compile(r"(?:(?:final|current|most recent|last) )?(?:(?:position|job) )?title", re.I),
    "start": re.compile(r"start date|from(?: date)?", re.I),
    "end": re.compile(r"end date|to(?: date)?", re.I),
}
# Classic SuccessFactors (Syncreon, Lear 2026-10-21) repeats education rows above the work rows: Education Level, Major
# or Subject, * School, From Date, End Date. They reuse the work rows' End Date, so no work End Date was ever placed,
# and every education row was answered from the first degree.
EDUCATION_ROW_LABELS = {
    "school": re.compile(r"school(?: name)?|university|college|institution", re.I),
    "degree": re.compile(r"degree|education level|level of education", re.I),
    "field_of_study": re.compile(r"major(?: or subject)?|field of study|discipline", re.I),
}


def name_phone_numbers(fields):
    """A bare "Number" input right after a phone country-code picker is that phone's number. iCIMS PowerSchool
    (2026-10-17) labelled it only "Number"; it stayed empty and the page answered "Phone Number: required"."""
    previous = None
    for field in fields:
        if (
            previous is not None
            and phone_fields.is_phone_country(previous)
            and field.get("type") in {"text", "tel", "number"}
            and re.fullmatch(r"\s*(?:number|phone ?#|#|no\.?)\s*\*?\s*", str(field.get("label", "")), re.I)
        ):
            field["label"] = "Phone Number"
        previous = field


SELF_ID_FORM = re.compile(r"self-identification|\bCC-305\b|\bVEVRAA\b", re.I)
# A page the applicant signs by typing: its one bare "Date" is the signature's date (Hyundai's SuccessFactors "Typed
# Signature" then "Date", 2026-10-27: today's date was refused as unsupported and the run stalled).
SIGNED_PAGE = re.compile(
    r"\b(?:typed|electronic|e-|digital) signature\b|\bsignature of (?:the )?applicant\b|\bapplicant.?s signature\b", re.I
)
HISTORY_SCOPE = re.compile(r"experience|employment|education|work history|school|degree", re.I)


# A text box that asks for a number of years takes a number. Oracle's "Total Experience In Years"
# (totalExperienceInYears, HEXAWARE 2026-10-23) got a paragraph and answered "Enter a valid number" until the run stalled.
YEAR_COUNT = re.compile(
    r"\bin (?:years|months|days)\b|\b(?:number|no\.?) of (?:years|months|days)\b|^how many (?:years|months|days)\b|"
    r"\byears of (?:total |relevant )?experience\b",
    re.I,
)  # Oracle's "Notice Period in Days" (HEXAWARE 2026-10-23): "0 days" was refused as not a number.


ORACLE_EDUCATION = re.compile(
    r"^(?:school(?: name)?|institution|degree|major|field of study|(?:start|end|graduation) date(?: month| year)?|"
    r"graduated|did you graduate\??)$",
    re.I,
)


def oracle_education_block(fields):
    """Oracle CX can render one education block without a row caption (Dallas County 2026-10-23): its "Start Date
    Month" then read as availability. A single ungrouped block with a school field is Education 1."""
    fields = list(fields)
    loose = [f for f in fields if "oraclecloud.com" in str(f.get("frame_url") or "") and not f.get("group")
             and ORACLE_EDUCATION.match(str(f.get("label") or "").strip())]
    if sum(re.match(r"^(?:school(?: name)?|institution)$", f["label"].strip(), re.I) is not None for f in loose) != 1:
        return
    for f in loose:
        f["group"] = "Education 1"


def numeric_year_counts(fields):
    for f in fields:
        if f.get("type") == "text" and not f.get("options") and YEAR_COUNT.search(str(f.get("label") or "")):
            f["type"] = "number"


# "Please enter your first name using ALL CAPITAL LETTERS (Example: JOHN)": Dallas County's Oracle form refused
# mixed-case names at submit with HTTP 400, twice, with no field error shown (2026-10-25).
UPPERCASE_ONLY = re.compile(
    r"\b(?:all\s+)?(?:capital|upper[- ]?case|block)\s+letters\b|\ball\s+caps\b|\bin\s+capitals\b|\buppercase\s+only\b",
    re.I,
)


def mark_uppercase_fields(fields, text):
    """A text field the form asks to be written in capital letters, in its own label or hint or in a page sentence
    that names it."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n", text or "") if UPPERCASE_ONLY.search(s)]
    for f in fields:
        if f.get("type") not in {"text", "textarea"}:
            continue
        own = " ".join(str(f.get(k) or "") for k in ("label", "hint"))
        name = normalize(f.get("label") or "")
        if UPPERCASE_ONLY.search(own) or (
            len(name) >= 4 and any(re.search(rf"\b{re.escape(name)}\b", normalize(s)) for s in sentences)
        ):
            f["letter_case"] = "upper"


def label_signature_date(fields, text):
    """The federal self-identification forms (CC-305 "Voluntary Self-Identification of Disability") date the form with
    three controls under a "Date" caption the scanner does not attach: bare Month, Day and Year (DecisionPoint, iCIMS,
    2026-10-21; the run stopped as "Required application facts are missing: Month; Day; Year"). On such a page they
    are today's date."""
    self_id = bool(SELF_ID_FORM.search(text or ""))
    if not (self_id or SIGNED_PAGE.search(text or "")):
        return
    parts = [f for f in fields if f["label"].strip() in {"Month", "Day", "Year"} and not f.get("section") and not f.get("group")]
    if self_id and {f["label"].strip() for f in parts} == {"Month", "Day", "Year"}:
        for f in parts:
            f["label"] = f"Today's date ({f['label'].strip()})"
    # Micron's Eightfold form (2026-10-22) dates it with one bare "Date" box behind a calendar (role=combobox): read as
    # a picker with no options it stayed empty, and the run stalled on it. Typed as MM/DD/YYYY it takes today.
    # Its section may be the form's own ("Application questions", Micron 2026-10-25); a history row's date never is.
    dates = [
        f
        for f in fields
        if re.fullmatch(r"\*?\s*Date\s*:?\s*\*?", f["label"].strip())
        and not HISTORY_SCOPE.search(" ".join(str(f.get(k) or "") for k in ("section", "group")))
    ]
    if len(dates) == 1 and dates[0]["type"] in {"text", "date", "combobox"} and not dates[0].get("options"):
        f = dates[0]
        f["label"] = "Today's date"
        if f["type"] == "combobox":
            f["type"], f["widget"], f["date_format"] = "text", "date-text", f.get("date_format") or "mdy"


ICIMS_HOST = re.compile(r"^https://[^/]*\.icims\.com/", re.I)
# innerText runs the name into its button ("Resume202610211051.pdfReplace Resume", Iridium 2026-10-21): no end boundary.
ICIMS_RENAMED_RESUME = re.compile(r"\bResume\d{12}\.(?:pdf|docx?)", re.I)


# Select exactly the options with these labels in a native <select multiple>, then fire input and change.
SELECT_LABELS_JS = """(e, labels) => {
  const wanted = new Set(labels.map(l => l.trim().toLowerCase()));
  for (const o of e.options) o.selected = wanted.has(o.text.trim().toLowerCase());
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
}"""


SITE_SEARCH = re.compile(
    r"(?:submit\s+)?search(?:\s+for)?(?:\s+(?:jobs|careers|keywords?|the site|this site|our site|positions|openings))?|"
    r"keywords?|job search|search by keyword",
    re.I,
)


def site_search(field):
    """A career site's search box (iPipeline's "Search", Iridium's hidden required "Submit search", Planview's
    required "Search for:" in the portal header, 2026-10-19/21). Read as a required application field it blocked the
    email and profile steps; it is never an application question."""
    label = str(field.get("label", "")).strip().rstrip(":").strip()
    if field.get("type") not in {"text", "search"}:
        return False
    # scan.js marks a lone text box whose only buttons search ("Search now" beside TikTok's tagline, 2026-10-27).
    return bool(SITE_SEARCH.fullmatch(label)) or (bool(field.get("search_box")) and not field.get("required"))


def link_companion_text(fields):
    """iCIMS names a question's free-text companion "<question>_Text". ICE's "If applicable, please provide info."
    (rcf3169_Text) belongs to "Do you have any legal restrictions to working in the country where this position is
    located?" and is required (2026-10-19); alone its label asks for nothing the resolver could place. The question's own
    label lets it be answered from the applicant's facts (the details, or "N/A")."""
    fields = list(fields)
    by_name = {f.get("name"): f for f in fields if f.get("name")}
    for field in fields:
        name = field.get("name") or ""
        parent = by_name.get(name[: -len("_Text")]) if name.endswith("_Text") else None
        if (
            parent
            and parent is not field
            and field.get("type") in {"text", "textarea"}
            and parent.get("label")
            and parent["label"] not in field.get("label", "")
        ):
            field["label"] = f"{parent['label']} {field.get('label', '')}".strip()


HISTORY_GROUP_NUMBER = re.compile(r"^(?:Work Experience|Education) (\d+)$")


def regroup_history_rows(fields, profile):
    """Give each repeated history row the profile entry its own Employer/Title (or School/Degree) already names, one
    entry per row; every other row keeps its place.

    America's Tire's iCIMS profile (2026-10-27) held five required experience rows. Row 3 still carried row 5's employer
    and title from an earlier fill, so both rows were read as entry 5: row 3 was refilled with the same job, the third
    job was never written, and every Submit Profile came back "Invalid Data Error" on row 3. An entry claimed by two rows
    stays with the row in its own place; the other row is answered by position, or by the first entry no row holds."""
    rows = {}
    for f in fields:
        if f.get("history_kind"):
            identity = tuple(sorted((f.get("history_identity") or {}).items()))
            rows.setdefault((f.get("frame_url", ""), f["history_kind"], f.get("group", ""), identity), []).append(f)
    by_kind = {}
    for (frame, kind, group, _), members in rows.items():
        number = HISTORY_GROUP_NUMBER.fullmatch(group)
        position = int(number[1]) - 1 if number else None
        identity = {k: v for k, v in (members[0].get("history_identity") or {}).items() if v}
        entries = getattr(profile, kind, []) or []
        matches = [
            i
            for i, entry in enumerate(entries)
            if identity and all(normalize(str(getattr(entry, k, ""))) == normalize(str(v)) for k, v in identity.items())
        ]
        by_kind.setdefault((frame, kind), []).append(
            {"members": members, "position": position, "match": matches[0] if len(matches) == 1 else None}
        )
    for (_, kind), kind_rows in by_kind.items():
        claimed = {}
        # A row already in its own entry's place keeps it; then the others claim entries in page order.
        for in_place in (True, False):
            for row in kind_rows:
                if row["match"] is None or row["match"] in claimed or "index" in row:
                    continue
                if (row["position"] == row["match"]) == in_place:
                    claimed[row["match"]] = row
                    row["index"] = row["match"]
        left = [row for row in kind_rows if "index" not in row]
        kept = {row["position"] for row in left if row["position"] is not None and row["position"] not in claimed}
        taken = set(claimed) | kept
        for row in left:
            if row["position"] is None:
                continue  # An unnumbered group is not a row place; it keeps its own name.
            if row["position"] in kept:
                row["index"] = row["position"]
            else:
                row["index"] = next(i for i in range(len(kind_rows) + len(taken) + 1) if i not in taken)
                taken.add(row["index"])
        for row in kind_rows:
            if "index" in row:
                group = ("Work Experience " if kind == "work" else "Education ") + str(row["index"] + 1)
                for f in row["members"]:
                    f["group"] = group


def mark_history_rows(fields):
    """Row N of a repeated, unnumbered work block is profile work entry N (current/most recent first).

    Every Employer needs its Position Title (one row needs all four labels). A label that repeats a
    different number of times (an education block reusing Start Date) is never placed."""
    fields = list(fields)
    for f in fields:
        f.pop("history_row", None)  # A rescan may have removed or reordered rows.
    fields = [
        f
        for f in fields
        if not re.search(
            r"(?:experience|employment|history|education)\s*\d+",
            (f.get("group") or "") + " " + (f.get("section") or ""),
            re.I,
        )
    ]
    clean = lambda f: re.sub(r"^[\s*]+|[\s*:]+$", "", str(f.get("label") or ""))  # noqa: E731
    matched = {
        attr: [f for f in fields if f.get("type") in {"text", "date"} and pattern.fullmatch(clean(f))]
        for attr, pattern in HISTORY_ROW_LABELS.items()
    }
    schools = {
        attr: [f for f in fields if f.get("type") in {"text", "combobox", "select"} and pattern.fullmatch(clean(f))]
        for attr, pattern in EDUCATION_ROW_LABELS.items()
    }
    position = {id(f): i for i, f in enumerate(fields)}
    work_anchors = [position[id(f)] for f in matched["company"] + matched["title"]]
    school_anchors = [position[id(f)] for row_fields in schools.values() for f in row_fields]

    date_positions = [position[id(f)] for attr in ("start", "end") for f in matched[attr]]
    # Classic SuccessFactors rows can open with their dates ("From Date, End Date, * Company, * Title", then "From Date,
    # End Date, * School", Sonepar 2026-10-23): nearest-anchor placement gave the education dates to the work row and
    # placed nothing. When the first date comes before every anchor, a date belongs to the anchor after it.
    dates_first = bool(date_positions) and min(date_positions) < min(work_anchors + school_anchors, default=10**9)

    def block(f):
        """Which block a date belongs to: the nearer of the work and education anchors ("" when tied or none)."""
        here = position[id(f)]
        if dates_first:
            work = min((a - here for a in work_anchors if a > here), default=None)
            school = min((a - here for a in school_anchors if a > here), default=None)
        else:
            work = min((abs(here - a) for a in work_anchors), default=None)
            school = min((abs(here - a) for a in school_anchors), default=None)
        if work is not None and (school is None or work < school):
            return "work"
        return "education" if school is not None and (work is None or school < work) else ""

    dates = {attr: matched.pop(attr) for attr in ("start", "end")}
    if len(schools["school"]) == 1:
        # A single school is answered as a whole from the highest degree; its own dates are that degree's. Left
        # unplaced, Sonepar's "From Date" went to the owner as a question.
        for attr, row_fields in dates.items():
            own = [f for f in row_fields if block(f) == "education"]
            if len(own) == 1:
                own[0]["history_row"] = {"kind": "education", "index": "highest"}
    if len(schools["school"]) > 1:
        # One education row is answered as a whole from the highest degree; only repeated rows are placed.
        education = {**schools, **{attr: [f for f in v if block(f) == "education"] for attr, v in dates.items()}}
        for attr, row_fields in education.items():
            if len(row_fields) == len(schools["school"]):
                for index, f in enumerate(row_fields):
                    f["history_row"] = {"kind": "education", "index": index}
    # A date no row claims (another block's "Start Date") makes that label ambiguous: it is not placed at all.
    matched.update({attr: ([f for f in v if block(f) == "work"] if all(block(f) for f in v) else [])
                    for attr, v in dates.items()})
    rows = len(matched["company"])
    if not rows or len(matched["title"]) != rows:
        return
    if rows == 1 and not all(len(row_fields) == 1 for row_fields in matched.values()):
        return  # A lone Employer/Title pair is a row only with its own Start and End Date.
    for attr, row_fields in matched.items():
        if len(row_fields) != rows:
            continue
        for index, f in enumerate(row_fields):
            f["history_row"] = {"kind": "work", "index": index}


ACKNOWLEDGE = re.compile(r"ok(?:ay)?|got it|i understand|understood|continue|dismiss|close", re.I)
DECLINE = re.compile(r"maybe later|not now|no,? thanks?|skip(?: for now| tour| this)?|remind me later|later", re.I)
# A dialog that asks to confirm something consequential is never acknowledged on the applicant's behalf.
INFORMATIONAL_DIALOG_REFUSES = re.compile(
    r"withdraw|delete|remov|discard|cancel (?:your |this )?application|sign ?out|log ?out|submit|are you sure|"
    r"lose (?:your )?(?:changes|progress)|terms|consent|agree|certif|attest|"
    # A receipt is evidence to read, and a cookie notice follows the owner's cookie setting, not an acknowledgement.
    r"thank|received|success|confirm|applied|cookie|accept",
    re.I,
)


def greenhouse_job_ref(parts):
    """(board, job id) of a Greenhouse board or embed URL (already split), or None."""
    if not re.fullmatch(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io", parts.hostname or ""):
        return None
    if board := re.fullmatch(r"/([A-Za-z0-9_-]+)/jobs/([0-9]+)/?", parts.path):
        return board[1], board[2]
    query = dict(parse_qsl(parts.query))
    if parts.path.rstrip("/") == "/embed/job_app" and query.get("for") and query.get("token"):
        return query["for"], query["token"]
    return None


class BrowserTabClosed(PlaywrightError):
    """The page under this owner is gone (closed tab or crashed renderer)."""


ACCOUNT_CREATE = re.compile(
    r"(?:create|register) (?:a |an |my |your |new )?(?:candidate )?(?:account|profile)|register(?: now)?|sign up|join now",
    re.I,
)
# Widget messages that carry only the employer's own option labels (never an applicant value).
SAFE_CONTROL_PREFIXES = ("No unique supported option in this ATS field's owned popup",)
SAFE_CONTROL_ERRORS = {
    "No matching option is offered",
    "Native option is ambiguous or changed",
    "The website did not commit the observed choice",
    "Choice is stale or outside this control",
}


class ChoiceRejected(ValueError):
    """A working control whose offered choice the applicant's evidence does not support."""

    def __init__(self, category, detail):
        super().__init__(detail)
        self.category, self.detail = category, detail


class ResumeAttachmentError(ValueError):
    """An owned upload needs control recovery; never append another copy blindly."""


FINAL = re.compile(
    r"\bsubmit\b|send (?:my )?application|complete (?:my )?application|finish application"
    # German and French submits (Sopra Steria, SmartRecruiters 2026-10-26): "Absenden" was clicked as an ordinary
    # step, the application went through outside the commit step and the run stalled on a success page. The page
    # guards (browser.GUARD, forms.click, workday.py) carry the same words.
    r"|\babsenden\b|bewerbung (?:abschicken|einreichen)|envoyer ma candidature|\bsoumettre\b",
    re.IGNORECASE,
)
# SuccessFactors application pages (classic career site, the candidate portal wizard, Career Site
# Builder's apply route). Their bare "Apply" is the application submit, never the posting's "Apply now".
SF_APPLICATION_PAGE = re.compile(
    r"successfactors\.(?:com|eu)/(?:portalcareer|careers?|sfcareer)\b|/talentcommunity/apply/", re.I
)
SF_CONFIRMATION_RE = re.compile(
    r"you have successfully submitted your application[^.]{0,60}"
    r"|thank you for (?:applying|your application)[^.]{0,60}"
    # The candidate portal names the job in it: "Your application for Software Developer Engineer II has been submitted"
    # (Optimum 2026-10-26 read as unconfirmed).
    r"|your application(?: for [^.\n]{1,120}?)? (?:has been|was) (?:successfully )?(?:submitted|received|sent)",
    re.I,
)
NOT_FINAL_SUBMIT = re.compile(
    r"submit (?:a |an |your )?(?:request|ticket|feedback|question|inquiry|enquiry|complaint|referral|idea)|privacy|cookie",
    re.I,
)
ENTRY = re.compile(
    r"^(?:apply(?: now| for (?:this|the) (?:job|position))?|start (?:your )?application|"
    r"continue application|i.m interested|apply manually|autofill with resume)$",
    re.I,
)
# A sign-in or code field. "password" is a word: "Why 1Password?" (1Password's own question, Ashby 2026-10-27) was
# taken for a sign-in field, never answered, and every run stopped as "Employer sign-in must complete".
AUTH = re.compile(r"\bpasswords?\b|verification code|one.time|authentication code|security code", re.IGNORECASE)
BACK_STEP = re.compile(r"^(?:back|previous|go back)(?: step)?$", re.I)
RESUME = re.compile(r"r[eé]sum[eé]|cv\b|curriculum", re.IGNORECASE)
# A submit is live only if rendered, enabled, and not inside an inert/hidden/aria-hidden ancestor.
LIVE_CONTROL_JS = """b => {
  const cs = getComputedStyle(b);
  if (!b.getClientRects().length || cs.visibility === 'hidden' || cs.visibility === 'collapse') return false;
  if (b.disabled || b.getAttribute('aria-disabled') === 'true') return false;
  return !b.closest('[hidden],[inert],[aria-hidden="true"]');
}"""
# Questions asking where the applicant is (not a fixed office list): their pickers are search-only typeaheads.
APPLICANT_LOCATION = re.compile(
    r"(?:location(?: \(city\))?|city|current (?:city|location)|(?:what is your )?current location|"
    r"where are you (?:currently )?(?:located|based)(?: (?:today|currently|now|from today))?)[\s?*:]*",
    re.I,
)
# The one visible, untagged, writable control with this name takes the field's tag (FormSession.retag).
RETAG_JS = """({jp, name}) => {
  const live = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && !e.disabled
    && !e.closest('[hidden],[inert],[aria-hidden="true"]');
  const found = [...document.getElementsByName(name)].filter(e => !e.dataset.jpId && live(e));
  if (found.length !== 1) return false;
  found[0].dataset.jpId = jp;
  return true;
}"""
# A talent-community / job-alert opt-in form has a headline and no resume upload (Roku sidebar).
TALENT_FORM_JS = r"""b => {
  const f = b.form || b.closest('form');
  if (!f || f.querySelector('input[type=file]')) return false;
  return /talent (?:community|network|pool)|join our (?:talent|community)|job alerts?|stay (?:connected|in touch)|career (?:updates|newsletter)|newsletter/i
    .test((f.innerText || '').slice(0, 600));
}"""
AUTOFILL = re.compile(r"auto.?fill|autofill|parse your|pre.?fill", re.IGNORECASE)
# An upload control that names no document ("Choose a file or drop it here"), in the languages career sites use.
GENERIC_UPLOAD = re.compile(
    r"\b(?:choose|select|upload|drop|drag|attach|browse)\b.{0,40}\bfiles?\b|"
    r"datei\b.{0,40}(?:auswählen|ablegen|hochladen)|(?:auswählen|hochladen).{0,40}datei|"
    r"(?:choisir|sélectionner|déposer|glisser).{0,40}fichier|(?:seleccion|arrastr|sub)\w*.{0,40}archivo",
    re.IGNORECASE,
)
PHOTO_UPLOAD = re.compile(r"photo|picture|profilbild|\bfoto|avatar|\bimage\b|\bbild\b", re.IGNORECASE)
NOT_RESUME = re.compile(
    r"cover letter|transcript|portfolio|writing sample|certificat|reference", re.IGNORECASE
)
# Optional fields worth one model call; other optional unknowns are left blank.
USEFUL_OPTIONAL = re.compile(
    r"linkedin|github|portfolio|website|salary|compensation|start date|notice|hear about|how did you|referr",
    re.IGNORECASE,
)
# Single-page ATSs whose react-select comboboxes keep their proven live behavior.
WORKDAY_HOST = re.compile(r"myworkdayjobs\.com|myworkdaysite\.com", re.I)
SINGLE_PAGE_HOSTS = re.compile(r"greenhouse\.io|lever\.co|ashbyhq\.com", re.I)
# Frames that never hold applicant fields: CAPTCHA/bot-check providers, analytics, media, chat.
PROVIDER_FRAME = re.compile(
    r"hcaptcha\.com|newassets\.hcaptcha|google\.com/recaptcha|recaptcha\.net|gstatic\.com/recaptcha|"
    r"challenges\.cloudflare\.com|turnstile|arkoselabs|funcaptcha|geetest|datadome|perimeterx|px-cdn|"
    r"captcha-delivery|googletagmanager|doubleclick|youtube\.com/embed|player\.vimeo|intercom|drift\.com|"
    r"zendesk|livechat",
    re.I,
)
MAX_FRAMES = 12
# A frame placed wholly outside the page (above or left of it) is a measuring helper, never an application document.
# FTI's iCIMS career site adds an about:blank "font-resize-helper" frame at top:-9999px per load; eleven of them used up
# the frame budget, and the Portal Specific Forms frame (fti-net.icims.com, nested in the content frame) was never
# scanned: the step read as an empty page with no fields or controls (FRONTIER TECHNOLOGY, 2026-10-27).
OFFSCREEN_FRAME_JS = """e => {
  const r = e.getBoundingClientRect(), w = e.ownerDocument.defaultView;
  return r.bottom + (w ? w.scrollY : 0) <= 0 || r.right + (w ? w.scrollX : 0) <= 0;
}"""
# A frame is known by its title, id and src without the query. iCIMS keeps its email step's GET submission in the content
# iframe's src ("/jobs/login?step=email&h-captcha-response=P1_..."); that query read as a CAPTCHA frame dropped Noblis's
# whole profile from uploads and page evidence, so the renamed resume never verified and every pass uploaded it again
# (2026-10-26..27). CAPTCHA frames name their provider in the host or path.
FRAME_IDENTITY_JS = "e=>[e.title,e.id,(e.src||'').split(/[?#]/)[0].slice(0,200)].join(' ')"
DEPENDENT_TYPES = {"radio", "radiogroup", "select", "dropdown", "pills", "checkbox", "combobox", "prompt"}


def implausible_link(field):
    """A link/URL field holding something that is not shaped like a web address."""
    if not (
        field.get("type") == "url"
        or re.search(r"\b(?:link|url|website|linkedin|github|portfolio)\b", field.get("label", ""), re.I)
    ):
        return False
    value = str(field.get("value") or "").strip()
    return bool(value) and not re.search(r"https?://|www\.|\w\.[a-z]{2,}", value, re.I)


def has_value(field):
    """An unticked checkbox reports "false", which is not an answer."""
    if field["type"] == "checkbox":
        return field["value"] == "true"
    return bool(field["value"])


CHAR_LIMIT = re.compile(
    r"(?:greater than|more than|longer than|exceeds?|maximum(?: of)?|max\.?|up to|no more than|limit(?:ed)? to|at most)"
    r"\s*(\d{2,5})\s*(?:characters|chars)",
    re.I,
)


def prose_for_constraint(field, value):
    """Repair only an explicit supported constraint on prose: forbidden punctuation, or a length limit
    the page states in its own validation ("Input greater than 199 characters", John Deere 2026-10-15)."""
    if field.get("type") not in {"text", "textarea", "contenteditable"}:
        return value
    limit = CHAR_LIMIT.search(field.get("error", ""))
    if limit and len(str(value)) > int(limit[1]) and len(str(value).split()) > 3:
        from .features.knowledge.writing import fit_length

        value = fit_length(str(value), int(limit[1]))
    if field.get("type") == "text":
        return value
    forbidden = re.search(
        r"cannot contain (?:the )?following characters:\s*([^\n]+)", field.get("error", ""), re.I
    )
    if forbidden:
        chars = forbidden[1].strip()
        if chars and all(c in ";<>|{}[]" or c.isspace() for c in chars):
            for char in chars:
                if not char.isspace():
                    value = str(value).replace(char, ". " if char == ";" else " ")
    return value


TOKEN_SEGMENT = re.compile(r"(?<=/)[A-Za-z0-9_-]{24,}(?=/|$)")
# Workday's own option sources: question definitions and value lists (employer content, no applicant answers).
WORKDAY_CATALOG = re.compile(
    r"myworkdayjobs\.com/wday/(?:calypso/)?cxs/.*/(?:questionnaire/[^/?]+(?:/definition)?|values/[^?]+)(?:\?|$)"
)
ERROR_KEY = re.compile(r"(?:error_?)?(?:message|title|detail|description|reason|error)", re.I)


class NetworkMonitor:
    """Counts in-flight XHR/fetch so waits follow the application instead of fixed sleeps.

    It also keeps a value-free journal of what the site did between steps (the REA analysis of 2026-10-17 showed
    failures hide in network behaviour: searches that load options, 4xx validations shown only as toasts). Each
    entry has method, type, host, path (long tokens redacted), query keys, status and timing, plus a redacted
    server message for a failed XHR/fetch. Step records carry the slice since the previous step.
    """

    def __init__(self, target):
        # A page (or a private context). Never a shared desktop context: other workers' traffic
        # would keep this run waiting.
        self.inflight = {}
        self.account_feedback = []
        self.last = time.monotonic()
        self.t0 = self.last
        self.journal, self.flushed = [], 0
        self.statuses, self.messages = {}, {}
        self.catalog = []  # Workday question definitions and value lists seen since the last step record
        # Each full Workday value list by its endpoint's last segment ("fieldsOfStudy" -> option names). Unlike the
        # step catalog it is not cleared at a step record: a prompt is filled after the step that loaded its list.
        self.values = {}
        self.watch(target)

    def value_list(self, kind):
        """The latest full (unsearched) Workday value list for `kind`, as option names, or None."""
        return self.values.get(kind)

    def take_catalog(self):
        entries, self.catalog = self.catalog, []
        return entries

    def take_journal(self, limit=80):
        """Requests finished since the previous step record, newest last."""
        entries = self.journal[self.flushed :]
        self.flushed = len(self.journal)
        return entries[-limit:]

    def _journal(self, request, started):
        try:
            parts = urlsplit(request.url)
            entry = {
                "t": round(started - self.t0, 2),
                "ms": int((time.monotonic() - started) * 1000),
                "method": request.method,
                "type": request.resource_type,
                "host": (parts.hostname or "")[:80],
                "path": TOKEN_SEGMENT.sub("[token]", parts.path)[:160],
                "query": sorted({key for key, _ in parse_qsl(parts.query, keep_blank_values=True)})[:8],
                "status": self.statuses.pop(request, None),
            }
            if request.failure:
                entry["failed"] = True
            pending = self.messages.pop(request, "")
            if pending is None:
                self.messages[request] = entry  # The body is still being read; _response attaches its message.
            elif pending:
                entry["message"] = pending
            self.journal.append(entry)
            if len(self.journal) > 600:
                drop = len(self.journal) - 400
                del self.journal[:drop]
                self.flushed = max(0, self.flushed - drop)
        except Exception:
            pass

    async def _catalog(self, response):
        """Keep Workday's option sources (the data the page renders its pickers from) for the step record."""
        try:
            if int(response.headers.get("content-length") or 0) > 400_000:
                return
            async with asyncio.timeout(3):
                body = await response.body()
            if len(body) > 400_000:
                return
            parts = urlsplit(response.url)
            query = sorted({key for key, _ in parse_qsl(parts.query, keep_blank_values=True)})[:8]
            payload = json.loads(body)
            self.catalog.append({
                "path": TOKEN_SEGMENT.sub("[token]", parts.path)[:200],
                "query": query,
                "payload": payload,
            })
            del self.catalog[:-40]
            if (
                "search" not in query
                and isinstance(payload, list)
                and payload
                and all(isinstance(item, dict) and isinstance(item.get("descriptor"), str) for item in payload)
            ):
                self.values[parts.path.rstrip("/").rsplit("/", 1)[-1]] = [item["descriptor"] for item in payload]
        except Exception:
            return

    async def _error_message(self, response):
        """The server's own error text from a JSON body, with emails and long numbers redacted."""
        try:
            if "json" not in (response.headers.get("content-type") or "").lower():
                return ""
            if int(response.headers.get("content-length") or 0) > 100_000:
                return ""
            async with asyncio.timeout(2):
                body = await response.body()
            if len(body) > 100_000:
                return ""
            payload = json.loads(body)
        except Exception:
            return ""
        texts = []

        def walk(node, depth=0):
            if depth > 4 or len(texts) >= 4:
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, str) and ERROR_KEY.fullmatch(str(key)):
                        texts.append(value.strip())
                    elif isinstance(value, (dict, list)):
                        walk(value, depth + 1)
            elif isinstance(node, list):
                for value in node[:10]:
                    walk(value, depth + 1)

        walk(payload)
        text = " | ".join(dict.fromkeys(t for t in texts if t))
        text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", text)
        return re.sub(r"\d[\d\s().-]{5,}\d", "[number]", text)[:300]

    def watch(self, target):
        target.on("request", self._start)
        target.on("requestfinished", self._end)
        target.on("requestfailed", self._end)
        target.on("response", self._response)

    async def _response(self, response):
        """Journal statuses; keep recognized business-error codes from this owned ADP page."""
        request = response.request
        if request in self.inflight:
            self.statuses[request] = response.status  # Before any await: requestfinished may journal it next.
        if response.status == 200 and request.resource_type in {"xhr", "fetch"} and WORKDAY_CATALOG.search(response.url):
            await self._catalog(response)
        if request in self.inflight:
            if response.status >= 400 and request.resource_type in {"xhr", "fetch"}:
                self.messages[request] = None
                message = await self._error_message(response)
                entry = self.messages.pop(request, None)
                if isinstance(entry, dict):
                    if message:
                        entry["message"] = message
                elif message:
                    self.messages[request] = message
        parts = urlsplit(response.url)
        operation = parts.path.rsplit("/", 1)[-1]
        if (
            parts.hostname != "workforcenow.adp.com"
            or "/candidate/login/" not in parts.path
            or operation not in {"signin", "person-otp-account.register"}
            or response.request.resource_type not in {"xhr", "fetch"}
        ):
            return
        try:
            if int(response.headers.get("content-length", "0")) > 200_000:
                return
            async with asyncio.timeout(2):
                body = await response.body()
            if len(body) > 200_000:
                return
            payload = json.loads(body)
            code = str(payload.get("errorCode", ""))
            if (operation, code) not in {("signin", "4040"), ("person-otp-account.register", "40014")}:
                return
            self.account_feedback.append({"operation": operation, "code": code})
            self.account_feedback = self.account_feedback[-6:]
        except (ValueError, TypeError, AttributeError, TimeoutError, PlaywrightError):
            return

    def account_conflict(self):
        observed = {(e["operation"], e["code"]) for e in self.account_feedback}
        return {("signin", "4040"), ("person-otp-account.register", "40014")} <= observed

    def _start(self, request):
        # Analytics requests neither commit fields nor load dependent options.
        # Their periodic traffic must not keep every application step unsettled.
        host = (urlsplit(request.url).hostname or "").lower()
        telemetry = ("google-analytics.com", "googletagmanager.com", "doubleclick.net",
                     "segment.io", "sentry.io", "datadoghq.com", "hotjar.com", "clarity.ms", "nr-data.net")
        if request.resource_type != "document" and any(host == d or host.endswith("." + d) for d in telemetry):
            return
        if request.resource_type in {"xhr", "fetch", "document"}:
            self.inflight[request] = time.monotonic()
            self.last = time.monotonic()

    def _end(self, request):
        started = self.inflight.pop(request, None)
        if started is not None:
            self.last = time.monotonic()
            self._journal(request, started)

    def pending(self, limit=3):
        """What keeps the transport busy right now, value-free: 'METHOD host path' of the requests that quiet()
        counts, newest first. Diagnoses a readiness wait that ran to its deadline."""
        now = time.monotonic()
        counted = [
            (t, request)
            for request, t in self.inflight.items()
            if request.resource_type == "document" or now - t < 4
        ]
        rows = []
        for _, request in sorted(counted, key=lambda item: -item[0])[:limit]:
            parts = urlsplit(request.url)
            rows.append(f"{request.method} {(parts.hostname or '')[:60]}{TOKEN_SEGMENT.sub('[token]', parts.path)[:100]}")
        return rows

    def quiet(self, period=0.3):
        # Ignore old background polling, but a document request can still replace
        # every observed control (for example, iCIMS resume parsing). The caller's
        # deadline bounds this wait; request age is not navigation completion.
        now = time.monotonic()
        recent = [
            t for request, t in self.inflight.items() if request.resource_type == "document" or now - t < 4
        ]
        return not recent and now - self.last >= period


# Renderer-side readiness wait. One protocol call holds until the frame has been free of meaningful mutations for
# `quiet` seconds and shows no loading indicator, or `budget` seconds pass; it answers whether the frame is still busy.
# Polling from Python cost one call per frame every 0.1 s, and each call waits for a busy renderer on the shared worker
# Chrome. The timers run in the page, which the host Chrome keeps unthrottled (--disable-background-timer-throttling).
SETTLE_JS = """async ({quiet, budget}) => {
  if (!window.__jpRenderQuiet) {
    const state = {changed: performance.now()};
    // Hidden templates, telemetry nodes and decorative text ticks do not
    // change the form. Waiting for their silence burns every step's timeout.
    const semantic = 'input,select,textarea,button,a,label,legend,option,h1,h2,h3,' +
      '[contenteditable=true],[role=button],[role=combobox],[role=listbox],' +
      '[role=option],[role=dialog],[role=alert],[role=heading],[aria-busy]';
    const element = n => n.nodeType === Node.ELEMENT_NODE ? n : n.parentElement;
    const ignored = n => {
      const e = element(n);
      if (!e || e.closest('head,script,style,template,[hidden],[aria-hidden=true]')) return true;
      return getComputedStyle(e).display === 'none' || getComputedStyle(e).visibility === 'hidden';
    };
    const meaningful = r => {
      const target = element(r.target);
      // Showing/hiding a container changes its live controls even when
      // the mutation's target is now hidden.
      if (r.type === 'attributes' && r.attributeName === 'hidden') return true;
      if (ignored(r.target)) return false;
      if (r.type === 'attributes' || target?.closest(semantic)) return true;
      return r.type === 'childList' && [...r.addedNodes, ...r.removedNodes].some(n => {
        const e = element(n);
        return e && !e.matches('script,style,template') &&
          (e.matches(semantic) || !!e.querySelector(semantic));
      });
    };
    new MutationObserver(records => {
      if (records.some(meaningful)) state.changed = performance.now();
    }).observe(document.documentElement, {subtree:true,childList:true,characterData:true,attributes:true,
      attributeFilter:['hidden','disabled','aria-busy','aria-invalid','aria-expanded','value','checked']});
    window.__jpRenderQuiet = state;
  }
  const isLoading = () => document.readyState !== 'complete' && document.readyState !== 'interactive' ||
    [...document.querySelectorAll('[aria-busy=true],[data-automation-id="loadingSpinner"],.oj-progress-circle,.spinner,.loading-spinner,.loader')]
    .some(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
  const end = performance.now() + budget * 1000;
  for (;;) {
    const now = performance.now();
    if (!isLoading() && now - window.__jpRenderQuiet.changed >= quiet * 1000) return false;
    if (now >= end) return true;
    await new Promise(resolve => setTimeout(resolve, 40));
  }
}"""

CONSENT_ACTION = re.compile(r"submit|continue|accept|agree|ok", re.I)


def consent_dialog_action(top, obs):
    """The single button of a consent-only privacy overlay, or None.

    Eightfold's "Please review before continuing" holds one checked privacy checkbox and one
    "Submit". It qualifies only when every field in the dialog is a checkbox, its text is about
    privacy or consent, and its one action is a plain Submit/Continue/Accept/Agree/OK. Any text,
    select or file input, or another control that could submit ("Submit application", "Apply"),
    leaves the decision to the agent and the human.
    """
    text = top.get("title", "") + " " + top.get("text", "")
    if not re.search(r"privacy|terms|data protection|consent", text, re.I):
        return None
    field_ids = set(top.get("field_ids", []))
    fields = [f for f in obs.get("fields", []) if f["id"] in field_ids]

    # Qualcomm's version adds a radio group already set to its agreeing choice; an unselected or
    # non-agreeing radio still leaves the decision to the agent.
    def agreed_radio(f):
        return f["type"] in {"radio", "radiogroup"} and bool(
            re.match(r"\s*(?:i )?(?:agree|accept|yes|consent)", str(f.get("value") or ""), re.I)
        )

    if not fields or any(f["type"] != "checkbox" and not agreed_radio(f) for f in fields):
        return None
    control_ids = set(top.get("control_ids", []))
    controls = [b for b in obs.get("controls", []) if b["id"] in control_ids]
    actions = [b for b in controls if re.fullmatch(CONSENT_ACTION.pattern, b["label"].strip(), re.I)]
    action_ids = {b["id"] for b in actions}
    others = [b for b in controls if b["id"] not in action_ids]
    if len(actions) != 1 or any(
        FINAL.search(b["label"]) or re.search(r"\bapply\b", b["label"], re.I) for b in others
    ):
        return None
    return actions[0]


GREENHOUSE_BOARD_HOSTS = {
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "boards.eu.greenhouse.io",
    "job-boards.eu.greenhouse.io",
}


def dayforce_tenant(url):
    """The client namespace of a jobs.dayforcehcm.com URL ("/en-US/ncmec/CANDIDATEPORTAL/..." -> "ncmec")."""
    parts = urlsplit(str(url or ""))
    if (parts.hostname or "").lower() != "jobs.dayforcehcm.com":
        return None
    segments = [x for x in parts.path.split("/") if x]
    if segments and re.fullmatch(r"[a-z]{2}-[A-Za-z]{2}", segments[0]):
        segments = segments[1:]
    return segments[0].lower() if segments else None


def display_date(text):
    """A date as shown by pickers ("14 Oct 2026", "10/14/2026", "2026-10-14", "Oct 14, 2026", "today")."""
    from datetime import date, datetime

    t = str(text or "").strip()
    if t.lower() in {"today", "now"}:
        return date.today()
    for fmt in ("%d %b %Y", "%d %B %Y", "%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


# Acknowledgements on German, French and Spanish career sites; read like the English ones, in a status region only.
LOCALIZED_CONFIRMATION = (
    r"bewerbung(?: [^.!?\n]{1,160}?)? (?:wurde |ist )?(?:erfolgreich )?"
    r"(?:eingereicht|versendet|abgeschickt|übermittelt|gesendet|eingegangen)\b|"
    r"vielen dank für (?:ihre|deine) bewerbung|"
    r"candidature (?:a (?:bien )?été )?(?:envoyée|soumise|transmise|reçue|enregistrée)|merci pour votre candidature|"
    r"(?:solicitud|candidatura|postulación) (?:ha sido |fue )?(?:enviada|recibida|registrada)|"
    r"gracias por (?:su |tu )?(?:solicitud|postulación|candidatura)"
)


EMPLOYER_CONFIRMATION_RE = re.compile(
    r"thank you for (?:submitting|sharing|completing)\b.{0,60}(?:application|information)"
    # Oracle (Texas Instruments 2026-10-14): "Thank you for your job application."
    r"|thank you for your (?:job )?application\b"
    r"|\bapplication (?:has been |was )?(?:successfully )?(?:received|submitted)\b"
    # Parallel Systems: "We wanted to let you know we received your application".
    r"|\bwe(?:'ve| have)? received your application\b"
    # Pinterest (Greenhouse embed) 2026-10-17: "Good news: your application is in!"
    r"|\byour application is in\b"
    r"|task complete!?",
    re.I,
)
EMPLOYER_CONFIRMATION_ERROR_RE = re.compile(
    r"(?:error|invalid|required|too short|could not|failed|try again)", re.I
)
EMPLOYER_APPLY_FORM_RE = re.compile(
    r"\bapply for this job\b|\bapplication form\b|\bsubmit (?:your )?application\b", re.I
)


def employer_confirmation_text(text):
    """Return the employer thank-you sentence from visible page text, or None.

    Pure text rule only; proof() also requires the page to have no fields or
    submit controls, and a Greenhouse job on a Greenhouse or employer-owned host."""
    squashed = " ".join((text or "").split())
    if (
        not squashed
        or EMPLOYER_CONFIRMATION_ERROR_RE.search(squashed)
        or EMPLOYER_APPLY_FORM_RE.search(squashed)
    ):
        return None
    match = EMPLOYER_CONFIRMATION_RE.search(squashed)
    return squashed[match.start() : match.start() + 300] if match else None


# iCIMS's self-identification radios carry codes, worded as the scan offers them (js/scan.js SELF_ID).
SELF_ID_CODES = {
    "ProtectedVeteran": "I identify as one or more of the classifications of protected veteran",
    "NotProtectedVeteran": "I am not a protected veteran",
    "optout": "I don't wish to answer",
}
ICIMS_RECEIPT = re.compile(
    r"your application (?:was|has been) submitted successfully[^\n]{0,60}|you are currently submitted to this job", re.I
)


def icims_receipt(job_url, frame_url, body):
    """iCIMS's receipt sits in its content frame, on the job's own page (ICE 2026-10-19, after the last packet form:
    "Your application was submitted successfully. Thank you for applying." and "You are currently submitted to this
    job."). Only a frame on the job's host and requisition counts; the outer page holds the employer's banner."""
    job, frame = urlsplit(job_url or ""), urlsplit(frame_url or "")
    requisition = re.search(r"/jobs/(\d+)(?:/|$)", job.path)
    if not (
        requisition
        and frame.scheme == job.scheme == "https"
        and frame.hostname == job.hostname
        and re.match(rf"/jobs/{requisition[1]}(?:/|$)", frame.path)
    ):
        return None
    found = ICIMS_RECEIPT.search(body or "")
    return found[0].strip() if found else None


def employer_greenhouse_page_owned(page_url, job):
    """A Greenhouse job's page is Greenhouse-hosted or on the job's own registrable domain."""
    from .accounts import registrable
    from .ats import detect

    job_url = str((job or {}).get("url") or "")
    job_parts = urlsplit(job_url)
    job_host = (job_parts.hostname or "").lower()
    page = urlsplit(page_url)
    page_host = (page.hostname or "").lower()
    if not job_host or not page_host or page.scheme != "https":
        return False
    job_ats = str((job or {}).get("ats") or "").lower()
    if job_ats:
        is_greenhouse = job_ats == "greenhouse"
    else:
        is_greenhouse = detect(job_url).id == "greenhouse" or "gh_jid=" in job_parts.query
    if not is_greenhouse:
        return False
    if page_host in GREENHOUSE_BOARD_HOSTS:
        if job_host in GREENHOUSE_BOARD_HOSTS:
            board = page.path.strip("/").split("/")[0]
            if board == "embed":
                # The embedded form and its confirmation (/embed/job_app/confirmation?for=asana&token=7962412)
                # name the board and the job post in the query; Asana's "Task complete!" was missed (2026-10-18).
                query = dict(parse_qsl(page.query))
                board = query.get("for", "")
                post, job_post = query.get("token"), re.search(r"/jobs/(\d+)", job_parts.path)
                if post and job_post and post != job_post[1]:
                    return False
            return bool(board) and board == job_parts.path.strip("/").split("/")[0]
        return True
    if detect(page_url).id != "custom":
        return False
    return registrable(page_host) == registrable(job_host)


# Section launchers that open a screener, not the application's final submit: Oracle's "Take Tax
# Credit Assessment" (Caesars) and Starbucks' "Complete WOTC" (Eightfold; opens a survey tab).
SURVEY_DONE = re.compile(
    r"thank you|survey (?:is |has been )?(?:complete|submitted)|you have (?:completed|finished)|"
    r"questionnaire (?:is |has been )?(?:complete|submitted)|you may (?:now )?close this (?:window|tab)",
    re.I,
)
LAUNCHER = (
    r"\s*(?:take|start|complete|begin)(?: the)? (?:(?:tax credit|wotc|work opportunity tax credit)"
    # Workday's step button says only "Take Assessment" (Tyson Foods' TaxCreditCo survey tab, 2026-10-24).
    r"(?: (?:assessment|screening|questionnaire|survey))?|assessment)\s*"
)


class FormSession:
    def __init__(self, page, resolver, resume_path, emit):
        self.page, self.resolver, self.resume_path, self.emit = page, resolver, resume_path, emit
        self.retyped_phones = set()
        # Greenhouse phones typed (and read back) in this run: the site's "too short" from an
        # earlier submit stays on the field until the next submit re-validates it.
        self.typed_phones = set()
        self.fields, self.buttons, self.frames = {}, {}, {}
        self.meta = {"headings": [], "errors": [], "automation": [], "busy": False, "title": "", "url": ""}
        self.expected = {}
        self.expected_labels = {}
        self.expected_slots = {}
        self.ledger = {}
        self.pending = []
        self.uploaded = False
        self.upload_verified = False
        self.last_problems = []
        self.network = None
        self.accept_prefilled = False
        self.resume_seen = False
        self.workday_resume_replaced = False
        self.control_recoveries = {}
        self.control_choices = {}
        self.optional_picker_omissions = set()
        self.document_uploads = {}
        self.owner_id = uuid.uuid4().hex
        self.revision = 0
        self._revision_hash = ""
        self.last_observation = {}
        self.last_action = None
        self.failed_strategies = {}
        self.resume_attachment = {}
        self.quality_checks = {}

    @staticmethod
    def frame_key(field):
        # Document ownership survives a React remount, but not a navigation to
        # another document. No authentication URL enters the persisted key.
        parts = field.get("id", "").split("-", 2)
        return parts[1] if len(parts) == 3 else ""

    @classmethod
    def field_key(cls, field):
        return (
            normalize(field["label"]),
            field["type"],
            field.get("slot", 0),
            normalize(field.get("section", "")),
            normalize(field.get("group", "")),
            field.get("name", ""),
            field.get("automation_id", ""),
            cls.frame_key(field),
        )

    @staticmethod
    def question_shape(field):
        # Native choices are complete in each scan. Custom popup choices are
        # observed by their driver at commit time, not by the closed-page scan.
        choices = (
            () if field["type"] in {"dropdown", "combobox", "prompt"} else tuple(field.get("options", []))
        )
        return (
            field["label"],
            field["type"],
            bool(field.get("required", False)),
            field.get("section", ""),
            field.get("group", ""),
            field.get("name", ""),
            field.get("widget", ""),
            field.get("date_format", ""),
            field.get("hint", ""),
            field.get("maxlength", -1),
            json.dumps(field.get("related_question", {}), sort_keys=True),
            choices,
            tuple(p["kind"] for p in field.get("parts", [])),
        )

    def matching_field(self, field):
        same_question = lambda f: f["label"] == field["label"] and f["type"] == field["type"]  # noqa: E731
        current = self.fields.get(field.get("id"))
        if current and same_question(current) and self.field_key(current) == self.field_key(field):
            return current
        matches = [
            f
            for f in self.fields.values()
            if same_question(f)
            and self.frame_key(f) == self.frame_key(field)
            and f.get("section", "") == field.get("section", "")
            and f.get("group", "") == field.get("group", "")
            and f.get("slot", 0) == field.get("slot", 0)
            and all(not field.get(k) or f.get(k) == field[k] for k in ("name", "automation_id"))
        ]
        if len(matches) != 1:
            raise ValueError("Question changed while filling")
        return matches[0]

    def record_answer(self, field, value, evidence_ids, verified=False):
        from .answer_context import digest

        options = list(field.get("options", []))
        if (
            field["type"]
            in {"combobox", "dropdown", "prompt", "select", "pills", "radio", "radiogroup", "buttonchoice"}
            and value
            and value not in options
        ):
            # The driver committed this exact offered label. The initial search
            # window need not have contained it; retain it for final quality checks.
            options.append(value)
        self.ledger[field["id"]] = {
            "label": field["label"],
            "type": field["type"],
            "slot": field.get("slot", 0),
            "field_key": list(self.field_key(field)),
            "answer": value,
            "evidence_ids": evidence_ids,
            "verified": verified,
            "profile_digest": digest(self.resolver.profile.model_dump()),
            "knowledge_digest": digest([entry.model_dump() for entry in self.resolver.kb.entries])
            if self.resolver.kb
            else "",
            "question_digest": digest([self.question_shape(field), field.get("context", "")]),
            "catalog_digest": self.catalog_digest(field),
            "options": options,
            "comparison": {
                key: deepcopy(field[key])
                for key in (
                    "required",
                    "section",
                    "group",
                    "name",
                    "automation_id",
                    "widget",
                    "date_format",
                    "options_partial",
                    "hint",
                    "context",
                    "related_question",
                    "maxlength",
                )
                if key in field
            }
            | {"parts": [{"kind": part["kind"]} for part in field.get("parts", [])]},
        }

    async def recover_controls(self, report):
        from .control_agent import recover

        try:
            async with asyncio.timeout(CONTROL_RECOVERY_SECONDS):
                return await recover(self, report)
        except Exception as exc:
            self.emit(
                "control_recovery",
                "Bounded control recovery did not complete",
                {"error_type": type(exc).__name__},
            )
            return report

    async def return_to_opener(self):
        """Back to the application tab once a launched survey tab is closed or finished."""
        openers = getattr(self, "openers", [])
        if not openers:
            return False
        survey = self.page
        self.page = openers.pop()
        if not survey.is_closed():
            try:
                await survey.close()
            except PlaywrightError:
                pass
        if callback := getattr(self, "on_popup", None):
            await callback(self.page)
        self.emit("survey_returned", "Returned to the application after the launched survey")
        # Starbucks (Eightfold) 2026-10-15: completing the WOTC survey re-rendered the form and cleared
        # the required privacy acknowledgement, so the final Submit silently did nothing. Refill once.
        try:
            await self.fill_current()
        except Exception:
            pass  # The owned navigator observes and repairs anything still incomplete.
        return True

    async def scan(self):
        if self.page.is_closed() and getattr(self, "openers", None):
            await self.return_to_opener()
        await widgets.open_collapsed_sections(self.page)
        # Keep the prior controls so a click on an id replaced by a re-render can rebind by label.
        self.last_buttons = (
            {**getattr(self, "last_buttons", {}), **self.buttons}
            if self.buttons
            else getattr(self, "last_buttons", {})
        )
        if len(self.last_buttons) > 400:
            self.last_buttons = dict(list(self.last_buttons.items())[-400:])
        self.fields, self.buttons, self.frames = {}, {}, {}
        texts = []
        meta = {
            "headings": [],
            "errors": [],
            "automation": [],
            "disabled_actions": [],
            "dialogs": [],
            "busy": False,
            "title": "",
            "url": self.page.url,
        }
        observed_frames = 0
        successful_frames = 0
        frame_errors = []
        options = []
        for n, frame in enumerate(self.page.frames):
            try:
                if frame != self.page.main_frame:
                    if frame.is_detached() or PROVIDER_FRAME.search(frame.url or ""):
                        continue
                    owner = await frame.frame_element()
                    identity = await owner.evaluate(FRAME_IDENTITY_JS)
                    # CAPTCHA widgets (Skip, Refresh Challenge, accessibility) are never applicant
                    # fields or navigation; the CAPTCHA solver owns them.
                    if PROVIDER_FRAME.search(identity) or re.search(
                        r"hcaptcha|recaptcha|challenge", identity, re.I
                    ):
                        continue
                    if not await owner.is_visible() or await owner.evaluate(OFFSCREEN_FRAME_JS):
                        continue
                if observed_frames >= MAX_FRAMES:
                    break
                observed_frames += 1
                try:
                    data = await asyncio.wait_for(frame.evaluate(SCAN), timeout=5)
                except TimeoutError:
                    if frame != self.page.main_frame:
                        raise
                    # A busy main frame often answers on a second try; retry once before reporting it.
                    data = await asyncio.wait_for(frame.evaluate(SCAN), timeout=5)
                # Navigation, frame teardown or site instrumentation can yield
                # null instead of a scanner object. Treat the frame as unobserved,
                # not as an empty valid form or a fatal worker exception.
                if (
                    not isinstance(data, dict)
                    or not isinstance(data.get("text"), str)
                    or not isinstance(data.get("document_id"), str)
                    or any(
                        not isinstance(data.get(key), list)
                        or any(
                            not isinstance(item, dict) or not isinstance(item.get("id"), str)
                            for item in data[key]
                        )
                        for key in ("fields", "buttons")
                    )
                ):
                    raise ValueError("scanner_payload")
            except Exception as exc:
                frame_errors.append({"frame": n, "error_type": type(exc).__name__})
                continue
            successful_frames += 1
            texts.append(data["text"])
            if n == 0:
                meta["title"] = data.get("title", "")
            for key in ("headings", "errors", "automation", "disabled_actions"):
                meta[key].extend(data.get(key, []))
            for d in data.get("dialogs", []):
                prefix = f"f{n}-{data['document_id']}-"
                meta["dialogs"].append(
                    {
                        "title": d.get("title", ""),
                        "text": d.get("text", ""),
                        "automation": d.get("automation", []),
                        "field_ids": [prefix + x for x in d.get("field_ids", [])],
                        "control_ids": [prefix + x for x in d.get("control_ids", [])],
                    }
                )
            meta["busy"] = meta["busy"] or data.get("busy", False)
            for kind in ("fields", "buttons"):
                for item in data[kind]:
                    if kind == "fields" and re.search(
                        r"how often.*receive an? alert|(?:job )?alert frequency",
                        item.get("label", ""),
                        re.I,
                    ):
                        # SAP job postings embed a required alert subscription beside Apply.
                        # Its frequency is not an applicant fact or an application field.
                        continue
                    if kind == "fields" and AUTH.search(item.get("label", "")) and item.get("value"):
                        # Codes and passwords never leave the page through an observation (model
                        # prompts, agent tools, traces); only that a value is present.
                        item["value"] = "********"
                    item["employer"] = getattr(self.resolver, "employer", "")
                    item["dom_id"] = item["id"]
                    item["id"] = f"f{n}-{data['document_id']}-{item['id']}"
                    item["frame_url"] = frame.url
                    item["member_ids"] = item.pop("members", [])
                    if kind == "fields" and site_search(item):
                        continue  # The career site's own search box is never an application question.
                    self.frames[item["id"]] = frame
                    getattr(self, kind)[item["id"]] = item
            try:
                frame_options = (await widgets.visible_options(frame))[:150]
                for option in frame_options:
                    if owner := option.get("owner_dom_id"):
                        owner_id = f"f{n}-{data['document_id']}-{owner}"
                        if owner_id in self.fields:
                            option["control_id"] = owner_id
                options.extend(frame_options)
            except PlaywrightError:
                pass
        self.meta = meta
        # An informational pop-up over the form ("Quick tip before you begin ... Okay", ADP's tax-credit questionnaire
        # from Workday, 2026-10-23): the page hides its questions behind it, the scan found no fields and the run
        # stalled twice. A dialog with no fields whose only action is an acknowledgement is acknowledged, then rescanned.
        ack = self.acknowledgement_only(meta) if getattr(self, "_acknowledged", 0) < 3 else None
        if ack:
            try:
                armed = await self.page.evaluate("Boolean(window.__jpArmed)")  # after the submit it may be the receipt
            except PlaywrightError:
                armed = True
            ack = None if armed else ack
        if ack:
            self._acknowledged = getattr(self, "_acknowledged", 0) + 1
            try:
                await self.locator(ack).click(timeout=4000)
                await self.settle(timeout=3)
                return await self.scan()
            except (PlaywrightError, ValueError):
                pass
        # Every execution route sees the active dialog, never controls behind it.
        dialogs = [d for d in meta["dialogs"] if d.get("field_ids") or d.get("control_ids")]
        if dialogs:
            active = dialogs[-1]
            useful = [
                b
                for b in self.buttons.values()
                if b["id"] in active["control_ids"]
                and not re.fullmatch(r"close|x|×|cancel", b["label"], re.I)
            ]
            if active["field_ids"] or useful:
                self.fields = {k: f for k, f in self.fields.items() if k in active["field_ids"]}
                self.buttons = {k: b for k, b in self.buttons.items() if k in active["control_ids"]}
        from .widgets_ats import read_combos

        # Previously each combo made up to three renderer round trips on every
        # whole-page scan; rematching N fields multiplied this cost by N again.
        combo_frames = {}
        for field in self.fields.values():
            if field["type"] == "combobox":
                combo_frames.setdefault(self.frames[field["id"]], []).append(field)
        for frame, fields in combo_frames.items():
            values = await read_combos(frame, [f["dom_id"] for f in fields])
            for field in fields:
                if current := values.get(field["dom_id"]):
                    field["value"] = current["value"]
                    field["provider_combo"] = current["family"]
        if (profile := getattr(getattr(self, "resolver", None), "profile", None)) is not None:
            regroup_history_rows(list(self.fields.values()), profile)
        # Checkbox lists answer one question; peers give context ("I do not want to answer" -> disability).
        boxes = {}
        for f in self.fields.values():
            if f["type"] == "checkbox":
                boxes.setdefault((f["frame_url"], f["section"], f["group"]), []).append(f)
        for peers in boxes.values():
            for f in peers:
                f["peers"] = " | ".join(x["label"] for x in peers if x is not f)[:600]
        slots = {}
        for field in self.fields.values():
            key = self.field_key(field)
            field["slot"] = slots.get(key, 0)
            slots[key] = field["slot"] + 1
            if field.get("group"):
                field["context"] = {
                    f["label"]: f["value"]
                    for f in self.fields.values()
                    if f.get("group") == field["group"]
                    and f["id"] != field["id"]
                    and f["type"] not in {"password", "file"}
                    and f["value"]
                    and not AUTH.search(f["label"])
                }
        mark_history_rows(self.fields.values())
        name_phone_numbers(list(self.fields.values()))
        link_companion_text(self.fields.values())
        label_signature_date(list(self.fields.values()), "\n".join(texts))
        mark_uppercase_fields(list(self.fields.values()), "\n".join([*texts, *getattr(self, "refusal_notes", [])]))
        icims_protocol.apply(list(self.fields.values()), list(self.buttons.values()), "\n".join(texts))
        numeric_year_counts(self.fields.values())
        oracle_education_block(self.fields.values())
        profile = getattr(getattr(self, "resolver", None), "profile", None)
        taleo_protocol.apply(
            list(self.fields.values()),
            [w.company for w in getattr(profile, "work", None) or []],
            [e.school for e in getattr(profile, "education", None) or []],
        )
        obs = {
            "fields": list(self.fields.values()),
            "controls": list(self.buttons.values()),
            "text": "\n".join(texts)[:22000],
            "url": self.page.url,
            **{k: meta[k] for k in ("headings", "errors", "automation", "dialogs", "busy", "title")},
            "options": options,
            "owner_id": self.owner_id,
            "scan_success": bool(successful_frames),
            "observation_failure": not bool(successful_frames),
            "frame_errors": frame_errors,
        }
        revision_hash = fingerprint(obs, revision=True)
        if revision_hash != self._revision_hash:
            self.revision += 1
            self._revision_hash = revision_hash
        obs["revision"] = self.revision
        obs["progress_fingerprint"] = fingerprint(obs)
        self.last_observation = obs
        if not successful_frames and self.page.is_closed():
            # A crashed or closed tab never recovers in place; fail fast so the run is retried.
            raise BrowserTabClosed("The application tab was closed")
        if successful_frames:
            self.last_good_observation = deepcopy(obs)
            self._scan_failure_reported = False
        elif not getattr(self, "_scan_failure_reported", False):
            self._scan_failure_reported = True
            self.emit(
                "observation_failure",
                "No application document could be inspected",
                {"frame_errors": frame_errors},
            )
        return obs

    def reset_document_verification(self):
        """A fresh entry must verify its actual fields, not an old document ledger."""
        self.ledger = {}
        self.expected = {}
        self.expected_labels = {}
        self.expected_slots = {}
        self.pending = []
        self.control_choices = {}
        self.quality_checks = {}
        self.uploaded = self.upload_verified = self.workday_resume_replaced = False
        self.resume_attachment = {}
        self.document_uploads = {}

    def checkpoint_state(self, resume_sha=""):
        from .runtime import engine_version
        from .answer_quality import VERSION as quality_version

        return {
            "version": 1,
            "engine_version": engine_version(),
            "resume_sha": resume_sha,
            "ledger": self.ledger,
            "uploaded": self.uploaded,
            "upload_verified": self.upload_verified,
            "resume_attachment": self.resume_attachment,
            "workday_resume_replaced": self.workday_resume_replaced,
            "document_uploads": [[list(k), value] for k, value in self.document_uploads.items()],
            "control_recoveries": [[list(k), value] for k, value in self.control_recoveries.items()],
            "failed_strategies": self.failed_strategies,
            "quality_checks": self.quality_checks,
            "quality_version": quality_version,
        }

    def restore_checkpoint(self, saved, resume_sha, source_engine=None):
        if not saved or saved.get("resume_sha") != resume_sha:
            return False
        self.ledger = saved.get("ledger", {})
        self.uploaded = bool(saved.get("uploaded"))
        self.upload_verified = bool(saved.get("upload_verified"))
        self.resume_attachment = saved.get("resume_attachment", {})
        self.workday_resume_replaced = bool(saved.get("workday_resume_replaced") and self.resume_attachment)
        self.document_uploads = {tuple(k): v for k, v in saved.get("document_uploads", [])}
        self.control_recoveries = {tuple(k): v for k, v in saved.get("control_recoveries", [])}
        self.failed_strategies = dict(saved.get("failed_strategies", {}))
        from .runtime import engine_version

        previous_engine = saved.get("engine_version") or source_engine
        if previous_engine and previous_engine != engine_version():
            # Keep outcomes as context, while a deployed code change gives the
            # repaired controller a fresh bounded attempt. Submission guards,
            # attachment identity and confirmed values are independent.
            self.failed_strategies.pop("__navigation__", None)
            self.failed_strategies.pop("__navigation_outcomes__", None)
            self.failed_strategies.pop("__recovery_blocker__", None)
            self.control_recoveries = {}
            for key, strategy in list(self.failed_strategies.items()):
                if not key.startswith("__") and isinstance(strategy, dict):
                    self.failed_strategies[key] = {**strategy, "episodes": 0, "attempts": []}
        self.quality_checks = dict(saved.get("quality_checks", {}))
        from .answer_quality import VERSION as quality_version

        if saved.get("quality_version") != quality_version:
            # These are derived proposals, not confirmed applicant facts. A changed
            # audit must observe and reason afresh rather than execute an old repair.
            self.quality_checks = {}
            self.failed_strategies.pop("__quality_corrections__", None)
            self.failed_strategies.pop("__quality_model_checks__", None)
            self.failed_strategies.pop("__quality_repair_attempts__", None)
            blocker = self.failed_strategies.get("__recovery_blocker__", {})
            if any(
                f.get("repair_cause")
                in {"confirmed_fact_mismatch", "punctuation_policy", "supported_quality_repair"}
                or "quality" in str(f.get("repair_cause", ""))
                for f in blocker.get("fields", [])
            ):
                self.failed_strategies.pop("__recovery_blocker__", None)
        return True

    async def assert_revision(self, revision, owner_id=None):
        obs = await self.scan()
        if revision != obs["revision"] or (owner_id and owner_id != self.owner_id):
            raise ValueError("Stale browser observation; observe before acting")
        return obs

    @staticmethod
    def strategy_key(field):
        return hashlib.sha256(json.dumps(field_identity(field)).encode()).hexdigest()

    async def widget_observation(self, field, query=""):
        current = await self.rematch(field)
        el, frame = self.locator(current["id"]), self.frames[current["id"]]
        options = (
            await self.radio_options(current)
            if current["type"] in {"radio", "radiogroup", "pills", "checkboxgroup"}
            else await self.native_options(current)
            if current["type"] in {"select", "select-multiple"}
            else await widgets.wait_options(frame, await widgets._popup_scope(el), timeout=1, owner=el)
        )
        option_count = len(options)
        if query:
            options = [o for o in options if normalize(query) in normalize(o["text"])]
        # Recovery history has a bounded token budget. Put a locally supported
        # choice first instead of cutting the relevant end of a country list.
        local = self.resolver.local({**current, "options": [o["text"] for o in options if not o["disabled"]]})
        if local and local.disposition == "answer":
            options.sort(key=lambda o: o["text"] != local.value)
        more = len(options) > 8
        options = [{k: o.get(k) for k in ("id", "text", "leaf", "disabled")} for o in options[:8]]
        obs = await self.scan()
        current = self.matching_field(current)
        return {
            "owner_id": self.owner_id,
            "revision": obs["revision"],
            "field": {
                k: current.get(k) for k in ("id", "label", "type", "value", "valid", "group", "widget")
            },
            "options": options,
            "option_count": option_count,
            "more_options": more,
            "progress_fingerprint": obs["progress_fingerprint"],
        }

    async def native_options(self, field):
        """Native options need no popup; identities bind owner, index and content."""
        options = await self.locator(field["id"]).evaluate("""e=>[...e.options].map((o,index)=>({
            index,value:o.value,text:o.text.trim(),leaf:true,
            disabled:e.disabled||o.disabled||!!o.closest('optgroup[disabled]')||o.value===''
        }))""")
        for option in options:
            identity = [field["id"], option["index"], option["value"], option["text"]]
            option["id"] = "native-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
        return options

    async def radio_options(self, field):
        """Observed members of this question only; no popup or click is needed."""
        frame = self.frames[field["id"]]
        members, labels = field.get("member_ids", []), field.get("options", [])
        if len(members) != len(labels):
            raise ValueError("Radio group changed; observe again")
        options = []
        for member, label in zip(members, labels, strict=True):
            control = frame.locator(f'[data-jp-id="{member}"]')
            if await control.count() != 1:
                raise ValueError("Radio member is no longer available")
            disabled = await control.evaluate("""e=>{
                const inputs=e.querySelectorAll('input[type=radio]');
                return !!e.disabled || e.getAttribute('aria-disabled')==='true' ||
                    (inputs.length===1 && inputs[0].disabled);
            }""")
            options.append({"id": member, "text": label, "leaf": True, "disabled": disabled})
        return options

    async def inspect_control_options(self, field, query=""):
        """Keep the owned menu open and return executable observed option IDs."""
        current = await self.rematch(field)
        el, frame = self.locator(current["id"]), self.frames[current["id"]]
        if not await widgets.owned_picker_open(el):
            await widgets.open_owned_picker(frame, el, current["type"])
        return await self.widget_observation(current, query=query)

    async def control_action(
        self, action, field_id, revision, *, owner_id=None, option_id="", query="", direction="down"
    ):
        """Execute one owned control operation and return observed, typed feedback."""
        before = await self.assert_revision(revision, owner_id)
        field = self.fields.get(field_id)
        if not field or AUTH.search(field["label"]) or field["type"] == "password":
            return ActionResult(
                action=action, failure_category="missing", detail="Owned applicant control unavailable"
            )
        el, frame = self.locator(field_id), self.frames[field_id]
        executed, failure, detail = False, None, ""
        try:
            if action == "open":
                await widgets.open_owned_picker(frame, el, field["type"])
            elif action == "search":
                if field["type"] not in {"prompt", "combobox"}:
                    raise ValueError("This observed control has no editable search")
                inp = el.locator("input:not([type=hidden])").first if field["type"] == "prompt" else el
                if not await inp.is_editable():
                    raise ValueError("The observed search input is not editable")
                await inp.fill("")
                await inp.press_sequentially(query[:80], delay=12, timeout=2000)
                if field["type"] == "prompt":
                    await inp.press("Enter")
            elif action == "choose":
                if field["type"] == "checkboxgroup":
                    raise ChoiceRejected(
                        "unsupported", "Use fill to commit the complete supported multiple selection"
                    )
                if job_catalog_search_box(field):
                    # A job board's own search suggestions are not an application answer. Do not
                    # route them through answer validation, which the controller counts as a retry.
                    raise ChoiceRejected("unsupported", "Job board search box is not an application choice")
                inline = field["type"] in {"radio", "radiogroup", "pills"}
                options = (
                    await self.radio_options(field)
                    if inline
                    else await self.native_options(field)
                    if field["type"] in {"select", "select-multiple"}
                    else await widgets.visible_options(frame, await widgets._popup_scope(el))
                )
                option = next((o for o in options if o["id"] == option_id and not o["disabled"]), None)
                decided = None
                if not option and (inline or field["type"] in {"select", "select-multiple"}):
                    # The controller named an option this control does not have (PepsiCo's tax-credit "Select a
                    # Language" radio, 2026-10-23: eight refused chooses). Take the option the evidence supports.
                    offered = [o for o in options if not o["disabled"]]
                    decided = (await self.resolver.resolve([{**field, "options": [o["text"] for o in offered]}]))[0]
                    AnswerModelUnavailable.raise_for(decided)
                    if decided.disposition == "answer":
                        option = next((o for o in offered if o["text"] == decided.value), None)
                if not option:
                    raise ValueError("Choice is stale or outside this control")
                supported = None
                if option["leaf"]:
                    partial = False
                    if field["type"] == "combobox":
                        from .widgets_ats import combo_family

                        if await combo_family(frame, el) == "oracle":
                            partial = field.get("options_partial", False) or await el.is_editable()
                    supported = decided or (
                        await self.resolver.resolve(
                            [
                                {
                                    **field,
                                    "options": [o["text"] for o in options if not o["disabled"]],
                                    "options_partial": partial,
                                }
                            ]
                        )
                    )[0]
                    AnswerModelUnavailable.raise_for(supported)
                    if supported.disposition != "answer":
                        # The control works; the answer is what is missing or rejected.
                        # Say so, so the controller stops retrying a working control.
                        if supported.blocker_type == "missing_fact":
                            self._pending(
                                {**field, "options": [o["text"] for o in options if not o["disabled"]]},
                                supported.reason,
                                "missing_fact",
                                supporting_facts=supported.supporting_facts,
                            )
                            raise ChoiceRejected(
                                "missing_fact", "Applicant fact required: " + supported.reason
                            )
                        raise ChoiceRejected(
                            "unsupported", "Answer validation rejected this choice: " + supported.reason
                        )
                    if supported.value != option["text"]:
                        # The evidence names a sibling option: choose it rather than refuse. BNY's salary menu offers
                        # 70000, 80000, ... for a saved "70,001 - 100,000"; every choose was refused with "supports
                        # '80000' instead" and the run stalled (2026-10-23).
                        alternative = next(
                            (o for o in options if o["text"] == supported.value and o["leaf"] and not o["disabled"]),
                            None,
                        )
                        if alternative is None:
                            raise ChoiceRejected(
                                "unsupported",
                                f"Applicant evidence supports the offered option {supported.value[:80]!r} instead",
                            )
                        option = alternative
                if inline or field["type"] == "select":
                    await self.fill(field, option["text"])
                    # SuccessFactors (Corning, live probe) keeps aria-invalid/"…is required" from an
                    # earlier validation until the control loses focus; the value is already set.
                    # Re-reading before blur reported a committed choice as "did not commit".
                    try:
                        await el.evaluate("e => { e.focus(); e.blur(); }")
                    except PlaywrightError:
                        pass
                elif field["type"] == "select-multiple":
                    # A single choice must not silently clear other approved values.
                    raise ValueError("Use fill to commit the complete supported multiple selection")
                else:
                    await widgets.click_option(frame, option, owner=el)
                if option["leaf"]:
                    if not inline:
                        await widgets.close_picker(el, frame)
                    await self.settle(timeout=2)
                    observed = await self.rematch(field)
                    if not observed["valid"] or not widgets.same_value(
                        observed, observed["value"], option["text"]
                    ):
                        raise ValueError("The website did not commit the observed choice")
                    self.expected[observed["id"]] = option["text"]
                    self.expected_slots[self.field_key(observed)] = option["text"]
                    self.record_answer(observed, option["text"], supported.evidence_ids, verified=True)
            elif action == "back_menu":
                await widgets.back_menu(frame, el)
            elif action == "scroll":
                if not await widgets.scroll_menu(frame, el, direction):
                    raise ValueError("The owned menu cannot scroll farther")
            elif action == "date":
                if field["type"] not in {"date", "date_parts"} and field.get("widget") != "date-text":
                    raise ValueError("This observed control is not a date input")
                answer = (await self.resolver.resolve([field]))[0]
                if answer.disposition != "answer":
                    return ActionResult(
                        action=action, failure_category="missing_fact", detail=answer.reason[:240]
                    )
                await self.fill(field, answer.value)
                self.record_answer(field, answer.value, answer.evidence_ids)
            elif action == "wait":
                await self.settle(timeout=2)
            else:
                raise ValueError("Unsupported owned control operation")
            executed = True
        except ChoiceRejected as exc:
            failure, detail = exc.category, exc.detail[:240]
        except (ValueError, PlaywrightError, TimeoutError) as exc:
            kind = widgets.interaction_failure(exc)
            failure = {
                "covered": "covered",
                "stale": "stale",
                "timeout": "timeout",
                "date_precision": "missing_fact",
                "unsupported_choice": "unsupported",
            }.get(kind, "unavailable")
            detail = kind  # Playwright logs may contain secrets; never return the call log.
            # Our own fixed messages carry no values and tell distinct select failures apart
            # (Corning/Microsoft country: "no matching option" vs "did not commit").
            if type(exc) is ValueError and str(exc) in SAFE_CONTROL_ERRORS:
                detail = f"{kind}: {exc}"
        after = await self.scan()
        self.last_action = action_result(
            action, before, after, executed=executed, failure=failure, detail=detail
        )
        return self.last_action

    async def masked_vision_observation(self):
        """Optional diagnostic seam. No screenshot/model call unless explicitly enabled."""
        from .vision import available

        if not available(self.resolver.config):
            return None
        folder = self.resolver.config.data_dir / "runs" / self.resolver.run_id
        folder.mkdir(exist_ok=True, parents=True, mode=0o700)
        path = folder / "recovery-masked.jpg"
        masks = [
            frame.locator('input,textarea,[contenteditable=true],[data-automation-id="selectedItem"]')
            for frame in await self.application_frames()
        ]
        await self.page.screenshot(path=str(path), type="jpeg", quality=50, mask=masks, timeout=3000)
        path.chmod(0o600)
        return {"path": str(path), "owner_id": self.owner_id, "revision": self.revision}

    async def quality_review(self, *, current_page=False):
        """Independent, bounded answer review; never reserves or submits."""
        from .answer_quality import audit_form

        return await audit_form(self, current_page=current_page)

    def quality_answer(self, field):
        """Revalidate a staged repair against this exact row and current facts."""
        from .answer_context import digest
        from .answer_quality import context, logical_identity, narrative, supported_narrative
        from .schemas import Answer

        repairs = self.failed_strategies.get("__quality_corrections__", {})
        saved = repairs.get(digest(logical_identity(field)))
        if not saved:
            return None
        facts, evidence, reviews = context(self.resolver)
        answer = Answer.model_validate({**saved, "field_id": field["id"]})
        answer = self.resolver._validate_answer(field, answer, facts, evidence, reviews)
        if narrative(field) and not supported_narrative(answer, facts, evidence, reviews):
            return None
        return answer if answer.disposition == "answer" else None

    def acknowledgement_only(self, meta):
        """The id of the one acknowledgement button of an informational dialog that holds no fields, else None."""
        dialogs = [d for d in meta.get("dialogs", []) if d.get("control_ids")]
        if not dialogs:
            return None
        active = dialogs[-1]
        if active.get("field_ids") or INFORMATIONAL_DIALOG_REFUSES.search(active.get("text") or ""):
            return None
        actions = [
            b for b in self.buttons.values()
            if b["id"] in active["control_ids"] and not re.fullmatch(r"close(?: dialog)?|x|×|cancel", b["label"].strip(), re.I)
        ]
        if len(actions) == 1 and ACKNOWLEDGE.fullmatch(actions[0]["label"].strip()):
            return actions[0]["id"]
        # A promotional prompt over the page offers its own way out. TikTok's careers site (2026-10-26, REA) opens an
        # "AI assistant" tour (Try Now / Maybe Later) whose mask swallowed every click: sign-in and "Forgot password?"
        # never reached the page, and the account flow stopped.
        declines = [b for b in actions if DECLINE.fullmatch(b["label"].strip())]
        if len(declines) == 1:
            return declines[0]["id"]
        return None

    def locator(self, field_id):
        item = self.fields.get(field_id) or self.buttons.get(field_id)
        if not item:
            raise ValueError("Unknown or stale field ID; inspect again")
        return self.frames[field_id].locator(f'[data-jp-id="{item["dom_id"]}"]')

    async def retag(self, field):
        """Tag a rebuilt control with the field's data-jp-id when one visible, untagged control carries the field's
        name and no other scanned field shares that name (repeated history rows go to the scoped rescan)."""
        item = self.fields.get(field.get("id")) or {}
        name = field.get("name") or item.get("name") or ""
        if not item.get("dom_id") or not name:
            return False
        if sum(1 for f in self.fields.values() if f.get("name") == name and self.frame_key(f) == self.frame_key(item)) != 1:
            return False
        try:
            return bool(await self.frames[field["id"]].evaluate(RETAG_JS, {"jp": item["dom_id"], "name": name}))
        except PlaywrightError:
            return False

    async def live_locator(self, field):
        """The field's current node. React forms rebuild inputs when a value commits or a section re-renders
        (Workday keys its TextField by value), dropping the tag the scan wrote; a write to the old tag waited out
        the field deadline (REA analysis 2026-10-17: HPE lost ~31 s on each of six fields, 95 such deadlines in
        15 Workday runs). Re-tag the rebuilt node, else rescan once and adopt the matched field."""
        el = self.locator(field["id"])
        try:
            if await el.count() == 1:
                return el
        except PlaywrightError:
            pass
        if await self.retag(field):
            return el
        current = await self.rematch(field)
        field.update(current)
        return self.locator(current["id"])

    async def settle(self, timeout=6.0, quiet=0.3):
        """Bound the entire readiness check, including stalled renderer calls."""
        started = time.monotonic()
        settled = False
        try:
            async with asyncio.timeout(timeout):
                settled = await self._settle(timeout, quiet)
                return settled
        except TimeoutError:
            return False
        finally:
            elapsed = time.monotonic() - started
            if elapsed >= 1 and hasattr(self, "emit"):
                busy = bool(self.network and not self.network.quiet(quiet))
                details = {"seconds": round(elapsed, 2), "settled": settled, "network_busy": busy}
                if not settled:
                    # What the wait ended on, so a deadline that was hit says whether the page or the site held it.
                    details["held_by"] = self.__dict__.get("settle_held_by", "")
                    if busy:
                        details["requests"] = self.network.pending()
                self.emit("settle_timing", "Application readiness wait", details)

    async def _settle(self, timeout, quiet):
        """Wait for rendering and transport to settle before trusting a read-back.

        Transport is watched in Python (no protocol calls while the site's own requests are in flight); rendering is
        watched in the page, one call per frame. Both must be quiet at the same moment: a request that starts while
        the page waited is caught by the check that follows it."""
        deadline = time.monotonic() + timeout
        frames = await self.application_frames()
        self.settle_held_by = ""
        while time.monotonic() < deadline:
            if self.network is not None and not self.network.quiet(quiet):
                self.settle_held_by = "network"
                await asyncio.sleep(0.05)
                continue
            try:
                budget = max(0.05, deadline - time.monotonic())
                self.settle_held_by = "page"  # Provisional: the deadline can arrive while the page still waits.
                states = await asyncio.gather(
                    *(frame.evaluate(SETTLE_JS, {"quiet": quiet, "budget": budget}) for frame in frames)
                )
                busy = any(states)
            except Exception:
                busy = True  # A navigation destroyed the context; the next call observes the new document.
                self.settle_held_by = "navigation"
                await asyncio.sleep(0.1)
            if not busy and (self.network is None or self.network.quiet(quiet)):
                return True
        return False

    def resume_fields(self):
        """The application's resume input. An input labelled by its own id/name/label wins over
        one labelled from surrounding text; resume-parser "autofill" panes come last (Ashby puts
        one before the real Resume field)."""
        files = [f for f in self.fields.values() if f["type"] == "file"]
        named = [f for f in files if RESUME.search(f["label"]) and not NOT_RESUME.search(f["label"])]
        if named:

            def rank(f):
                own = RESUME.search(f.get("name", "")) or RESUME.search(f["label"].split(" (")[0])
                return (bool(AUTOFILL.search(f["label"] + " " + f.get("name", ""))), not own)

            return sorted(named, key=rank)[:1]
        unnamed = [f for f in files if not NOT_RESUME.search(f["label"])]
        if len(files) == 1:
            return unnamed[:1]
        # Generic "Upload a file" inputs named by their section (Jabil: Resume/CV/... beside Additional Attachments).
        grouped = [f for f in unnamed if RESUME.search(f.get("group") or "")]
        if len(grouped) == 1:
            return grouped
        # Drop zones that name no document, beside a profile photo: the first is the resume's. SmartRecruiters' German form
        # labels each "Eine Datei auswählen oder hier ablegen." inside its shadow DOM; nothing was attached and "Weiter"
        # never advanced (Sopra Steria, four jobs, 2026-10-25/26).
        generic = [f for f in unnamed if GENERIC_UPLOAD.search(f["label"]) and not PHOTO_UPLOAD.search(f["label"])]
        photo = any(PHOTO_UPLOAD.search(f["label"]) for f in files)
        if not grouped and generic and len({normalize(f["label"]) for f in generic}) == 1 and (len(generic) > 1 or photo):
            return generic[:1]
        return []

    async def control_structure(self, control_id):
        """Redacted DOM shape of a control that failed to commit: tags, roles, ids/classes and
        option texts only — every input value, attribute value and free text is dropped.
        Lets each new widget kind (SuccessFactors custom dropdowns…) be diagnosed from events.
        """
        try:
            return await asyncio.wait_for(self.locator(control_id).evaluate(
                r"""el => {
                  let box = el; for (let i = 0; i < 3 && box.parentElement; i++) box = box.parentElement;
                  const pick = n => ({tag: n.tagName.toLowerCase(), role: n.getAttribute('role') || '',
                    id: (n.id || '').slice(0, 40), cls: String(n.className || '').slice(0, 80),
                    popup: n.getAttribute('aria-haspopup') || '', expanded: n.getAttribute('aria-expanded') || '',
                    controls: (n.getAttribute('aria-controls') || '').slice(0, 40), type: n.getAttribute('type') || ''});
                  const nodes = [box, ...box.querySelectorAll('*')].slice(0, 40).map(pick);
                  const options = [...box.querySelectorAll('option,[role=option],li')].slice(0, 25)
                    .map(o => (o.textContent || '').trim().slice(0, 40));
                  return {self: pick(el), nodes, options, option_count: box.querySelectorAll('option,[role=option],li').length};
                }""", timeout=500
            ), timeout=0.75)
        except Exception:
            return {}

    async def fill_target(self, control_id):
        """Fill the one field the navigator chose, even when it is optional.

        Sarnova (Dayforce): "I agree to Dayforce's Global Privacy Statement…" is an optional
        checkbox, so fill_current skipped it; Next stayed inert and was refused as a repeat.
        Only a supported answer is written; passwords and auth fields are never touched.
        """
        field = self.fields.get(control_id)
        if not field or field["type"] in {"password", "file"} or AUTH.search(field["label"]):
            return False
        try:
            answer = (await self.resolver.resolve([field]))[0]
        except Exception:
            return False
        AnswerModelUnavailable.raise_for(answer)
        if answer.disposition != "answer" or answer.value in (None, ""):
            return False
        if (
            has_value(field)
            and field.get("valid", True)
            and widgets.same_value(field, field["value"], answer.value)
        ):
            # An equal value the website marked invalid is rewritten (Greenhouse kept a stale
            # "(240) ..." phone it rejected as "too short" while every repair skipped it).
            return False
        try:
            await self.fill(field, answer.value)
        except AnswerModelUnavailable:
            raise
        except Exception:
            return False
        self.record_answer(field, answer.value, answer.evidence_ids)
        return True

    async def signup_form(self, control_id):
        """A talent-community / job-alert signup is not this job's application.

        Roku (run d201ebad): the job page's "Join our Talent Community" sidebar (5 contact
        fields, no resume) was filled and submitted instead of opening Apply. A real
        application form carries a file upload, so a community opt-in checkbox inside an
        application does not trigger this.
        """
        try:
            return bool(
                await self.locator(control_id).evaluate(
                    r"""b => {
                      const f = b.form || b.closest('form');
                      if (!f || f.querySelector('input[type=file]')) return false;
                      // Greenhouse drops the file input once the resume is attached, and Braze's application asks
                      // "join Braze's Talent Community?" (2026-10-23): a resume or a long questionnaire is the job's.
                      if (/\b(?:resume|résumé|cv)\b/i.test(f.innerText || '')
                        || f.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]),select,textarea')
                          .length > 12) return false;
                      if (!/talent (?:community|network|pool)|join our (?:talent|community)|job alerts?|stay (?:connected|in touch)|career (?:updates|newsletter)|newsletter/i
                        .test((f.innerText || '').slice(0, 4000))) return false;
                      // Roku's sidebar sits beside the job's own Apply; a final application page
                      // (Dayforce acknowledgement) has no separate Apply control.
                      return [...document.querySelectorAll('a,button,[role=button]')].some(a => !f.contains(a)
                        && a.getClientRects().length && /^apply(?: now| for this (?:job|position))?$/i.test((a.innerText || '').trim()));
                    }"""
                )
            )
        except Exception:
            return False

    def first_of_month(self, field, value):
        """The owner's rule (answer_rules.history_day "first"): a full-date field gets the 1st for a month/year answer
        from any source. Quora's Ashby box refused the owner's own "05/2026" reply as too imprecise (2026-10-24)."""
        try:
            month, day, year = widgets.parse_date(value)
        except ValueError:
            # Not a date: the field's own writer reports the precise refusal. Raised here, it surfaced as an
            # unclassified failure of this rule (OmegaHires' "Current contract end date", 2026-10-27).
            return value
        kinds = [p["kind"] for p in field.get("parts", [])] or ["month", "day", "year"]
        rules = getattr(self.resolver, "answer_rules", None)
        if month and year and not day and "day" in kinds and getattr(rules, "history_day", "") == "first":
            return f"{year}-{month}-01"
        return value

    def referral_route(self):
        """A referral form sends another person's information, not an application."""
        urls = {self.page.url, *(frame.url for frame in self.frames.values())}
        return any(re.search(r"/referral/?$", urlsplit(url).path, re.I) for url in urls)

    async def open_agreement_links(self, field):
        """A clickwrap agreement counts only after its linked documents were opened.

        Dayforce ID: "I agree to Dayforce's Global Privacy Statement and to the Terms of Use, I clicked
        the links above and reviewed these documents." Its Next did nothing until both links had been
        clicked (2026-10-16). Only links named in the agreement that open a new tab are clicked; each
        tab is closed again. A link that would navigate this page away is never clicked."""
        label = field.get("label", "")
        if not re.search(r"\b(?:clicked|opened|reviewed|read)\b.{0,40}\blinks?\b", label, re.I):
            return
        rules = getattr(self.resolver, "answer_rules", None)
        if rules is not None and not rules.open_agreement_links:
            return
        opened = getattr(self, "agreement_links", set())
        self.agreement_links = opened
        for b in list(self.buttons.values()):
            name = b.get("label", "").strip()
            if len(name) < 5 or name.casefold() not in label.casefold() or (b["id"], self.page.url) in opened:
                continue
            link = self.locator(b["id"])
            try:
                if await link.get_attribute("target", timeout=1500) != "_blank":
                    continue
                async with self.page.context.expect_page(timeout=8000) as popup:
                    await link.click(timeout=4000)
                tab = await popup.value
                await tab.close()
            except (PlaywrightError, PlaywrightTimeoutError):
                continue
            opened.add((b["id"], self.page.url))
            self.emit("agreement_link", "Opened a document the agreement asks to review", {"link": name})

    async def set_checked(self, el, checked):
        # Dayforce ID's <evr-checkbox> owns its state: a click on its shadow input changes nothing,
        # a click on the component itself toggles its checkedstate (2026-10-16).
        host = await el.evaluate_handle("e => { const h = e.getRootNode()?.host; return h?.tagName === 'EVR-CHECKBOX' ? h : null; }")
        host = host.as_element()
        if host is not None:
            state = lambda: host.evaluate("h => h.getAttribute('checkedstate') === 'checked'")  # noqa: E731
            if await state() == checked:
                return
            await host.click(timeout=4000)
            for _ in range(20):
                if await state() == checked:
                    return
                await asyncio.sleep(0.1)
            raise ValueError("The agreement checkbox did not change state")
        if await el.is_checked() == checked:
            return
        direct = False
        if await el.is_visible():
            try:
                await el.set_checked(checked, timeout=1500)
                direct = True
            except Exception:
                # Background tabs can stall pointer stability checks. Use the
                # native keyboard interaction, then verify the actual state.
                try:
                    if await el.is_checked() != checked and await el.is_enabled():
                        await el.press("Space", timeout=1500)
                    direct = await el.is_checked() == checked
                except Exception:
                    pass  # A styled input may still need its associated label.
        if not direct:
            label = await el.evaluate_handle(
                "e=>e.labels?.[0] || document.getElementById((e.getAttribute('aria-labelledby')||'').split(' ')[0])"
            )
            target = label.as_element()
            if not target or not await target.is_visible():
                raise ValueError("Checkbox or radio label is not visible")
            await widgets.field_click(target, timeout=4000)
            if (
                await el.get_attribute("type") == "checkbox"
                and not await el.is_checked()
                and checked
                and self.resolver.profile.accept_all_application_terms
                and re.search(r"agree|terms|privacy|consent", await target.inner_text(), re.I)
            ):
                handle = await el.element_handle()
                frame = await handle.owner_frame()
                # Honeywell's Oracle privacy dialog says "ACCEPT" rather than "Agree" (2026-10-14).
                agree = frame.get_by_role("button", name=re.compile(r"^(?:I )?(?:Agree|Accept)$", re.I))
                # Oracle loads the legal text asynchronously after clicking its hidden checkbox label.
                for _ in range(30):
                    if await el.is_checked() or (await agree.count() == 1 and await agree.is_visible()):
                        break
                    await asyncio.sleep(0.1)
                if await agree.count() == 1 and await agree.is_visible():
                    agreed = await agree.element_handle()
                    await agreed.evaluate("e=>e.dataset.jpAuthControl='true'")
                    try:
                        await agreed.click(timeout=4000)
                    finally:
                        await agreed.evaluate("e=>delete e.dataset.jpAuthControl")
                    await self.page.wait_for_timeout(200)
        if await el.is_checked() != checked:
            raise ValueError("Checkbox or radio selection was not accepted")

    async def fill(self, field, value):
        # Measure the driver: Workday history rows took ~95 s per 4-6 fields with almost no
        # model time (2026-10-13). Slow writes name their widget, never the value.
        started = time.monotonic()
        error = ""
        try:
            school = field.get("type") in {"prompt", "combobox"} and SCHOOL_LABEL.search(field.get("label") or "")
            budget = 30 if field.get("type") == "file" else SCHOOL_WRITE_SECONDS if school else FIELD_WRITE_SECONDS
            async with asyncio.timeout(budget):
                return await self._fill(field, value)
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            seconds = round(time.monotonic() - started, 1)
            if seconds >= 3:
                self.emit(
                    "field_timing",
                    "A field write was slow",
                    {
                        "label": str(field.get("label", ""))[:80],
                        "type": field.get("type", ""),
                        "widget": field.get("widget", ""),
                        "seconds": seconds,
                        "error": error,
                    },
                )

    async def _fill(self, field, value):
        from .ats import oracle_job_catalog

        if oracle_job_catalog(self.page.url):
            raise ValueError("Open the owned posting before filling applicant fields")
        from .answers import display_name_case

        from .widgets_ats import combo_family, select_combo

        if phone_fields.is_phone_extension(field):
            # Optional extension: it stays blank and never receives the phone number.
            return await self.blank_phone_extension(field)
        phone_field = field
        if phone_fields.is_phone_number(field) and phone_fields.has_phone_country(self.fields.values()):
            # A separate calling-code selector owns the prefix: type the national digits.
            phone_field = {**field, "phone_format": "national"}
        value = widgets.phone_value(
            phone_field, value, self.resolver.profile.phone_country_code if self.resolver else ""
        )
        # Driver boundary: local fast paths, recovery fills and quality repairs all pass here.
        value = display_name_case(field.get("label", ""), value)
        if field.get("letter_case") == "upper" and isinstance(value, str):
            value = value.upper()  # The form asks for this field in capital letters (mark_uppercase_fields).
        if field.get("type") in {"text", "textarea", "contenteditable"}:
            value = str(value).replace("\u2014", ", ")
            # Preserve prose/facts while honoring an explicit field validation
            # constraint. Never apply this transform to URLs or identity fields.
            value = prose_for_constraint(field, value)
        el = await self.live_locator(field)
        frame = self.frames[field["id"]]
        kind = field["type"]
        if (
            re.search(r"\bgpa\b|grade point average", field["label"], re.I)
            and kind in {"text", "number"}
            and (scale := re.fullmatch(r"\s*(\d(?:\.\d+)?)\s*/\s*\d+(?:\.\d+)?\s*", str(value)))
            and (
                re.search(r"invalid", str(field.get("error") or ""), re.I)
                # A GPA box takes the number unless it shows a "3.5/4.0" format itself: Taleo (Bealls 2026-10-17,
                # 'contains an invalid value'), Oracle (INTEGRIS 2026-10-23) and iCIMS (Medpace 2026-10-27, "Invalid
                # Data Error" with GPA flagged) all refused "3.9/4.0" given whole from a remembered answer.
                or not re.search(
                    r"\d\s*/\s*\d",
                    " ".join(str(field.get(k) or "") for k in ("label", "hint", "placeholder")),
                )
            )
        ):
            value = scale[1]
        if kind in {"text", "url"} and "linkedin" in field["label"].lower():
            parsed_url = urlsplit(str(value))
            if parsed_url.hostname == "linkedin.com" and parsed_url.path.startswith("/in/"):
                value = urlunsplit(parsed_url._replace(netloc="www.linkedin.com"))
        committed = value

        async def choose(options, *, partial=False):
            from .answers import DEMOGRAPHIC

            if self.resolver and DEMOGRAPHIC.search(str(field.get("label", ""))):
                # Self-identification pickers use the local policy (decline when offered, else the
                # applicant's confirmed preference) before any model; Roblox's race multi-select
                # otherwise reached evidence interpretation and stopped as "facts disagree".
                local = self.resolver.local({**field, "options": options[:250]})
                if local and local.disposition == "answer" and local.value in options:
                    self.emit(
                        "option_selected", f"Selected the applicant's confirmed option for {field['label']}"
                    )
                    return local.value
            owner = self.resolver and self.resolver.owner_policy_answer({**field, "options": options})
            if owner and owner.value in options:
                self.emit("option_selected", f"Selected the owner-policy option for {field['label']}")
                return owner.value
            if self.resolver and (picked := self.resolver.policy_choice(field, options)):
                # Owner policy: degree/major/source equivalents are chosen without a model call.
                self.emit("option_selected", f"Selected the owner-approved option for {field['label']}")
                return picked
            decision = (
                await self.resolver.resolve(
                    [{**field, "options": options[:250], "options_partial": partial}], use_local=False
                )
            )[0]
            AnswerModelUnavailable.raise_for(decision)
            if decision.disposition == "answer" and decision.value in options:
                self.emit(
                    "option_selected",
                    f"Selected an offered option for {field['label']} using applicant context",
                )
                return decision.value
            return None

        if kind == "checkbox":
            want = widgets.checkbox_value(value) == "true"
            if want:
                await self.open_agreement_links(field)
            if await el.evaluate("e => e.tagName === 'INPUT'"):
                # Label clicks, plus an "I Agree" dialog some portals open from the checkbox (Oracle).
                await self.set_checked(el, want)
                committed = "true" if want else "false"
            else:
                committed = await widgets.checkbox(el, value, field.get("widget", ""))
        elif kind == "checkboxgroup" and field.get("widget") == "oracle-multi-pills":
            committed = await widgets.multi_pills(frame, field, value)
        elif kind == "checkboxgroup":
            wanted = {normalize(v) for v in str(value).splitlines() if v.strip()}
            matched = set()
            members = field.get("member_ids", [])
            if not members:
                raise ValueError("Checkbox group ownership is unavailable; observe again")
            for member in members:
                checkbox = frame.locator(f'[data-jp-id="{member}"]')
                if await checkbox.count() != 1:
                    raise ValueError("Checkbox group changed; observe again")
                label = await checkbox.evaluate("""e=>{
                    const labelled=[...(e.labels||[])].map(x=>x.innerText.trim()).join(' ');
                    if(labelled)return labelled;
                    let n=e.parentElement;
                    for(let i=0;i<3&&n;i++,n=n.parentElement){
                      if(n.querySelectorAll('input,select,textarea').length>1)break;
                      const t=n.innerText.replace(/\\s+/g,' ').trim();if(t)return t;
                    }
                    return e.value;
                }""")
                selected = normalize(label) in wanted
                await self.set_checked(checkbox, selected)
                if selected:
                    matched.add(normalize(label))
            if matched != wanted or not wanted:
                raise ValueError("Checkbox answer is no longer present")
        elif kind == "select-multiple":
            choices = [v.strip() for v in str(value).splitlines() if v.strip()]
            try:
                await el.select_option([{"label": v} for v in choices], timeout=4000)
            except Exception:
                # iCIMS's EEO "Race (EEO)" (iCIMS_Forms_MultiSelect) refused select_option on every attempt and the
                # page answered "Race (EEO): required" (Liberty Mutual 2026-10-21). Select the same labelled options in
                # the page and announce the change; the readback below still decides.
                await el.evaluate(SELECT_LABELS_JS, choices)
            committed = "\n".join(await el.evaluate("e=>[...e.selectedOptions].map(o=>o.text.trim())"))
        elif kind == "select":
            committed = await widgets.select(el, value, field.get("widget", ""))
        elif kind == "radio":
            found = False
            candidates = []
            members = field.get("member_ids", [])
            if not members:
                raise ValueError("Radio group ownership is unavailable; observe again")
            for member in members:
                radio = frame.locator(f'[data-jp-id="{member}"]')
                # iCIMS's self-identification radios have no label; the scan offers their value (ICE 2026-10-19).
                labels, own = await radio.evaluate(
                    "e => [[...e.labels].map(x=>x.innerText).join(' ').trim() || (e.value === 'on' ? '' : e.value || ''),"
                    " e.value === 'on' ? '' : e.value || '']"
                )
                candidates.append((radio, SELF_ID_CODES.get(labels, labels), SELF_ID_CODES.get(own, own)))
            texts = [text for _, text, _ in candidates]
            values = [own for _, _, own in candidates]
            if len(set(texts)) < len(texts) and all(values) and len(set(values)) == len(values):
                # Captions read from a neighbour repeat ("Yes", "Yes"); the scan offered the values (js/scan.js).
                candidates = [(radio, own, own) for radio, _, own in candidates]
            candidates = [(radio, text) for radio, text, _ in candidates]
            exact = [r for r, text in candidates if normalize(text) == normalize(value)]
            chosen = exact[0] if exact else None
            if chosen is None:
                match = widgets.best_option([t for _, t in candidates], value)
                chosen = next((r for r, t in candidates if t == match), None) if match else None
            if chosen is not None:
                try:
                    await self.set_checked(chosen, True)
                except Exception:
                    # Never select a different hidden/covered group's label via an unchecked click.
                    raise ValueError("Radio selection was not accepted") from None
                found = True
            if not found:
                raise ValueError("Radio option is no longer present")
        elif kind == "radiogroup":
            options = await self.radio_options(field)
            match = widgets.best_option([o for o in options if not o["disabled"]], value)
            if match is not None:
                chosen = frame.locator(f'[data-jp-id="{match["id"]}"]')
                if await chosen.evaluate("e=>e.tagName==='INPUT'"):
                    await self.set_checked(chosen, True)
                elif await chosen.evaluate("e=>e.hasAttribute('ui5-radio-button')"):
                    if not await widgets.ui5wc_radio(chosen):
                        raise ValueError("Radio selection was not accepted")
                else:
                    native = chosen.locator('input[type="radio"]')
                    if await native.count() == 1:
                        await self.set_checked(native, True)
                    else:
                        await widgets.field_click(chosen, timeout=4000)
            else:
                raise ValueError("Radio option is no longer offered by this question")
        elif kind == "buttonchoice":
            buttons = el.get_by_role("button")
            labels = [text.strip() for text in await buttons.all_text_contents()]
            match = widgets.best_option(labels, value)
            if match is None:
                raise ValueError("The button answer is no longer offered")
            await widgets.field_click(buttons.nth(labels.index(match)))
            committed = match
        elif field.get("widget") == "sdf-select":
            committed = await widgets.sdf_select(frame, el, value, choose=choose)
        elif field.get("widget") == "ui5wc-select":
            committed = await widgets.ui5wc_select(frame, el, value)
        elif field.get("widget") == "ui5wc-valuehelp":
            # SuccessFactors pick lists keep only a picked entry; pick the offered spelling.
            committed = await widgets.ui5wc_pick(
                frame, el, widgets.best_option(field.get("options") or [], value) or value
            )
        elif kind == "combobox" and (family := await combo_family(frame, el)):

            async def rematch_combo():
                current = await self.rematch(field)
                if self.frames[current["id"]] != frame:
                    raise ValueError("ATS selection owner changed frames; observe again")
                return self.locator(current["id"])

            async def choose_search_results(options):
                # Oracle's editable remote result window is not the complete
                # catalog. Keep confirmed facts valid even when this response
                # omits them; only an exact uniquely offered choice may commit.
                partial = bool(field.get("options_partial")) or await el.evaluate(
                    "e=>!e.readOnly && !e.disabled", timeout=500
                )
                return await choose(options, partial=partial)

            committed = await select_combo(
                frame,
                el,
                field,
                value,
                choose_search_results if family == "oracle" else choose,
                rematch=rematch_combo,
                applicant_state=self.resolver.profile.address.state if self.resolver else "",
            )
        elif kind == "combobox" and field.get("widget") == "lever-location":
            committed = await widgets.lever_location(frame, el, value, self.resolver.profile.address, choose)
        elif (
            kind == "combobox"
            and await el.get_attribute("aria-haspopup") == "dialog"
            and re.search(r"\bdate\b", f"{field['label']} {await el.get_attribute('id') or ''}", re.I)
        ):
            # A date picker exposed as role=combobox (PayPal Eightfold "Date", 2026-10-14): there are no
            # list options to choose. A valid shown date that matches (or any date for "today") is kept.
            # The expectation is the date as shown: a boolean here failed every readback (Micron
            # Self-ID "Date" 2026-10-16 kept "16 Oct 2026" and was verified against True).
            shown = await el.input_value()
            want, have = display_date(str(value)), display_date(shown)
            if have and (want is None or want == have):
                committed = shown
            else:
                target = want or display_date("today")
                await el.fill(target.strftime("%m/%d/%Y"))
                await el.press("Escape")
                shown = await el.input_value()
                committed = shown if display_date(shown) == target else target.strftime("%m/%d/%Y")
        elif (
            kind == "combobox"
            and re.search(r"\b(?:phone|mobile|telephone)\b", field["label"], re.I)
            and re.fullmatch(r"[\d\s()+.\-]{7,}", str(value).strip())
        ):
            # Eightfold (Lumen 2026-10-14) labels its dial-code picker "Phone": a phone number never
            # matches its options. Choose the applicant's country there; the number has its own input.
            country = (
                (getattr(self.resolver.profile.address, "country", "") or "United States")
                if self.resolver
                else "United States"
            )
            committed = await widgets.combobox(frame, el, country, choose=choose)
        elif kind == "combobox":
            from .answers import EDUCATION

            school_picker = bool(SCHOOL_LABEL.search(field["label"]))
            search_queries = []
            if school_picker and "," in str(value):
                search_queries = [
                    re.sub(r"[,\s]+", " ", str(value)).strip(),
                    str(value).split(",", 1)[0].strip(),
                ]
            city = getattr(getattr(getattr(self.resolver, "profile", None), "address", None), "city", "") or ""
            if city and self.location_resolver(field) and normalize(city) != normalize(value):
                # A location typeahead finds places by name: "Springfield, Illinois, United States" typed whole found
                # nothing on Lyft's Greenhouse form, and the refill cleared a choice already made (2026-10-25). The city
                # is searched too, and the address picks its own state's result.
                search_queries = [*search_queries, city]
            rules = getattr(self.resolver, "answer_rules", None)
            fallback = (getattr(rules, "major_fallback", "") or "").strip()
            major_pick = None
            if fallback and EDUCATION["field_of_study"].search(field["label"]) and normalize(fallback) != normalize(value):
                # A searched catalog without the recorded major is searched for the profile's fallback major and
                # picked by name, never by a model guess: SAS's iCIMS "Field of Study" lacked Data Science, the guess
                # outran the 12 s write budget on every attempt, and the run stalled (2026-10-21).
                search_queries = [*search_queries, fallback]

                def major_pick(texts):
                    for wanted in (value, fallback):
                        named = [t for t in texts if normalize(t) == normalize(wanted)]
                        if len(named) == 1:
                            return named[0]
                    return None

                async def no_guess(texts):
                    return None

                choose = no_guess
            broad_queries, degree_pick = [], None
            if EDUCATION["degree"].search(field["label"]):
                from .answers.history import DEGREE_FALLBACK, degree_level
                from .answers.text import first_matching

                if level := degree_level(str(value)):
                    # A server-searched degree list is searched by the degree's level, whose results hold the catalog's
                    # own spelling ("Master's Level Degree" for "Master of Science", Emory iCIMS 2026-10-25), and the
                    # row is the level's offered equivalent by the same rules as a known catalog.
                    broad_queries = [level.capitalize()]

                    def degree_pick(texts):
                        return first_matching(texts, DEGREE_FALLBACK[level])
            multiselect = field.get("widget") in {"spl-multiselect", "oracle-multiselect"}
            try:
                committed = await (widgets.multi_combobox if multiselect else widgets.combobox)(
                    frame,
                    el,
                    value,
                    choose=choose,
                    **({} if multiselect else {"multi": False}),
                    prefer_catalog=topic(field["label"]) == "source"
                    or field.get("widget") == "oracle-multiselect",
                    search_queries=search_queries,
                    school_scope=school_picker,
                    resolve=self.location_resolver(field) or major_pick or degree_pick,
                    employer_scope=bool(EMPLOYER_PICKER.search(field["label"])) and not school_picker,
                    # Optional pickers get one search, no paging: each is a request to the employer's servers.
                    thorough=bool(field.get("required")),
                    broad_queries=broad_queries,
                )
            except ValueError as exc:
                if str(exc).startswith(widgets.NO_OPTIONS_READABLE):
                    self.emit(
                        "option_observation", "No offered choices were readable", {"field": field["label"]}
                    )
                raise
        elif kind == "dropdown":
            committed = await widgets.dropdown(
                frame,
                el,
                value,
                choose=choose,
                prefer=(lambda offered: self.resolver.policy_choice(field, offered)) if self.resolver else None,
            )
        elif kind == "prompt":
            if re.search(r"\bskills?\b", field["label"], re.I):
                choices = [v.strip() for v in re.split(r"[,;\n]", value) if v.strip()][:8]
                accepted = []
                for skill in choices:
                    try:
                        accepted.append(await widgets.prompt(frame, el, skill, choose=choose))
                    except ValueError:
                        # Employer taxonomies need not offer every resume skill.
                        # Retain the supported choices that actually committed.
                        continue
                    if len(accepted) >= 3:
                        break
                if not accepted:
                    raise ValueError("No supported skill was offered by this employer")
                committed = ", ".join(accepted)
            else:
                from .answers import EDUCATION

                async def major_policy(options, *, partial=False):
                    # A major is the recorded one, a name made of its words, or (below) the profile's fallback major;
                    # a model guess among unrelated results committed "Data Processing" for Data Science.
                    return self.resolver.policy_choice(field, options) if self.resolver else None

                is_major = bool(EDUCATION["field_of_study"].search(field["label"]))
                network = getattr(self, "network", None)
                catalog = network.value_list("fieldsOfStudy") if is_major and network is not None else None
                if catalog:
                    # The page's own Field of Study list decides first; only the chosen exact name is searched, so no
                    # probe search can commit a sole result the policy rejects (REA, 2026-10-25).
                    rules = getattr(self.resolver, "answer_rules", None)
                    fallback = (getattr(rules, "major_fallback", "") or "").strip()
                    wanted = [value] + ([fallback] if fallback and normalize(fallback) != normalize(value) else [])
                    committed = await widgets.prompt(frame, el, wanted, choose=major_policy, catalog=catalog)
                    if normalize(committed) != normalize(value):
                        self.emit(
                            "option_selected",
                            "Used the profile's fallback major for a catalog without the recorded one",
                            {"field": field["label"]},
                        )
                else:
                    try:
                        committed = await widgets.prompt(
                            frame,
                            el,
                            value,
                            choose=major_policy if is_major else choose,
                            school_scope=bool(SCHOOL_LABEL.search(field["label"])),
                        )
                    except ValueError:
                        rules = getattr(self.resolver, "answer_rules", None)
                        fallback = (getattr(rules, "major_fallback", "") or "").strip()
                        if not is_major or not fallback or normalize(value) == normalize(fallback):
                            raise
                        # A major the employer's search catalog lacks falls back to the profile's fallback major
                        # (answer_rules.major_fallback; R1 RCM: "Data Science" was not offered).
                        # The fallback is policy-matched too: a model choosing among Jabil's alphabetical first page
                        # committed "Actuarial Science" for Data Science (2026-10-23).
                        committed = await widgets.prompt(frame, el, fallback, choose=major_policy)
                        self.emit(
                            "option_selected",
                            "Used the profile's fallback major for a catalog without the recorded one",
                            {"field": field["label"]},
                        )
        elif kind == "date_parts":
            committed = await widgets.date_parts(frame, field, value)
        elif kind == "pills":
            committed = await widgets.pills(el, value)
        elif field.get("widget") == "date-text":
            if re.fullmatch(r"\s*(?:immediately|immediate|asap|as soon as possible|right away)\s*", str(value), re.I):
                # Availability "Immediately" in a date box is today's date: Paylocity's "Available to Start" raised
                # "Unsupported date format" on every write (Lightology, Space Tango 2026-10-26).
                value = "today"
            value = self.first_of_month(field, value)
            widgets.date_text(field, value)
            month, day, year = widgets.parse_date(value)
            order = field.get("date_format", "mdy") or "mdy"
            displayed = (
                f"{month}/{year}"
                if order == "month-year"
                else f"{year}-{month}-{day}"
                if order == "ymd"
                else f"{day}/{month}/{year}"
                if order == "dmy"
                else f"{month}/{day}/{year}"
            )
            await el.fill(displayed, timeout=4000)
            if "workable.com" in (urlsplit(self.page.url).hostname or ""):
                # Workable's calendar froze its renderer when blurred with the popup open
                # (Saalex: 600 s hangs). Close the popup first, within a bound.
                try:
                    await asyncio.wait_for(el.press("Escape"), 3)
                except Exception:
                    pass
            await asyncio.wait_for(el.blur(), 8)
            if order == "month-year":
                await asyncio.sleep(0.15)
                provider_date = await el.evaluate("""e => {
                  const host=e.getRootNode()?.host;
                  if(host?.tagName!=='SPL-DATE-PICKER')return {provider:false};
                  return {provider:true,value:typeof host.value==='string'?host.value:''};
                }""")
                if provider_date["provider"] and not widgets.same_value(
                    field, provider_date["value"].split("T")[0], value
                ):
                    raise ValueError("Date picker did not commit the selected month and year")
            committed = f"{year}-{month}" if order == "month-year" else f"{year}-{month}-{day}"
        elif kind == "date":
            value = self.first_of_month(field, value)
            widgets.date_text(field, value)  # Require the precision a native date input needs.
            month, day, year = widgets.parse_date(value)
            committed = f"{year}-{month}-{day}"
            await el.fill(committed, timeout=4000)
        elif kind == "month":
            widgets.date_text({**field, "parts": [{"kind": "month"}, {"kind": "year"}]}, value)
            month, _, year = widgets.parse_date(value)
            committed = f"{year}-{month}"
            await el.fill(committed, timeout=4000)
        elif kind == "url":
            committed = str(value).strip()
            if committed and not re.match(r"^[a-z][a-z0-9+.-]*://", committed, re.I):
                committed = "https://" + committed
            await el.fill(committed, timeout=4000)
            await el.blur()
        else:
            if kind == "text" and await widgets.is_ui5wc_date(el):
                if re.fullmatch(r"\s*(?:immediately|asap|as soon as possible|right away|now)\s*", str(value), re.I):
                    # SuccessFactors' "When are you available to start?" is a UI5 date picker the scan reads as text:
                    # "Immediately" could not be parsed and Optimum stalled three times (2026-10-23). It means today.
                    zone = getattr(getattr(getattr(self, "resolver", None), "config", None), "timezone", "") or "UTC"
                    value = datetime.now(ZoneInfo(zone)).strftime("%m/%d/%Y")
                committed = await widgets.ui5wc_date(el, value)
            elif (
                kind == "text"
                and re.search(r"\b(?:state|province|region)\b", field["label"], re.I)
                and await widgets.is_ui5wc_input(el)
                and await el.get_attribute("aria-haspopup") in {"dialog", "listbox"}
            ):
                committed = await widgets.ui5wc_pick(frame, el, value)
            elif (
                kind == "text"
                and re.search(r"\b(?:state|province|region)\b", field["label"], re.I)
                and await widgets.is_ui5_input(el)
            ):
                # SAP UI5 region inputs (SuccessFactors) keep only a clicked suggestion; typed text is cleared on blur.
                await widgets.human_type(el, value, timeout=4000)
                try:
                    committed = await widgets._ui5_combobox(frame, el, value)
                except ValueError as exc:
                    if str(exc) != widgets.NO_OPTIONS_READABLE:
                        raise
                    # A plain UI5 Input without suggestions keeps the typed value; the readback below checks it.
            elif kind == "tel" or (
                re.search(r"phone|mobile|telephone|\bcell(?:ular)?\b", field["label"], re.I) and "@" not in str(value)
            ):
                async def rematch_phone():
                    nonlocal el
                    if await self.retag(field):
                        el = self.locator(field["id"])
                        return el
                    current = await self.rematch(field)
                    el = self.locator(current["id"])
                    return el

                # ADP (ZincFive 2026-10-16): "Email or Mobile Number" takes the email; this branch retyped
                # only its digits, clearing the field on every run. An email is typed as text.
                # Taleo (Bealls 2026-10-17): "Format: 555-555-5555" beside the input, and "The value does not
                # match the pattern 999-999-9999" after "(555) 555-0100". Write the number in the shown mask.
                errors = " ".join(map(str, (getattr(self, "last_observation", None) or {}).get("errors", []) or []))
                mask = phone_fields.phone_mask(await widgets.nearby_text(el)) or phone_fields.phone_mask(errors)
                if mask and (masked := phone_fields.apply_mask(value, mask)):
                    value = masked
                # Masks differ: iCIMS rejects per-keystroke input, while Greenhouse's
                # intl-tel-input ignores a value write ("Phone number is too short").
                # Write once; if the widget rejected it, type the digits instead.
                visible_phone = frame.locator("fieldset.phone-input input[type=tel]:visible")
                try:
                    if await visible_phone.count() == 1:
                        # Long-lived (resumed) Greenhouse tabs can hold more than one phone input;
                        # writes to a stale one left the visible "(240) ..." unchanged (Datacor).
                        el = visible_phone
                except PlaywrightError:
                    pass
                if await widgets.keystroke_phone(el):
                    # Greenhouse's phone fieldset keeps React state from key events only: a value
                    # write displays the number, then submit re-renders it empty ("Phone number is
                    # too short"; 7 jobs on 2026-10-13). Type the national digits instead.
                    # Type the self-describing international form (+1...): the widget then sets its
                    # own country, so the number is valid whatever the Country picker's state is.
                    code = re.sub(r"\D", "", self.resolver.profile.phone_country_code or "")[:3] or "1"
                    typed = str(value).strip()
                    typed = (
                        "+" + re.sub(r"\D", "", typed)
                        if typed.startswith("+")
                        else "+" + code + widgets.national_digits(value)
                    )
                    await el.fill("", timeout=4000)
                    await el.press_sequentially(typed, delay=45, timeout=8000)
                    shown = await el.input_value(timeout=1500)
                    if re.sub(r"\D", "", shown)[-10:] == re.sub(r"\D", "", typed)[-10:]:
                        self.typed_phones.add(field["id"])
                    self.emit(
                        "phone_typed",
                        "Typed the phone into the visible phone input",
                        {
                            "matches": re.sub(r"\D", "", shown)[-10:] == re.sub(r"\D", "", typed)[-10:],
                            "visible_inputs": await visible_phone.count(),
                        },
                    )
                elif await widgets.web_component_phone(el):
                    # Its own country picker holds the code; the component takes the national digits by keystrokes.
                    await el.fill("", timeout=4000)
                    await el.press_sequentially(widgets.national_digits(value), delay=40, timeout=8000)
                    shown = await el.input_value(timeout=1500)
                    if re.sub(r"\D", "", shown)[-10:] == widgets.national_digits(value)[-10:]:
                        self.typed_phones.add(field["id"])
                else:
                    await el.fill(value, timeout=4000)
                if await el.count() != 1:
                    await rematch_phone()
                if await widgets.phone_rejected(el, value, rematch=rematch_phone):
                    await el.fill("", timeout=4000)
                    await el.press_sequentially(re.sub(r"\D", "", str(value)), delay=40, timeout=8000)
                    if await widgets.phone_rejected(el, value, rematch=rematch_phone):
                        # The widget cleared the typed digits too: one more keystroke pass.
                        await el.fill("", timeout=4000)
                        await el.press_sequentially(re.sub(r"\D", "", str(value)), delay=60, timeout=8000)
            else:
                # John Deere (Eightfold 2026-10-16): the correct email kept "Email is invalid" from an
                # earlier value, and refills skipped it as already shown. Retyping re-runs validation.
                async def rematch_text():
                    if await self.retag(field):
                        return self.locator(field["id"])
                    current = await self.rematch(field)
                    return self.locator(current["id"])

                el = await widgets.human_type(
                    el, value, timeout=4000, retype=bool(field.get("error")), rematch=rematch_text
                )
            try:
                await el.blur()
            except Exception:
                pass
            if kind == "text" and await widgets.ui5_uncommitted(el, committed):
                # UI5 clears or flags a typed value that no suggestion committed; report it like a rejected choice.
                raise ValueError("The website did not commit the observed choice")
            if (
                self.resolver.profile.autonomous
                and kind == "text"
                and str(value).casefold() == "negotiable"
                and re.search(r"desired|expected|expectations?|requirements?|preferred", field["label"], re.I)
                and re.search(r"salary|compensation|pay", field["label"], re.I)
                and not await el.input_value()
            ):
                # Workday can implement a numeric filter on type=text without
                # declaring inputmode or a pattern. Replan the delegated future
                # compensation preference using the observed numeric constraint.
                numeric = {
                    **field,
                    "type": "number",
                    "hint": "The website discarded Negotiable; this input requires an annual numeric compensation preference.",
                }
                answer = (await self.resolver.resolve([numeric], use_local=False))[0]
                if answer.disposition != "answer" or not re.fullmatch(r"\d+(?:\.\d+)?", answer.value or ""):
                    raise ValueError("The website requires a numeric compensation preference")
                committed = answer.value
                await el.fill(committed, timeout=4000)
                await el.blur()
                self.emit(
                    "control_recovery",
                    "Adapted a delegated compensation preference to the website's numeric input",
                )
        if kind == "combobox" and field["label"] == "Country":
            # Greenhouse's phone-country picker shows only the dial code after
            # an exact option was clicked (for example, "United States +1" -> "+1").
            displayed = await el.evaluate(
                "e=>e.closest('[class*=__control]')?.querySelector('[class*=__single-value]')?.textContent.trim() || ''"
            )
            if re.fullmatch(r"\+\d+", displayed) and str(committed).endswith(displayed):
                committed = displayed
        if kind in {"dropdown", "combobox", "prompt"}:
            await widgets.close_picker(el, frame)
        self.expected[field["id"]] = committed
        self.expected_labels[(normalize(field["label"]), kind)] = committed
        self.expected_slots[self.field_key(field)] = committed
        return committed

    def location_resolver(self, field):
        """For a location search box, pick the result matching the applicant's address."""
        from .answers import CONTACT, address_location_option

        if not self.resolver or normalize(field.get("label", "")) not in CONTACT["location"] | {
            "city",
            "current location city",
        }:
            return None
        address = self.resolver.profile.address
        return lambda texts: address_location_option(texts, address)

    async def _combobox_single_page(self, field, el, value):
        """Greenhouse/Lever/Ashby react-select comboboxes (the behavior proven on live runs)."""
        await el.click(timeout=4000)
        if await el.evaluate("e=>['INPUT','TEXTAREA'].includes(e.tagName)"):
            await el.fill(value, timeout=4000)
        options = self.frames[field["id"]].locator('[role="option"]')
        exact = options.filter(has_text=re.compile("^" + re.escape(value) + "$"))
        if re.search(r"location|city|currently based", field["label"], re.I):
            await options.first.wait_for(timeout=5000)
            labels = await options.all_text_contents()
            location = self.resolver.profile.location
            region = location.split(",")[1].strip() if "," in location else ""
            from .answers import US_STATES

            region = US_STATES.get(region.upper(), region).casefold()
            candidates = [
                label
                for label in labels
                if value.casefold() in label.casefold() and region and region in label.casefold()
            ]
            if candidates:
                value = candidates[0]
                exact = self.frames[field["id"]].get_by_role("option", name=value, exact=True)
        try:
            # Options often load after typing (Greenhouse school search): wait for the match.
            await exact.first.wait_for(state="visible", timeout=5000)
            await exact.first.click(timeout=5000)
        except Exception:
            await widgets.wait_options(
                self.frames[field["id"]], None, timeout=3.0, selector='[role="option"]'
            )
            match = widgets.best_option([t.strip() for t in await options.all_text_contents()], value)
            if not match:
                raise ValueError("Option is not offered") from None
            await options.filter(has_text=re.compile("^" + re.escape(match) + "$")).first.click(timeout=5000)
            value = match
        return value

    async def discover_options(self, field):
        """Open custom selects once to read their real choices before the resolver decides."""
        try:
            async with asyncio.timeout(8):
                # Resume parsing can replace an entire iCIMS frame while a
                # fill batch still holds its earlier field list. Never spend
                # several default Playwright timeouts on detached targets. A live node needs no rescan
                # (one full scan per picker was paid on every Workday page).
                await self.live_locator(field)
                await self._discover_options(field)
        except (TimeoutError, PlaywrightError, ValueError):
            field["options_observed"] = False
            self.emit(
                "option_observation",
                "Picker observation changed or exceeded its time limit",
                {"field": field["label"]},
            )

    async def _discover_options(self, field):
        frame = self.frames[field["id"]]
        el = self.locator(field["id"])
        field["options_observed"] = False
        if field["type"] == "combobox" and APPLICANT_LOCATION.fullmatch(field["label"].strip()) and await el.evaluate(
            "e => ['INPUT','TEXTAREA'].includes(e.tagName) && !e.readOnly && !e.disabled"
            " && ['list','both'].includes(e.getAttribute('aria-autocomplete'))"
        ):
            # The applicant's own location is a search-only typeahead (Greenhouse Geocode Earth, Ashby, SmartRecruiters):
            # it lists nothing until typed, so opening it cost 1.4-2.3 s for zero choices. The resolver answers from
            # the profile; the driver types the city and picks the offered row.
            field["options"], field["options_partial"], field["search_only"] = [], True, True
            return
        try:
            await widgets.dismiss_pickers(frame)
            if field.get("widget") == "sdf-select":
                # ADP's sdf-select-simple lists its items in its own children: no popup is opened to read them. Midco's
                # render only once opened, and a filterable one shows a window of 20 (State): neither is the catalog.
                field["options"] = [
                    text for _, text in await el.evaluate(widgets.SDF_ITEMS_JS) if text and not widgets.placeholder_option(text)
                ][:400]
                field["options_partial"] = not field["options"] or bool(await el.locator(widgets.SDF_FILTER).count())
            elif field.get("widget") == "ui5wc-select":
                field["options"] = [
                    o
                    for o in await widgets.ui5wc_select_options(el)
                    if o and not widgets.placeholder_option(o)
                ][:400]
                field["options_partial"] = False
            elif field.get("widget") == "ui5wc-valuehelp":
                field["options"] = (await widgets.ui5wc_options(frame, el))[:400]
                field["options_partial"] = len(field["options"]) >= 50
                if not field["options"]:
                    # An unreadable pick list is answered as text and committed by a typed-prefix pick.
                    field["type"] = "text"
            elif field["type"] == "combobox" and (select_id := await widgets.icims_dropdown(el)):
                # iCIMS's icimsDropdown: a static select lists its whole catalog; a server-searched one holds only
                # its pick, and its 25 visible rows are a window of the catalog, never all of it.
                static = await widgets.icims_select_texts(el)
                if len(static) > 1:
                    field["options"], field["options_partial"] = static[:400], False
                else:
                    await el.click(timeout=3000)
                    rows = await widgets.icims_rows(frame, select_id, timeout=3)  # first page only
                    field["options"], field["options_partial"] = [text for _, text in rows][:250], True
            elif field["type"] == "combobox":
                if field.get("widget") == "oracle-multiselect":
                    # This is query text, not the saved pills. An obsolete query
                    # must not hide the catalog from the answer/recovery agent.
                    await el.fill("", timeout=1000)
                await widgets.open_combobox(el)
                scope = await widgets._popup_scope(el)
                opts = await widgets.wait_options(frame, scope, timeout=1.2, owner=el)
                field["options"] = [
                    o["text"] for o in opts if not o["disabled"] and not widgets.placeholder_option(o["text"])
                ][:250]
                # Editable search results are a window, not the complete catalog.
                # Ashby/React may declare "both" or omit aria-autocomplete.
                editable = await el.evaluate(
                    "e => ['INPUT','TEXTAREA'].includes(e.tagName) && !e.readOnly && !e.disabled"
                )
                field["options_partial"] = (
                    editable
                    or await el.get_attribute("aria-autocomplete") in {"list", "both"}
                    # Lazy pickers render a window: Westinghouse SuccessFactors Country showed
                    # exactly 99 (Afghanistan..Hungary), so United States looked "not offered".
                    or len(field["options"]) >= 50
                )
                # One owner-aware dismissal in finally. A second Escape on
                # Oracle's now-closed picker clears its accepted selection.
            elif field["type"] == "dropdown":
                await widgets.field_click(el, timeout=2000)
                scope = await widgets._popup_scope(el)
                opts = await widgets.wait_options(frame, scope, timeout=2, owner=el)
                field["options"] = [
                    o["text"] for o in opts if not o["disabled"] and not widgets.placeholder_option(o["text"])
                ][:400]
                # A long list may be a virtualized window; the driver can type to reach other options.
                field["options_partial"] = len(field["options"]) >= 10
                await frame.page.keyboard.press("Escape")
            elif field["type"] == "prompt":
                await widgets.open_prompt(el)
                opts = await widgets.wait_options(
                    frame,
                    None,
                    timeout=1.2,
                    selector="[data-automation-id=promptOption],[role=option]",
                    owner=el,
                )
                field["options"] = [o["text"] for o in opts][:250]
                # Search-driven: the listed options are only the first level.
                field["options_partial"] = True
                await frame.page.keyboard.press("Escape")
            field["options_observed"] = bool(field.get("options"))
        except (PlaywrightError, ValueError):
            pass
        finally:
            await widgets.close_picker(el, frame)
            if not field["options_observed"]:
                self.emit("option_observation", "No offered choices were readable", {"field": field["label"]})

    def _pending(self, field, reason, blocker_type="missing_fact", retryable=False, supporting_facts=None):
        self.pending.append(
            {
                "question": field["label"],
                "options": field["options"],
                "key": answer_key(field),
                "reason": reason,
                "blocker_type": blocker_type,
                "retryable": retryable,
                "supporting_facts": supporting_facts or [],
                "type": field["type"],
                "required": field["required"],
                "field_id": field["id"],
                "slot": field.get("slot", 0),
                "scope": {k: field.get(k, "") for k in ("section", "group", "name", "automation_id")},
                # What the owner needs to answer it (owner_questions.context_from): which history row, the form's
                # hint, accepted file types, and whether the options were only partly read.
                "context": {
                    "hint": str(field.get("hint") or "")[:600],
                    "accept": field.get("accept", ""),
                    "history_kind": field.get("history_kind", ""),
                    "history_identity": field.get("history_identity") or {},
                    "options_partial": bool(field.get("options_partial")),
                    # How the field takes its value, so the owner is told what to send (owner 2026-10-23: "it does
                    # not tell the type of field, what input is required").
                    "date_format": field.get("date_format", ""),
                    "date_parts": field.get("date_parts") or [],
                    "maxlength": field.get("maxlength", -1),
                    "widget": field.get("widget", ""),
                },
            }
        )

    async def attach_owner_documents(self):
        """Attach a document the owner sent for an upload question (a transcript, a work sample) to each required,
        empty file field asking for that kind of document. True when a file was attached.

        Owner 2026-10-23: "cannot even get the intent of the field, i.e. if it is uploading something". A required
        transcript was asked as a text question; the answer could never be typed into a file input."""
        resolver = getattr(self, "resolver", None)
        if resolver is None or not getattr(resolver, "config", None) or not getattr(resolver, "db", None):
            return False
        from .owner_questions import document_for

        attached = False
        for field in list(self.fields.values()):
            if (
                field["type"] != "file"
                or not field["required"]
                or field["value"]
                or self.field_key(field) in self.document_uploads
                or (RESUME.search(field["label"]) and not NOT_RESUME.search(field["label"]))
            ):
                continue
            path = document_for(resolver.db, resolver.config.data_dir, field["label"], resolver.employer)
            if path is None:
                continue
            try:
                await self.locator(field["id"]).set_input_files(str(path), timeout=15000)
                await self.wait_upload()
            except (PlaywrightError, ValueError):
                continue  # The required-document check still reports the field.
            self.document_uploads[self.field_key(field)] = path.name
            attached = True
            self.emit("document_upload", "Attached the document you sent for this question", {"field": field["label"]})
        return attached

    @property
    def workday(self):
        return bool(WORKDAY_HOST.search(self.page.url))

    def icims_renamed_resume(self, text):
        """iCIMS stores an uploaded resume as "Resume<yyyyMMddHHmm>.pdf" and lists that name, never the uploaded one
        (Iridium 2026-10-21). Read as "not accepted", the resume was uploaded again; each upload reloaded the page and
        cleared the password fields, so Submit Profile was refused "Password: required" on every pass. Accepted only on
        an iCIMS page after this run uploaded the file itself."""
        return bool(
            self.uploaded
            and ICIMS_HOST.search(self.page.url or "")
            and ICIMS_RENAMED_RESUME.search(text or "")
            and re.search(r"replace resume|select your resume", text or "", re.I)
        )

    async def wait_upload(self):
        """Wait for the website's own upload acknowledgement, not a fixed delay."""
        try:
            greenhouse = await self.greenhouse_uploads()
            if greenhouse:
                for widget in greenhouse:
                    await widget.locator(".file-upload__filename").wait_for(timeout=15000)
            elif self.workday:
                await self.page.get_by_text(re.compile(r"^Successfully Uploaded!", re.I)).first.wait_for(
                    timeout=15000
                )
            else:
                await self.page.wait_for_timeout(150)
                await self.settle(timeout=12)
        except Exception:
            pass  # verify() reads the result back and reports a missing attachment.

    async def cancel_reupload_dialog(self):
        """SuccessFactors asks to confirm a second resume upload ("will overwrite the prefilled
        data"). Yes opens another native file dialog nobody can drive; Cancel keeps the data.
        Found by its message text, since UI5 dialogs do not always expose a dialog role."""
        try:
            note = self.page.get_by_text(
                re.compile(r"upload(?:ing)? your (?:resume|cv) again.{0,80}overwrite", re.I)
            ).locator("visible=true")
            if not await note.count():
                return False
            # The nearest ancestor that holds buttons is the dialog itself.
            box = note.first.locator("xpath=ancestor::*[.//button or .//*[@role='button']][1]")
            cancel = box.get_by_role("button", name=re.compile(r"^(?:cancel|no)$", re.I))
            if await cancel.count() != 1:
                return False
            await widgets.safe_click(cancel.first, timeout=4000)
            # SuccessFactors asks this only when a resume is already attached to the application, so it
            # is verified too. The new step wizard shows that tile only on its first step; without this,
            # every later step failed "Resume attachment was not accepted" and Next was never pressed
            # (ASSA ABLOY dry run 2026-10-15).
            self.uploaded = self.upload_verified = self.resume_on_file = True
            self.emit("dialog_dismissed", "Kept the prefilled data instead of uploading the resume again")
            await self.settle(timeout=2)
            return True
        except Exception:
            return False

    async def accept_terms_dialog(self):
        """Resume uploads can open an authorized privacy overlay after the initial page scan.

        Returns True when it ended on a scan that nothing has changed since, so the caller can use `self.fields`
        instead of scanning the same page again."""
        await self.cancel_reupload_dialog()
        if not self.resolver.profile.accept_all_application_terms:
            return False
        clicked = set()
        for _ in range(2):
            obs = await self.scan()
            dialogs = obs.get("dialogs", [])
            if not dialogs:
                return True
            top = dialogs[-1]
            if not re.search(
                r"privacy|terms|data protection|consent",
                top.get("title", "") + " " + top.get("text", ""),
                re.I,
            ):
                return True
            buttons = [
                b
                for b in obs["controls"]
                if b["id"] in top["control_ids"]
                and re.fullmatch(r"(?:i )?(?:agree|accept)(?: all)?", b["label"], re.I)
            ]
            if len(buttons) == 1:
                action = buttons[0]
            else:
                action = consent_dialog_action(top, obs)
                if action is None:
                    return True
            if action["id"] in clicked:
                # The same overlay survived its own action: stop rather than loop.
                self.emit(
                    "dialog_stuck",
                    "Privacy agreement overlay stayed open after its action",
                    {"title": top.get("title", "")[:80]},
                )
                return True
            for f in obs["fields"]:
                if f["id"] in top["field_ids"] and f["type"] == "checkbox":
                    answer = self.resolver.local(f)
                    if answer and answer.disposition == "answer":
                        await self.fill(f, answer.value)
            clicked.add(action["id"])
            await self.click(action["id"], auth_control=True)
            self.emit("consent", "Accepted the authorized privacy agreement overlay")
        return False

    def catalog_digest(self, field):
        from .answer_context import digest

        context = field.get("context", "")
        if isinstance(context, dict) and re.fullmatch(r"Education \d+", field.get("group", "")):
            # Adjacent GPA/date/degree values change while this same history
            # row is filled. They do not change the meaning of its catalog.
            # The row scope and complete profile digest still bind the answer.
            context = None
        return digest([self.question_shape(field), context])

    def catalog_answer(self, field):
        """Reuse an owned, verified interpretation as a proposal, never a new fact."""
        from .answer_context import digest
        from .schemas import Answer

        if field["type"] not in {"prompt", "combobox", "dropdown"} or not re.fullmatch(
            r"field of study|major|discipline|area of study|school(?: or university)?|university",
            normalize(field["label"]),
        ):
            return None
        key = list(self.field_key(field))[:-1]
        candidates = [
            item
            for item in self.ledger.values()
            if item.get("field_key", [])[:-1] == key
            and item.get("verified")
            and item.get("evidence_ids")
            and item.get("answer")
            and item.get("profile_digest") == digest(self.resolver.profile.model_dump())
            and (
                item.get("catalog_digest") == self.catalog_digest(field)
                or item.get("question_digest")
                == digest([self.question_shape(field), field.get("context", "")])
            )
        ]
        if len({item["answer"] for item in candidates}) != 1:
            return None
        item = candidates[-1]
        return Answer(
            field_id=field["id"],
            value=item["answer"],
            disposition="answer",
            evidence_ids=item["evidence_ids"],
            reason="Previously verified owned catalog choice",
        )

    def preselected(self, field, experience):
        """Workday values the employer or its resume parser already set; recorded, not re-entered."""
        supported = self.resolver.local(field)
        if supported and supported.disposition == "review" and supported.blocker_type == "missing_fact":
            return False
        if re.fullmatch(r"degree(?: type| level)?", normalize(field["label"])) and re.fullmatch(
            r"Education \d+", field.get("group", "")
        ):
            known = self.resolver._entry_answer(
                {**field, "options": [], "options_partial": True}, normalize(field["label"])
            )
            if (
                known
                and not widgets.same_value(field, field.get("value", ""), known.value)
                and not widgets.offered_degree_equivalent(field, known.value)
            ):
                return False  # A previous website readback cannot overrule the awarded degree.
        correction = self.quality_answer(field)
        if correction and not widgets.same_value(field, field.get("value", ""), correction.value):
            return False
        # Reuse a cited answer only after the same owned question reads back
        # its committed value, with unchanged applicant evidence and scope.
        # Closed pickers also lose their inventory: preserve the offered leaf.
        from .answer_context import digest

        if (
            field["type"]
            in {
                "prompt",
                "combobox",
                "dropdown",
                "text",
                "textarea",
                "number",
                "date",
                "tel",
                "email",
                "url",
                "select",
                "radio",
                "radiogroup",
                "pills",
                "buttonchoice",
                "checkbox",
            }
            and has_value(field)
            and field["valid"]
            and not field.get("error")
            and not AUTH.search(field["label"])
        ):
            prior = self.catalog_answer(field)
            if prior and widgets.same_value(field, field["value"], prior.value):
                self.expected[field["id"]] = field["value"]
                self.expected_slots[self.field_key(field)] = field["value"]
                self.record_answer(field, field["value"], prior.evidence_ids, verified=True)
                return True
            previous = next(
                (
                    item
                    for item in self.ledger.values()
                    if item.get("field_key") == list(self.field_key(field))
                    and item.get("verified")
                    and item.get("evidence_ids")
                    and item.get("profile_digest") == digest(self.resolver.profile.model_dump())
                    and (
                        field["type"] in {"prompt", "combobox", "dropdown"}
                        or item.get("knowledge_digest")
                        == (
                            digest([entry.model_dump() for entry in self.resolver.kb.entries])
                            if self.resolver.kb
                            else ""
                        )
                    )
                    and item.get("question_digest")
                    == digest([self.question_shape(field), field.get("context", "")])
                    and widgets.same_value(field, field["value"], item.get("answer", ""))
                ),
                None,
            )
            if previous:
                known = self.resolver.local(field)
                if (
                    field["type"] not in {"prompt", "combobox", "dropdown"}
                    and known
                    and known.disposition == "answer"
                    and not widgets.same_value(field, field["value"], known.value)
                ):
                    return False
                self.expected[field["id"]] = field["value"]
                self.expected_slots[self.field_key(field)] = field["value"]
                self.record_answer(field, field["value"], previous["evidence_ids"], verified=True)
                return True
        parsed = experience and has_value(field) and field["valid"]
        if parsed:
            known = self.resolver.local(field)
            if (
                known
                and known.disposition == "answer"
                and not widgets.same_value(field, field["value"], known.value)
            ):
                return False  # Correct parser output when the saved profile supplies the exact fact.
            if known and known.disposition != "answer" and HISTORY_UNKNOWN in (known.evidence_ids or []):
                # iCIMS's parser put the company name into an employer "Address" and the home zip into its "Zip"
                # (Atlassian 2026-10-19): the profile records neither, so the fill loop clears the box.
                return False
        known = self.resolver.local(field)
        same_value = widgets.same_value
        if self.workday and topic(field["label"]) == "source":
            from .answer_quality import equivalent

            same_value = equivalent
        confirmed = bool(
            has_value(field)
            and field["valid"]
            and known
            and known.disposition == "answer"
            and same_value(field, field["value"], known.value)
        )
        recovered = self.control_choices.get(self.field_key(field))
        if recovered and known and known.disposition == "answer" and known.value == recovered["desired"]:
            confirmed = field["valid"] and widgets.same_value(field, field["value"], recovered["answer"])
        if (
            confirmed
            and field.get("type") == "tel"
            and "greenhouse" in (urlsplit(self.page.url).hostname or "")
            and field["id"] not in self.retyped_phones
        ):
            # A resumed Greenhouse tab can show a phone its widget no longer holds (Datacor: the
            # stale "(240) ..." was rejected as too short on submit). Type it once per run.
            self.retyped_phones.add(field["id"])
            confirmed = False
        if (
            not confirmed
            and has_value(field)
            and field["valid"]
            and not field.get("error")
            and not re.search(
                r"\b(?:work experience|employment|education|school|university)\b",
                normalize(" ".join(str(field.get(key, "")) for key in ("section", "group"))),
            )
        ):
            from .answers import CONTACT, address_location_option

            # A closed location picker exposes its qualified display value but
            # no popup inventory. Preserve a confirmed selection across remounts
            # instead of retyping it against a new, unrelated result window.
            if normalize(field["label"]) in CONTACT["location"] | {"city", "current location city"}:
                confirmed = bool(address_location_option([field["value"]], self.resolver.profile.address))
            elif self.workday and topic(field["label"]) == "source":
                # Reuse the audit's supported equivalence for a committed leaf.
                # Reopening a correct picker can hide that leaf in a category
                # and repeatedly fail an exact search for the shorter policy.
                from .answer_quality import equivalent

                confirmed = equivalent(field, field["value"], self.resolver.profile.application_source)
        contact = (
            self.workday
            and bool(field["value"])
            and (
                (field["label"] == "Country" and field["value"] == "United States of America")
                or (field["label"] == "Phone Device Type" and field["value"] == "Mobile")
                or (
                    field["label"] == "Country Phone Code"
                    and field["value"] == "United States of America (+1)"
                )
            )
        )
        if not (parsed or contact or confirmed):
            return False
        key = (normalize(field["label"]), field["type"])
        self.expected[field["id"]] = field["value"]
        self.expected_labels[key] = field["value"]
        self.expected_slots[self.field_key(field)] = field["value"]
        self.record_answer(
            field,
            field["value"],
            ["profile:confirmed" if confirmed else "resume:parsed" if parsed else "employer:preselected"],
        )
        return True

    async def rematch(self, field):
        await self.scan()
        return self.matching_field(field)

    async def resume_widgets(self):
        """One owned widget per attachment set, even with duplicate upload inputs."""
        result, seen = [], set()
        for field in self.resume_fields():
            el = self.locator(field["id"])
            marker = await el.evaluate(r"""e => {
              const removes='[data-automation-id*="delete" i],[data-automation-id*="remove" i],button[aria-label*="Remove" i],button[aria-label*="Delete" i]';
              // A nested resumeUpload is only the drop zone; cards can be siblings.
              let root=e.closest('[data-automation-id="resumeSection"],[data-automation-id="resumeAttachments"],[data-automation-id="formField-resume"]');
              if(!root) {
                for(let n=e.parentElement;n && n.tagName!=='BODY';n=n.parentElement) {
                  const inputs=[...n.querySelectorAll('input[type=file]')];
                  if(inputs.some(x=>/cover.?letter|transcript|other document/i.test([x.name,x.id,x.getAttribute('aria-label')].join(' '))))break;
                  if(n.querySelector(removes) && /resume|cv|curriculum/i.test(n.innerText||'')) {root=n;break;}
                }
              }
              root ||= e.closest('[data-automation-id="resumeUpload"],[data-automation-id="file-upload-container"]') || e.parentElement;
              if(!root)return null;
              // Unknown attachments alongside this resume must not be deleted.
              const inputs=[...root.querySelectorAll('input[type=file]')];
              if(inputs.some(x=>/cover.?letter|transcript|other document/i.test([x.name,x.id,x.getAttribute('aria-label')].join(' '))))return null;
              root.dataset.jpResumeWidget ||= 'resume-'+Math.random().toString(36).slice(2,10);
              return root.dataset.jpResumeWidget;
            }""")
            if not marker:
                raise ResumeAttachmentError(
                    "Resume section could not be identified; refusing to append an attachment"
                )
            frame = self.frames[field["id"]]
            key = (id(frame), marker)
            if key not in seen:
                result.append((field, frame.locator(f'[data-jp-resume-widget="{marker}"]')))
                seen.add(key)
        return result

    async def replace_workday_resume(self):
        """Remove old resume cards, upload once per widget, then verify exact count."""
        if not self.workday or not self.resume_fields():
            return
        if self.workday_resume_replaced:
            current = await self.observe_resume_attachment()
            if (
                current.get("verified")
                and current.get("sha256") == self.resume_attachment.get("sha256")
                and current.get("widgets") == self.resume_attachment.get("widgets")
            ):
                self.uploaded = self.upload_verified = True
                return
        for _, widget in await self.resume_widgets():
            controls = widget.locator(
                '[data-automation-id*="delete" i],[data-automation-id*="remove" i],button[aria-label*="Remove" i],button[aria-label*="Delete" i]'
            )
            for _ in range(12):
                visible = [b for b in await controls.all() if await b.is_visible()]
                if not visible:
                    break
                await widgets.field_click(visible[0], timeout=3000)
                await self.settle(timeout=2)
            if any([await b.is_visible() for b in await controls.all()]):
                raise ResumeAttachmentError(
                    "Stored resumes could not be removed; no new attachment was appended"
                )
            if await self.resume_names(widget):
                raise ResumeAttachmentError(
                    "Stored resume is still present after removal; no new attachment was appended"
                )
        self.uploaded = self.upload_verified = False
        await self.scan()
        for field, _ in await self.resume_widgets():
            await self.locator(field["id"]).set_input_files(str(self.resume_path), timeout=15000)
            await self.wait_upload()
        self.uploaded = True
        await self.scan()
        self.resume_attachment = await self.observe_resume_attachment()
        if not self.resume_attachment.get("verified"):
            raise ResumeAttachmentError(
                "Resume upload did not produce exactly one accepted attachment; no additional copy will be uploaded"
            )
        self.upload_verified = True
        self.workday_resume_replaced = True
        self.emit("resume_replaced", "Verified exactly one resume attachment", {"attachment_count": 1})

    async def resume_names(self, widget):
        return await widget.evaluate(r"""root => {
          const nodes=[...root.querySelectorAll('[data-automation-id*="file-name" i],[data-automation-id*="fileName" i],[data-automation-id="attachmentFilename"]')].filter(n=>n.getClientRects().length);
          if(nodes.length)return nodes.filter(n=>!nodes.some(p=>p!==n&&p.contains(n))).map(n=>(n.innerText||'').trim());
          const leaves=[...root.querySelectorAll('span,a,p,div')].filter(n=>n.getClientRects().length && !n.children.length && /\.(pdf|docx?|rtf|txt)$/i.test((n.innerText||'').trim()));
          if(leaves.length)return leaves.map(n=>n.innerText.trim().replace(/^Successfully Uploaded!\s*/i,''));
          return [...root.querySelectorAll('input[type=file]')].flatMap(e=>[...(e.files||[])].map(f=>f.name));
        }""")

    async def observe_resume_attachment(self):
        if not self.resume_path or not self.resume_path.exists():
            return {}
        observed = []
        identities = []
        for field, widget in await self.resume_widgets():
            names = await self.resume_names(widget)
            observed.extend(names)
            identities.append([self.frame_key(field), await widget.get_attribute("data-jp-resume-widget")])
        return {
            "sha256": hashlib.sha256(self.resume_path.read_bytes()).hexdigest(),
            "filename": self.resume_path.name,
            "verified": observed == [self.resume_path.name],
            "attachment_count": len(observed),
            "widgets": identities,
        }

    def catalog_report(self):
        return {
            "ok": False,
            "problems": ["Open the owned posting; this is a job recommendation catalog"],
            "verified_fields": 0,
            "resume_attached": False,
            "pending": [],
            "auth_required": False,
            "blocker_type": "control_failure",
        }

    async def fill_current(self, accept_prefilled=None):
        from .ats import oracle_job_catalog

        if oracle_job_catalog(self.page.url):
            return await self.verify()
        if accept_prefilled is not None:
            self.accept_prefilled = accept_prefilled
        if not await self.accept_terms_dialog():
            await self.scan()
        self.pending = []
        self.expected = {}
        self.expected_labels = {}
        self.expected_slots = {}
        await self.replace_workday_resume()
        if self.workday:
            body = await self._page_text()
            if self.resume_path.name in body and "Successfully Uploaded!" in body:
                self.uploaded = self.upload_verified = True
        # Upload first: parsing can overwrite fields and change the page.
        page_text = await self._page_text() if self.uploaded else ""
        if self.resume_fields():
            self.resume_seen = True  # This application asks for a resume somewhere.
        for field in self.resume_fields():
            if getattr(self, "resume_on_file", False):
                # SuccessFactors confirmed this application already holds a resume; its wizard keeps the
                # first step's hidden file input, and uploading into it reopened the same dialog forever.
                continue
            if self.workday:
                # A stale required-file banner can survive a verified upload.
                # Re-upload only after explicit validation rejection, through
                # replace_workday_resume; the generic fill pass never appends.
                continue
            # Some ATSs clear the input after upload and list the file instead.
            if field.get("error") or (
                not field["value"]
                and self.resume_path.name not in page_text
                # iCIMS lists our upload as "Resume<timestamp>.pdf"; each upload reloads the profile and clears the
                # passwords typed into it, so uploading again looped until "Password: required" (Iridium 2026-10-21).
                and not self.icims_renamed_resume(page_text)
            ):
                if field.get("widget") == "filechooser":
                    before = set(self.buttons)
                    before_fields = set(self.fields)
                    upload_frame = self.frames[field["id"]]
                    chooser = None
                    try:
                        async with self.page.expect_file_chooser(timeout=2000) as pending:
                            await self.locator(field["id"]).click(timeout=4000)
                        chooser = await pending.value
                    except PlaywrightTimeoutError:
                        # Upload tiles can open a source menu before opening
                        # the OS chooser. Only a newly visible, same-frame
                        # local-device action qualifies; cloud sign-in does not.
                        await self.scan()
                        native = [
                            f
                            for f in self.fields.values()
                            if f["type"] == "file"
                            and f.get("widget") != "filechooser"
                            and f["id"] not in before_fields
                            and self.frames[f["id"]] == upload_frame
                        ]
                        candidates = [
                            b
                            for b in self.buttons.values()
                            if b["id"] not in before
                            and self.frames[b["id"]] == upload_frame
                            and re.fullmatch(
                                r"browse(?:\.\.\.| from (?:device|computer))?|choose file|(?:upload|browse) from (?:my |your )?(?:device|computer)|my device",
                                b["label"],
                                re.I,
                            )
                        ]
                        if len(native) == 1:
                            await self.locator(native[0]["id"]).set_input_files(
                                str(self.resume_path), timeout=15000
                            )
                        elif len(candidates) != 1:
                            raise ResumeAttachmentError(
                                "Resume source menu needs an unambiguous local-device upload action"
                            ) from None
                        else:
                            async with self.page.expect_file_chooser(timeout=8000) as pending:
                                await self.locator(candidates[0]["id"]).click(timeout=4000)
                            chooser = await pending.value
                    if chooser is not None:
                        await chooser.set_files(str(self.resume_path), timeout=15000)
                else:
                    await self.locator(field["id"]).set_input_files(str(self.resume_path), timeout=15000)
                await self.wait_upload()
            self.uploaded = True
        if not await self.accept_terms_dialog():
            await self.scan()
        experience = self.workday and bool(
            await self.page.get_by_role("heading", name="My Experience", exact=True).count()
        )
        wrote_letter = False
        for field in list(self.fields.values()):
            if (
                field["type"] != "file"
                or not field["required"]
                or not re.search(r"cover.?letter", field["label"], re.I)
            ):
                continue
            if field["value"] or self.field_key(field) in self.document_uploads:
                continue
            from .letters import cover_letter

            try:
                letter = await cover_letter(self.resolver)
            except (RuntimeError, ValueError, TimeoutError):
                self._pending(
                    field,
                    "Cover letter generation was unavailable",
                    blocker_type="model_output",
                    retryable=True,
                )
                continue
            await self.locator(field["id"]).set_input_files(str(letter), timeout=15000)
            await self.wait_upload()
            self.document_uploads[self.field_key(field)] = letter.name
            wrote_letter = True
            self.emit("document_upload", "Attached the required role-specific cover letter")
        wrote_letter = await self.attach_owner_documents() or wrote_letter
        handled, rounds = {}, 0
        unavailable_fields = []
        # The scan above is current unless a cover letter was attached or a phone extension blanked since.
        blanked = await self.blank_phone_extensions()
        wrote = wrote_letter or blanked
        observed_now = False  # True while the last scan saw the page as it is, with no write since.
        while rounds < 4:
            rounds += 1
            if rounds > 1 or wrote:
                await self.scan()
            observed_now = True
            todo = []
            for x in self.fields.values():
                if phone_fields.is_phone_extension(x):
                    continue  # Optional; blank is the deterministic answer.
                if (
                    self.field_key(x) in self.optional_picker_omissions
                    and not x["required"]
                    and not has_value(x)
                    and x["valid"]
                    and not x.get("error")
                ):
                    continue
                if (
                    x["type"] in ("file", "password")
                    or x.get("site_account")
                    or handled.get(x["id"]) == self.question_shape(x)
                    or AUTH.search(x["label"])
                    or (
                        self.accept_prefilled
                        and has_value(x)
                        and x["valid"]
                        and not x["required"]
                        and topic(x["label"]) != "source"
                        and self.resolver.local(x) is None
                        and not implausible_link(x)
                    )
                ):
                    continue
                if self.preselected(x, experience):
                    handled[x["id"]] = self.question_shape(x)
                    continue
                if (
                    experience
                    and not x["required"]
                    and not has_value(x)
                    and not self.resolver.knows(x)
                    and not (
                        x["type"] == "checkbox"
                        and re.search(r"currently work|current.*(?:job|role)", x["label"], re.I)
                    )
                ):
                    continue  # Optional Workday experience details without a profile answer stay blank.
                todo.append(x)
            if not todo:
                break
            observed_now = False
            # Bealls' Education page kept 'The field "GPA" contains an invalid value' in its alert banner while
            # the scan's error list came back empty; read the visible alerts too.
            try:
                from .browser import PAGE_MESSAGES

                messages = await self.page.evaluate(PAGE_MESSAGES)
            except Exception:
                messages = []
            named = self.error_named_fields(messages)
            ask = [
                x
                for x in todo
                if x["required"]
                or x["id"] in named
                or has_value(x)
                or USEFUL_OPTIONAL.search(x["label"])
                or x["type"] == "checkbox"
                or self.resolver.knows(x)
            ]
            # Calling-code and phone-type selectors commit before the phone number is typed.
            ask = phone_fields.order_phone_fields(ask)
            # An address country controls the child widget implementations.
            # Oracle replaces them when it changes. Commit a supported country
            # before opening or reasoning over those obsolete child catalogs.
            address_parents = []
            for field in ask:
                context = field.get("context")
                scope = " ".join(
                    [str(field.get(k) or "") for k in ("section", "group")]
                    + ([context] if isinstance(context, str) else [])  # never the group's other values
                )
                if (
                    re.fullmatch(r"country(?: region| territory)?", widgets.norm(field["label"]))
                    and re.search(r"\baddress\b", scope, re.I)
                    and not re.search(r"citizen|nationality|birth", scope, re.I)
                ):
                    answer = self.resolver.local(field)
                    if (
                        answer
                        and answer.disposition == "answer"
                        and (
                            not field.get("valid", True)
                            or not widgets.same_value(field, field.get("value", ""), answer.value)
                        )
                    ):
                        address_parents.append(field)
            if address_parents:
                ask = address_parents
            for field in ask:
                # A list holding only its placeholder is unread, not empty: Emory's server-searched iCIMS degree menu
                # showed "— Make a Selection —", was taken as its whole catalog, and Master of Science as not offered.
                if field["type"] in {"combobox", "dropdown", "prompt"} and not [
                    o for o in field["options"] if not widgets.placeholder_option(str(o))
                ]:
                    await self.discover_options(field)
            local = [
                (
                    f,
                    self.quality_answer(f)
                    or self.phone_answer(f)
                    or self.catalog_answer(f)
                    or self.wotc_answer(f)
                    or self.resolver.local(f),
                )
                for f in ask
            ]
            known = [(f, a) for f, a in local if a and a.disposition == "answer"]
            deferred = bool(known and len(known) < len(ask))
            if deferred:
                # Commit confirmed parent fields first. Their events may hide
                # follow-ups or supply the context needed to answer them. A
                # model must see that new state rather than the initial form.
                selected = [f for f, _ in known]
                answers = [a for _, a in known]
            else:
                selected = ask
                answers = [a for _, a in known] if known else await self.resolver.resolve(ask) if ask else []
            handled.update({x["id"]: self.question_shape(x) for x in selected})
            original_fields = {x["id"]: deepcopy(x) for x in selected}
            changed = False
            for answer in answers:
                original = original_fields.get(answer.field_id)
                field = original
                if not field:
                    continue
                try:
                    AnswerModelUnavailable.raise_for(answer)
                except AnswerModelUnavailable:
                    # A failed sibling batch must not throw away supported
                    # parent answers. Commit those first; they can hide children.
                    unavailable_fields.append(deepcopy(field))
                    continue
                try:
                    field = await self.rematch(original)
                except ValueError:
                    changed = True  # Reconsider a replaced or newly worded question next round.
                    continue
                if original["id"] in named and not field.get("required"):
                    # The page error still demands it; the re-read DOM does not mark it (Taleo Region), and the
                    # flag's loss read as a changed question that was then left blank instead of asked.
                    field = {**field, "required": True}
                if self.question_shape(field) != self.question_shape(original):
                    if field["type"] in {"combobox", "dropdown", "prompt"}:
                        await self.discover_options(field)
                    answer = (await self.resolver.resolve([field]))[0]
                    self.emit(
                        "question_changed",
                        "Reconsidered a question after the form changed",
                        {"field": field["label"]},
                    )
                else:
                    # Preserve observed custom choices until the driver reads
                    # its actual popup. A closed scan has no option inventory.
                    for attr in ("options", "options_partial", "options_observed"):
                        if attr in original:
                            field[attr] = deepcopy(original[attr])
                handled[field["id"]] = self.question_shape(field)
                if answer.disposition != "answer":
                    if field["required"] or (
                        answer.disposition == "review"
                        and answer.blocker_type == "missing_fact"
                        and has_value(field)
                    ):
                        if not field["required"]:
                            field = {**field, "required": True}
                        if field.get("options_observed") is False and not field.get("options_partial"):
                            self._pending(
                                field,
                                "Could not commit the answer: offered choices were not readable",
                                blocker_type="observation_failure",
                                retryable=True,
                            )
                            continue
                        self._pending(
                            field,
                            answer.reason,
                            blocker_type=getattr(answer, "blocker_type", None) or "missing_fact",
                            retryable=getattr(answer, "retryable", False),
                            supporting_facts=getattr(answer, "supporting_facts", []),
                        )
                    elif implausible_link(field) or (
                        answer.disposition == "skip"
                        and not getattr(answer, "retryable", False)
                        and has_value(field)
                        and (not field.get("valid", True) or HISTORY_UNKNOWN in (answer.evidence_ids or []))
                        and field["type"] in {"text", "textarea", "number", "url", "tel"}
                        and not AUTH.search(field["label"])
                    ):
                        # Oracle's résumé parse put "Alex" in optional "Link 1"; an employer
                        # should see an empty link, not a name in a URL field.
                        try:
                            await self.fill(field, "")
                            self.record_answer(field, "", [], verified=False)
                            self.emit(
                                "field_cleared",
                                "Cleared an unsupported invalid optional value",
                                {"field": field["label"]},
                            )
                        except Exception:
                            pass
                    continue
                if (
                    has_value(field)
                    and field["valid"]
                    and widgets.same_value(field, field["value"], answer.value)
                ):
                    self.expected[field["id"]] = field["value"]
                    committed = field["value"]
                else:
                    try:
                        committed = await self.fill(field, answer.value)
                        changed = changed or field["type"] in DEPENDENT_TYPES
                    except AnswerModelUnavailable:
                        raise
                    except Exception as exc:
                        if field["type"] in {"dropdown", "combobox", "prompt"}:
                            try:
                                await widgets.close_picker(self.locator(field["id"]))
                            except ValueError:
                                pass  # A dependent answer may have removed it.
                        structure = await self.control_structure(field["id"])
                        self.emit(
                            "field_error",
                            f"Could not commit {field['label']}",
                            {
                                "error_type": type(exc).__name__,
                                "interaction": widgets.interaction_failure(exc),
                                "source_trace": [
                                    {
                                        "file": Path(frame.filename).name,
                                        "line": frame.lineno,
                                        "function": frame.name,
                                    }
                                    for frame in traceback.extract_tb(exc.__traceback__)
                                    if "/jobpilot/" in frame.filename
                                ][-6:],
                                # A field deadline replaces the cancelled task's stack with this frame's; the
                                # cancellation it wraps still holds where the write was waiting (PepsiCo's phone code
                                # timed out at 12 s on every run with no trace below fill, 2026-10-27).
                                "cancelled_at": [
                                    {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                                    for frame in traceback.extract_tb(exc.__context__.__traceback__)
                                    if "/jobpilot/" in frame.filename
                                ][-8:]
                                if isinstance(exc, TimeoutError) and exc.__context__ is not None
                                else [],
                                "safe_detail": str(exc)[:400]
                                if type(exc) is ValueError
                                and (str(exc) in SAFE_CONTROL_ERRORS or str(exc).startswith(SAFE_CONTROL_PREFIXES))
                                else "",
                                "structure": structure,
                                # What the employer's picker offered for each search (option labels only).
                                "offered": getattr(exc, "offered", None),
                                "field": {
                                    k: field.get(k)
                                    for k in (
                                        "label",
                                        "type",
                                        "widget",
                                        "date_format",
                                        "parts",
                                        "section",
                                        "group",
                                    )
                                },
                            },
                        )
                        if (
                            not field["required"]
                            and not has_value(field)
                            and field["valid"]
                            and not field.get("error")
                            and field["type"] in {"prompt", "combobox", "dropdown"}
                            # An optional picker that cannot commit within its write budget is left empty too.
                            and widgets.interaction_failure(exc) in {"unsupported_choice", "deadline"}
                        ):
                            self.optional_picker_omissions.add(self.field_key(field))
                            self.emit(
                                "optional_omitted",
                                "Left an optional picker empty after its offered choices did not support the answer",
                                {"field": field["label"]},
                            )
                        if field["required"]:
                            interaction = widgets.interaction_failure(exc)
                            reason = (
                                "Missing exact date for "
                                + (field.get("group") or field["label"])
                                + "; the saved history has insufficient date precision"
                                if interaction == "date_precision"
                                else f"Could not commit the answer: {interaction}"
                            )
                            self._pending(
                                field,
                                reason,
                                blocker_type="missing_fact"
                                if interaction == "date_precision"
                                else "control_failure",
                                retryable=interaction != "date_precision",
                            )
                        continue
                verified = False
                if field["type"] in {"prompt", "combobox", "dropdown"}:
                    try:
                        observed = await self.rematch(field)
                        verified = (
                            observed["valid"]
                            and not observed.get("error")
                            and widgets.same_value(observed, observed["value"], committed)
                        )
                    except (ValueError, PlaywrightError):
                        pass  # A remounted child is verified by the next scan.
                self.record_answer(field, committed, answer.evidence_ids, verified=verified)
                # A dependent input can remount a widget and a later round can succeed.
                # Do not keep an earlier control error after committing its answer.
                self.pending = [
                    p
                    for p in self.pending
                    if self.field_key(
                        {
                            **p.get("scope", {}),
                            "id": p["field_id"],
                            "label": p["question"],
                            "type": p["type"],
                            "slot": p.get("slot", 0),
                        }
                    )
                    != self.field_key(field)
                ]
            if not changed and not deferred:
                break
            await self.settle(timeout=3)
        # A later answer can hide a question ("I currently work here" hides "To"); drop those. A loop that ended
        # because nothing was left to ask has just scanned, with no write since: a second scan reads the same page
        # (one full scan per fill_current call on every page that needs no work, and the navigator makes that call
        # each step).
        if not observed_now:
            await self.scan()
        pending = []
        for p in self.pending:
            try:
                current = self.matching_field(
                    {
                        **p.get("scope", {}),
                        "id": p["field_id"],
                        "label": p["question"],
                        "type": p["type"],
                    }
                )
            except ValueError:
                continue  # Hidden/removed questions no longer need a personal answer.
            pending.append({**p, "field_id": current["id"], "slot": current.get("slot", 0)})
        self.pending = pending
        # Validation often appears only after the first input/blur. Repair our
        # own known prose once before returning control to navigation; verify()
        # stays read-only and never changes applicant data.
        for field in list(self.fields.values()):
            expected = self.expected.get(field["id"], self.expected_slots.get(self.field_key(field)))
            kept = " ".join(str(field.get("value") or "").split())
            wanted = " ".join(str(expected or "").split())
            if (
                field.get("type") in {"text", "textarea", "contenteditable"}
                and len(kept) >= 30
                and len(wanted) > len(kept)
                and wanted.startswith(kept)
                and len(wanted.split()) > 3
            ):
                # John Deere (Eightfold) 2026-10-15: the page silently cut a long explanation at 199
                # characters (no maxlength attribute, no error text), so the read-back never matched.
                # The kept prefix is the field's real limit: fit whole sentences to it once.
                from .features.knowledge.writing import fit_length

                try:
                    fitted = fit_length(wanted, len(kept))
                    record = next(
                        (
                            r
                            for r in self.ledger.values()
                            if tuple(r.get("field_key", [])) == self.field_key(field)
                        ),
                        {},
                    )
                    committed = await self.fill(field, fitted)
                    self.expected[field["id"]] = committed
                    self.expected_slots[self.field_key(field)] = committed
                    self.record_answer(field, committed, record.get("evidence_ids", []))
                    self.emit(
                        "validation_repair",
                        "Fitted prose to the field's enforced length",
                        {"field": field["label"]},
                    )
                except (ValueError, PlaywrightError):
                    pass
                continue
            if not field.get("valid", True) and not field.get("error") and expected:
                # John Deere (Eightfold) 2026-10-15: "Input greater than 199 characters" is page-level
                # text, not tied to the input, so the field's own error stayed empty.
                page_errors = " ".join(
                    map(str, (getattr(self, "last_observation", None) or {}).get("errors", []) or [])
                )
                if limit := CHAR_LIMIT.search(page_errors):
                    field = {**field, "error": limit[0]}
            if field.get("valid", True) or not field.get("error"):
                continue
            if expected is None or prose_for_constraint(field, expected) == expected:
                continue
            record = next(
                (r for r in self.ledger.values() if tuple(r.get("field_key", [])) == self.field_key(field)),
                {},
            )
            try:
                committed = await self.fill(field, expected)
                self.record_answer(field, committed, record.get("evidence_ids", []))
                self.emit(
                    "validation_repair", "Repaired an explicit prose constraint", {"field": field["label"]}
                )
            except (ValueError, PlaywrightError):
                pass  # Keep the real validation blocker for owned-page recovery.
        try:
            await self.settle_phone()
        except (ValueError, PlaywrightError):
            pass  # verify() still reports a phone that the website refused.
        await self.settle(timeout=2, quiet=0.15)
        for original in unavailable_fields:
            try:
                current = self.matching_field(original)
            except ValueError:
                continue
            if current.get("required"):
                self._pending(
                    current,
                    "The answer provider is unavailable for this required question",
                    blocker_type="site_transient",
                    retryable=True,
                )
        report = await self.verify()
        self.emit("verification", "Read back the form after filling", report)
        if checkpoint := getattr(self, "on_checkpoint", None):
            await checkpoint()
        for original in unavailable_fields:
            try:
                current = self.matching_field(original)
            except ValueError:
                continue  # A supported parent answer made this question inapplicable.
            if current.get("required"):
                raise AnswerModelUnavailable()
        return report

    @staticmethod
    def _phone_input(field):
        return phone_fields.is_phone_number(field)

    def phone_answer(self, field):
        """Calling-code and phone-type selectors commit from the saved profile before any model sees them."""
        from .answer_quality import confirmed_phone_country, confirmed_phone_type

        if not self.resolver:
            return None
        return confirmed_phone_country(self, field) or confirmed_phone_type(self, field)

    async def popup_scope(self, control_id, fields):
        """(fields inside, popup text) for the open dialog holding this control, else None."""
        try:
            data = await self.locator(control_id).evaluate(
                """(e, ids) => {
                  let d = e.closest('[role=dialog],[aria-modal="true"],dialog');
                  if (!d) {
                    // Qualcomm (Eightfold) 2026-10-15: an overlay with no dialog role. Use the nearest
                    // ancestor of this control that holds form fields, when it is a small panel.
                    let n = e.parentElement;
                    while (n && n !== document.body && !n.querySelector('input,select,textarea')) n = n.parentElement;
                    if (n && n !== document.body && (n.innerText || '').length < 3000) d = n;
                  }
                  if (!d) return null;
                  const inside = ids.filter(id => { const n = id && document.querySelector('[data-jp-id="' + id + '"]'); return n && d.contains(n); });
                  return {inside, text: (d.innerText || '').slice(0, 400),
                          dialog: !!e.closest('[role=dialog],[aria-modal="true"],dialog')};
                }""",
                [(self.fields.get(f["id"]) or {}).get("dom_id") for f in fields],
                timeout=1500,
            )
        except PlaywrightError:
            return None
        if not data:
            return None
        self.last_popup_dialog = bool(data.get("dialog"))
        inside = set(data["inside"])
        return [f for f in fields if (self.fields.get(f["id"]) or {}).get("dom_id") in inside], data["text"]

    def error_named_fields(self, messages=()):
        """Ids of fields a page-level error requires although they are not marked required.

        Taleo (Bealls 2026-10-17): 'you must enter a number in the "Cellular Number" field' and 'You must
        select ALL possible values in the section Place of Residence' named an optional phone input and the
        Region picker; the page-wide fill skipped both and every Save and Continue came back."""
        text = " ".join(
            [*map(str, (getattr(self, "last_observation", None) or {}).get("errors", []) or []), *map(str, messages)]
        )
        if not text:
            return set()
        names = {normalize(n) for n in re.findall(r'"([^"]{2,60})"', text)}
        complete = bool(re.search(r"select all (?:possible|required) values in the section", text, re.I))
        # "The value does not match the pattern 999-999-9999": a phone written in another shape is rewritten.
        mask = phone_fields.phone_mask(text) if re.search(r"pattern|format", text, re.I) else None
        named = set()
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for field in self.fields.values():
            label = normalize(re.sub(r"[\s.*:]+$", "", field["label"]))
            if label in names:
                # The sentence that names it is this field's error ('The field "GPA" contains an invalid value').
                said = next((x for x in sentences if '"' in x and label in normalize(x)), "")
                field["error"] = field.get("error") or said
                named.add(field["id"])
            elif (
                mask
                and phone_fields.is_phone_number(field)
                and has_value(field)
                and phone_fields.apply_mask(field.get("value"), mask) not in (None, field.get("value"))
            ):
                field["error"] = field.get("error") or "The value does not match the pattern " + mask
                named.add(field["id"])
            elif (
                complete
                and field["type"] in {"select", "combobox", "dropdown"}
                and not has_value(field)
                and re.search(r"\b(?:country|state|province|region|county|city|district)\b", field["label"], re.I)
            ):
                named.add(field["id"])
        # The page requires them: a field without a known answer is asked of the owner with its options
        # (Telegram), never left blank for the page to refuse again.
        for field_id in named:
            self.fields[field_id]["required"] = True
        return named

    def wotc_answer(self, field):
        """Unlabeled required Yes/No radios on a tax-credit (WOTC) screener answer No when the
        applicant confirmed it (Caesars/Oracle exposed no question text for three of them)."""
        if (
            not self.resolver
            or (self.resolver.profile.facts or {}).get("tax_credit_screening_no") is not True
        ):
            return None
        if field.get("type") not in {"radio", "radiogroup"} or not field.get("required"):
            return None
        if str(field.get("label", "")).strip() and not re.fullmatch(
            r"(?:yes|no|yes/no)\??", str(field["label"]).strip(), re.I
        ):
            return None  # A labeled question (e.g. "Are you under the age of 40?") is answered on its own merits.
        options = [str(o).strip() for o in field.get("options") or []]
        if sorted(o.casefold() for o in options) != ["no", "yes"]:
            return None
        text = str((getattr(self, "last_observation", None) or {}).get("text", ""))
        if not re.search(r"work opportunity tax credit|\bWOTC\b|tax credit", text, re.I):
            return None
        from .schemas import Answer

        return Answer(
            field_id=field["id"],
            value=next(o for o in options if o.casefold() == "no"),
            disposition="answer",
            evidence_ids=["fact:tax_credit_screening_no"],
            reason="Confirmed No (tax-credit screener)",
        )

    async def refresh_greenhouse_phone(self):
        """Right before the final click on Greenhouse: retype the visible phone as +1....

        A stale value in a resumed tab passed every check and was rejected after the click as
        "Phone number is too short" (Datacor, Coherent, Parallel, Authentic; 2026-10-13)."""
        if "greenhouse" not in (urlsplit(self.page.url).hostname or "") or not self.resolver:
            return
        phone = str(self.resolver.profile.phone or "")
        if len(re.sub(r"\D", "", phone)) < 7:
            return
        try:
            target = self.page.locator("fieldset.phone-input input[type=tel]:visible")
            if await target.count() != 1:
                return
            code = re.sub(r"\D", "", self.resolver.profile.phone_country_code or "")[:3] or "1"
            typed = (
                "+" + re.sub(r"\D", "", phone)
                if phone.strip().startswith("+")
                else "+" + code + widgets.national_digits(phone)
            )
            await target.fill("", timeout=4000)
            await target.press_sequentially(typed, delay=45, timeout=8000)
            await target.blur(timeout=1500)
            shown = await target.input_value(timeout=1500)
            self.emit(
                "phone_typed",
                "Retyped the phone before the final submit",
                {
                    "matches": re.sub(r"\D", "", shown)[-10:] == re.sub(r"\D", "", typed)[-10:],
                    "stage": "pre_submit",
                },
            )
        except PlaywrightError:
            return

    async def blank_phone_extension(self, field):
        """Leave an optional phone extension empty; a phone number must never land there."""
        if field.get("value"):
            try:
                await self.locator(field["id"]).fill("", timeout=4000)
            except PlaywrightError:
                pass
        self.expected[field["id"]] = ""
        self.expected_slots[self.field_key(field)] = ""
        return ""

    async def blank_phone_extensions(self):
        """Blank every filled optional extension; True when it wrote to the page."""
        blanked = False
        for field in list(self.fields.values()):
            if phone_fields.is_phone_extension(field) and field.get("value"):
                await self.blank_phone_extension(field)
                blanked = True
        return blanked

    @staticmethod
    def phone_refused(field):
        """Empty phone input that the website itself marks as too short or invalid
        (Greenhouse's intl-tel-input prints "Phone number is too short" under a blank field)."""
        return not field.get("value") and bool(
            re.search(
                r"too (?:short|long)|invalid phone|not a valid|enter a valid",
                str(field.get("error") or ""),
                re.I,
            )
        )

    async def settle_phone(self):
        """Re-enter a phone number that a later answer cleared (e.g. the phone-country picker).

        Runs once every other answer is committed. At most two passes; a field is
        refilled only when it is empty, invalid, or refused by the widget's own check.
        """
        if not any(self._phone_input(f) for f in self.fields.values()):
            return
        for _ in range(2):
            await self.scan()
            stale = []
            for field in list(self.fields.values()):
                if not self._phone_input(field):
                    continue
                expected = self.expected.get(field["id"], self.expected_slots.get(self.field_key(field)))
                if not expected or not re.search(r"\d", str(expected)) or "@" in str(expected):
                    continue
                el = self.locator(field["id"])
                try:
                    current = await el.input_value()
                    if (
                        not re.sub(r"\D", "", current)
                        or not field.get("valid", True)
                        or await widgets.phone_rejected(el, expected)
                    ):
                        stale.append((field, expected))
                except PlaywrightError:
                    continue
            if not stale:
                return
            for field, expected in stale:
                try:
                    await self.fill(field, expected)
                except (ValueError, PlaywrightError):
                    pass

    async def authentication_required(self):
        for f in self.fields.values():
            if (f["type"] != "password" and not AUTH.search(f["label"])) or f.get("site_account"):
                continue
            if f["type"] != "password":
                # A code/"security code" question blocks only while it is required and unanswered.
                if f.get("required") and not (f.get("value") and f.get("valid", True)):
                    return True
                continue
            if not f.get("required") and not f.get("value"):
                continue  # Optional "create an account" password (MyGreenhouse) is not a sign-in gate.
            if not await self.locator(f["id"]).evaluate(
                "e=>e.dataset.jpEmbeddedAccountReady==='true' && Boolean(e.value) && e.checkValidity()"
            ):
                return True
        return False

    async def _reduce_submit_controls(self, controls):
        """Reduce several submit matches to one, or return them unchanged when still ambiguous.

        Dropped: controls no longer visible/enabled or inside an inert/hidden/aria-hidden ancestor,
        duplicates of one element, and talent-community / job-alert opt-in forms that carry no
        resume upload (Roku sidebar). Then the control in the resume's form wins; several in one
        form resolve to the last in document order. Two different candidate forms are never guessed.
        """
        if len(controls) <= 1:
            return controls
        live = []  # (control, element handle)
        for control in controls:
            try:
                handle = await self.locator(control["id"]).element_handle(timeout=2000)
            except Exception:
                continue
            if handle is None or not await handle.evaluate(LIVE_CONTROL_JS):
                continue
            if any([await handle.evaluate("(a,b)=>a===b", other) for _, other in live]):
                continue
            if await handle.evaluate(TALENT_FORM_JS):
                continue
            live.append((control, handle))
        if not live:
            return controls
        if len(live) == 1:
            return [live[0][0]]
        identities = [
            (self.frames.get(control["id"]), *(await handle.evaluate("b=>[b.name||'',(b.value||b.innerText||'').trim()]")))
            for control, handle in live
        ]
        if identities[0][1] and len(set(identities)) == 1:
            # One action repeated: iCIMS packet forms put "Save & Return Later | Submit" above and below the form, both
            # <input type=submit name=icims_submit> outside one shared <form>; SAS's final Submit was refused as "not
            # unique" three times (2026-10-21). Same frame, same name, same label: the last in document order.
            last = live[0]
            for item in live[1:]:
                if await last[1].evaluate(
                    "(a,b)=>Boolean(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING)", item[1]
                ):
                    last = item
            return [last[0]]
        forms = []
        for _, handle in live:
            has_form = await handle.evaluate("b=>Boolean(b.form||b.closest('form'))")
            forms.append(await handle.evaluate_handle("b=>b.form||b.closest('form')") if has_form else None)
        if all(form is not None for form in forms) and all(
            [await forms[0].evaluate("(a,b)=>a===b", form) for form in forms[1:]]
        ):
            last = live[0]
            for item in live[1:]:
                if await last[1].evaluate(
                    "(a,b)=>Boolean(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING)", item[1]
                ):
                    last = item
            return [last[0]]
        resume_forms = []
        for field in self.resume_fields():
            try:
                handle = await self.locator(field["id"]).element_handle(timeout=2000)
            except Exception:
                continue
            if handle is not None and await handle.evaluate("e=>Boolean(e.form||e.closest('form'))"):
                resume_forms.append(await handle.evaluate_handle("e=>e.form||e.closest('form')"))
        matches = []
        for (control, _), form in zip(live, forms, strict=True):
            if form is not None and any([await form.evaluate("(a,b)=>a===b", rf) for rf in resume_forms]):
                matches.append(control)
        if len(matches) == 1:
            return matches
        # Greenhouse job boards (Sparksoft 2026-10-18): a header button labelled "Submit Application" (type=button,
        # outside any form) only scrolls to the form. With exactly one native submit inside a form and every other
        # candidate outside any form, the native one is the application's submit.
        native = [
            control
            for (control, handle), form in zip(live, forms, strict=True)
            if form is not None and await handle.evaluate("b=>b.type==='submit'")
        ]
        if len(native) == 1 and sum(form is None for form in forms) == len(live) - 1:
            return native
        return controls

    async def application_submit_controls(self):
        """Distinguish prepared application submits from public Apply entry links."""
        # Merck (Workday) 2026-10-15: the footer link "View a country's privacy page or submit a request."
        # made the real Submit "not unique". Request/privacy links and sentence-length labels are not it.
        final = [
            b
            for b in self.buttons.values()
            if FINAL.search(b["label"])
            and not NOT_FINAL_SUBMIT.search(b["label"])
            and len(b["label"].split()) <= 6
            and not b.get("stepper")  # Dayforce's progress step "Submit" is not the button
        ]
        if final:
            return await self._reduce_submit_controls(final)
        for button in self.buttons.values():
            # Toast's real submit is labelled "Apply now!"; trailing punctuation must not hide it.
            if not re.fullmatch(r"apply(?: now)?[\s!.]*", button["label"], re.I):
                continue
            frame = self.frames[button["id"]]
            passwords = [
                f for f in self.fields.values() if f["type"] == "password" and self.frames[f["id"]] == frame
            ]
            files = [f for f in self.resume_fields() if self.frames[f["id"]] == frame]
            # An uploaded, verified application can end in bare "Apply" without
            # embedded account fields (Rippling). Keep entry links outside this
            # classification: it must be the native submit of the resume's form.
            if (
                not passwords
                and files
                and self.uploaded
                and self.upload_verified
                and button["type"] == "submit"
            ):
                handle = await self.locator(button["id"]).element_handle()
                if all(
                    [
                        await self.locator(f["id"]).evaluate(
                            "(e,b)=>Boolean(e.form) && e.form===b.form", handle
                        )
                        for f in files
                    ]
                ):
                    final.append(button)
                    continue
            if (
                str((getattr(self.resolver, "job", {}) or {}).get("ats", "")) == "successfactors"
                and not passwords
                and re.fullmatch(r"apply[\s!.]*", button["label"], re.I)
                and sum(bool(re.fullmatch(r"apply[\s!.]*", b["label"], re.I)) for b in self.buttons.values())
                == 1
                and (
                    (self.upload_verified and sum(f["type"] != "file" for f in self.fields.values()) >= 5)
                    # The wizard's "7. Review" lists the answers as text beside the resume inputs; its
                    # Apply submitted a preview run (ASSA ABLOY 2026-10-15).
                    or (
                        (
                            files
                            or getattr(self, "resume_on_file", False)
                            or (self.uploaded and self.upload_verified)
                        )
                        and SF_APPLICATION_PAGE.search(self.page.url)
                    )
                )
            ):
                # SuccessFactors' long application form ends in a single "Apply" (ASSA ABLOY 2026-10-14);
                # the attached resume is a widget outside the button's HTML form. The posting page's
                # Apply has no applicant fields, so it never qualifies here.
                final.append(button)
                continue
            if len(passwords) < 2:
                continue
            if not files and not await self.accepted_resume_tile(button["id"]):
                continue
            handle = await self.locator(button["id"]).element_handle()
            if all(
                [
                    await self.locator(f["id"]).evaluate(
                        "(e,b)=>Boolean(e.form||e.closest('form')) && (e.form||e.closest('form'))===b.form && "
                        "(e.type!=='password' || (e.dataset.jpEmbeddedAccountReady==='true' && Boolean(e.value) && e.checkValidity()))",
                        handle,
                    )
                    for f in [*passwords, *files]
                ]
            ):
                final.append(button)
        return await self._reduce_submit_controls(final)

    async def accepted_resume_tile(self, submit_id=None):
        if not self.resume_path or not self.uploaded:
            return False
        for frame in await self.application_frames():
            if submit_id and self.frames[submit_id] != frame:
                continue
            button = await self.locator(submit_id).element_handle() if submit_id else None
            if await frame.evaluate(
                r"""({filename,button}) => {
              const visible=e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
              const hasError=root=>[...root.querySelectorAll('[aria-invalid=true],[role=alert],.error,.file-upload-error')]
                .some(e=>visible(e)&&(e.getAttribute('aria-invalid')==='true'||e.textContent.trim()));
              const exact=e=>[e.getAttribute('title'),e.getAttribute('aria-label'),e.innerText]
                .some(t=>t?.trim()===filename);
              const files=[...document.querySelectorAll('a,span,div,p,img,button')].filter(e=>visible(e)&&exact(e)
                && (!button || (button.form && e.closest('form')===button.form)));
              for(const file of files){
                // Oracle's native accepted tile has no visible Resume label;
                // its custom element supplies the attachment identity.
                const oracleResume=file.closest('resume-upload-button');
                if(oracleResume){
                  if(file.matches('button.attachment-upload-button__download')
                     && file.closest('.attachment-upload-button__filled')
                     && !hasError(oracleResume))return true;
                  continue;
                }
                // SuccessFactors places its label six ancestors above the
                // filename, inside this field's own attachment component.
                const component=file.closest('.attachmentField');
                if(component){
                  const text=(component.innerText||'').replaceAll(filename,'');
                  // SuccessFactors keeps a stale aria-invalid until blur and varies its
                  // success button class; visible error text is the rejection signal.
                  const errorText=[...component.querySelectorAll('[role=alert],.error,.file-upload-error,.errorText')]
                    .some(e=>visible(e)&&e.textContent.trim());
                  if(/\bresume\b|\bcv\b/i.test(text) && !/cover letter|transcript|other documents?/i.test(text)
                     && !errorText)return true;
                  continue;
                }
                let root=file;
                for(let depth=0;depth<5&&root&&root.tagName!=='BODY';depth++,root=root.parentElement){
                  const text=(root.innerText||'').replaceAll(filename,'');
                  if(/cover letter|transcript|other documents?/i.test(text))break;
                  if(/(?:upload|attachment|file).{0,40}(?:failed|rejected|error)|invalid file/i.test(text))break;
                  const label=[root.id,root.className,root.getAttribute('aria-label'),root.getAttribute('data-bind'),text].join(' ');
                  if(/\bresume\b|\bcv\b/i.test(label) && !hasError(root))return true;
                }
              }
              return false;
            }""",
                {"filename": self.resume_path.name, "button": button},
            ):
                return True
        return False

    async def resume_browse_pending(self):
        """A visible Browse/Upload control for the resume, while neither the pinned file nor its tile is shown."""
        if not self.resume_path:
            return False
        text = await self._page_text()
        if self.resume_path.name in text:
            return False
        return any(widgets.opens_resume_chooser(b["label"], text, False) for b in self.buttons.values())

    async def _widget_snapshot(self, field):
        """Record a rejected control's redacted structure once so the widget can be fixed."""
        seen = self.__dict__.setdefault("_snapshotted", set())
        if field["id"] in seen:
            return
        seen.add(field["id"])
        structure = await self.control_structure(field["id"])
        if structure and callable(getattr(self, "emit", None)):
            self.emit(
                "widget_snapshot",
                f"Recorded the redacted structure of {field['label']}",
                {"field": field["label"], "type": field["type"], "structure": structure},
            )

    async def verify(self):
        obs = await self.scan()
        from .ats import oracle_job_catalog

        if oracle_job_catalog(self.page.url):
            return self.catalog_report()
        if await self.cancel_reupload_dialog():
            # The overwrite confirmation sits over the form and blocks finish; Cancel keeps the prefilled data.
            obs = await self.scan()
        if obs.get("observation_failure"):
            return {
                "ok": False,
                "observation_failure": True,
                "problems": ["Application document inspection failed"],
                "verified_fields": 0,
                "resume_attached": False,
                "pending": [],
            }
        current_keys = {self.field_key(f) for f in self.fields.values()}
        for field_id, record in self.ledger.items():
            key = tuple(record.get("field_key", []))
            # Recheck current fields; preserve acknowledged earlier steps for
            # a final review page without editable controls. Legacy checkpoints
            # without scoped keys must be verified again before counting them.
            if not key or field_id in self.fields or key in current_keys:
                record["verified"] = False
        problems = []
        if self.uploaded and not self.upload_verified and not self.workday and not self.resume_fields():
            # SuccessFactors replaces its upload control with an attachment
            # tile. Verify that tile after this owner uploaded the pinned file;
            # a missing input alone must neither fail nor prove acceptance.
            self.upload_verified = await self.accepted_resume_tile()
        elif (
            not self.uploaded
            and not self.workday
            and not self.resume_fields()
            and await self.resume_browse_pending()
        ):
            # A visible resume Browse button with nothing attached is not a verified step; Next would refuse it.
            problems.append("Resume attachment was not accepted")
        if (
            not self.upload_verified
            and not self.resume_fields()
            and self.resume_path.name.casefold() in (await self._page_text()).casefold()
        ):
            # A resumed draft already lists our resume file (ASSA ABLOY SuccessFactors "CV/Resume:
            # Alex_Doe_Resume.pdf", 2026-10-14). The page itself is the evidence; a page that says
            # "No Response" (Jabil Workday) does not name the file and stays unverified. Pinpoint prints the name in
            # capitals and swaps its file input for a hidden one (Kenway 2026-10-27).
            self.uploaded = self.upload_verified = True
        greenhouse_uploads = await self.greenhouse_uploads()
        if greenhouse_uploads:
            filenames = [
                name
                for widget in greenhouse_uploads
                for name in await widget.locator(".file-upload__filename").all_text_contents()
            ]
            self.upload_verified = any(self.resume_path.name in name for name in filenames)
            if not self.upload_verified:
                problems.append("Resume upload has not been accepted by the website")
        elif self.uploaded and self.workday:
            body = await self._page_text()
            self.upload_verified = (
                "Successfully Uploaded!" in body and self.resume_path.name in body
            ) or self.upload_verified
        for f in self.fields.values():
            expected = self.expected.get(f["id"])
            if expected is None:
                expected = self.expected_slots.get(self.field_key(f))
            if (
                f["id"] in self.typed_phones
                and expected is not None
                and widgets.same_value(f, f["value"], expected)
            ):
                f["valid"], f["error"] = True, ""  # Stale site error; the next submit re-validates it.
            if phone_fields.is_phone_extension(f) and not f.get("value"):
                continue  # Optional phone extension: blank is correct and never blocks submit.
            if expected is not None:
                try:
                    if (
                        f["type"] == "combobox"
                        and await self.locator(f["id"]).get_attribute("aria-invalid", timeout=2000) == "true"
                    ):
                        # UI5 pickers clear aria-invalid only on blur; re-read validity after leaving the control.
                        await self.locator(f["id"]).evaluate("e => e.blur()", timeout=2000)
                        f["valid"] = await self.locator(f["id"]).get_attribute("aria-invalid", timeout=2000) != "true"
                except PlaywrightError:
                    # A control the page replaced since the scan (Prime's packet step waited 30 s here and raised,
                    # 2026-10-21) is read again by the next scan, not here.
                    f["valid"] = False
                if (
                    not f["value"]
                    and f["type"] in {"dropdown", "select", "combobox"}
                    and re.fullmatch(r"\s*(?:not specified|none selected|no selection)\s*", str(expected), re.I)
                ):
                    # The scanner reads these as an unselected prompt, but some lists offer them as the decline choice
                    # (Sentry Workday gender: Female / Male / Not Specified looped as "Value not accepted" on
                    # 2026-10-18). The control's own text decides.
                    try:
                        shown = await self.locator(f["id"]).inner_text(timeout=1000)
                    except PlaywrightError:
                        shown = ""
                    if widgets.norm(shown) == widgets.norm(str(expected)):
                        f["value"] = str(expected)
                same = widgets.same_value(f, f["value"], expected) and f["valid"] and not f.get("error")
                # Custom comboboxes must close after committing a choice.
                if f["type"] == "combobox" and not self.workday and same:
                    try:
                        same = await self.locator(f["id"]).get_attribute("aria-expanded", timeout=2000) != "true"
                    except PlaywrightError:
                        same = False  # Replaced since the scan: unverified until the next scan reads it.
                ledger = self.ledger.get(f["id"])
                if ledger is None:
                    ledger = next(
                        (
                            item
                            for item in self.ledger.values()
                            if item.get("field_key") == list(self.field_key(f))
                        ),
                        None,
                    )
                if ledger is not None:
                    ledger["verified"] = same
                if not same:
                    problems.append(f"Value not accepted: {f['label']}")
                    await self._widget_snapshot(f)
            if f["type"] == "password" or AUTH.search(f["label"]) or f.get("site_account"):
                continue  # A signed-in profile's saved login is the account, not an answer (Schwab 2026-10-21).
            if f in self.resume_fields():
                if (self.workday and self.uploaded and self.upload_verified) or getattr(
                    self, "resume_on_file", False
                ):
                    continue
                # The field's own value, or (for widgets that clear the input) the file listed on
                # the page while this field is empty.
                text = "" if f["value"] else await self._page_text()
                # Starbucks (Eightfold, signed in) 2026-10-14: the saved resume is preselected in a
                # resume picker whose input value, not page text, carries the exact file name.
                picked = not f["value"] and any(
                    self.resume_path.name in str(g.get("value") or "")
                    for g in self.fields.values()
                    if g is not f and g["type"] != "file"
                )
                self.upload_verified = (
                    self.resume_path.name in f["value"]
                    or self.resume_path.name in text
                    or picked
                    or self.icims_renamed_resume(text or await self._page_text())
                )
                if not self.upload_verified:
                    problems.append("Resume attachment was not accepted")
                continue
            if f["type"] == "file":
                uploaded = self.document_uploads.get(self.field_key(f))
                accepted = bool(uploaded and (uploaded in f["value"] or uploaded in await self._page_text()))
                if f["required"] and not f["value"] and not accepted and not RESUME.search(f["label"]):
                    # A required document we cannot supply (e.g. a cover letter file): stop here.
                    problems.append(f"Required document missing: {f['label']}")
                    if not any(p.get("field_id") == f["id"] for p in self.pending):
                        self._pending(f, "Required document upload")
                continue
            empty = not f["value"] or (f["type"] == "checkbox" and f["value"] != "true")
            if f["required"] and f["type"] != "file" and (not f["valid"] or empty):
                problems.append(f"Required field incomplete: {f['label']}")
            elif has_value(f) and not f["valid"]:
                problems.append(f"Entered field is invalid: {f['label']}")
            elif self._phone_input(f) and self.phone_refused(f):
                # An optional phone the widget emptied and rejected still blocks submit.
                problems.append(f"Phone rejected by the website: {f['label']}")
            if f["required"] and f["type"] != "file" and expected is None:
                if not (self.accept_prefilled and has_value(f) and f["valid"]):
                    problems.append(f"Required field has no verified answer: {f['label']}")
        for q in self.pending:
            problems.append(f"Needs an approved answer: {q['question']}")
        if (
            not (self.uploaded and self.upload_verified)
            and str((getattr(self.resolver, "job", {}) or {}).get("ats", "")) == "successfactors"
            and not self.resume_fields()
            and self.resume_path.name.casefold() in (await self._page_text()).casefold()
        ):
            # Corning (SuccessFactors classic, 2026-10-16): the candidate profile's My Documents already
            # lists the applicant's resume and the application form has no upload of its own.
            self.uploaded = self.upload_verified = self.resume_on_file = True
        self.last_problems = list(dict.fromkeys(problems))
        return {
            "ok": not problems,
            "problems": self.last_problems,
            "verified_fields": sum(x["verified"] for x in self.ledger.values()),
            "resume_attached": self.uploaded and self.upload_verified,
            "pending": self.pending,
            "auth_required": await self.authentication_required(),
        }

    async def _page_text(self):
        texts = []
        for frame in await self.application_frames():
            try:
                text = await frame.locator("body").inner_text(timeout=1500)
                shadow = await frame.evaluate(r"""() => {
                  const texts=[];
                  const walk=r=>{for(const e of r.querySelectorAll('*')) if(e.shadowRoot){
                    if(e.getClientRects().length && getComputedStyle(e).visibility!=='hidden')
                      texts.push([...e.shadowRoot.children].map(n=>n.innerText||'').join('\n'));
                    walk(e.shadowRoot);
                  }};walk(document);return texts.join('\n');
                }""")
                texts.append(text + "\n" + (shadow if isinstance(shadow, str) else ""))
            except PlaywrightError:
                continue
        return "\n".join(texts)

    async def application_frames(self):
        """The same application documents for fields, uploads and evidence."""
        frames = []
        for frame in self.page.frames:
            if frame.is_detached() or PROVIDER_FRAME.search(frame.url or ""):
                continue
            if frame != self.page.main_frame:
                try:
                    owner = await frame.frame_element()
                    identity = await owner.evaluate(FRAME_IDENTITY_JS)
                    if not isinstance(identity, str):
                        continue  # An unobserved frame cannot establish application ownership.
                    if PROVIDER_FRAME.search(identity) or re.search(r"captcha|challenge", identity, re.I):
                        continue
                    if not await owner.is_visible() or await owner.evaluate(OFFSCREEN_FRAME_JS):
                        continue
                except PlaywrightError:
                    continue
            frames.append(frame)
            if len(frames) >= MAX_FRAMES:
                break
        return frames

    async def greenhouse_uploads(self):
        uploads = []
        for frame in await self.application_frames():
            widget = frame.locator('.file-upload[aria-labelledby="upload-label-resume"]')
            if await widget.count():
                uploads.append(widget)
        return uploads

    async def intermediate_submit(self, control_id, before):
        """Classify the action's own fields/dialog, not its Submit label alone.

        Both the navigator and form guard use this same evidence. Application
        fields or required uploads cannot masquerade as account/consent steps.
        """
        button = next((b for b in before.get("controls", []) if b["id"] == control_id), None)
        if not button:
            return False
        scope_fields, scope_text = before.get("fields", []), " ".join(before.get("headings", []))
        self.last_popup_dialog = False
        if re.fullmatch(r"submit|continue|next|save", button["label"].strip(), re.I):
            popup = await self.popup_scope(control_id, before.get("fields", []))
            if popup is not None:
                scope_fields, scope_text = popup
            else:
                # Qualcomm (Eightfold) 2026-10-15: its "Create your profile" overlay has no dialog role
                # and no heading in the scan, so the guard refused its Submit for minutes. The page's
                # own opening text names the step; the field checks below still apply.
                # The overlay is appended after the page chrome, so read the whole text; the
                # account-field and no-upload checks below are what make this safe.
                scope_text = scope_text + " " + str(before.get("text", ""))
        account_form = (
            re.fullmatch(r"submit|continue|next|save", button["label"].strip(), re.I)
            and ACCOUNT_CREATE.search(scope_text)
            and scope_fields
            # Qualcomm (Eightfold) 2026-10-15: the "Create your profile" modal keeps its optional
            # "Upload your resume" alternative as a hidden file input. Inside a real modal dialog an
            # optional upload does not make this the application; a required one still does.
            and not any(
                f["type"] == "file" and (f.get("required") or not self.last_popup_dialog)
                for f in scope_fields
            )
            and all(
                f["type"] in {"email", "password", "checkbox", "tel"}
                or (f["type"] == "file" and not f.get("required") and self.last_popup_dialog)
                # Qualcomm (Eightfold) sign-up also asks how profile data is shared and about job alerts.
                or re.search(
                    r"e-?mail|name|country|region|password|phone|consent|agree|terms|privacy|location|city|"
                    r"profile data|your data|manage your data|job alerts?|preferences|communications",
                    f["label"],
                    re.I,
                )
                for f in scope_fields
            )
        )
        # Qualcomm (Eightfold) 2026-10-14: a "Please review before continuing" pop-up holding only the
        # privacy checkbox, the data-sharing choice and a job-alerts box. Its Submit confirms consent;
        # the application itself is submitted later. Only checkbox/radio consent fields qualify.
        consent_dialog = bool(
            re.fullmatch(r"submit|continue|next|save|confirm", button["label"].strip(), re.I)
            and scope_fields is not before.get("fields", [])
            and scope_fields
            and all(
                f["type"] in {"checkbox", "radio", "radiogroup"}
                and re.search(
                    r"consent|agree|privacy|policy|terms|data|alerts?|preferences|communications",
                    f["label"],
                    re.I,
                )
                for f in scope_fields
            )
        )
        return bool(account_form or consent_dialog)

    async def click(
        self, control_id, auth_control=False, step_control=False, revision=None, owner_id=None, launch=False
    ):
        if await dismiss_cookies(self.page, authorized=self.resolver.profile.accept_all_application_terms):
            await self.settle(timeout=2)
        before = await self.assert_revision(revision, owner_id) if revision is not None else await self.scan()
        button = self.buttons.get(control_id)
        if not button and control_id in self.fields:
            # City of SF (SmartRecruiters): the model "clicked" the "I currently attend" checkbox
            # field and was told "stale" five times. Say what it is and which action works.
            field = self.fields[control_id]
            raise ValueError(
                f"'{field['label'][:80]}' is a {field['type'] or 'form'} field, not a button; use fill, "
                "which sets it from the applicant's facts"
            )
        if not button and control_id:
            # Re-rendered editors replace element ids between observation and action. Rebind only
            # to one current control with the same label, type and section.
            previous = (getattr(self, "last_buttons", {}) or {}).get(control_id)
            if previous:
                matches = [
                    b
                    for b in self.buttons.values()
                    if b["label"] == previous["label"]
                    and b.get("type") == previous.get("type")
                    and b.get("section") == previous.get("section")
                ]
                if len(matches) == 1:
                    control_id, button = matches[0]["id"], matches[0]
        if not button:
            raise ValueError("Control is stale; inspect again")
        if re.fullmatch(
            r"(?:explore|view|browse|search|see)(?: all| available)? jobs(?: emphasized)?|careers home|job search|create (?:job )?alert",
            button["label"].strip(),
            re.I,
        ):
            raise ValueError(
                "This opens the general job catalog, not this application. Save any required candidate "
                "profile first, then use return_to_job to resume the exact original posting."
            )
        if re.fullmatch(
            r"auto.?fill (?:my|your) application|auto.?fill with (?:my ?)?greenhouse|use (?:my|your) ?(?:my)?greenhouse profile",
            button["label"].strip(),
            re.I,
        ):
            # Braze (Greenhouse) 2026-10-18: MyGreenhouse autofill asks for an email and a security code before it
            # fills anything; the run then waited on a code meant for a login it never needed.
            raise ValueError(
                "MyGreenhouse autofill signs in by emailed code; the application form is filled directly instead. "
                "Fill the form's fields."
            )
        if (
            button.get("href")
            and not auth_control
            and (
                re.fullmatch(
                    r"(?:privacy(?: policy| notice| statement)?|terms(?: (?:and|&) conditions| of (?:use|service))?|"
                    r"legal(?: information| notice)?|cookie(?: policy| settings)?)(?:[ .]*)",
                    button["label"].strip(),
                    re.I,
                )
                # "Dayforce's Global Privacy Statement" (Sarnova) left the sign-up form for good.
                or re.search(
                    r"\b(?:privacy (?:policy|notice|statement)|terms (?:and|&) conditions|terms of (?:use|service)|"
                    r"cookie (?:policy|notice|settings)|legal notice)\b",
                    button["label"],
                    re.I,
                )
                and len(button["label"]) <= 80
            )
        ):
            raise ValueError(
                "This informational policy link leaves the application. Fill its consent checkbox or use the acknowledgement dialog."
            )
        from .history_editor_guard import education_save_context, education_saved

        education_context = education_save_context(self, before, button)
        back_step = bool(BACK_STEP.fullmatch(button["label"]))
        if back_step and await self.frames[control_id].evaluate("Boolean(window.__jpArmed)"):
            raise ValueError("Back navigation is unavailable after submission")
        allowed = auth_control or step_control
        # A pure account-creation control registers the applicant; it cannot submit this
        # application (Westinghouse "Create Account" was refused here twice, so new accounts
        # were never created). Labels naming apply/submit stay final-submit controls.
        account = bool(ACCOUNT_CREATE.fullmatch(button["label"].strip())) and not FINAL.search(
            button["label"]
        )
        # Microsoft's sign-up form says "Create an account" and submits with a plain
        # "Submit": only account fields (email/name/country/password/consent), no
        # resume upload. That registers the applicant; it is not the application.
        # Eightfold's "Create your profile" popup (Starbucks) sits over the application: judge
        # only the popup that holds this button, when there is one.
        account_form = await self.intermediate_submit(control_id, before)
        if (
            FINAL.search(button["label"])
            and not auth_control
            and not account_form
            and not await self.frames[control_id].evaluate("Boolean(window.__jpArmed)")
        ):
            raise ValueError(
                "Use the submit action for final submission; it verifies fields and reserves the application"
            )
        entry = bool(ENTRY.fullmatch(button["label"])) and not any(
            f["type"] not in {"search", "hidden"}
            and not re.search(r"search|keyword|location", f["label"], re.I)
            for f in self.fields.values()
        )
        # Never let a generic navigation action activate a bare HTML submit control.
        if (
            not allowed
            and not entry
            and not back_step
            and not account
            # Qualcomm (Eightfold) 2026-10-17: the "Create your profile" popup passed the final-submit
            # guard above as an account form, then this guard refused its type=submit "Submit".
            and not account_form
            and button["type"] == "submit"
            and not re.search(r"next|continue|save|review|sign in|log in", button["label"], re.IGNORECASE)
            # Repair controls are HTML submit buttons too (SAIC Oracle "Edit" after a refused submit).
            and not re.fullmatch(
                r"\s*(?:edit|back|previous|add(?: another)?|remove|delete|cancel|upload|attach|"
                r"replace|view|close|go to (?:next|previous) issue)\b.*",
                button["label"],
                re.IGNORECASE,
            )
            # Oracle's required "Take Tax Credit Assessment" (Caesars) opens a screener; it is a
            # section launcher, not the application's final submit.
            and not re.fullmatch(LAUNCHER, button["label"], re.IGNORECASE)
            # iCIMS's email step submits with "I Acknowledge the Privacy Notice" (Schwab 2026-10-18 stalled on it,
            # REA icims.md risk 2). An acknowledgement that names no submission is a step.
            and not (
                re.fullmatch(r"\s*(?:i )?acknowledge(?: the)? privacy (?:notice|policy|statement)\s*", button["label"], re.I)
            )
            # iCIMS's candidate profile is saved with "Update Profile" before the application's own steps (Rivian
            # 2026-10-27: a fully filled profile was refused here and the run stopped at step 1 of 4).
            and not re.fullmatch(r"\s*(?:update|create|save)(?: (?:my|your|a))? profile\s*", button["label"], re.I)
        ):
            raise ValueError("This may submit the application; use submit_application")
        opener = self.page
        opened = []

        def on_popup(page):
            opened.append(page)

        opener.on("popup", on_popup)
        el = self.locator(control_id)
        launcher = launch or bool(re.fullmatch(LAUNCHER, button["label"], re.IGNORECASE))
        # A "Submit" that this guard allowed for a profile/account popup or a tax-credit
        # launcher must also pass the page's own click guard, or the click is silently
        # cancelled (Starbucks "Create your profile": the agent clicked Submit for minutes).
        page_final_ok = bool(account_form or launcher)
        # Marked controls pass the page's submit guard: auth/step controls, entry links, and
        # ordinary navigation labels.
        permitted = (
            allowed
            or page_final_ok
            or entry
            or back_step
            or account
            or bool(
                re.fullmatch(
                    r"next|continue|save (?:and|&) continue|review(?: application)?", button["label"], re.I
                )
            )
        )
        if permitted:
            try:
                # A re-rendered control is stale; waiting 30 s for its old element ended runs on Oracle job pages as
                # browser-control failures (American Express, Uber 2026-10-24). The caller rescans and clicks again.
                el = await el.element_handle(timeout=5000)
            except PlaywrightTimeoutError:
                opener.remove_listener("popup", on_popup)
                raise ValueError("Control is stale; inspect again") from None
            marked = await el.evaluate(
                r"""(el,auth) => {
                  const label=(el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
                  if(!auth && !window.__jpArmed && /\bsubmit\b|send (?:my )?application|complete (?:my )?application|finish application|\babsenden\b|bewerbung (?:abschicken|einreichen)|envoyer ma candidature|\bsoumettre\b/i.test(label))return false;
                  el.dataset.jpAuthControl='true';el.dataset.jpControlLabel=label;
                  el.dataset.jpFinalAuth=String(auth);
                  const f=el.closest('form');if(f)f.__jpAllowedControl=el;
                  return true;
                }""",
                bool(auth_control or page_final_ok),
            )
            if not marked:
                opener.remove_listener("popup", on_popup)
                raise ValueError("Control became a final submission; inspect again")
        try:
            new_tab = await el.evaluate("e => !!e.closest('a[target=_blank],a[target=_new]')")
        except Exception:
            new_tab = False
        try:
            if button.get("type") == "history_select":
                valid = await el.evaluate("""e => e.matches('[data-profile-item-tile="true"][tabindex]') &&
                    ['group','button'].includes(e.getAttribute('role')) && e.tabIndex >= 0 &&
                    /a11y\\.keysHandler/.test(e.getAttribute('data-bind') || '') &&
                    /keys:\\s*['"]enter['"]/.test(e.getAttribute('data-bind') || '')""")
                if not valid:
                    raise ValueError("The observed history selection action changed")
                await el.press("Enter", timeout=4000)
            elif await el.evaluate("""e => e.tagName === 'BUTTON' && !e.disabled &&
                e.matches('.beautiful-timeline-item__edit-icon') &&
                !!e.closest('[data-profile-item-tile="true"]') &&
                e.getAttribute('aria-label')?.toLowerCase() === 'edit'"""):
                await el.press("Enter", timeout=4000)
            elif (
                self.resume_path
                and self.resume_path.exists()
                and widgets.opens_resume_chooser(button["label"], before.get("text", ""), self.uploaded)
            ):
                # A bare Browse button opens an OS dialog nobody can drive: answer the
                # file chooser with the selected resume instead (SuccessFactors).
                if await widgets.click_for_file_chooser(
                    opener, lambda: widgets.safe_click(el, timeout=6000), self.resume_path
                ):
                    await self.wait_upload()
                    self.uploaded = True
            else:
                await widgets.safe_click(el, timeout=6000)
            await opener.wait_for_timeout(120)
            # Playwright reports a popup only once its first response starts loading,
            # so a real cross-site tab arrives well after 120 ms (Otter.ai job links).
            for _ in range(40 if new_tab or launcher else 0):
                if opened:
                    break
                await opener.wait_for_timeout(200)
        finally:
            opener.remove_listener("popup", on_popup)
            if permitted:
                try:
                    await el.evaluate(
                        "el => {delete el.dataset.jpAuthControl;delete el.dataset.jpControlLabel;delete el.dataset.jpFinalAuth;const f=el.closest('form');if(f?.__jpAllowedControl===el)delete f.__jpAllowedControl}"
                    )
                except Exception:
                    pass
        # Only popups opened by this page: with a shared desktop browser, other workers' tabs
        # live in the same context and must never be adopted.
        application_popups = []
        for popup in opened:
            await popup.wait_for_load_state("domcontentloaded", timeout=15000)
            parsed = urlsplit(popup.url)
            if (parsed.hostname or "").endswith(".oraclecloud.com") and parsed.path.endswith(
                "/ircEmailPreview"
            ):
                # Oracle may open an email preview while the original page
                # advances to PIN entry. The preview is neither an application
                # nor an authenticated mailbox; never expose its code to agents.
                await popup.close()
                self.emit(
                    "email_preview_closed",
                    "Kept the application page after the employer opened an email preview",
                )
                continue
            application_popups.append(popup)
        if application_popups:
            if launcher:
                # Come back here when the survey finishes (return_to_opener).
                self.__dict__.setdefault("openers", []).append(opener)
            self.page = application_popups[-1]
            if callback := getattr(self, "on_popup", None):
                await callback(self.page)
            await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        await self.settle(timeout=8)
        after = await self.scan()
        if (
            getattr(self, "openers", None)
            and not after["fields"]
            and SURVEY_DONE.search(after.get("text", ""))
        ):
            await self.return_to_opener()
            await self.settle(timeout=8)
            after = await self.scan()
        education_saved(education_context, after)
        self.last_action = action_result("click", before, after)
        return after

    def evidence_context(self):
        return {
            "job_id": getattr(self.resolver, "job", {}).get("id", ""),
            "run_id": getattr(self.resolver, "run_id", ""),
        }

    async def proof(self):
        host = (urlsplit(self.page.url).hostname or "").lower()
        # Greenhouse can acknowledge a submitted requisition in its document
        # title before React paints the confirmation body. Bind both the board
        # and numeric requisition to the owned job, never a generic success URL.
        expected = urlsplit(str(getattr(self.resolver, "job", {}).get("url") or ""))
        current = urlsplit(self.page.url)
        if (
            host.endswith(".oraclecloud.com")
            and current.scheme == expected.scheme == "https"
            and current.netloc == expected.netloc
            and "/my-profile" in current.path
        ):
            cards = await self.page.locator("li.cs-application-tile:visible").evaluate_all(
                r"""(els,job) => els.flatMap(el => {
                  const links=[...el.querySelectorAll('a[href]')].filter(a => {
                    const u=new URL(a.href);return u.origin===job.origin && u.pathname.replace(/\/$/,'')===job.path;
                  });
                  // Some tenants link the tile elsewhere; its text still shows the requisition number
                  // (Texas Instruments: "Texas Instruments • 25018445 • Applied on 10/14/2026").
                  const jobLinks=[...el.querySelectorAll('a[href]')].filter(a => /\/job\//.test(new URL(a.href).pathname));
                  const byNumber=!jobLinks.length && job.req && new RegExp('(^|\\D)'+job.req+'(\\D|$)').test(el.innerText);
                  if(links.length!==1 && !byNumber)return [];
                  return [{heading:(links[0]||el).textContent.trim().slice(0,200),text:el.innerText,
                    confirm:[...el.querySelectorAll('button')].some(b=>b.getClientRects().length && b.textContent.trim().toLowerCase()==='confirm')}];
                })""",
                {
                    "origin": f"{expected.scheme}://{expected.netloc}",
                    "path": expected.path.rstrip("/"),
                    "req": (re.search(r"/job/(\d{4,})", expected.path) or [None, ""])[1],
                },
            )
            if len(cards) == 1 and not cards[0]["confirm"]:
                # Ford: "Applied on 10/10/2026 ... Status: Under Consideration" (status in a dropdown).
                status = re.search(
                    r"\bStatus:?\s*(Under Consideration|Under Review|Submitted|Application Received|"
                    r"Application/Resume Received)\b",
                    cards[0]["text"].strip(),
                    re.I,
                ) or re.search(r"\b(Applied on \d{1,2}/\d{1,2}/\d{2,4})\b", cards[0]["text"], re.I)
                if status:
                    return {
                        "url": self.page.url,
                        "confirmation": "Application status: " + status[1],
                        "title": await self.page.title(),
                        "type": "explicit_confirmation_page",
                        "evidence_version": 2,
                        **self.evidence_context(),
                        "evidence_selector": "li.cs-application-tile a[href]",
                        "status_region": "job_application_status",
                        "observed_job_heading": cards[0]["heading"],
                    }
        if (
            host in {"job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"}
            and current.scheme == expected.scheme == "https"
            and current.netloc == expected.netloc
            and re.fullmatch(r"/[^/]+/jobs/[0-9]+", expected.path.rstrip("/"))
            and current.path == expected.path.rstrip("/") + "/confirmation"
            and (title := (await self.page.title()).strip()).casefold() == "thank you for applying"
        ):
            return {
                "url": self.page.url,
                "confirmation": title,
                "title": title,
                "type": "explicit_confirmation_page",
                "evidence_version": 2,
                **self.evidence_context(),
                "evidence_selector": "greenhouse_job_confirmation_document_title",
                "status_region": "job_application_status",
            }
        if (
            host in {"job-boards.greenhouse.io", "job-boards.eu.greenhouse.io", "boards.greenhouse.io"}
            and current.scheme == "https"
            and current.path.rstrip("/") == "/embed/job_app/confirmation"
            and (owned := greenhouse_job_ref(expected))
            and owned == (dict(parse_qsl(current.query)).get("for"), dict(parse_qsl(current.query)).get("token"))
        ):
            # Greenhouse redirects an accepted embed POST to this route. Invisible Technologies (2026-10-23..26) draws
            # "Thank you. Your application has been submitted." as an image: the text checks found only "View more
            # jobs" and "Back to job post", the submitted run was reconciled as unsent, and three later runs parked on
            # the same page. The route for this exact board and job, with no application form left, is the receipt.
            try:
                form_left = await self.page.locator(
                    "input[type=file],textarea:visible,button[type=submit]:visible,input[type=submit]:visible"
                ).count()
                body = " ".join((await self.page.locator("body").inner_text(timeout=1500)).split())
            except PlaywrightError:
                form_left, body = 1, ""
            if not form_left:
                said = re.search(r"[^.!?]{0,60}application (?:has been|was) (?:successfully )?(?:submitted|received)",
                                 body, re.I)
                return {
                    "url": urlunsplit(current._replace(query="", fragment="")),
                    "confirmation": said[0].strip()[:300] if said else "Greenhouse confirmation page for this job",
                    "title": await self.page.title(),
                    "type": "explicit_confirmation_page",
                    "evidence_version": 2,
                    **self.evidence_context(),
                    "evidence_selector": "greenhouse_embed_confirmation_route",
                    "status_region": "job_application_status",
                }
        # Employer-hosted Greenhouse forms acknowledge a commit with plain text
        # and no form fields. Fields or a submit/apply control keep it a form.
        job = getattr(self.resolver, "job", {}) or {}
        if str(job.get("ats", "")) == "greenhouse":
            # The embed told its parent page the application was accepted (browser.GREENHOUSE_RECEIPT).
            try:
                confirmed_at = await self.page.evaluate("window.__jpGreenhouseConfirmation || 0")
            except PlaywrightError:
                confirmed_at = 0
            if confirmed_at:
                return {
                    "url": self.page.url,
                    "confirmation": "Greenhouse confirmed the embedded application (greenhouse.confirmation)",
                    "title": await self.page.title(),
                    "type": "explicit_confirmation_page",
                    "evidence_version": 2,
                    **self.evidence_context(),
                    "evidence_selector": "greenhouse_embed_confirmation_message",
                    "status_region": "application_acknowledgement",
                }
        if employer_greenhouse_page_owned(self.page.url, job):
            try:
                body = await self.page.locator("body").inner_text(timeout=1500)
                fields = await self.page.locator("input:visible,textarea:visible,select:visible").count()
                actions = await self.page.locator(
                    "button:visible,input[type=submit]:visible,input[type=button]:visible,[role=button]:visible"
                ).evaluate_all(
                    "els => els.map(e => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim())"
                )
            except PlaywrightError:
                body, fields, actions = "", 1, []
            confirmation = employer_confirmation_text(body)
            if (
                confirmation
                and fields == 0
                and not any(re.search(r"\b(?:submit|apply)\b", action, re.I) for action in actions)
            ):
                return {
                    "url": self.page.url,
                    "confirmation": confirmation,
                    "title": await self.page.title(),
                    "type": "explicit_confirmation_page",
                    "evidence_version": 2,
                    **self.evidence_context(),
                    "evidence_selector": "employer_greenhouse_thank_you",
                    "status_region": "job_application_status",
                }
        if str(job.get("ats", "")) == "successfactors":
            # SuccessFactors' classic career site replaces the form with its success screen after applyJob succeeds:
            # "Back to Job Listings" / "View Profile" (div.rcmSuccessBackToResultsBtn). L3Harris leaves its message area
            # empty, so six submitted applications read as unconfirmed (2026-10-27).
            try:
                success = await self.page.locator("div.rcmSuccessBackToResultsBtn:visible").count()
                editable = await self.page.locator(
                    "input:visible:not([type=hidden]):not([type=search]):not([type=button]):not([type=submit]),"
                    "textarea:visible,select:visible"
                ).count()
            except PlaywrightError:
                success, editable = 0, 1
            if success and not editable:
                return {
                    "url": self.page.url,
                    "confirmation": "SuccessFactors application success screen",
                    "title": await self.page.title(),
                    "type": "explicit_confirmation_page",
                    "evidence_version": 2,
                    **self.evidence_context(),
                    "evidence_selector": "div.rcmSuccessBackToResultsBtn",
                    "status_region": "application_success",
                }
        if str(job.get("ats", "")) == "icims":
            for frame in self.page.frames:
                try:
                    body = await frame.locator("body").inner_text(timeout=1500)
                except PlaywrightError:
                    continue
                if confirmation := icims_receipt(str(job.get("url") or ""), frame.url, body):
                    return {
                        "url": frame.url,
                        "confirmation": confirmation,
                        "title": await self.page.title(),
                        "type": "explicit_confirmation_page",
                        "evidence_version": 2,
                        **self.evidence_context(),
                        "evidence_selector": "icims_job_page_submitted_message",
                        "status_region": "job_application_status",
                    }
        if str(job.get("ats", "")) == "eightfold":
            # Eightfold (Qualcomm 2026-10-17): "Thank you for your application, Alex!" sits in
            # #form-submission-success-message above a star-rating survey whose inputs made the generic
            # check treat the page as an open form. John Deere's identical page was captured.
            # Qualcomm's career site renders it as Eightfold's empty-state title (div.empty-module_empty-title__...).
            # Boston Scientific shows it in a modal dialog over the profile page.
            notice = self.page.locator(
                '#form-submission-success-message:visible,[class*="empty-module_empty-title"]:visible,'
                '[role="dialog"]:visible,[aria-modal="true"]:visible'
            )
            try:
                texts = [" ".join(t.split()) for t in (await notice.all_inner_texts())[:4]]
                text = next(
                    (t for t in texts if re.search(r"thank you for (?:your )?(?:application|applying)", t, re.I)), ""
                )
                if (
                    re.search(r"thank you for (?:your )?(?:application|applying)|application (?:was |has been )?submitted", text, re.I)
                    and not await self.page.get_by_role("button", name=FINAL).count()
                ):
                    return {
                        "url": self.page.url,
                        "confirmation": text[:400],
                        "title": await self.page.title(),
                        "type": "explicit_confirmation_page",
                        "evidence_version": 2,
                        **self.evidence_context(),
                        "evidence_selector": "#form-submission-success-message",
                        "status_region": "application_acknowledgement",
                    }
            except PlaywrightError:
                pass
        if host == "jobs.dayforcehcm.com" and dayforce_tenant(self.page.url) == dayforce_tenant(job.get("url", "")):
            # Dayforce (NCMEC 2026-10-17): "Congratulations, your application has been submitted. The confirmation
            # number is ..." sits outside the generic status regions, so every Dayforce success ended unknown.
            notice = self.page.get_by_text(
                re.compile(r"^\s*Congratulations,\s+your\s+application\s+has\s+been\s+submitted\b", re.I)
            )
            try:
                if (
                    await notice.count()
                    and await notice.first.is_visible()
                    and not await self.page.get_by_role("button", name=FINAL).count()
                ):
                    return {
                        "url": self.page.url,
                        "confirmation": " ".join((await notice.first.inner_text(timeout=1000)).split())[:400],
                        "title": await self.page.title(),
                        "type": "explicit_confirmation_page",
                        "evidence_version": 2,
                        **self.evidence_context(),
                        "evidence_selector": "dayforce_application_submitted",
                        "status_region": "job_application_status",
                    }
            except PlaywrightError:
                pass
        if str(job.get("ats", "")) == "successfactors" and SF_APPLICATION_PAGE.search(self.page.url):
            # EY (SuccessFactors classic) 2026-10-16: "You have successfully submitted your application to
            # this role." on the career page once the form is gone.
            try:
                state = await self.page.evaluate(
                    """() => {
                      const shown = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
                      const fields = [...document.querySelectorAll('input:not([type=hidden]),textarea,select')]
                        .filter(e => shown(e) && !/search|keyword|location/i.test(
                          [e.type, e.name, e.id, e.placeholder, e.getAttribute('aria-label')].join(' ')));
                      const apply = [...document.querySelectorAll('button,input[type=submit],a')]
                        .some(e => shown(e) && /^\\s*apply\\s*$/i.test(e.innerText || e.value || ''));
                      return {text: document.body.innerText.slice(0, 20000), fields: fields.length, apply};
                    }"""
                )
            except PlaywrightError:
                state = {"text": "", "fields": 1, "apply": True}
            found = SF_CONFIRMATION_RE.search(" ".join(state["text"].split()))
            if found and not state["fields"] and not state["apply"]:
                return {
                    "url": self.page.url,
                    "confirmation": found[0],
                    "title": await self.page.title(),
                    "type": "explicit_confirmation_page",
                    "evidence_version": 2,
                    **self.evidence_context(),
                    "evidence_selector": "successfactors_confirmation_text",
                    "status_region": "job_application_status",
                }
        if (
            host.endswith((".myworkdayjobs.com", ".myworkdaysite.com"))
            and "/job/" in urlsplit(self.page.url).path
        ):
            notice = self.page.get_by_text(
                re.compile(
                    r"^You(?: have|['’]ve) already applied (?:for|to) this (?:job|position)[.!]?$", re.I
                )
            )
            if (
                await notice.count() == 1
                and await notice.is_visible()
                and not await self.page.locator("input:visible,textarea:visible").count()
                and await self.page.get_by_role("heading").count()
            ):
                return {
                    "url": self.page.url,
                    "confirmation": (await notice.inner_text()).strip(),
                    "title": await self.page.title(),
                    "type": "explicit_confirmation_page",
                    "evidence_version": 2,
                    **self.evidence_context(),
                    "evidence_selector": "employer_already_applied_notice",
                    "status_region": "job_application_status",
                    "existing_application_notice": True,
                    "observed_job_heading": (
                        await self.page.get_by_role("heading").last.inner_text()
                    ).strip()[:200],
                }
        if host.endswith((".myworkdayjobs.com", ".myworkdaysite.com")):
            # Workday announces a submitted application in a modal over the job
            # search page ("Application Submitted", or ZT's "Congratulations! Your
            # application was submitted successfully" with an optional account
            # form). The page behind it has inputs, so the generic check skipped
            # it and HPE, Visa, Booz Allen, BlackRock, ZT... ended as unknown.
            for dialog in await self.page.locator(
                '[data-automation-id="popUpDialog"]:visible,[role="dialog"]:visible,[aria-modal="true"]:visible'
            ).all():
                try:
                    text = " ".join((await dialog.inner_text(timeout=1000)).split())
                except Exception:
                    continue
                if (
                    len(text) < 3000
                    # A close control or icon label can precede the heading.
                    and re.match(
                        r"(?:\W*\w+){0,3}?\W*(?:application submitted|congratulations)\b", text, re.I
                    )
                    and re.search(
                        r"application (?:was |has )?(?:successfully )?(?:been )?(?:successfully )?submitted|"
                        r"submitted successfully|"
                        # Adobe: "Congratulations! You've successfully submitted your application" (the same
                        # dialog holds a create-account form, so the generic no-inputs rule never reads it).
                        r"(?:successfully )?submitted your application|"
                        # Chewy: "Congratulations! We have received your application".
                        r"(?:we )?(?:have |'ve )?received your application|"
                        r"thank you for (?:applying|submitting)",
                        text,
                        re.I,
                    )
                ):
                    return {
                        "url": self.page.url,
                        "confirmation": text[:300],
                        "title": await self.page.title(),
                        "type": "explicit_confirmation_page",
                        "evidence_version": 2,
                        **self.evidence_context(),
                        "evidence_selector": "workday_application_submitted_dialog",
                        "status_region": "application_success",
                    }
            # Workday's own completion route for this tenant (LSEG ended here as unknown).
            if (
                re.search(r"/jobTasks/completed/application/?$", current.path)
                and current.netloc == expected.netloc
            ):
                completed = re.compile(
                    r"application (?:was |has been )?(?:successfully )?(?:submitted|received)|"
                    r"submitted successfully|thank you for (?:applying|submitting)",
                    re.I,
                )
                body = " ".join((await self.page.locator("body").inner_text()).split())
                for _ in range(8):
                    # Medtronic and CACI (2026-10-18) were read while the route still showed only its header and
                    # footer; the completion message renders a few seconds later.
                    if completed.search(body):
                        break
                    await self.page.wait_for_timeout(1000)
                    body = " ".join((await self.page.locator("body").inner_text()).split())
                if completed.search(body):
                    return {
                        "url": self.page.url,
                        "confirmation": "Workday application completion page",
                        "title": await self.page.title(),
                        "type": "explicit_confirmation_page",
                        "evidence_version": 2,
                        **self.evidence_context(),
                        "evidence_selector": "workday_completed_application_route",
                        "status_region": "application_success",
                    }
            # Clayton: after submit Workday lands on Candidate Home, whose card for this job says
            # "You applied for this job on October 7, 2026." Only today's date on the card titled
            # with this job counts; an older application's card is not a new receipt.
            title = str(getattr(self.resolver, "job", {}).get("title") or "").strip()
            cards = await self.page.evaluate(
                r"""() => [...document.querySelectorAll('body *')].filter(e => !e.children.length
                  && /^You applied for this job on [A-Z][a-z]+ \d{1,2}, \d{4}\.?$/.test((e.innerText||'').trim())
                  && e.getClientRects().length).map(e => {
                    let n = e, heading = '';
                    for (let i = 0; i < 5 && n && !heading; i++, n = n.parentElement)
                      heading = (n.querySelector('h1,h2,h3,h4,[role=heading]')?.innerText || '').trim();
                    return {notice: e.innerText.trim(), heading};
                  })"""
            )
            for card in cards:
                when = re.search(r"on ([A-Z][a-z]+ \d{1,2}, \d{4})", card["notice"])
                try:
                    applied = datetime.strptime(when[1], "%B %d, %Y").date() if when else None
                except ValueError:
                    applied = None
                fresh = applied and abs((datetime.now(UTC).date() - applied).days) <= 1
                if (
                    fresh
                    and title
                    and " ".join(card["heading"].split()).casefold() == " ".join(title.split()).casefold()
                ):
                    return {
                        "url": self.page.url,
                        "confirmation": card["notice"][:200],
                        "title": await self.page.title(),
                        "type": "explicit_confirmation_page",
                        "evidence_version": 2,
                        **self.evidence_context(),
                        "evidence_selector": "workday_candidate_home_applied_card",
                        "status_region": "job_application_status",
                        "observed_job_heading": card["heading"][:200],
                    }
        # Ashby lets employers customize the success copy (e.g. "Thanks,
        # got it!"). Its application-success component is stronger evidence
        # than a generic Success heading or a phrase in the job description.
        for frame in self.page.frames:
            if (urlsplit(frame.url).hostname or "").lower() != "jobs.ashbyhq.com":
                continue
            success = frame.locator(".ashby-application-form-success-container:visible")
            if await success.count() == 1:
                text = (await success.inner_text()).strip()
                if (
                    text
                    and not await frame.locator(
                        ".ashby-application-form input:visible,.ashby-application-form textarea:visible"
                    ).count()
                ):
                    return {
                        "url": self.page.url,
                        "confirmation": text[:500],
                        "title": await self.page.title(),
                        "type": "explicit_confirmation_page",
                        "evidence_selector": ".ashby-application-form-success-container",
                        "evidence_version": 2,
                        **self.evidence_context(),
                        "status_region": "application_success",
                        "frame_url": frame.url,
                    }
        pattern = (
            r"(?:thank you|thanks) for (?:applying|your (?:job )?(?:application|submission))|"
            r"(?:thank you|thanks) for (?:submitting|sending) your (?:job )?application|"
            r"application (?:has |was )?(?:successfully )?(?:been )?(?:successfully )?(?:submitted|received|sent)|"
            r"we(?:'ve| have)? received your application|successfully (?:applied|submitted)|"
            r"thank you for sending us your online job submission|"
            r"your application (?:is |has been |was )?(?:complete|submitted|received|sent|on its way)|"
            r"your application is in\b|"
            r"(?:^|(?<=[.!?\n]))\s*you(?:'ve| have) (?:successfully )?applied|"
            # German, French and Spanish career sites (Sopra Steria 2026-10-26: "Bewerbung eingereicht!").
            + LOCALIZED_CONFIRMATION
        )
        # A phrase in a help block, description or still-editable form cannot
        # establish that this application was accepted. Observe an actual status
        # region after the application controls have disappeared.
        regions = (
            '[data-automation-id="applicationSubmittedPage"],'
            '[data-automation-id="applicationSuccess"],'
            '[data-automation-id="applicationConfirmation"],'
            ".application-success,.application-confirmation,.application-confirmation-container,"
            "#rcmJobApplicationCtr #applyConfirmMsg,"
            '#application-confirmation,#success_message,[role="status"],[role="alert"],h1,h2,h3,p,body'
        )
        for frame in await self.application_frames():
            editable = frame.locator(
                "input:visible:not([type=hidden]):not([type=search]):not([type=button]):not([type=submit]),"
                "textarea:visible,select:visible,[contenteditable=true]:visible"
            )
            try:
                if await editable.count():
                    continue
                if await frame.get_by_role("button", name=FINAL).count():
                    continue
                regions_found = await frame.locator(regions).all()
            except PlaywrightError:
                # Ford (Oracle) 2026-10-15: the submit navigated and detached this frame mid-check;
                # a vanished frame holds no proof, and the next poll reads the new document.
                continue
            for region in regions_found:
                try:
                    if not await region.is_visible():
                        continue
                    excluded = await region.evaluate(r"""e => !!e.closest(
                      '[data-automation-id="jobPostingDescription"],.job-description,.jobDescription,'+
                      '.help,.faq,blockquote,details,[data-help],[data-testid*=description]')""")
                    if excluded:
                        continue
                    text = (await region.inner_text(timeout=1000)).strip()
                except PlaywrightError:
                    continue  # The page re-rendered after a submit (Uber Oracle 2026-10-14).
                if not text or len(text) > 1800:
                    continue
                match = re.search(pattern, text, re.IGNORECASE)
                if not match or re.search(
                    r"\b(?:password|activation|verify your (?:account|email))\b", text, re.I
                ):
                    continue
                # Hedges disqualify only the acknowledgement's own sentence: "Thank you for
                # submitting your application! We are delighted that you would consider
                # joining" (Perpay) is a real receipt.
                start = max(text.rfind(c, 0, match.start()) for c in ".!?\n") + 1
                ends = [i for i in (text.find(c, match.end()) for c in ".!?\n") if i >= 0]
                sentence = text[start : min(ends) if ends else len(text)]
                if re.search(
                    r"\b(?:would|should|example|display|after applying|when you apply)\b", sentence, re.I
                ):
                    continue
                if re.search(r"\bwill\b", text[: match.start()], re.I):
                    continue  # Expected future display, not a current acknowledgement.
                # Plain paragraphs/headings must themselves be acknowledgement
                # statements, rather than prose that quotes an expected result.
                tag = await region.evaluate("e=>e.tagName")
                if tag == "BODY" and await region.evaluate("e=>e.children.length>0"):
                    continue
                if tag in {"H1", "H2", "H3", "P", "BODY"} and match.start() > 40:
                    continue
                selector = await region.evaluate(
                    "e=>e.dataset.automationId ? '[data-automation-id='+e.dataset.automationId+']' : "
                    "e.id ? '#'+e.id : e.getAttribute('role') ? '[role='+e.getAttribute('role')+']' : e.tagName.toLowerCase()"
                )
                parsed = urlsplit(self.page.url)
                return {
                    "url": urlunsplit(parsed._replace(query="", fragment="")),
                    "confirmation": text[:700],
                    "title": await self.page.title(),
                    "type": "explicit_confirmation_page",
                    "evidence_version": 2,
                    **self.evidence_context(),
                    "evidence_selector": selector,
                    "status_region": "application_acknowledgement",
                }
        return None

    @staticmethod
    def inline_field_errors(obs):
        # Many forms report errors beside the fields, without a rejection banner.
        # Greenhouse's phone widget prints "Phone number is too short" after
        # a refused submit without marking the input invalid.
        return [
            f["label"]
            for f in obs["fields"]
            if f.get("error")
            and (
                not f.get("valid", True)
                or re.search(
                    r"\btoo (?:short|long)\b|\binvalid\b|\bis required\b|enter a valid|must be",
                    str(f["error"]),
                    re.I,
                )
            )
        ]

    async def rejection(self):
        text = await self._page_text()
        match = re.search(
            r"(?:we )?(?:couldn.t|could not|unable to) submit your application|"
            r"application submission was (?:flagged|rejected)|"
            r"your form needs corrections|missing entry for required field|"
            r"your application (?:could not|couldn.t) be submitted|"
            r"there was an error verifying your application\. please try again|"
            # Dayforce's toast after a refused Submit (York 2026-10-16); its retry was accepted.
            r"your application wasn.t submitted\. try again later|"
            # SuccessFactors keeps its application page and lists field errors under the banner
            # ("End date must be after Start date.", SAP 2026-10-16): nothing was submitted.
            r"please correct the errors? below|"
            # SmartRecruiters' refusal dialogs (oneclick-ui showApplicationErrorMessage, REA 2026-10-18). Six runs
            # ended "unknown" because "Cannot submit" matched none of the phrases above.
            r"cannot submit your application|problem saving answers to preliminary question|this job ad has expired|"
            # Its client-side check: a required preliminary question left empty never POSTs at all.
            r"please make sure that you answered all questions marked as required|"
            # JazzHR re-renders the form with this banner (Axtria 2026-10-25); Workday's review page lists "Errors
            # Found" with this hint while its finalize call answers 200 (Airbus 2026-10-25). Nothing was submitted.
            r"please fix the highlighted errors and try again|answer all required questions to submit this application|"
            # Workable's banner with HTTP 412 from /api/v1/jobs/<code>/apply when it throttles a candidate
            # (2026-10-23): nothing was accepted, and the same application went through later.
            r"something went wrong\. we are working on this,? please try again later",
            text,
            re.I,
        )
        if match:
            missing = re.findall(r"Missing entry for required field:\s*([^\n]+)", text, re.I)
            corrections = "correct the error" in match.group().lower()
            phrase = match.group().lower()
            tail = text[match.end() : match.end() + 600]
            if "expired" in phrase:
                return {"type": "explicit_rejection_page", "url": self.page.url, "category": "closed",
                        "evidence": text[max(0, match.start() - 20) : match.end() + 200], "missing_fields": []}
            from .application_pacing import application_cap

            if cap := application_cap(tail):
                # The employer's own application limit (Benchling, Aurora, OpenAI on Ashby): nothing was accepted.
                return {"type": "explicit_rejection_page", "url": self.page.url, "category": "employer_limit",
                        "cap": cap, "evidence": text[max(0, match.start() - 20) : match.end() + 300],
                        "missing_fields": []}
            if re.search(r"answered all questions|highlighted errors|answer all required questions", phrase):
                return {"type": "explicit_rejection_page", "url": self.page.url, "category": "validation",
                        "evidence": text[max(0, match.start() - 20) : match.end() + 200],
                        "missing_fields": self.inline_field_errors(await self.scan())[:10]}
            if "problem saving answers" in phrase or (
                "cannot submit" in phrase and re.match(r"[^\n]{0,10}due to validation issues", tail, re.I)
            ):
                # SmartRecruiters lists the refused sections under the message ("Date ranges in education
                # section", "Please provide your first name"); the answers variant names none.
                sections = [
                    line.strip()
                    for line in re.split(r"\n+", tail.split("listed below", 1)[-1])[1:8]
                    if 3 < len(line.strip()) < 120 and not re.search(r"^(?:ok|close|back|submit)$", line.strip(), re.I)
                ]
                return {"type": "explicit_rejection_page", "url": self.page.url, "category": "validation",
                        "evidence": text[max(0, match.start() - 20) : match.end() + 400],
                        "missing_fields": sections[:10]}
            if "cannot submit" in phrase or "we are working on this" in phrase:
                # "Cannot submit your application. Please try again in a while." Nothing was accepted.
                return {"type": "explicit_rejection_page", "url": self.page.url, "category": "temporary",
                        "evidence": text[max(0, match.start() - 20) : match.end() + 200], "missing_fields": []}
            if missing or corrections:
                # A banner names only some of the fields it refused. An inline error that
                # arrives with the same refusal must change this rejection, or an identical
                # banner from before the commit would hide it.
                missing = list(dict.fromkeys(missing + self.inline_field_errors(await self.scan())))
            if corrections and not missing:
                # Name the fields from SuccessFactors' sentences only when they are observed labels.
                labels = {normalize(f["label"]): f["label"] for f in self.fields.values()}
                missing = list(
                    dict.fromkeys(
                        labels[normalize(subject)]
                        for subject in re.findall(
                            r"(?m)^\s*([A-Z][A-Za-z /&-]{2,40}?) (?:must be|is required|is invalid|cannot be)\b",
                            text,
                        )
                        if normalize(subject) in labels
                    )
                )
            return {
                "type": "explicit_rejection_page",
                "url": self.page.url,
                "evidence": text[max(0, match.start() - 20) : match.end() + 400],
                "category": "validation"
                if missing or corrections
                else (
                    "security"
                    if re.search(r"error verifying your application|wasn.t submitted\. try again", match.group(), re.I)
                    else "rejection"
                ),
                "missing_fields": missing[:20],
            }
        # Many forms report errors beside the fields, without a rejection banner.
        # Only explicit website validation is evidence; an empty field alone is
        # not enough to replay a submission.
        obs = await self.scan()
        missing = self.inline_field_errors(obs)
        if not missing and re.search(r"\b(?:this field|field) is required\b|\bis required\.", text, re.I):
            # Greenhouse keeps the form and prints "This field is required." beside a
            # hidden upload whose input carries no error. A still-empty required field
            # with that notice is a client-side refusal, not an uncertain submission.
            missing = [
                f["label"]
                for f in obs["fields"]
                if f.get("required") and not has_value(f) and f["type"] not in {"hidden", "password"}
            ]
        if not missing and re.search(r"\bcannot be left blank\b|\b\d+ errors? found\b", text, re.I):
            # Starbucks (Eightfold) 2026-10-14: Submit left the form with "Work Opportunity Tax
            # Credit (WOTC)* 1 error found ... This field cannot be left blank." The step is a
            # survey button, not a field, so name it from the text before the error count.
            step = re.search(r"([^\n]{3,80})\n\s*\d+ errors? found", text, re.I)
            missing = [
                step.group(1).strip().rstrip("*").strip()
                if step
                else "Required step reported by the employer"
            ]
        if missing:
            return {
                "type": "explicit_validation_rejection",
                "category": "validation",
                "url": self.page.url,
                "missing_fields": list(dict.fromkeys(missing))[:20],
            }
        return None

    async def repair_validation(self, rejection):
        """Only repair a website's explicit missing-field response; silence is never retryable."""
        names = {normalize(q) for q in rejection.get("missing_fields", [])}
        if rejection.get("category") != "validation" or not names:
            return None
        await self.scan()
        targets = [f for f in self.fields.values() if normalize(f["label"]) in names]
        if {normalize(f["label"]) for f in targets} != names:
            return None  # Cannot repair a question that has not actually been observed.
        before = {normalize(f["label"]): f["value"] for f in targets}
        # Reassert rejected choices through their handlers even if their visual
        # state already looks selected. The server rejected that state.
        repaired = False
        resume_rejected = False
        for target in targets:
            if target["type"] == "file":
                if any(f["id"] == target["id"] for f in self.resume_fields()):
                    self.uploaded = self.upload_verified = self.workday_resume_replaced = False
                    if not self.workday:
                        await self.locator(target["id"]).set_input_files([], timeout=5000)
                    resume_rejected = True
                continue  # File uploads are handled by fill_current, never answer resolution.
            answers = await self.resolver.resolve([target])
            if not answers:
                continue
            answer = answers[0]
            AnswerModelUnavailable.raise_for(answer)
            if answer.disposition != "answer":
                continue
            try:
                if target["type"] in {"buttonchoice", "radio", "radiogroup"}:
                    alternative = next((o for o in target["options"] if o != answer.value), None)
                    if alternative:
                        await self.fill(target, alternative)
                elif target["type"] == "checkbox" and await self.locator(target["id"]).is_checked():
                    await self.set_checked(self.locator(target["id"]), False)
                elif target["type"] == "checkboxgroup":
                    # A checked native control can disagree with the site's
                    # saved form state. An explicit rejection requires a real
                    # change event, not a no-op set_checked(True).
                    for checkbox in await self.locator(target["id"]).locator('input[type="checkbox"]').all():
                        if await checkbox.is_checked():
                            await self.set_checked(checkbox, False)
                await self.fill(await self.rematch(target), answer.value)
                repaired = True
            except AnswerModelUnavailable:
                raise
            except Exception as exc:
                self.emit(
                    "validation_repair",
                    "Rejected field could not be reasserted",
                    {"field": target["label"], "error_type": type(exc).__name__},
                )
        report = await self.fill_current(accept_prefilled=True)
        changed = any(
            f["value"] != before[normalize(f["label"])]
            for f in self.fields.values()
            if normalize(f["label"]) in before
        )
        return {**report, "changed": changed or repaired or (resume_rejected and report["resume_attached"])}
