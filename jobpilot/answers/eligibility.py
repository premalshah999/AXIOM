"""Answer patterns: eligibility."""

import re


# Owner policy (2026-10-13): always "No" to employment with this employer, its parent/sister/
# affiliates, clients, partners or listed companies; family/household/close relationships there;
# government employment and government-official / anti-corruption questions.
OWNER_NO_SUBJECT = re.compile(
    r"client|prime contractor|partner|subsidiar|affiliat|parent (?:company|organi[sz]ation)|sister compan|"
    r"any of the following|family|household|relative|spouse|in.law|close (?:personal )?relationship|"
    r"government|post.government|public official|politically exposed|anti.?(?:corruption|bribery)|board of directors|"
    # Leidos 2026-10-14: "Section 847 ... requires certain current and former Department of Defense (DoD) officials ..."
    r"department of defense|\bdod\b|federal (?:agency|official)|military official",
    re.I,
)


OWNER_NO_ACT = re.compile(
    r"(?:are|were|is|have|has|had) (?:you|any)\b.{0,80}(?:employ(?:ed|ee)|worked|work(?:s|ing)? (?:for|at)|interned)|"
    r"(?:current|former|previous) (?:or former )?employee|employment history|government employment|"
    r"official|close (?:personal )?relationship|related to|relative|member of (?:your )?(?:family|household)|"
    r"family member|household member|"
    # Goldman Sachs 2026-10-18: "Do you currently hold, or have you held in the past five years, a position with a
    # government, regulatory, or Intergovernmental Organization (IGO)?"
    r"\b(?:hold|held|holding)\b.{0,60}\bposition\b",
    re.I,
)


# Willingness, consent and eligibility questions about the same subjects are not "No" questions.
OWNER_NO_EXCLUDE = re.compile(
    # "National Defense Authorization Act" names a law; it is not a work-authorization question.
    r"willing|able to|consent|agree|acknowledg|authori[sz](?!ation\s+act)|understand|eligib|clearance|citizen|sponsor|"
    r"background (?:check|investigation)|emergency contact|refer",
    re.I,
)


NO_OPTION = re.compile(
    r"^\s*no\b|\bnever\b|\bi am not\b|\bi have not\b|\bi do not\b|\bnone of\b|^none\b|not (?:a |an )?(?:current|former)|"
    r"^\s*not to my knowledge\b",
    re.I,
)


# Work Opportunity Tax Credit screeners (Caesars, Oracle): benefits and veteran questions.
WOTC = re.compile(
    r"\bSNAP\b|food stamps|\bTANF\b|temporary assistance for needy|supplemental security income|\bSSI\b|"
    r"vocational rehabilitation|ticket to work|unemployment (?:compensation|benefits|insurance)|"
    r"empowerment zone|rural renewal|summer youth|veteran|"
    # ADP's tax-credit questionnaire (Terminix via Workday, 2026-10-23): follow-ups that do not repeat the program name.
    r"household received assistance|state workforce agency|\bSWA\b|work opportunity tax credit|"
    r"served in the (?:u\.?s\.? )?(?:military|armed forces)",
    re.I,
)


# Federal healthcare program exclusion / sanction screens (Ochsner).
HEALTHCARE_EXCLUSION = re.compile(
    r"medicare|medicaid|federal health ?care program|\bOIG\b|excluded from (?:participation|federal)|"
    r"debarred|exclusion list|sanctioned by",
    re.I,
)


def no_fact(value):
    """A confirmed No: False or "No (confirmed by applicant)"."""
    return (
        value is False
        or bool(re.match(r"\s*(?:no|false)\b", str(value or ""), re.I))
        and value not in (None, "")
    )


US_STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}


# Jerry (Ashby) 2026-10-18: "Where do you currently reside? Jerry.ai can only hire in the locations listed below. If your
# location is not listed, unfortunately we are unable to process your application." Maryland was not listed.
RESIDENCE_QUESTION = re.compile(
    r"where do you (?:currently )?(?:reside|live)|(?:state|country|location) of (?:current )?residence|"
    r"which (?:state|country|location)s? do you (?:currently )?(?:reside|live)|(?:currently )?(?:reside|live) in one of",
    re.I,
)


HIRING_LIMIT = re.compile(
    r"can only (?:hire|employ|consider)|only (?:able to )?(?:hire|employ)|(?:unable|not able) to (?:process|consider|hire|employ)|"
    r"(?:only|currently) hiring in|not (?:currently )?hiring in",
    re.I,
)


# NerdWallet (Ashby) 2026-10-19: "What province in Canada will you be working from?" lists only provinces.
WORK_FROM_QUESTION = re.compile(
    r"(?:which|what) (?:province|state|territory|country)(?: (?:in|of) [A-Z][A-Za-z ]{2,20})? (?:will|would|do|are) you "
    r"(?:be )?(?:working|work|live|living|based|located) (?:from|in)\b",
    re.I,
)


# "Which province in South Africa do you currently live in?" (Mass Markets), "What province in Canada will you be
# working from?" (NerdWallet): the job needs residence in another country.
FOREIGN_RESIDENCE = re.compile(
    r"\b(?:which|what) (?:province|state|region|territory|county|city)\b.{0,15}?\bin (?P<country>[a-z .]{2,30}?) "
    r"(?:do|will|are|would) you\b.{0,30}?\b(?:live|reside|resid|based|work(?:ing)? from)",
    re.I,
)


def residence_outside(question, options, facts, location=""):
    """The applicant's US state when a residence question limits hiring to listed places that exclude it, else None."""
    limited = RESIDENCE_QUESTION.search(question or "") and HIRING_LIMIT.search(question or "")
    foreign = FOREIGN_RESIDENCE.search(question or "")
    foreign = bool(foreign and OTHER_COUNTRY.search(foreign["country"]))
    if not (limited or foreign or WORK_FROM_QUESTION.search(question or "")) or not (options or foreign):
        return None
    state = str((facts or {}).get("state_of_residence") or "").strip()
    code = next((c for c, name in US_STATES.items() if name.casefold() == state.casefold()), "")
    if not code and (match := re.search(r",\s*([A-Z]{2})\b", location or "")) and match[1] in US_STATES:
        code, state = match[1], US_STATES[match[1]]
    if not code:
        return None
    if foreign:
        return state  # Every offered place is in that other country.
    for option in map(str, options):
        if re.search(rf"\b(?:{re.escape(state)}|{code})\b", option, re.I) or re.search(
            r"\bother\b|\bnone of\b|not listed|\banywhere\b|\bremote\b", option, re.I
        ):
            return None
    return state


# Facts saved for the US (authorization, sponsorship, citizenship, visa) do not answer the same
# question about another country.
COUNTRY_SCOPED = {
    "requires_sponsorship",
    "requires_future_sponsorship_us",
    "needs_sponsorship",
    "work_authorized_us",
    "work_authorized",
    "work_authorization_us",
    "us_citizen",
    "visa_status",
}


OTHER_COUNTRY = re.compile(
    r"\b(?:canada|canadian|united kingdom|u\.?k\.?|britain|british|england|scotland|ireland|irish|germany|"
    r"german|france|french|netherlands|dutch|spain|spanish|italy|portugal|poland|switzerland|swiss|sweden|"
    r"norway|denmark|finland|belgium|austria|europe|european union|e\.?u\.?|eea|india|indian|china|"
    r"chinese|japan|singapore|australia|australian|new zealand|mexico|brazil|argentina|israel|uae|"
    r"dubai|philippines|south africa|korea|hong kong|taiwan|vietnam|indonesia|malaysia|colombia|chile|"
    r"costa rica|qatar|saudi|egypt|nigeria|kenya|turkey|romania|czech|hungary|ukraine|greece)\b",
    re.I,
)


NOT_DESIRED = re.compile(r"\b(?:current|previous|present|last|prior|most recent|past)\b", re.I)


NOT_AVAILABILITY = re.compile(
    r"\b(?:current|previous|last|most recent|prior|past) (?:job|role|position|employer|company)|"
    r"start date (?:at|with|of|for) (?:your|the|this)? ?(?:current|previous|last|prior)",
    re.I,
)
