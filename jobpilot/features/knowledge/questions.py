"""Meaning-preserving question identity.

Comparators, negation, time scope, modality, employer and history row are part
of the identity, so "less than 5 years" and "more than 5 years" never collide,
and an employer named "Visa" is a placeholder, not an immigration topic.
"""

import re

from .._store import digest, norm
from .models import QuestionIdentity

KEY_VERSION = "kq1"

# Longest phrases first; each maps to a distinct symbol.
COMPARATORS = [
    (r"no more than|not more than|at most|up to|or less|or fewer|maximum of", "<="),
    (r"no less than|not less than|at least|minimum of|or more|or greater", ">="),
    (r"less than|fewer than|under|below", "<"),
    (r"more than|greater than|over|above|exceeding|in excess of", ">"),
    (r"between", "between"),
]
SYMBOLS = re.compile(r"(<=|>=|<|>)")
NEGATION = re.compile(r"\b(?:not|never|no|without|none)\b|n't\b")
TEMPORAL = [
    ("current", r"\b(?:currently|current|presently|at present|right now|today)\b"),
    ("past", r"\b(?:previously|ever|formerly|former|in the past|have you been|were you|prior)\b"),
    ("future", r"\b(?:will you|in the future|future|upon hire|after hire)\b"),
]
MODALITY = [
    ("willingness", r"\b(?:willing|open to|comfortable with|agree to|able and willing)\b"),
    ("experience", r"\b(?:experience (?:with|in|using)|years of experience|worked with|have you used)\b"),
    ("ability", r"\b(?:able to|can you|capable of)\b"),
    ("status", r"\b(?:are you|do you (?:hold|have|possess)|is your|status)\b"),
]
# Ordered: earlier categories win. Run on text where employer names are already
# replaced by <employer>, so an employer called "Visa" cannot look like immigration.
# Bump when CATEGORY_RULES change: stored facts are recategorized once (metadata only).
CATEGORY_VERSION = "c2"
CATEGORY_RULES = [
    ("employer_relationship", r"<employer>|relative|related to|referr|know anyone|our company|this company"),
    # Authorizing an organization to use your information is consent, not work eligibility.
    (
        "consent",
        r"authori[sz]e\b.{0,80}\bto (?:use|process|collect|store|retain|share|verify|contact)|"
        r"\bconsent\b|privacy (?:policy|notice|statement)|terms (?:and|&) conditions",
    ),
    (
        "work_authorization",
        r"authori[sz]|eligible to work|right to work|entitled to work|legally (?:able|permitted|allowed) to work|"
        r"lawfully work|work permit|permit to work|permission to work|eligib\w* (?:for|to) (?:employment|work)|"
        r"employment eligibility|sponsor|\bvisa\b|h-?1b|opt\b|"
        r"citizen|permanent resident|green card|immigration|residen(?:ce|t) (?:status|country)",
    ),
    (
        "demographics",
        r"gender|race|racial|ethnic|hispanic|latin[oax]|veteran|disabilit|pronoun|"
        r"sexual orientation|transgender",
    ),
    ("clearance", r"clearance|ts/sci|top secret|\bsecret\b|polygraph"),
    ("agreements", r"non-?compete|non-?disclosure|\bnda\b|non-?solicit|restrictive covenant|agreement"),
    (
        "education",
        r"degree|gpa|graduat|school|university|college|major|field of study|bachelor|master|ph\.?d",
    ),
    (
        "experience",
        r"years of (?:professional |relevant )?experience|employer|job title|employment|worked (?:at|for)",
    ),
    ("skills", r"experience (?:with|in|using)|proficien|familiar with|fluent|language|programming"),
    ("projects", r"\bproject"),
    (
        "personal",
        r"\bname\b|e-?mail|phone|address|city|\bstate\b|zip|postal|linkedin|github|website|portfolio",
    ),
]
EMPLOYER_SPECIFIC = re.compile(
    r"<employer>|our company|this company|this role|this position|our team|why do you want|"
    r"why are you interested|our mission|our product"
)


# An alias that is also an ordinary word ("Visa") stays a word inside an
# immigration phrase: "visa sponsorship", "work visa", "visa status".
IMMIGRATION_BEFORE = re.compile(r"(?:^|\s)(?:work|student|employment|immigrant|h-?1b|f-?1|j-?1|tn|o-?1|a)$")
IMMIGRATION_AFTER = re.compile(r"^(?:sponsor\w*|status|type|holder|transfer|application|petition)\b")


def replace_employer(text: str, aliases) -> str:
    """Exact whole-phrase replacement of known employer aliases (longest first)."""
    for alias in sorted({norm(a) for a in aliases if a and len(norm(a)) >= 2}, key=len, reverse=True):
        pattern = re.compile(r"(?<![\w])" + re.escape(alias) + r"(?![\w])")

        def swap(m, text=text):
            before = " ".join(text[: m.start()].split()[-1:])
            after = " ".join(text[m.end() :].split()[:1])
            if IMMIGRATION_BEFORE.search(before) or IMMIGRATION_AFTER.search(after):
                return m.group(0)
            return "<employer>"

        text = pattern.sub(swap, text)
    return text


def symbolize(text: str) -> str:
    for pattern, symbol in COMPARATORS:
        text = re.sub(r"\b(?:" + pattern + r")\b", f" {symbol} ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify(text: str) -> str:
    for category, pattern in CATEGORY_RULES:
        if re.search(pattern, text):
            return category
    return "additional"


def identify(question: str, employer_aliases=(), history_row: str | None = None) -> QuestionIdentity:
    base = norm(question)
    base = re.sub(r"(?:\s*\(required\)|\*|✱)\s*$", "", base).rstrip("?:. ").strip()
    text = symbolize(replace_employer(base, employer_aliases))
    comparator = next((m for m in SYMBOLS.findall(text)), None)
    if comparator is None and re.search(r"\bbetween\b", text):
        comparator = "between"
    temporal = next((name for name, p in TEMPORAL if re.search(p, text)), "none")
    modality = next((name for name, p in MODALITY if re.search(p, text)), "none")
    category = classify(text)
    identity = {
        "v": KEY_VERSION,
        "text": text,
        "row": history_row or "",
    }
    return QuestionIdentity(
        key=KEY_VERSION + ":" + digest(identity)[:24],
        text=text,
        category=category,
        comparator=comparator,
        quantities=tuple(re.findall(r"\d+(?:\.\d+)?", text)),
        negated=bool(NEGATION.search(text)),
        temporal=temporal,
        modality=modality,
        employer_specific=bool(EMPLOYER_SPECIFIC.search(text)),
        history_row=history_row,
    )


def structured_key(key: str) -> str:
    """Identity for a structured profile fact (e.g. 'work_authorized_us')."""
    return "kf:" + norm(key).replace(" ", "_")
