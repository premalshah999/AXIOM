"""Answer patterns: consent."""

import re
from ..answer_context import question_head
from ..knowledge import GOVERNMENT
from ..knowledge import IMMIGRATION_VISA
from ..knowledge import NDA_INSTRUCTION
from ..knowledge import topic


# Consent to process data is not an identity question, even when it names the data
# ("I consent to the collection ... of my sensitive personal data, including racial and ethnic
# origin information" - AbbVie 2026-10-14). The consent policy answers it.
DATA_CONSENT = re.compile(
    r"\bI (?:hereby )?(?:consent|agree|acknowledge|authori[sz]e)\b[^.?]{0,80}"
    r"(?:collect|process|use|disclos|stor|transfer|shar)\w*",
    re.I,
)


SIGNATURE_REQUEST = re.compile(
    r"(?:candidate|applicant|full|legal|your)?\s*name\s*:\s*(?:and\s*)?(?:date\s*:?)?\s*$|"
    r"(?:please )?(?:type|enter|include|provide)[^.]{0,40}(?:full |legal )?name(?: and (?:the |today'?s )?date)?(?: as (?:your |an )?(?:electronic |e-?)?signature)?[.:]?\s*$",
    re.I,
)


AGREEMENT_TEXT = re.compile(r"\bagree|acknowledg|certif|hereby|consent|terms and conditions|signature", re.I)


SELECT_ALL = re.compile(r"\s*(?:select|check|accept|agree to) all\b", re.I)


# Legal disclosures a model may answer only from the applicant's facts. Precise on purpose: "National Defense
# Authorization Act", "Authentication, Authorization" (a security skill), "authorized to prescribe", "government ID",
# "Citizens" (a bank), "agenda" and "referred to as" are not disclosures (2026-10-18/19 production refusals).
CRITICAL = re.compile(
    IMMIGRATION_VISA + r"|sponsor|authori[sz](?:ed|ation)\b.{0,40}\b(?:work|be employed|employment)\b|"
    r"(?:work|employment)\s+authori[sz]|\bauthori[sz]e\b|\bcitizen(?:ship)?\b|\bcitizens of\b|convict|criminal|"
    + GOVERNMENT
    + r"|\bnda\b|non.?disclosure|corrupt|restrictive|non[\s-]?(?:compet|solicit)|\breferr\w*\b(?!\s+to\s+as\b)|consent|"
    r"(?<!dis)agree\b|certify|export.?control|sanction|embargo|security clearance|clearance level|\bfired\b|"
    r"involuntarily discharged|asked to resign",
    re.IGNORECASE,
)


CONTACT_CONSENT = re.compile(
    r"\b(?:I )?(?:authori[sz]e|consent|agree)\b.{0,80}\b(?:communicat\w*|contact\w*|reach out|text(?: message)?s?|sms|call)\b",
    re.I,
)


ACKNOWLEDGEMENT = re.compile(
    r"\bI (?:understand|acknowledge|have read|agree|accept|certify|consent|authori[sz]e|am consenting|hereby)\b|"
    r"\bif you consent\b|\bdo you (?:consent|agree)\b|\bby (?:checking|selecting|clicking|submitting)\b.{0,40}\byou (?:consent|agree)\b",
    re.I,
)


# Consent to a hiring check ("If extended a job offer, are you willing to submit to a comprehensive background check?",
# "I am willing to submit to a review of my criminal history"). It discloses nothing about the applicant's record, and a
# recorded clean record does not answer it: that answered "No" to a willingness question (2026-10-19).
CHECK_WILLINGNESS = re.compile(
    r"\b(?:willing|agree|consent|authori[sz]e)\b.{0,40}\b(?:submit|undergo|complete|to)\b.{0,60}"
    r"\b(?:background|criminal|drug|screen\w*|check|investigation|review|verification)\b",
    re.I,
)


# A statement about the applicant's own record turns a consent into a disclosure.
RECORD_STATEMENT = re.compile(
    r"\b(?:have|has|had)\s+(?:not\s+|never\s+)?(?:ever\s+)?been\s+(?:convicted|charged|arrested)|\bconvicted\b|"
    r"\b(?:no|any|never)\b.{0,20}\bcriminal (?:record|history|conviction)",
    re.I,
)


# Applicant disclosures an acknowledgement could embed. Phrase-level, so legal boilerplate
# ("no representative is authorized to ...", "terminate my employment") does not count.
# A signature that only acknowledges reading a notice asserts nothing about the applicant. CDW (2026-10-22): "If your
# role requires it, you will be asked after hire ... your status as a U.S. Person ... By typing my name below, I
# acknowledge that I have read the following statement" went to the owner as an export-control disclosure.
READ_ACKNOWLEDGEMENT = re.compile(r"\bI (?:acknowledge|confirm|understand)(?: that)? I (?:have )?read\b", re.I)
SELF_ASSERTION = re.compile(
    r"\bI (?:am|was|have been|have never|do not|don't|will not|certify that|attest that|declare that)\b", re.I
)


EMBEDDED_DISCLOSURE = re.compile(
    r"authori[sz]ed to work|work authori[sz]ation|eligib\w* to work|sponsor|\bvisa\b|citizen|"
    r"convict|felon|misdemeanor|\b(?:no|any|never)\b.{0,20}criminal|non[\s-]?(?:compet|solicit)|restrictive covenant|"
    r"government (?:employ|official)|security clearance|export control|sanction|"
    r"(?:been|ever been) (?:fired|terminated|discharged)|asked to resign|related to|relative",
    re.I,
)


# Blackstone 2026-10-18: "please select those you are most interested in applying to. By selecting below, you consent
# to Blackstone ... considering you for relevant opportunities within other business units."
SELECTION_CONSENT = re.compile(
    r"\bby (?:selecting|choosing|checking)\b[^.]{0,30}\b(?:below|an? option|any|these|this)\b[^.]{0,20}"
    r"\byou (?:consent|agree|acknowledge)\b",
    re.I,
)


# A typed signature box under a certification ("By entering your name into the field below, you certify that this
# application is truthful and complete").
SIGNED_CERTIFICATION = re.compile(
    r"\b(?:sign(?:ature|ed|ing)?|e-?signature|initials)\b|"
    r"\b(?:enter|entering|type|typing|input|provide)\s+(?:in\s+)?(?:your\s+|my\s+)?(?:full\s+|legal\s+)?name\b|"
    r"\b(?:certify|attest)\b",
    re.I,
)


def policy_acknowledgement(label, answer, facts, subjects):
    """An affirmed acknowledgement ("I understand employment is at will") follows the consent policy.

    MassMutual 2026-10-14: options "Yes, I agree / No, I do not consent" made the statement
    critical with no subject, so the applicant's accept-all-terms policy was refused and the
    question went to the owner. A statement that also embeds a real disclosure (conviction,
    sponsorship, non-compete, ...) still needs its own fact.
    """
    if "fact:application_consent_policy" not in facts or set(subjects) - {"consent", "background_check"}:
        return False
    if SELECTION_CONSENT.search(label) and not EMBEDDED_DISCLOSURE.search(label):
        return True  # Choosing an offered option is the consent; the choice itself is validated as any other.
    willing = CHECK_WILLINGNESS.search(label) and not RECORD_STATEMENT.search(label)
    if not (willing or ACKNOWLEDGEMENT.search(label)) or EMBEDDED_DISCLOSURE.search(label):
        return False
    return bool(
        re.match(
            r"\s*(?:yes|i agree|agree|accept|i accept|i understand|i acknowledge|i consent|consent|opt[- ]?in|"
            r"i am willing|willing|true|checked)\b",
            str(answer.value or ""),
            re.I,
        )
    )


def critical_question(label):
    # Protect scoped disclosures by meaning even if their label has no legal
    # keyword. Employer names must not be doing the work of this safeguard.
    return bool(CRITICAL.search(NDA_INSTRUCTION.sub(" ", label))) or topic(label) in {
        "prior_employment",
        "client_relationship",
        "consent",
        "restriction",
    }


# Routine acknowledgements (reported in the consent ledger; acceptance follows the profile policy).
STANDARD_CONSENT = re.compile(
    r"privacy (?:policy|notice|statement)|terms (?:and|&) conditions|terms of (?:use|service)|data (?:privacy|protection|processing)|"
    r"(?:read|reviewed|understand|acknowledge|accept|agree)\b.{0,80}\b(?:policy|notice|statement|terms|conditions|consent)|"
    r"consent to (?:the )?(?:processing|collection|storage|use) of my|"
    r"(?:certify|attest|confirm) (?:that )?(?:the |all )?(?:information|answers|statements).{0,100}(?:true|accurate|correct|complete)|"
    r"information (?:provided|submitted) (?:is|was) (?:true|accurate|complete)|electronic signature|e.?signature|"
    r"retain my (?:information|data|application)|talent (?:community|pool|network)|future (?:opportunities|openings)",
    re.IGNORECASE,
)


# Rivian (Ashby) 2026-10-18: "I authorize the RV Tech Talent Acquisition team to consider me for other job opportunities
# within RV Tech ..." Being considered for more roles is what the applicant wants, and it discloses nothing.
OTHER_ROLES_CONSENT = re.compile(
    r"consider (?:me|my (?:application|profile|candidacy|resume|information))\b.{0,60}"
    r"\b(?:other|additional|future|similar|alternative)\b.{0,30}(?:opportunit|opening|role|position|job)",
    re.I,
)


# A request for consent to a hiring step (S&P Global / Kensho "Candidate Consent: Use of AI-Assisted Screening Tool
# (HiredScore) ... requests your consent"). Its explanation may list protected traits it ignores; it asks for none.
CONSENT_REQUEST = re.compile(
    r"\brequests? your consent\b|^\s*candidate consent\b|\bdo you (?:consent|agree) to (?:the |our )?(?:use|processing)\b",
    re.I,
)


# Gas South (Greenhouse) 2026-10-15: "... Please select "Yes" in the dropdown to confirm that you have read this
# statement." The label names the option that acknowledges the statement.
# An option that only records reading an instruction.
ACKNOWLEDGING_ONLY = re.compile(
    r"(?:thank you|thanks|ok(?:ay)?|understood|i understand|acknowledged?|i acknowledge|noted|got it|done|"
    r"i have read(?: this| the above)?|read and understood)"
)


INSTRUCTED_OPTION = re.compile(
    r"\b(?:select|choose|check|click|mark)\s+[\"'“‘](?P<option>[^\"'”’]{1,40})[\"'”’]"
    r"(?:\s+(?:in|from|on)\s+the\s+(?:dropdown|drop-down|list|menu|box|options?))?\s+to\s+"
    r"(?:confirm|acknowledge|indicate|certify)\s+(?:that\s+)?you\s+(?:have\s+)?(?:read|reviewed|received|understood|understand)",
    re.I,
)


# Not identity questions, though they name protected traits: a consent request or a statement to acknowledge, and an
# availability question that exempts religious or medical time off (Kairos Power 2026-10-15: "Other than time off
# for reasons related to your religion, a disability, or a medical condition, are there any days or times when you
# are unavailable to work?").
NOT_IDENTITY = re.compile(
    CONSENT_REQUEST.pattern
    + r"|"
    + INSTRUCTED_OPTION.pattern
    + r"|\b(?:un)?available to work\b|\bdays or times\b",
    re.I,
)


# Optional marketing/SMS opt-ins are never auto-accepted.
MARKETING = re.compile(
    r"text message|sms|\btext(?:s|ing)? (?:you|me|alerts?|notifications?|updates?)\b|"
    r"marketing|newsletter|promotional|job alerts?",
    re.IGNORECASE,
)


TODAY = re.compile(
    r"^(?:today\s*s?\s+)?date$|^date signed$|signature date|today\s*s?\s+date|^date of signature$|"
    # Dayforce (HH Global 2026-10-16): "Disability Questionnaire Answered Date" is the form's own date stamp.
    r"\b(?:answered|completed|completion) date$|\bdate (?:answered|completed)$",
    re.I,
)


# "Do you require any reasonable accommodations to participate in the interview process?"
ACCOMMODATION = re.compile(
    r"\b(?:need|needs|require|requires|requiring|request|requesting)\b.{0,60}\baccommodat|"
    r"\baccommodat\w*\b.{0,40}\b(?:needed|required|necessary)\b",
    re.I,
)


SIGNATURE = re.compile(
    r"\b(?:signature|sign your (?:full )?name|type your (?:full )?(?:legal )?name|by entering your (?:full )?name|"
    r"full name as signature|e[\s-]?sign(?:ature)?|"
    r"full legal name as evidence.{0,60}(?:read|agree))\b",
    re.I,
)


NEGATED = re.compile(r"\b(?:not|never|no longer|n't|unable|cannot)\b", re.IGNORECASE)


def negation_text(label):
    """The question head without agreement names that contain "not" ("covenants not to compete")."""
    return re.sub(r"\bnot to (?:compete|solicit)\b", "", question_head(label), flags=re.I)
