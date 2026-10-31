"""Voluntary questionnaires: optional modules of questions the applicant may answer once, ahead of time.

Answers are facts on the profile (`profile.facts`), the store every other fact already lives in (it is migrated into
the versioned knowledge store like the rest), so the resolver reads them through the same `fact:` keys. Nothing here
is required to apply: an unanswered question simply keeps being asked when an application needs it.

A fact exists only after the applicant saved it. Suggestions (for example "No" to each tax-credit question) are shown
for the applicant to confirm and are never used by the resolver on their own.

Modules:
- wotc: the statutory Work Opportunity Tax Credit screening questions (one fact per question).
- contacts: supervisors per work-history row and up to three references.
- faq: the owner-answerable questions applications most often stop on (production Needs-you statistics, 2026-10-29).
"""

import re
from dataclasses import dataclass, field
from datetime import date

from ...db import Setting

YES_NO = ("Yes", "No")
MAY_CONTACT = (
    ("yes", "Yes, at any time"),
    ("after_offer", "Only after an offer"),
    ("no", "No"),
)
REFERENCE_MAX = 3


@dataclass(frozen=True)
class Question:
    key: str  # The profile fact the answer is stored under; the resolver reads this key.
    prompt: str
    type: str  # yes_no | choice | text | date | work_rows | references
    options: tuple = ()
    help: str = ""
    suggestion: object = None  # Shown to the applicant to confirm; never used until saved.
    section: str = ""
    fields: tuple = ()  # work_rows / references: the sub-fields (key, prompt, type, options)


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    purpose: str
    questions: tuple = field(default_factory=tuple)


def q(key, prompt, type="yes_no", **kw):
    return Question(key, prompt, type, **kw)


# ---------------------------------------------------------------------------------------------------------------
# Tax-credit (WOTC) screening
# ---------------------------------------------------------------------------------------------------------------

WOTC_HELP = (
    "Employers screen every applicant for the federal Work Opportunity Tax Credit. Most people answer No to all of it."
)
WOTC_QUESTIONS = (
    q("wotc_snap_6mo", "In the past 6 months, did you receive SNAP (food stamp) benefits?", suggestion=False),
    q("wotc_tanf_2yr", "In the past 2 years, did you or your family receive TANF (cash welfare)?", suggestion=False),
    q(
        "wotc_tanf_9_of_18",
        "Did your family receive TANF for at least 9 of the past 18 months?",
        suggestion=False,
    ),
    q("wotc_tanf_18mo", "Did your family receive TANF for at least 18 months in a row?", suggestion=False),
    q(
        "wotc_tanf_stopped",
        "Did your family stop being eligible for TANF within the past 2 years because of a time limit?",
        suggestion=False,
    ),
    q(
        "wotc_veteran_snap",
        "Are you a veteran in a family that received SNAP benefits for at least 3 months in the past 15 months?",
        suggestion=False,
    ),
    q(
        "wotc_veteran_disability",
        "Are you a veteran with a service-connected disability?",
        suggestion=False,
    ),
    q(
        "wotc_veteran_unemployed",
        "Are you a veteran who was unemployed for 4 weeks or more in the past year?",
        suggestion=False,
    ),
    q(
        "wotc_unemployed_27wk",
        "Were you unemployed for 27 weeks or more and receiving unemployment compensation?",
        suggestion=False,
    ),
    q(
        "wotc_ssi_60d",
        "In the past 60 days, did you receive Supplemental Security Income (SSI)?",
        suggestion=False,
    ),
    q(
        "wotc_voc_rehab",
        "Did you complete vocational rehabilitation, or hold a Ticket to Work?",
        suggestion=False,
    ),
    q(
        "wotc_felony",
        "Were you convicted of a felony, or released from prison, within the past year?",
        suggestion=False,
    ),
    q(
        "wotc_empowerment_zone",
        "Do you live in a designated community: an empowerment zone or rural renewal county?",
        help="Employers look this up by home address; answer No if you are not sure.",
        suggestion=False,
    ),
    q(
        "wotc_swa_certification",
        "Do you hold a conditional certification from a state workforce agency?",
        suggestion=False,
    ),
    q("wotc_summer_youth", "Are you a summer youth employee (age 16 or 17) living in an empowerment zone?", suggestion=False),
)
WOTC = Module(
    "wotc",
    "Tax-credit screening",
    "The standard Work Opportunity Tax Credit questions many employers ask. Saved answers fill them automatically.",
    tuple(Question(**{**x.__dict__, "help": x.help or WOTC_HELP}) for x in WOTC_QUESTIONS),
)

# ---------------------------------------------------------------------------------------------------------------
# Supervisors and references
# ---------------------------------------------------------------------------------------------------------------

SUPERVISOR_FIELDS = (
    ("name", "Supervisor name", "text", ()),
    ("title", "Supervisor title", "text", ()),
    ("phone", "Supervisor phone", "text", ()),
    ("email", "Supervisor email", "text", ()),
    ("may_contact", "May we contact this supervisor?", "choice", tuple(v for v, _ in MAY_CONTACT)),
)
REFERENCE_FIELDS = (
    ("name", "Full name", "text", ()),
    ("relationship", "Relationship", "text", ()),
    ("company", "Company", "text", ()),
    ("phone", "Phone", "text", ()),
    ("email", "Email", "text", ()),
)
CONTACTS = Module(
    "contacts",
    "Supervisors and references",
    "Who to name when an application asks for a supervisor or references. Never invented: unanswered stays a question.",
    (
        q(
            "work_supervisors",
            "Supervisor for each job in your work history",
            "work_rows",
            fields=SUPERVISOR_FIELDS,
            help="Fills Supervisor name, title, phone and the 'may we contact' question in the matching job.",
        ),
        q(
            "references",
            "References (up to 3)",
            "references",
            fields=REFERENCE_FIELDS,
            help="Fills Reference name, relationship, company, phone and email in order.",
        ),
    ),
)

# ---------------------------------------------------------------------------------------------------------------
# Frequently asked (production Needs-you statistics)
# ---------------------------------------------------------------------------------------------------------------

FAQ = Module(
    "faq",
    "Frequently asked",
    "The questions applications most often stop on. Answer once and they are filled everywhere.",
    (
        q("start_date", "Earliest date you can start", "date", section="Availability"),
        q(
            "notice_period",
            "Notice period",
            "text",
            help="For example: Immediately, 2 weeks, 1 month.",
            section="Availability",
        ),
        q("willing_to_relocate", "Are you willing to relocate?", section="Location and travel"),
        q(
            "requires_relocation_assistance",
            "Do you need relocation assistance?",
            section="Location and travel",
        ),
        q("willing_to_travel", "Are you willing to travel for work?", section="Location and travel"),
        q(
            "willing_onsite",
            "Are you willing to work on-site or hybrid at the job's office?",
            section="Location and travel",
        ),
        q(
            "previously_employed_here",
            "Have you ever worked for an employer you are applying to (other than those in your work history)?",
            help="Answered only when the employer is not one in your work history.",
            suggestion=False,
            section="Employment relationships",
        ),
        q(
            "has_relatives_at_employer",
            "Do you have relatives or close friends who work at the employers you apply to?",
            suggestion=False,
            section="Employment relationships",
        ),
        q(
            "government_employment_history",
            "Have you ever worked for a government or the military?",
            section="Employment relationships",
        ),
        q(
            "has_non_compete",
            "Are you bound by a non-compete or non-solicitation agreement?",
            suggestion=False,
            section="Employment relationships",
        ),
        q("criminal_conviction", "Have you ever been convicted of a crime?", section="Background"),
        q("has_pending_charges", "Do you have criminal charges currently pending?", section="Background"),
        q("ever_fired", "Have you ever been fired or asked to resign?", section="Background"),
        q(
            "professional_sanctions",
            "Have you ever been found responsible of professional misconduct or sanctioned by a regulator?",
            section="Background",
        ),
        q("ever_bonded", "Have you ever been bonded?", section="Background"),
        q("ever_refused_bond", "Have you ever been refused a bond?", section="Background"),
        q("valid_drivers_license", "Do you hold a valid driver's license?", section="Other"),
        q("has_security_clearance", "Do you hold an active security clearance?", section="Other"),
        q("uses_nicotine", "Do you use nicotine or tobacco products?", section="Other"),
        q("has_intellectual_property", "Do you own intellectual property (patents, inventions)?", section="Other"),
        q(
            "ai_screening_consent",
            "Do you consent to AI being used to evaluate your application?",
            section="Consents",
        ),
        q(
            "prior_military_service",
            "Do you have prior or current service in the U.S. Armed Forces?",
            section="Other",
        ),
    ),
)

MODULES = (WOTC, CONTACTS, FAQ)
BY_ID = {m.id: m for m in MODULES}


def owned_fact_keys():
    """Profile fact keys the questionnaires write."""
    return {question.key for module in MODULES for question in module.questions}


# ---------------------------------------------------------------------------------------------------------------
# Matching a form question to a WOTC question
# ---------------------------------------------------------------------------------------------------------------

_VETERAN = re.compile(r"\bveterans?\b|armed forces|military", re.I)
_ASSIST = re.compile(
    r"\bTANF\b|temporary assistance for needy|government assistance|public assistance|welfare|cash assistance", re.I
)
# (fact key, every pattern must match the label); first rule wins, so the narrower variants come first.
WOTC_RULES = (
    ("wotc_veteran_snap", (_VETERAN, re.compile(r"\bSNAP\b|food stamps|supplemental nutrition", re.I))),
    ("wotc_veteran_disability", (_VETERAN, re.compile(r"disab", re.I))),
    ("wotc_veteran_unemployed", (_VETERAN, re.compile(r"unemploy", re.I))),
    ("wotc_tanf_9_of_18", (_ASSIST, re.compile(r"\b(?:9|nine)\b.{0,30}months", re.I))),
    ("wotc_tanf_18mo", (_ASSIST, re.compile(r"\b(?:18|eighteen)\s*(?:consecutive )?months", re.I))),
    (
        "wotc_tanf_stopped",
        (_ASSIST, re.compile(r"stopped|ceased|no longer eligible|time limit|federal or state limit", re.I)),
    ),
    ("wotc_tanf_2yr", (_ASSIST,)),
    ("wotc_snap_6mo", (re.compile(r"\bSNAP\b|food stamps|supplemental nutrition", re.I),)),
    ("wotc_unemployed_27wk", (re.compile(r"unemploy", re.I),)),
    ("wotc_ssi_60d", (re.compile(r"supplemental security income|\bSSI\b", re.I),)),
    ("wotc_voc_rehab", (re.compile(r"vocational rehabilitation|ticket to work|employment network", re.I),)),
    ("wotc_felony", (re.compile(r"\bfelony\b|convicted|released from prison", re.I),)),
    (
        "wotc_empowerment_zone",
        (re.compile(r"empowerment zone|rural renewal|renewal communit|designated communit", re.I),),
    ),
    ("wotc_swa_certification", (re.compile(r"state workforce agency|\bSWA\b|conditional certification", re.I),)),
    ("wotc_summer_youth", (re.compile(r"summer youth", re.I),)),
)


def wotc_key(label):
    """The WOTC question a form question asks, or None when it names none of the known ones."""
    text = str(label or "")
    for key, patterns in WOTC_RULES:
        if all(p.search(text) for p in patterns):
            return key
    return None


def as_bool(value):
    """A stored yes/no in any of the shapes facts have held it (True, "No", "No (confirmed by applicant)")."""
    if isinstance(value, bool):
        return value
    match = re.match(r"\s*(yes|no|true|false)\b", str(value or ""), re.I)
    return None if not match else match[1].lower() in {"yes", "true"}


def wotc_value(facts, label):
    """(answer, evidence id) a saved screening answer gives this tax-credit question, or None.

    The question's own saved answer decides; the older blanket fact `tax_credit_screening_no` (the applicant said No
    to the whole screener) covers any statutory question without one."""
    key = wotc_key(label)
    if key and (value := as_bool((facts or {}).get(key))) is not None:
        return value, "fact:" + key
    if (facts or {}).get("tax_credit_screening_no") is True:
        return False, "fact:tax_credit_screening_no"
    return None


# ---------------------------------------------------------------------------------------------------------------
# Reading and saving
# ---------------------------------------------------------------------------------------------------------------

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _text(value, limit=200):
    text = " ".join(str(value).split())
    if len(text) > limit:
        raise ValueError(f"Keep answers under {limit} characters")
    return text


def _date(value):
    text = str(value).strip()
    for pattern, order in ((r"(\d{4})-(\d{1,2})-(\d{1,2})", "ymd"), (r"(\d{1,2})/(\d{1,2})/(\d{4})", "mdy")):
        if m := re.fullmatch(pattern, text):
            y, mo, d = (int(m[i + 1]) for i in ({"ymd": (0, 1, 2), "mdy": (2, 0, 1)}[order]))
            try:
                return date(y, mo, d).isoformat()
            except ValueError:
                break
    raise ValueError("Enter a date as YYYY-MM-DD")


def clean(question, value):
    """The value to store for one answer, or None to clear it. Raises ValueError for an unusable value."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if question.type == "yes_no":
        answer = as_bool(value)
        if answer is None:
            raise ValueError(f"Answer Yes or No: {question.prompt}")
        return answer
    if question.type == "choice":
        match = next((o for o in question.options if str(o).casefold() == str(value).strip().casefold()), None)
        if match is None:
            raise ValueError(f"Choose one of: {', '.join(map(str, question.options))}")
        return match
    if question.type == "date":
        return _date(value)
    return _text(value)


def clean_entry(fields, value):
    """One supervisor or reference: known sub-fields only, empty ones dropped; {} when nothing is filled."""
    if not isinstance(value, dict):
        raise ValueError("Expected an object of fields")
    unknown = set(value) - {f[0] for f in fields}
    if unknown:
        raise ValueError("Unknown field: " + ", ".join(sorted(unknown)))
    out = {}
    for key, prompt, kind, options in fields:
        if key not in value:
            continue
        sub = Question(key, prompt, kind, options)
        if (cleaned := clean(sub, value[key])) is not None:
            if key == "email" and not EMAIL.match(cleaned):
                raise ValueError("Enter a valid email address")
            out[key] = cleaned
    return out


def work_row_id(row):
    from .migration import row_id

    return row_id("exp", row, ("company", "title", "start"))


def work_rows(profile):
    """The profile's work-history rows with the stable id each supervisor answer is stored under."""
    rows = []
    for i, w in enumerate(profile.get("work") or []):
        rows.append(
            {
                "row_id": work_row_id(w),
                "index": i,
                "company": w.get("company", ""),
                "title": w.get("title", ""),
                "start": w.get("start", ""),
                "end": w.get("end", ""),
                "current": bool(w.get("current")),
            }
        )
    return rows


def effective(profile, question):
    """The saved answer for a question, or None."""
    facts = profile.get("facts") or {}
    if question.type == "yes_no":
        value = as_bool(facts.get(question.key))
        if value is None and question.key.startswith("wotc_") and facts.get("tax_credit_screening_no") is True:
            return False  # The applicant said No to the whole screener before the per-question answers existed.
        return value
    value = facts.get(question.key)
    return None if value in (None, "", [], {}) else value


def module_view(profile, module):
    facts = profile.get("facts") or {}
    rows = work_rows(profile)
    questions, answered, total = [], 0, 0
    for item in module.questions:
        view = {
            "key": item.key,
            "prompt": item.prompt,
            "type": item.type,
            "options": list(item.options),
            "help": item.help,
            "suggestion": item.suggestion,
            "section": item.section,
        }
        if item.type == "work_rows":
            saved = facts.get("work_supervisors") or {}
            view["fields"] = [_field_view(f) for f in item.fields]
            view["rows"] = [{**r, "answer": saved.get(r["row_id"]) or {}} for r in rows]
            view["option_labels"] = {"may_contact": dict(MAY_CONTACT)}
            n = sum(1 for r in view["rows"] if r["answer"])
            answered, total = answered + n, total + len(rows)
            view["answered"] = n > 0
        elif item.type == "references":
            saved = [r for r in (facts.get("references") or []) if r][:REFERENCE_MAX]
            view["fields"] = [_field_view(f) for f in item.fields]
            view["max"] = REFERENCE_MAX
            view["answer"] = saved
            answered, total = answered + len(saved), total + REFERENCE_MAX
            view["answered"] = bool(saved)
        else:
            value = effective(profile, item)
            view["answer"] = value
            view["answered"] = value is not None
            answered, total = answered + (value is not None), total + 1
        questions.append(view)
    return {
        "id": module.id,
        "title": module.title,
        "purpose": module.purpose,
        "optional": True,
        "answered": answered,
        "total": total,
        "percent": round(100 * answered / total) if total else 0,
        "status": "not_started" if not answered else "complete" if answered >= total else "partial",
        "questions": questions,
    }


def _field_view(f):
    return {"key": f[0], "prompt": f[1], "type": f[2], "options": list(f[3])}


def overview(profile):
    modules = [module_view(profile, m) for m in MODULES]
    answered, total = sum(m["answered"] for m in modules), sum(m["total"] for m in modules)
    return {
        "modules": modules,
        "completion": {"answered": answered, "total": total, "percent": round(100 * answered / total) if total else 0},
    }


def summary(profile):
    """Per-module completion for the onboarding status (no questions)."""
    full = overview(profile)
    return {
        "modules": [
            {k: m[k] for k in ("id", "title", "answered", "total", "percent", "status")} for m in full["modules"]
        ],
        "completion": full["completion"],
        "optional": True,
    }


def apply_answers(profile, module, answers, accept_suggestions=False):
    """Merge partial answers into a profile dict (in place) and return the keys it changed.

    A key mapped to an empty value clears that answer; keys left out are untouched."""
    if not isinstance(answers, dict):
        raise ValueError("answers must be an object")
    facts = dict(profile.get("facts") or {})
    changed = []
    known = {x.key: x for x in module.questions}
    if unknown := set(answers) - set(known):
        raise ValueError("Unknown question: " + ", ".join(sorted(unknown)))
    if module.id == "wotc" and facts.get("tax_credit_screening_no") is True:
        # Materialize the blanket No first, so changing one question does not silently drop the others.
        for item in module.questions:
            facts.setdefault(item.key, False)
    if accept_suggestions:
        for item in module.questions:
            if item.suggestion is not None and item.type not in {"work_rows", "references"}:
                if effective({**profile, "facts": facts}, item) is None and item.key not in answers:
                    answers = {**answers, item.key: item.suggestion}
    rows = {r["row_id"] for r in work_rows(profile)}
    for key, value in answers.items():
        item = known[key]
        if item.type == "work_rows":
            if not isinstance(value, dict):
                raise ValueError("Supervisors are an object keyed by work-history row id")
            saved = dict(facts.get("work_supervisors") or {})
            for row_id, entry in value.items():
                if row_id not in rows:
                    raise ValueError("Unknown work-history row")
                cleaned = clean_entry(item.fields, entry or {})
                if cleaned:
                    saved[row_id] = cleaned
                else:
                    saved.pop(row_id, None)
            _set(facts, "work_supervisors", saved)
        elif item.type == "references":
            if value in (None, []):
                value = []
            if not isinstance(value, list) or len(value) > REFERENCE_MAX:
                raise ValueError(f"Give at most {REFERENCE_MAX} references")
            _set(facts, "references", [e for e in (clean_entry(item.fields, v) for v in value) if e])
        else:
            _set(facts, key, clean(item, value))
        changed.append(key)
    if module.id == "wotc":
        # The blanket fact means "No to every question": true only while every question is answered No.
        if all(facts.get(x.key) is False for x in module.questions):
            facts["tax_credit_screening_no"] = True
        else:
            facts.pop("tax_credit_screening_no", None)
    profile["facts"] = facts
    return changed


def _set(facts, key, value):
    if value in (None, [], {}):
        facts.pop(key, None)
    else:
        facts[key] = value


def save(db, module_id, answers, accept_suggestions=False):
    module = BY_ID.get(module_id)
    if module is None:
        raise KeyError(module_id)
    with db.exclusive() as s:
        row = s.get(Setting, "profile")
        profile = dict(row.value) if row else {}
        changed = apply_answers(profile, module, answers, accept_suggestions)
        if row:
            row.value = profile
        else:
            s.add(Setting(key="profile", value=profile))
    return changed
