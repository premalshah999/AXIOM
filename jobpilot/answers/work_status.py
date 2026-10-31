"""Work-authorization questions, read by their own definition and answered from the applicant's recorded status.

No model and no saved answer takes part: a stored contradicting answer loses. In October 2026 an applicant on F-1 OPT
told Credence "authorized to work as a U.S. citizen: Yes" and ExxonMobil "protected individual under 8 USC 1324b: Yes",
because a generic "authorized to work" fact swallowed the question's qualifier.

The order of reading, each from the question's own wording:
1. An instruction tying an answer to the applicant's status ("OPT participants should answer No").
2. A list of statuses (citizen, national, permanent resident, refugee, asylee, protected individual): the facts against
   that list. One more clause opening it to anyone with valid work authorization makes it an authorization question.
3. "Indefinite", "permanent" or "unrestricted" authorization: time-limited status (OPT, H-1B ...) is not that.
4. "Do you currently hold OPT?": the recorded status, whatever the question before it said.
5. Sponsorship whose own definition names OPT, EAD, CPT, a STEM extension, Form I-983, forms for a school or government
   agency, a temporary visa or authorization that expires: what the employer asked, whatever the generic fact says.
6. Plain sponsorship and plain authorization: the saved facts.

THE ONE PLACE TO CHANGE THE PLAIN SPONSORSHIP ANSWER is the applicant's saved fact `requires_future_sponsorship_US`
(SPONSOR_KEYS, Knowledge > facts). It answers "will you now or in future require sponsorship?" and nothing else; it is
the owner's decision. No code here sets or overrides it.
"""

import re
import unicodedata
from dataclasses import dataclass

from ..answer_context import AUTH_KEYS
from ..answer_context import NEGATION
from ..answer_context import SPONSOR_KEYS
from ..answer_context import STATUS_NAMES
from ..answer_context import WITHOUT_SPONSOR
from ..answer_context import boolean
from ..answer_context import recorded_status
from ..answer_context import status_defined_answer
from ..answer_context import supported_boolean
from .eligibility import OTHER_COUNTRY


# Every answer from here says so, so that a saved answer on the form is never preferred to it (forms.py).
WORK_STATUS = "Work status rule"

US = r"(?:u\.?\s?s\.?(?:a\.?)?|united\s+states(?:\s+of\s+america)?|american)"
# One status class per entry. Citizenship words only count when tied to the U.S.: "Are you an Indian Citizen" is not one.
CLASS_WORDS = (
    (
        "citizen",
        rf"\b{US}[\s-]+citizen|\bcitizen(?:ship)?\s+(?:or\s+(?:a\s+)?national\s+)?(?:of|in)\s+(?:the\s+)?{US}\b|\(USC\)",
    ),
    (
        "national",
        rf"\b{US}[\s-]+(?:non-?citizen\s+)?national\b(?!\s+(?:guard|security|origin))|\bnon-?citizen\s+(?:{US}\s+)?national\b|"
        r"\bcitizen(?:\s+or|,)\s+(?:a\s+)?national\b",
    ),
    ("lpr", r"\bpermanent\s+resid|\bgreen\s*-?\s*card|\bLPR\b"),
    ("temporary", r"\btemporary\s+(?:permanent\s+)?resident"),
    ("refugee", r"\brefugees?\b"),
    ("asylee", r"\basylees?\b|\basylum\b"),
    ("protected", r"\bprotected\s+(?:individual|person)|\b1324b\b"),
    ("us_person", rf"\b{US}[\s-]+(?:person|worker)s?\b"),
)
# 8 USC 1324b and the export-control "U.S. person" are themselves lists; OPT is on neither.
PROTECTED_CLASSES = frozenset({"citizen", "national", "lpr", "temporary", "refugee", "asylee"})
FACT_CLASSES = {
    "citizen": ("us_citizen",),
    "national": ("us_national",),
    "lpr": ("permanent_resident", "lawful_permanent_resident", "green_card_holder", "green_card"),
    "temporary": ("temporary_resident",),
    "refugee": ("has_refugee_status", "refugee"),
    "asylee": ("has_asylee_status", "asylee"),
}
STATUS_TEXT_CLASSES = (
    ("citizen", r"\bcitizen\b"),
    ("lpr", r"\bpermanent\s+resident\b|\bgreen\s*card\b"),
    ("refugee", r"\brefugee\b"),
    ("asylee", r"\basylee\b"),
)
# The question is about another country, or a list of them: the facts here are about the U.S.
FOREIGN_ASK = re.compile(
    r"\b(?:other|another|outside|following|foreign|any of (?:these|the))\b[^.?]{0,30}\bcountr|"
    r"\bcountr(?:y|ies)\b[^.?]{0,25}\b(?:other|outside)\b|\bcountry\s*/\s*region\b|"
    r"\b(?:citizen|resident)\b[^.?]{0,30}\bof\s+the\s+country\b|"
    r"\b(?:cuba|iran|north korea|syria|crimea|russia|belarus)\b",
    re.I,
)
FOREIGN_PLACE = re.compile(
    r"\b(?:work|right|employ\w*|authori[sz]ed)\b[^.?]{0,30}\bin\s+"
    r"(?!United\b|America\b|the\b|this\b|any\b|that\b|your\b|which\b|our\b|[A-Z]\.|US\b|USA\b)[A-Z][a-z]{2,}"
)
# A leading condition is not the ask: "If you will require sponsorship, do you currently hold OPT?"
LEADING_IF = re.compile(r"^\W*(?:\w\W+)?if\b", re.I)
AFTER_IF_ASK = re.compile(r"\b(?:are|do|can|have|does|will|would|did)\s+you\b", re.I)
# Words that open the applicant's own standing to question. "Certain jobs may require that I am a United States Citizen"
# states nothing about the applicant.
LIST_ASK = re.compile(
    r"\bare\s+you\b|\bwhether\s+you\s+are\b|(?<!if )\byou\s+are\s+(?:an?|one|any|either|in)\b|"
    r"\bselect\s+\W?yes\W?\s+if\s+any\b|"
    r"\bdo\s+you\s+(?:fall|qualify|belong|meet)\b|\bcan\s+you\s+(?:meet|provide\s+proof)\b|"
    r"\bwill\s+you\s+be\s+able\s+to\s+provide\s+proof\b|\bwork\b[^?]{0,70}\bas\s+an?\b",
    re.I,
)
STATEMENT_ASK = re.compile(r"^\W*(?:i\s+(?:certify|confirm|attest)\s+that\s+)?i\s+am\s+(?:an?|one)\b", re.I)
# The classes are stated elsewhere in the question: "Are you in one of the above five groups?", "Are you any of the following?"
LIST_REFERENCE = re.compile(
    r"\b(?:one|any)\s+of\s+(?:the\s+)?(?:above|following|these|listed|categories)|\bthe\s+above\b|"
    r"\b(?:this|that|these)\s+(?:requirements?|qualifications?)\b|\bcategories\s+(?:below|above)\b",
    re.I,
)
# One more clause admits anyone holding valid work authorization: John Deere's "You are legally eligible if you are a
# U.S. citizen or if you have a valid work authorization"; "a citizen ... or an alien authorized by USCIS to work".
OPEN_TO_AUTHORIZED = re.compile(
    r"\bor\s+(?:if\s+you\s+(?:have|hold)\s+|have\s+|hold\s+|holder\s+of\s+|otherwise\s+|are\s+)?(?:an?\s+)?"
    r"(?:(?:alien|person|individual|foreign\s+national|non-?citizen)\s+)?(?:valid\s+|legal(?:ly)?\s+|lawful(?:ly)?\s+)?"
    r"(?:work\s+(?:visa|permit|authori[sz]ation)|employment\s+authori[sz]ation|authori[sz]ed)\b",
    re.I,
)
# A second condition the facts here cannot speak to; a non-citizen fails the first half regardless.
EXTRA_CONDITION = re.compile(
    r"\b(?:and|with|who)\b[^?]{0,50}\b(?:ability|able|eligible|willing|obtain|clearance|without)\b", re.I
)
TERM_WORDS = re.compile(
    r"\bindefinite(?:ly)?\b|\bpermanent(?:ly)?\b(?!\s+resid)|\bunrestricted\b|\bwithout\s+(?:any\s+)?(?:restrictions?|limitations?)\b|"
    r"\bnow\s+and\s+in\s+the\s+future\b[^.?]{0,40}\bany\s+employer\b|\bany\s+employer\b[^.?]{0,40}\bnow\s+and\s+in\s+the\s+future\b",
    re.I,
)
TERM_CONTEXT = re.compile(r"\b(?:work\w*|employ\w*)\b", re.I)
ASKS = re.compile(r"\b(?:are|do|can|have|does|will)\s+you\b|\bis\s+your\s+(?:work|authori[sz]ation|right)\b", re.I)
# Not a yes/no question at all: "In what countries do you have the unrestricted right to work?"
OPEN_QUESTION = re.compile(r"^\W*(?:\w\W+)?(?:in\s+)?(?:what|which|how|where|when|why|who)\b", re.I)
# "without the need for current or future employer sponsorship": no sponsorship is being asked for.
NO_SPONSORSHIP = re.compile(r"\bwithout\b[^?]{0,90}\bsponsor", re.I)
# Examples of what sponsorship is are not a list of statuses the applicant might hold.
EXAMPLES = re.compile(r"\((?:for\s+example|e\.?g|ex\b|such\s+as)[^)]*\)", re.I)
TEMPORARY_ASK = re.compile(r"\bare\s+you\s+(?:only\s+)?temporar\w+\s+authori[sz]ed", re.I)
SPONSOR_ASK = re.compile(
    r"\b(?:will|would|do|does|are|might|may|can)\b[^?]{0,130}?\b(?:require[sd]?|requiring|need(?:s|ed|ing)?|anticipate)\b"
    r"[^?]{0,130}?\b(?:sponsor\w*|immigration[- ](?:related[- ])?(?:support|assistance|benefits?|filings?)|"
    r"\b(?:work|employment|employee|immigration|a|an|any|the)\s+(?:\w+\s+)?visas?\b|\bvisas?\s+(?:status|sponsor\w*|support|petition|transfer|or\s+work)|"
    r"work\s+permit|work\s+status|(?:employer|company)\s+(?:support|assistance))",
    re.I,
)
# What a sponsorship question's own definition may name. Acronyms are case-sensitive: "opt out" is not OPT.
STUDENT_NAMES = (
    r"\bOPT\b|\bCPT\b|\bSTEM\b|(?i:optional\s+practical)|(?i:curricular\s+practical)|\bF-?\s?1\b|\bf-?1\b|"
    r"(?i:\bstudent\s+visa)|\bI-?983\b|(?i:\btraining\s+plan)|\bM-?1\b"
)
# "H-4 EAD" and "L-2 EAD" are someone else's card.
EAD_NAMES = r"(?<!H-4 )(?<!H4 )(?<!L-2 )(?<!L2 )\bEAD\b|(?i:employment\s+authori[sz]ation\s+document)"
# A definition that reaches any time-limited status without naming one: "(for example, if you are on a temporary visa)",
# "work authorization that expires in the next 2-3 years", "work authorization outside of permanent residence/citizenship",
# "work authorization which MAY later require sponsorship", "any type of immigration filing", "a F, J, H or L visa status".
# "Require sponsorship to maintain your work authorization" is no definition: that is the plain question, and the saved fact's.
TIME_LIMITED_NAMES = (
    r"(?i:\btemporary\s+(?:\w+\s+)?visas?\b|"
    r"\bauthori[sz]ation\b[^.?]{0,40}\bexpir(?:e|es|ing|ation)\b|"
    r"\b(?:work|employment)\s+authori[sz]ation\s+(?:outside|other\s+than)\s+(?:of\s+)?(?:permanent\s+resid\w+|citizenship)|"
    r"\bwork\s+authori[sz]ation\s+(?:which|that)\s+may\b[^.?]{0,30}\brequire\b|"
    r"\bany\s+(?:type\s+of\s+)?immigration\s+filing\b|\bother\s+action\s+requiring\b|"
    r"\bprovide\s+(?:\w+\s+)?(?:documentation|paperwork|forms?)\b[^.?]{0,80}\b(?:immigration|work\s+authori[sz]ation)\b)|"
    r"\bF(?:,\s*|\s+or\s+|\s*/\s*)[JHLM]\b"
)
# "Are you authorized to work in the United States? This does not include practical training."
AUTH_EXCLUDES_STUDENT = re.compile(
    r"\b(?:(?:does|do|did)\s+not\s+includ\w+|not\s+including|excluding|excludes?)\b[^.?]{0,40}"
    r"(?:(?i:practical\s+training)|\bOPT\b|\bCPT\b|(?i:student\s+visa))",
    re.I,
)
FORM_DUTY = re.compile(
    r"\b(?:forms?|letters?|paperwork|documents?|documentation|submissions?|verifications?|signature|sign)\b[^.?]{0,110}"
    r"\b(?:schools?|government\s+agenc\w+|federal\s+agenc\w+|program\s+sponsors?|DSO)\b|"
    r"\b(?:schools?|government\s+agenc\w+|federal\s+agenc\w+|program\s+sponsors?|DSO)\b[^.?]{0,110}"
    r"\b(?:forms?|letters?|paperwork|documents?|documentation|submissions?|verifications?|signature)\b",
    re.I,
)
# "Do you currently hold OPT?", "Are you currently in F-1 Visa status?", "Are you currently in a period of OPT?"
HOLD_ASK = re.compile(
    r"\b(?:do|are|have)\s+you\b(?:\s+(?:currently|presently|now|still|also))*\s+"
    r"(?:hold(?:ing)?|have|possess|a\s+holder\s+of|an?|on|in|under|working\s+(?:on|under))\b",
    re.I,
)
HOLD_FILLER = re.compile(
    r"\W*(?:an?|the|any|valid|current|currently|active|period|of|status|visa|student|trainee|work|authorization|or|and|"
    r"immigration|employment|on|in|US|USA|[A-Z](?=,|\s+or\b|\s+non-?immigrant))\b\W*",
    re.I,
)
INVOLVES_ASK = re.compile(r"\bdoes\s+your\s+(?:\w+\s+){0,3}authori[sz]ation\b[^?]{0,80}\binvolve\b", re.I)
SCHOOL_APPROVAL_ASK = re.compile(r"\brequired\s+to\s+be\s+authori[sz]ed\s+by\b[^?]{0,120}\b(?:SEVIS|DSO|USCIS)\b")
PAST_ASK = re.compile(r"\bhave\s+you\s+(?:ever|in\s+the\s+past|previously)|\bever\s+been\b|\bin\s+the\s+past\b", re.I)
# (name, pattern) as a question may state a status. STEM OPT and F-1 CPT are read whole before their parts.
QUESTION_STATUS = (
    ("stem_opt", r"(?:\bF-?1\s+)?\bSTEM[\s-]+OPT\b"),
    ("cpt", r"(?:\bF-?1\s+)?\bCPT\b|(?i:curricular\s+practical)"),
    ("opt", r"(?:\bF-?1\s+)?\bOPT\b|(?i:optional\s+practical)"),
    ("f1", r"\bF-?\s?1\b|(?i:\bstudent\s+(?:visa|status))|(?i:\binternational\s+student)"),
    ("j1", r"\bJ-?1\b"),
    ("m1", r"\bM-?1\b"),
    ("h1b", r"\bH-?1-?B?1?\b"),
    ("l1", r"\bL-?1[AB]?\b"),
    ("o1", r"\bO-?1\b"),
    ("tn", r"\bTN\b"),
    ("e3", r"\bE-?3\b"),
    ("ead", EAD_NAMES),
    ("nonimmigrant", r"(?i:\bnon-?immigrant\b)"),
)
# Which of those names the recorded status satisfies. The EAD is the OPT card.
HELD_NAMES = (
    (r"\bOPT\b|optional practical", {"opt", "f1", "ead", "nonimmigrant"}),
    (r"\bCPT\b|curricular practical", {"cpt", "f1", "nonimmigrant"}),
    (r"\bSTEM\b", {"stem_opt"}),
    (r"\bF-?1\b", {"f1", "nonimmigrant"}),
    (r"\bJ-?1\b", {"j1", "nonimmigrant"}),
    (r"\bH-?1B", {"h1b", "nonimmigrant"}),
    (r"\bL-?1\b", {"l1", "nonimmigrant"}),
    (r"\bO-?1\b", {"o1", "nonimmigrant"}),
    (r"\bTN\b", {"tn", "nonimmigrant"}),
    (r"\bE-?3\b", {"e3", "nonimmigrant"}),
)
STATUS_REF = "fact:current_immigration_status_US"
# A Yes/No-shaped question. Anything else in a free-text box is the question's detail, not its answer.
DETAIL_ASK = re.compile(r"\b(?:explain|describe|specify|provide|list|details?|elaborate|indicate which|please share)\b", re.I)
AUTH_ASK = re.compile(
    r"\b(?:are|do)\s+you\b[^?]{0,70}?(?:\b(?:authori[sz]ed|eligible|permitted|entitled)\b[^?]{0,30}?\b(?:to\s+work|for\s+employment|to\s+be\s+employed)|"
    r"\b(?:have|possess|hold)\b[^?]{0,30}\b(?:work|employment)\s+authori[sz]ation|\blegal\s+right\s+to\s+work)",
    re.I,
)
PLACEHOLDER = re.compile(r"^\s*(?:no\s+answer|no\s+preference|not\s+sure|n/?a)\b", re.I)
NEGATIVE_OPTION = re.compile(r"\bnot\b|\bnone\b|\bneither\b|\bnon\b|\bother\b|n['’]t\b|^\s*no\b", re.I)
AUTHORIZED_OPTION = re.compile(r"\b(?:authori[sz]ed|allowed|eligible|permitted)\s+to\s+work\b|\bwork\s+authori[sz]", re.I)
UNKNOWN_OPTION = re.compile(r"\bunknown\b|\bnot\s+(?:authori[sz]ed|allowed|eligible)\b|\bseeking\b", re.I)
# Words that make an option no claim of a status: a negation, a catch-all, or another country's residents.
NOT_A_CLAIM = re.compile(
    r"\bnot\b|\bnone\b|\bneither\b|\bnon\b|n't\b|\bother\b|\bothers\b|\bincludes?\b|\bincluding\b|\bvisa\b|"
    r"\bdifferent\s+country\b|\bcanadian\b|\baustralian\b",
    re.I,
)
# Nothing but a status name, as a free-text answer.
STATUS_ONLY = re.compile(r"\W*(?:F-?1|J-?1|H-?1B|OPT|CPT|STEM|EAD|STEM\s+OPT|F-?1\s+(?:STEM\s+)?OPT|F-?1\s+CPT)\W*", re.I)
TEMPORARY_OPTION = re.compile(r"\btemporar|\bcurrently\s+(?:eligible|authori[sz]ed)|\bbut\s+will\b", re.I)


def plain(text):
    """The label with its display punctuation settled, so that sentence ends are sentence ends."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.translate(str.maketrans({"‐": "-", "‑": "-", "–": "-", "—": "-", "’": "'", "‘": "'", "“": '"', "”": '"'}))
    text = re.sub(r"\b[Uu]\.\s?[Ss]\.\s?[Cc]\.", "USC", text)
    text = re.sub(r"\b[Uu]\.\s?[Ss]\.(?:\s?[Aa]\.)?", "US", text)
    text = re.sub(r"\b(e\.g|i\.e|etc|sec|vs)\.", lambda m: m[1], text, flags=re.I)
    return " ".join(text.split())


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.?!])\s+", text) if s.strip()]


def asked(sentence):
    """The sentence without a leading condition, unless the condition is all there is."""
    condition = LEADING_IF.match(sentence)
    ask = AFTER_IF_ASK.search(sentence, condition.end()) if condition else None
    return sentence[ask.start() :] if ask else sentence


def classes_in(text, *, expand=True):
    text = re.sub(r"citizenship\s+(?:and|&)\s+immigration\s+services", " ", text, flags=re.I)
    found = {name for name, words in CLASS_WORDS if re.search(words, text, re.I)}
    if "lpr" in found and re.search(r"(?<!non-)(?<!non )\bcitizens?\b", text, re.I):
        found.add("citizen")  # "A citizen or permanent resident" names no country here, and means this one.
    if expand and found & {"protected", "us_person"}:
        found |= PROTECTED_CLASSES
    return found - {"protected", "us_person"}


@dataclass(frozen=True)
class Standing:
    """What the applicant's facts decide. A class in neither set is unknown, and nothing is answered from it."""

    held: frozenset
    denied: frozenset
    names: frozenset  # The status names the recorded status satisfies (HELD_NAMES).
    refs: tuple

    @property
    def nonimmigrant(self):
        return "nonimmigrant" in self.names

    @property
    def settled(self):
        """Time-limited (True), not time-limited (False), or unknown (None)."""
        if self.nonimmigrant:
            return True
        return False if self.held & {"citizen", "national", "lpr", "refugee", "asylee"} else None


def standing(facts):
    values = {str(k).casefold().removeprefix("fact:"): (str(k).removeprefix("fact:"), v) for k, v in (facts or {}).items()}
    held, denied, refs = set(), set(), []
    for name, keys in FACT_CLASSES.items():
        for key in keys:
            if key not in values:
                continue
            truth = boolean(values[key][1])
            if truth is True:
                held.add(name)
                refs.append("fact:" + values[key][0])
            elif truth is False:
                denied.add(name)
                refs.append("fact:" + values[key][0])
    status = recorded_status(facts)
    names = set()
    # "Previously on OPT" and "not a citizen" state nothing current.
    if status and not re.search(r"\b(?:not|no\s+longer|non|previous(?:ly)?|formerly|expired|pending)\b", status, re.I):
        for own, satisfied in HELD_NAMES:
            if re.search(own, status, re.I):
                names |= satisfied
        held |= {name for name, words in STATUS_TEXT_CLASSES if re.search(words, status, re.I)}
        if names or held:
            refs.append(STATUS_REF)
    if "citizen" in held:
        held.add("national")
    # One standing at a time: an F-1 student or a green-card holder is none of the other classes.
    if names or held:
        denied |= (PROTECTED_CLASSES - held)
    return Standing(frozenset(held), frozenset(denied - held), frozenset(names), tuple(dict.fromkeys(refs)))


def plain_sponsorship(facts):
    """The owner's saved answer to the plain sponsorship question, (needs it, evidence) or None. The one place it is read."""
    facts = {str(k).casefold().removeprefix("fact:"): v for k, v in (facts or {}).items()}
    return supported_boolean(facts, SPONSOR_KEYS)


def plain_authorization(facts):
    facts = {str(k).casefold().removeprefix("fact:"): v for k, v in (facts or {}).items()}
    return supported_boolean(facts, AUTH_KEYS)


def _named(text, pattern):
    """The text names it, and no negation sits just before the name ("excluding OPT")."""
    for hit in re.finditer(pattern, text):
        if not NEGATION.search(text[max(0, hit.start() - 45) : hit.start()]):
            return True
    return False


def sponsorship_defined(text, stand):
    """The employer's definition of sponsorship covers the applicant's recorded status, or does not name it at all."""
    if not stand.names:
        return False
    if stand.nonimmigrant and _named(text, TIME_LIMITED_NAMES):
        return True
    if "f1" in stand.names and (
        _named(text, STUDENT_NAMES) or _named(text, EAD_NAMES) or FORM_DUTY.search(text) or _indefinite(text)
    ):
        return True
    if _indefinite(text) or FORM_DUTY.search(text):
        return True  # Any nonimmigrant status runs out, and any one needs the employer's filing.
    return any(_named(text, name) for own, name in STATUS_NAMES if own and re.search(own, " ".join(stand.names), re.I))


def _indefinite(text):
    return bool(re.search(r"\b(?:on\s+an?\s+)?indefinite(?:ly)?\b|\bon\s+a\s+permanent\s+basis\b", text, re.I))


def _held_names(sentence):
    """The statuses the sentence names, reading "F-1 STEM OPT" as the extension and not as its parts."""
    names, rest = set(), sentence
    for name, pattern in QUESTION_STATUS:
        if re.search(pattern, rest):
            names.add(name)
            rest = re.sub(pattern, " ", rest)
    return names


def _hold_ask(sentence):
    """The statuses a "do you currently hold it" question names, or None when it asks something else."""
    if INVOLVES_ASK.search(sentence):
        return _held_names(sentence)
    for hit in HOLD_ASK.finditer(sentence):
        rest = sentence[hit.end() :]
        while True:
            filler = HOLD_FILLER.match(rest)
            if not filler or not filler.end():
                break
            rest = rest[filler.end() :]
        rest = re.sub(r"^\W+", "", rest)
        if any(re.match(pattern, rest) for _, pattern in QUESTION_STATUS):
            # Only what follows the ask: a condition before it may name another status.
            return _held_names(sentence[hit.end() :])
    return None


@dataclass(frozen=True)
class Reading:
    kind: str
    truth: bool | None
    refs: tuple = ()
    why: str = ""


def _reading(label, options, facts):
    """How the question reads against the facts. `truth` None: this kind of question, with nothing to answer it from."""
    text = plain(label)
    joined = " ".join(str(o) for o in options or [])
    if FOREIGN_ASK.search(text):
        return None
    stand = standing(facts)
    defined = status_defined_answer(text, {k: v for k, v in (facts or {}).items()})
    if defined:
        return Reading("defined", defined[0], tuple(defined[1]), "The question defines its answer for this status")
    for sentence in sentences(text):
        ask = asked(sentence)
        if OPEN_QUESTION.search(ask):
            return None
        if OTHER_COUNTRY.search(ask) or FOREIGN_PLACE.search(ask):
            continue  # The facts here are about the U.S.
        if TEMPORARY_ASK.search(ask):
            term = stand.settled
            return Reading("status_held", term, stand.refs, "The recorded immigration status") if term is not None else None
        # "If you will require sponsorship, do you currently hold OPT?": the condition is not the question.
        named = _hold_ask(ask)
        no_need = bool(WITHOUT_SPONSOR.search(ask) or NO_SPONSORSHIP.search(ask))
        if named is not None:
            if PAST_ASK.search(ask) and not (named & stand.names):
                return None  # The past is not recorded, only the present.
            if not named & stand.names and named == {"stem_opt"} and "opt" in stand.names:
                return None  # A STEM extension is not recorded either way.
            if named & stand.names:
                return Reading("status_held", True, (STATUS_REF,), "The recorded immigration status")
            if stand.names or stand.held:
                return Reading("status_held", False, stand.refs, "The recorded immigration status")
            return Reading("status_held", None)
        if SCHOOL_APPROVAL_ASK.search(ask):
            if "f1" in stand.names:
                return Reading("status_held", True, (STATUS_REF,), "The recorded immigration status")
            return Reading("status_held", False, stand.refs, "The recorded immigration status") if stand.held else None
        term = TERM_WORDS.search(ask)
        if (
            term
            and ASKS.search(ask)
            and TERM_CONTEXT.search(ask)
            and not (SPONSOR_ASK.search(ask) and not no_need)
            and not re.search(r"\bage\b", ask, re.I)  # "the minimum age requirement for unrestricted employment".
        ):
            settled = stand.settled
            if settled is True:
                return Reading("term", False, stand.refs, "A time-limited status is not indefinite or unrestricted")
            if settled is False:
                return Reading("term", True, stand.refs, "Citizen or permanent status")
            return Reading("term", None)
        if SPONSOR_ASK.search(ask) and not no_need:
            if sponsorship_defined(text, stand):
                return Reading(
                    "sponsor_defined", True, (STATUS_REF,), "The employer's definition of sponsorship covers this status"
                )
            if OTHER_COUNTRY.search(text):
                return None
            known = plain_sponsorship(facts)
            return Reading(
                "sponsor_plain",
                known[0] if known else None,
                tuple(known[1]) if known else (),
                "The applicant's saved sponsorship answer",
            )
        statements = EXAMPLES.sub(" ", ask)
        if LIST_ASK.search(statements) or STATEMENT_ASK.search(statements):
            listed = classes_in(statements) or (classes_in(text + " " + joined) if LIST_REFERENCE.search(statements) else set())
            if listed:
                return _list_reading(statements, text, listed, stand, facts)
        if AUTH_ASK.search(ask) and not no_need and not re.search(r"\bthat\s+country\b", ask, re.I):
            if OTHER_COUNTRY.search(text):
                return None
            if "f1" in stand.names and AUTH_EXCLUDES_STUDENT.search(text):
                return Reading("auth_defined", False, (STATUS_REF,), "The question excludes this status")
            known = plain_authorization(facts)
            return Reading(
                "auth_plain", known[0] if known else None, tuple(known[1]) if known else (), "The applicant's saved authorization"
            )
        if ask.endswith("?") or ask.endswith("?)"):
            return None  # The question itself is something else; what follows it only explains it.
    return None


def _list_reading(ask, text, listed, stand, facts):
    if OPEN_TO_AUTHORIZED.search(ask):
        known = plain_authorization(facts)
        if known and known[0]:
            return Reading("auth_defined", True, tuple(known[1]), "The question admits anyone with valid work authorization")
        return Reading("auth_defined", None)
    if stand.held & listed:
        if EXTRA_CONDITION.search(ask) or re.search(r"\bclearance\b", text, re.I):
            return None  # A citizen's "and eligible for a clearance" is another fact.
        return Reading("status_list", True, stand.refs, "The applicant holds a status the question lists")
    if listed <= stand.denied:
        return Reading("status_list", False, stand.refs, "The applicant holds none of the statuses the question lists")
    return Reading("status_list", None)


def _yes_no(options):
    yes = [o for o in options if re.match(r"\s*yes\b", str(o), re.I)]
    no = [o for o in options if re.match(r"\s*no\b", str(o), re.I) and not PLACEHOLDER.match(str(o))]
    return yes, no


def _names_own_status(option, stand):
    return bool(_held_names(plain(option)) & stand.names)


def category_choice(options, stand):
    """The option for a list of statuses (no yes/no): the applicant's own, else the one that claims none it lacks."""
    options = [o for o in options if str(o).strip() and not PLACEHOLDER.match(str(o))]
    asserted = {str(o): classes_in(plain(o)) if not NEGATIVE_OPTION.search(plain(o)) else set() for o in options}
    if not any(asserted.values()):
        return None
    if stand.held:
        # The class named outright, before an umbrella such as "A protected individual" that also covers it.
        own = [o for o in options if asserted[str(o)] and classes_in(plain(o), expand=False) & stand.held]
        own = own or [o for o in options if asserted[str(o)] & stand.held]
        return own[0] if len(own) == 1 else None
    if not stand.nonimmigrant:
        return None
    open_options = [o for o in options if not asserted[str(o)]]
    for wanted in (
        lambda o: _names_own_status(o, stand) and not UNKNOWN_OPTION.search(str(o)),
        lambda o: AUTHORIZED_OPTION.search(str(o)) and not UNKNOWN_OPTION.search(str(o)),
        lambda o: NEGATIVE_OPTION.search(str(o)) and not UNKNOWN_OPTION.search(str(o)),
    ):
        hits = [o for o in open_options if wanted(o)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    return None


def _pick(reading, field, stand):
    """The offered answer for a settled reading, or None when the form offers no truthful single choice."""
    kind = field.get("type")
    truth = reading.truth
    options = [str(o) for o in field.get("options") or [] if str(o).strip()]
    if kind == "checkbox":
        return "true" if truth else "false"
    if len(options) < 2:
        options = []  # A closed picker remembers only what it holds: that is no catalog, and no answer either.
    yes, no = _yes_no(options)
    if yes or no:
        pool = yes if truth else no
        if reading.kind == "term" and not truth:
            pool = [o for o in options if TEMPORARY_OPTION.search(o) and not re.match(r"\s*yes\b", o, re.I)] or pool
        # "No, I am a U.S. citizen, permanent resident ..." says more than No (2026-10): never for an applicant who is none.
        pool = [o for o in pool if not false_claim(o, stand)]
        if len(pool) > 1:
            pool = [o for o in pool if _names_own_status(o, stand)] or pool
        if len(pool) > 1 and truth and reading.kind == "sponsor_defined":
            pool = [o for o in pool if re.search(r"\bfuture\b", o, re.I)] or pool
        if len(pool) > 1:
            pool = [o for o in pool if re.fullmatch(r"\s*(?:yes|no)\W*", o, re.I)] or pool
        return pool[0] if len(pool) == 1 else None
    if options and any(classes_in(plain(o)) for o in options):
        return category_choice(options, stand)
    if not options or field.get("options_partial"):
        if kind in {"text", "textarea", "contenteditable"} and DETAIL_ASK.search(str(field.get("label") or "")):
            return None  # "If so, please explain" wants prose, from the model.
        return "Yes" if truth else "No"
    return None


@dataclass(frozen=True)
class Verdict:
    """What the question reads as, and the value to give when the facts settle it (None when they do not)."""

    kind: str
    value: str | None
    refs: tuple
    why: str


# A question for the applicant's country of citizenship ("In which country/region do you have citizenship?").
CITIZENSHIP_ASK = re.compile(r"\bcountr(?:y|ies)\b[^.?]{0,25}?\b(?:citizenship|nationality)\b", re.I)
# "Are you now, or have you ever been, a citizen of any other country?" Other than those just listed: Aptiv's form asks
# for the countries of citizenship first, and three passes of one run answered No, Yes, No (2026-10-21).
OTHER_CITIZENSHIP_ASK = re.compile(r"\bcitizen(?:ship)?\b[^?]{0,30}\bany\s+other\s+countr", re.I)
FURTHER_CITIZENSHIP_KEY = re.compile(
    r"^(?:second(?:ary)?|other|additional|dual)_(?:country_of_)?(?:citizenship|nationality)$|"
    r"^(?:citizenship|nationality)_(?:2|two|second(?:ary)?|other|additional)$"
)


def other_citizenship(field, facts):
    """The group's own answers name the recorded citizenship, so "any other" means beyond it."""
    label = plain(field.get("label") or "")
    context = field.get("context")
    if not OTHER_CITIZENSHIP_ASK.search(label) or field.get("type") == "checkbox" or not context:
        return None
    values = {str(k).casefold().removeprefix("fact:"): (str(k).removeprefix("fact:"), v) for k, v in (facts or {}).items()}
    own = next((k for k in ("country_of_citizenship", "citizenship") if str(values.get(k, ("", ""))[1]).strip()), None)
    if not own or str(values[own][1]).strip().casefold() not in str(context).casefold():
        return None
    further = [k for k, (_, v) in values.items() if FURTHER_CITIZENSHIP_KEY.match(k) and str(v).strip()]
    yes, no = _yes_no([str(o) for o in field.get("options") or []])
    pool = yes if further else no
    if (yes or no) and len(pool) != 1:
        return None
    value = pool[0] if pool else ("Yes" if further else "No")
    refs = tuple("fact:" + values[k][0] for k in (own, *further))
    return Verdict("other_citizenship", value, refs, f"{WORK_STATUS}: The recorded citizenship facts")


def decide(field, facts):
    """Read one question against the facts: a Verdict, or None when it is not a work-authorization question here."""
    if verdict := other_citizenship(field, facts):
        return verdict
    reading = _reading(field.get("label") or "", field.get("options") or [], facts)
    if reading is None:
        return None
    value = None if reading.truth is None else _pick(reading, field, standing(facts))
    return Verdict(reading.kind, value, reading.refs, f"{WORK_STATUS}: {reading.why}")


def false_claim(value, known):
    """The status classes an offered answer asserts when the facts say the applicant holds none of them: "(a) U.S. citizen
    or national", "No, I am a U.S. citizen, permanent resident". SpaceX's Citizenship Status held the former for an
    applicant on OPT (2026-10-13). `known` is the facts or their Standing."""
    text = plain(value)
    if not text or NOT_A_CLAIM.search(text):
        return set()
    stand = known if isinstance(known, Standing) else standing(known)
    asserted = classes_in(text)
    return asserted if asserted and not asserted & stand.held and asserted <= stand.denied else set()


def holds(value, verdict):
    """The answer the form holds is not contradicted by this reading. Wording may differ; polarity, a false status claim
    and a status typed into a yes/no question may not ("F-1 OPT" as the answer to "Will you require sponsorship?")."""
    if verdict.value is None:
        return True
    if " ".join(str(value or "").split()).casefold() == " ".join(str(verdict.value).split()).casefold():
        return True
    want, have = yes_no_value(verdict.value), yes_no_value(value)
    if want is not None and have is not None:
        return want == have
    return not STATUS_ONLY.fullmatch(plain(value)) if want is not None else True


def yes_no_value(value):
    match = re.match(r"\s*(yes|no|true|false)\b", str(value or ""), re.I)
    if not match or PLACEHOLDER.match(str(value)):
        return None
    return match[1].casefold() in {"yes", "true"}
