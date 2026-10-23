"""What the owner sees for a question the engine could not answer, and how the owner's reply maps back onto it.

Owner 2026-10-23: "fix telegram, a lot of questions it asks are almost always incomplete, does not have dropdowns, and
the screenshots don't show the right questions, also it's not smart, it cannot infer details or answers from my
telegram answers (cannot even get the intent of the field, i.e. if it is uploading something), and also, the webpage
has stopped taking answers."

A review used to keep only the field's bare label ("End Date", "C", "Search for:") and its options. Telegram showed
the first eight options as buttons cut at 50 characters, and an upload field was asked as a text question. Telegram
and the web app now share this module, so both show the same complete question:

- context_from(): the field facts a review keeps (type, section, history row, hint, accepted files, partial options)
- display(): the full question with its section or history row ("Work history: Acme · Analyst › End Date")
- kind(): "choice", "upload" or "text"; an upload is recognised from the field type or the question's wording
- choices(): the offered options without "Select…" placeholders, each with its index in the stored list
- match_option(): the nearest offered choice for a typed reply; anything but an exact match is confirmed first
- owner_reason(): why the question is asked, in plain words, never an internal code or a stack message
- documents: files the owner sent for upload questions, reused when another form asks for the same document
"""

import hashlib
import re
from difflib import SequenceMatcher
from pathlib import Path

from .answers import normalize

UPLOAD_NOISE = re.compile(
    r"\s*(?:\*\s*)?(?:drop (?:your )?files? here|drag (?:and|&) drop|browse files?|choose (?:a )?file|no file chosen|"
    r"successfully uploaded)\b.*$",
    re.I | re.S,
)
# "Please upload your unofficial transcript.", "Upload a file (5MB max)", "Attach your portfolio".
UPLOAD_WORDING = re.compile(
    r"^(?:please\s+)?(?:upload|attach)\b|\b(?:upload|attach)\s+(?:a|an|your|the|any|one)\b|\(\s*\d+\s*mb max\)|"
    r"\bfile\b.{0,30}\b\d+\s*mb\b|\bdrop files? here\b",
    re.I,
)
NON_CHOICES = re.compile(r"no results?(?: found)?|loading\W*|no options?|searching\W*", re.I)
PAGE_SIZE = 8


def context_from(item):
    """The field facts a review keeps from the engine's pending question (forms._pending)."""
    scope = item.get("scope") or {}
    extra = item.get("context") or {}
    value = {
        "type": item.get("type") or extra.get("type") or "",
        "section": scope.get("section") or extra.get("section") or "",
        "group": scope.get("group") or extra.get("group") or "",
        "hint": str(extra.get("hint") or "")[:600],
        "accept": str(extra.get("accept") or "")[:200],
        "history_kind": extra.get("history_kind") or "",
        "history_identity": {
            k: str(v)[:120] for k, v in (extra.get("history_identity") or {}).items() if v and isinstance(k, str)
        },
        "options_partial": bool(extra.get("options_partial")),
        "field_id": str(item.get("field_id") or ""),
        "slot": item.get("slot") if isinstance(item.get("slot"), int) else 0,
        "source": extra.get("source") or "",
    }
    return {k: v for k, v in value.items() if v not in ("", {}, False, 0, None)}


def tidy(question):
    """The question as the form asks it: upload-widget text and required markers removed."""
    original = " ".join(str(question or "").split())
    text = UPLOAD_NOISE.sub("", original)
    text = re.sub(r"^\s*[*✱]\s*", "", text)
    text = re.sub(r"\s*(?:\*|✱|\(required\))\s*$", "", text, flags=re.I).strip()
    return text or original


def _human(text, limit=120):
    """A section heading worth showing: words, not an element id such as "workExperience-3"."""
    text = " ".join(str(text or "").split())
    if not text or len(text) > limit or not re.search(r"[A-Za-z]{3}", text):
        return ""
    if re.fullmatch(r"[\w.-]*\d[\w.-]*", text) or re.fullmatch(r"[a-z]+(?:[A-Z][a-z0-9]+)+", text):
        return ""
    return text


def where(context):
    """The part of the form a question belongs to, for the owner."""
    context = context or {}
    identity = context.get("history_identity") or {}
    if context.get("history_kind") == "work":
        label, parts = "Work history", [identity.get("company", ""), identity.get("title", "")]
    elif context.get("history_kind") == "education":
        label, parts = "Education", [identity.get("school", ""), identity.get("degree", "")]
    else:
        return _human(context.get("section"))
    detail = " · ".join(p for p in parts if p)
    return f"{label}: {detail}" if detail else label


def display(question, context=None):
    """The complete question: the form's own wording with the section or history row it sits in."""
    text = tidy(question)
    place = where(context)
    if place and normalize(place) not in normalize(text):
        return f"{place} › {text}"
    return text


QUESTION_WORDS = re.compile(
    r"^(?:what|which|how|why|when|where|who|do|does|did|are|is|was|were|have|has|had|will|would|can|could|"
    r"should|please|list|describe|tell|provide|enter|select)\b",
    re.I,
)


def bare_label(question):
    """A label that names a field rather than asking something ("End date month", "C", "Search for:")."""
    text = tidy(question)
    return len(normalize(text).split()) <= 3 and "?" not in text and not QUESTION_WORDS.search(text)


def knowledge_question(question, context=None):
    """The question saved with the owner's answer.

    A bare label ("End Date", "C") means nothing without its row or section, and saved bare it would answer every
    other row's "End Date" too. Ordinary questions keep their own wording so the next form's identical label still
    finds the answer."""
    context = context or {}
    text = tidy(question)
    if context.get("history_kind") or (len(normalize(text).split()) <= 2 and where(context)):
        return display(question, context)
    return text


def placeholder(option):
    from .widgets import placeholder_option

    text = " ".join(str(option or "").split())
    return not text or placeholder_option(text) or bool(NON_CHOICES.fullmatch(text))


def choices(options):
    """Offered options the owner can pick: (index in the stored list, text), placeholders and duplicates removed."""
    seen, result = set(), []
    for index, option in enumerate(options or []):
        text = " ".join(str(option or "").split())
        if placeholder(text) or normalize(text) in seen:
            continue
        seen.add(normalize(text))
        result.append((index, text))
    return result


def kind(question, options=(), context=None, reason=""):
    """choice, upload or text. An upload field is never asked as a text question."""
    context = context or {}
    if context.get("type") == "file" or reason == "Required document upload":
        return "upload"
    if choices(options):
        return "choice"
    if UPLOAD_WORDING.search(tidy(question)):
        return "upload"
    return "text"


def free_text_allowed(options, context=None):
    """A searchable or partly read list, or one whose only "option" was a placeholder, takes a typed answer."""
    return bool((context or {}).get("options_partial")) or not choices(options)


def match_option(options, text):
    """Nearest offered option for a typed reply: (position in `options`, exact) or None.

    Only an exact (case and punctuation insensitive) match is used without asking; every other match is offered to
    the owner to confirm, because a near match is not the owner's answer until they say so."""
    from .widgets import best_option

    options = [str(o) for o in options]
    want = normalize(str(text or ""))
    if not want or not options:
        return None
    exact = [i for i, o in enumerate(options) if normalize(o) == want]
    if len(exact) == 1:
        return exact[0], True
    number = re.fullmatch(r"#?\s*(\d{1,3})\s*[.)]?", str(text).strip())
    if number and 1 <= int(number[1]) <= len(options):
        return int(number[1]) - 1, False  # The options are listed with numbers.
    hit = best_option(options, str(text))
    if hit is not None and hit in options:
        return options.index(hit), False
    words = set(want.split())
    covering = [i for i, o in enumerate(options) if words <= set(normalize(o).split())]
    if len(covering) == 1:
        return covering[0], False
    named = [
        i
        for i, o in enumerate(options)
        if normalize(o) and re.search(r"(?:^| )" + re.escape(normalize(o)) + r"(?: |$)", want)
    ]
    if len(named) == 1:
        return named[0], False
    scored = sorted(
        ((SequenceMatcher(None, want, normalize(o)).ratio(), i) for i, o in enumerate(options)), reverse=True
    )
    if scored[0][0] >= 0.6 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.1):
        return scored[0][1], False
    return None


def owner_reason(reason="", question_kind="text"):
    """Why the owner is asked, in plain words. Engine reasons are diagnostics, never owner text.

    The web app showed "Repair agent: The run never reached submission: browser control crashed (ValueError …)",
    "disclosure_scope" and "Required question was not resolved" under questions (2026-10-23)."""
    if question_kind == "upload":
        return "This application needs a file your profile doesn't have yet."
    if str(reason or "").startswith("Repair agent:"):
        return "Your saved profile doesn't cover this, and the application can't continue without it."
    return "Your saved profile doesn't answer this yet."


# Engine reasons that read as diagnostics to the owner (2026-10-20..23 waiting_browser reasons sent to Telegram:
# "Clean twin browser's hCaptcha token delivered to website callback", "Central identifier step did not reach its
# password screen", "Workday session/server error persisted after two draft restarts").
INTERNAL_WORDS = re.compile(
    r"\w+\.py\b|traceback|exception|\berror:|valueerror|typeerror|keyerror|callback|twin|token|selector|combobox|"
    r"identifier step|draft restart|recovery scheduled|tab retained|worker|observation|validation|repair agent|"
    r"model|resolver|pipeline|\brun\b|did not advance|did not respond as expected|_[a-z]+_|\b[a-z]+_[a-z_]+\b",
    re.I,
)
BLOCKERS = (
    (r"verification requires .*chrome tab|complete this verification in the open chrome",
     "The employer needs an email verification finished in the open Chrome window. Reply “done” when it's finished."),
    (r"captcha|hcaptcha|recaptcha|security check",
     "The employer's site showed a security check (CAPTCHA) that wasn't accepted."),
    (r"verification code email|verification email|did not arrive",
     "The employer's verification email didn't arrive, so the application couldn't continue."),
    (r"shows a confirmation, but this run did not submit|already applied",
     "The employer's site says you may have applied already. Please check before it's tried again."),
    (r"posting page does not exist|posting (?:was )?not found|no longer exists",
     "The employer says this posting no longer exists."),
    (r"credential|sign(?:ing)? in|sign-in|password|person not found|already has an account|account",
     "The employer's site didn't accept the sign-in or account step for your account there."),
    (r"workday session|server error",
     "The employer's site kept returning errors."),
)


def owner_blocker(reason, blocker_type=""):
    """A browser blocker in plain words for the owner: never a file name, internal step name or diagnostic."""
    text = " ".join(str(reason or "").split())
    probe = text + " " + str(blocker_type or "")
    for pattern, sentence in BLOCKERS:
        if re.search(pattern, probe, re.I):
            return sentence
    if text and len(text) <= 300 and not INTERNAL_WORDS.search(text):
        from .failure_reports import clean

        return clean(text)
    return "The application stopped at a step it couldn't finish on its own."


def present(review):
    """The owner-facing form of a review row (a dict from db.record) for the web app."""
    context = review.get("context") or {}
    options = review.get("options") or []
    question_kind = kind(review.get("question", ""), options, context, review.get("reason", ""))
    return {
        **review,
        "options": [text for _, text in choices(options)],
        "display_question": display(review.get("question", ""), context),
        "kind": question_kind,
        "why": owner_reason(review.get("reason", ""), question_kind),
        "allow_other": question_kind == "choice" and free_text_allowed(options, context),
        "accept": context.get("accept", "") if question_kind == "upload" else "",
        "reason": owner_reason(review.get("reason", ""), question_kind),
    }


# ----- documents the owner sends for upload questions --------------------------------------------------------------
DOCUMENT_KINDS = (
    ("transcript", r"transcript|mark ?sheet|grade report|academic record"),
    ("cover_letter", r"cover.?letter|motivation letter"),
    ("writing_sample", r"writing sample|work sample|publication|portfolio|research"),
    ("certificate", r"certificat|licen[cs]e|diploma"),
    ("recommendation", r"recommendation|reference letter"),
    ("resume", r"\bresume\b|\bcv\b|curriculum"),
)
ALLOWED_SUFFIXES = {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".png", ".jpg", ".jpeg"}
MAX_DOCUMENT_BYTES = 10_000_000
DOCUMENTS_SETTING = "owner_documents"


def document_kind(question, employer=""):
    """The kind of document a field asks for. An unnamed one ("Upload a file") is reused only for that employer."""
    text = tidy(question)
    for name, pattern in DOCUMENT_KINDS:
        if re.search(pattern, text, re.I):
            return name
    identity = normalize(str(employer or "")) + "|" + normalize(text)
    return "other:" + hashlib.sha256(identity.encode()).hexdigest()[:16]


def document_folder(data_dir):
    folder = Path(data_dir) / "owner_documents"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder


def save_document(db, data_dir, question, filename, content, employer=""):
    """Keep a file the owner sent for an upload question; the next form asking for that document gets it."""
    from .db import now

    name = Path(str(filename or "document")).name
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Please send a PDF, Word, text or image file.")
    if not content or len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("That file is empty or larger than 10 MB. Please send a smaller file.")
    safe = re.sub(r"[^\w.-]+", "_", name).strip("._")[-80:] or "document" + suffix
    if Path(safe).suffix.lower() != suffix:
        safe += suffix
    # The employer sees the file's own name; a folder per content keeps two files of one name apart.
    folder = document_folder(data_dir) / hashlib.sha256(content).hexdigest()[:16]
    folder.mkdir(exist_ok=True, mode=0o700)
    path = folder / safe
    path.write_bytes(content)
    path.chmod(0o600)
    entry = {
        "kind": document_kind(question, employer),
        "folder": folder.name,
        "name": safe,
        "question": tidy(question)[:300],
        "saved_at": now(),
    }
    documents = dict(db.get_setting(DOCUMENTS_SETTING, {}) or {})
    documents[entry["kind"]] = entry
    db.set_setting(DOCUMENTS_SETTING, documents)
    return entry


def document_for(db, data_dir, question, employer=""):
    """The owner's saved file for this field, or None. Resumes and cover letters keep their own flows."""
    found = document_kind(question, employer)
    if found in {"resume", "cover_letter"}:
        return None
    entry = (db.get_setting(DOCUMENTS_SETTING, {}) or {}).get(found)
    if not entry:
        return None
    folder, name = Path(str(entry.get("folder", ""))).name, Path(str(entry.get("name", ""))).name
    if not folder or not name or folder in {".", ".."} or name in {".", ".."}:
        return None
    path = Path(data_dir) / "owner_documents" / folder / name
    return path if path.is_file() else None
