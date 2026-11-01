"""Target-role matching for every discovery path, driven by each profile's own target roles.

A role matches a posting title when one of its known title forms appears in the title as whole words (in any
order), so "Software Development Engineer" also finds "Software Engineer II" and "SDE, New Grad", and
"Data Science" finds "Data Scientist". Nothing here is specific to one applicant: the roles come from the
profile, and the synonym groups are ordinary job-title vocabulary.
"""

import re

# Equivalent title forms. A target role in any group matches every form in that group.
SYNONYMS = (
    ("software engineer", "software developer", "software development engineer", "sde", "swe",
     "software engineering", "software dev engineer", "developer engineer"),
    ("machine learning engineer", "ml engineer", "mle", "machine learning developer", "ml developer",
     "machine learning engineering"),
    ("ai engineer", "artificial intelligence engineer", "applied ai engineer", "ai ml engineer",
     "generative ai engineer", "genai engineer", "llm engineer", "ai developer", "ai engineering"),
    ("data scientist", "data science", "data science engineer"),
    ("data engineer", "data engineering"),
    ("data analyst", "data analytics", "analytics analyst"),
    ("analytics engineer",),
    ("machine learning scientist", "applied scientist", "ml scientist"),
    ("research engineer",),
    ("research scientist",),
    ("mlops engineer", "ml platform engineer", "machine learning platform engineer", "ml ops engineer"),
    ("full stack engineer", "full stack developer", "fullstack engineer", "fullstack developer"),
    ("backend engineer", "back end engineer", "backend developer", "back end developer"),
    ("frontend engineer", "front end engineer", "frontend developer", "front end developer"),
    ("devops engineer", "site reliability engineer", "sre"),
    ("cloud engineer",),
    ("product manager",),
)
STOP = {"and", "or", "of", "the", "a", "an", "in", "for", "with", "to", "at"}


def words(text):
    """Lowercase title words; "AI/ML", "Full-Stack" and "Engineer, ML" split into plain words."""
    return [w for w in re.split(r"[^a-z0-9+#]+", str(text or "").casefold()) if w and w not in STOP]


def forms(role):
    """Every title form the role stands for (its synonym group, or the role itself)."""
    phrase = " ".join(words(role))
    for group in SYNONYMS:
        if phrase in group:
            return group
    return (phrase,) if phrase else ()


def form_in_title(form, title_words):
    """A form matches when all of its words are in the title (order and separators do not matter)."""
    needed = form.split()
    return bool(needed) and all(word in title_words for word in needed)


def matching_role(title, roles):
    """The first target role the title matches, or None."""
    title_words = set(words(title))
    for role in roles:
        if any(form_in_title(form, title_words) for form in forms(role)):
            return role
    return None


# A profile that names one role also fits the specialisations of its family. "Software Engineer" matched "Software
# Engineer I" but not "Front End Engineer II", "Android Engineer" or "Associate Programmer", and "AI Engineer" /
# "Machine Learning Scientist" missed "Research Scientist New Grad - Conversational AI": 360 of 1,198 curated postings
# were counted off_role in one sweep (2026-10-29) and the titles above were among them.
CRAFT = r"(?:engineer|engineers|developer|programmer|scientist|researcher|swe|sde)"
NOISE = re.compile(
    r"annotat|\blabel(?:er|ing)\b|\btrainer\b|data (?:collect|entry)|teleoperat|\bsales\b|marketing|recruit|postdoc|"
    r"\bscout|\blegal\b|\beditor\b|journalis|clinical|\bconsultant\b|\bsupport\b|\btester\b|\bwriter\b",
    re.I,
)
SOFTWARE_FAMILY = frozenset({
    "software", "developer", "swe", "sde", "backend", "frontend", "fullstack", "stack", "programmer", "application",
})
INFRA_FAMILY = frozenset({"platform", "infrastructure", "cloud", "devops", "sre", "reliability"})
DATA_FAMILY = frozenset({"machine", "learning", "ai", "ml", "data", "scientist", "analytics", "analyst", "research", "llm"})
SOFTWARE_TITLE = re.compile(
    r"\b(?:front[- ]?end|back[- ]?end|full[- ]?stack|mobile|ios|android|web|application development|application|software)\b"
    r".*\b" + CRAFT + r"\b|\b(?:programmer|programmer analyst)\b|\b" + CRAFT + r"\b.*\b(?:front[- ]?end|back[- ]?end|full[- ]?stack|ios|android)\b",
    re.I,
)
INFRA_TITLE = re.compile(
    r"\b(?:devops|dev ops|sre|site reliability|cloud|platform|infrastructure)\b.*\b" + CRAFT + r"\b", re.I
)
AI_TITLE = re.compile(
    r"\b(?:ai|ml|machine learning|deep learning|nlp|computer vision|llm|generative|genai)\b.*\b" + CRAFT + r"\b|"
    r"\b" + CRAFT + r"\b.*\b(?:ai|ml|machine learning|deep learning|nlp|computer vision|llm|generative|genai)\b",
    re.I,
)
INFRA_OUTSIDE = re.compile(r"hardware|silicon|security|cyber", re.I)  # "Cloud Hardware Development Engineer"
BI_TITLE = re.compile(r"\b(?:business intelligence|bi) analyst\b", re.I)


def related_role(title, roles):
    """The target role a title fits as a specialisation of that role's family (see above), or None. It never
    overrides matching_role and needs an engineer/developer/scientist-type noun, so an "AI Data Annotator" or an "AI
    Video Creator" stays out."""
    title = str(title or "")
    if NOISE.search(title):
        return None
    for role in roles:
        family = set(words(role))
        if family & SOFTWARE_FAMILY and SOFTWARE_TITLE.search(title):
            return role
        if family & INFRA_FAMILY and INFRA_TITLE.search(title) and not INFRA_OUTSIDE.search(title):
            return role
        if family & DATA_FAMILY and (AI_TITLE.search(title) or ({"analyst", "analytics", "data"} & family and BI_TITLE.search(title))):
            return role
    return None


def role_fits(title, roles):
    """Whether a title is one of the profile's roles or a specialisation of its family: the one question every stage asks.
    Discovery admitted "Cloud Engineer" through related_role, the ranker scored it 64-68 Ready, and the autopilot's and the
    queue's own check (matching_role alone) refused it: 7 Ready jobs, 6 never tried, sat as "outside the target roles"
    (2026-11-01)."""
    return bool(matching_role(title, roles) or related_role(title, roles))


def search_terms(roles, limit=6):
    """Search phrases for the profile's roles: each role plus its most common alternate title."""
    terms = []
    for role in roles:
        group = forms(role)
        terms.append(str(role).strip())
        if len(group) > 1 and group[0].casefold() != " ".join(words(role)):
            terms.append(group[0].title())
    return list(dict.fromkeys(t for t in terms if t))[:limit]


# A profile with admit_by_title applies by title to postings of its own target roles, whatever the fit score said:
# most underscored postings in a role the applicant named were good matches. Roles named only by a generic word
# ("Developer", "Graduate Engineer", "New Grad Engineer", "Platform Engineer", "Research Engineer") are too broad to
# trust by title: they caught silicon, verification and lab work, so they never qualify.
BROAD_WORDS = frozenset({
    "engineer", "developer", "scientist", "analyst", "specialist", "associate", "junior", "graduate", "grad", "new",
    "entry", "level", "early", "career", "university", "platform", "infrastructure", "research",
})
# Title words that mark work outside a software or data profile. Words the profile's own roles contain (a
# mechanical engineer's "mechanical") are not exclusions for that profile.
OUTSIDE_FIELD_WORDS = frozenset({
    "embedded", "firmware", "hardware", "silicon", "soc", "fpga", "asic", "rtl", "verification", "validation",
    "semiconductor", "packaging", "analog", "mechanical", "electrical", "manufacturing", "bilingual",
})
# A numbered level from 3 up ("Full-stack Engineer 4", "Data Engineer 3") is past early career.
LEVEL = re.compile(r"\b(?:engineer|scientist|developer|analyst)\s*(?:[3-9]|v)\b", re.I)
# Requirements the applicant's facts cannot meet; a title never overrides them.
HARD_BLOCKER = re.compile(
    r"clearance|polygraph|\bcitizen(?:ship)?\b|green card|permanent resident|export.control|\bitar\b|bilingual|"
    r"fluen(?:t|cy) in (?!english)[a-z]+|"
    # A ranking that places the role outside the US (Quest's Canada role, MCI's Canada/Philippines/South Africa roles,
    # 2026-10-21): the title never overrides where the job is.
    r"\b(?:canada|canadian|mexico|united kingdom|\buk\b|ireland|germany|france|netherlands|poland|spain|portugal|"
    r"india|philippines|south africa|singapore|australia|brazil|argentina|colombia|costa rica|israel|japan|china)\b|"
    r"outside (?:of )?the (?:us|u\.s\.|united states)|non-us\b|not (?:located )?in the (?:us|united states)",
    re.I,
)

# A ranking that reads a visa bar in the posting: "explicitly bars F-1 OPT", "restriction naming OPT/CPT as disqualifying",
# which a title admitted anyway (2026-11-01). A ranking may also say the opposite, "no explicit F-1/OPT prohibition appears".
_VISA = r"(?:f-?1|(?-i:OPT|CPT)|h-?1b|temporary.visas?)"
_BARS = r"(?:bars?|barred|barring|prohibit\w*|exclu\w+|disqualif\w+)"
VISA_BAR = re.compile(
    rf"\b{_VISA}\b[^.;|]{{0,80}}\b(?:{_BARS}|ineligible|not eligible)\b|\b(?:{_BARS}|restriction)\b[^.;|]{{0,80}}\b{_VISA}\b",
    re.I,
)
NOT_A_BAR = re.compile(r"\b(?:no|not|without|nor|never)\b[^.;|]{0,60}\b(?:prohibit\w*|restrict\w*|ban|bars?|exclu\w+)\b", re.I)


def specific_roles(roles):
    """The target roles named by a discipline rather than only by a generic word."""
    return [role for role in roles or () if set(words(role)) - BROAD_WORDS]


def admitted_by_title(title, reason="", roles=()):
    """Whether a posting's title alone qualifies it for a profile with admit_by_title: it matches one of the
    profile's own specific target roles, is not outside the profile's field or past early career, and the ranking
    found no requirement the applicant cannot meet."""
    specific = specific_roles(roles)
    if not specific:
        return False
    own = {word for role in specific for word in words(role)}
    outside = sorted(OUTSIDE_FIELD_WORDS - own)
    if outside and re.search(r"\b(?:" + "|".join(outside) + r")\b", title or "", re.I):
        return False
    if LEVEL.search(title or "") or HARD_BLOCKER.search(reason or ""):
        return False
    if VISA_BAR.search(NOT_A_BAR.sub(" ", reason or "")):
        return False
    return bool(matching_role(title, specific))


# The job board's level filter (owner, 2026-11-01: "role level filters in job board"). One place reads a posting's level, from
# its title first and its text second, and says "Unknown" when neither states one. Nothing else guesses a level.
LEVELS = ("Intern", "New grad", "Entry", "Mid", "Senior", "Unknown")
_INTERN = re.compile(r"\b(?:interns?|internships?|co-?op|apprentice(?:ship)?s?)\b", re.I)
_NEW_GRAD = re.compile(
    r"\b(?:new[ -]?(?:college[ -])?grad(?:uate)?s?|recent graduates?|(?:university|college)[ -]?grad(?:uate)?s?|early[ -]?career|"
    r"graduate (?:engineer|developer|scientist|analyst|program(?:me)?|software|data|trainee|rotational)|"
    r"(?:engineer|developer|scientist|analyst),? (?:new )?grad)\b",
    re.I,
)
_SENIOR_ASSOCIATE = r"associate(?:\s+(?:vice|general|managing))|associate (?:director|principal|partner|dean|professor)"
_ENTRY = re.compile(
    r"\b(?:entry[ -]?level|junior|jr\.?|level\s*(?:1|i)|(?:engineer|developer|scientist|analyst|swe|sde|specialist|"
    r"programmer|technician|designer|consultant)\s*(?:1|i)(?![\w+])|associate\s+(?:software|data|machine|ai|ml|cloud|"
    r"backend|frontend|full|systems?|network|security|product|business|research|analytics?)?\s*(?:engineer|developer|"
    r"scientist|analyst|consultant|programmer))\b",
    re.I,
)
_MID = re.compile(
    r"\b(?:mid[ -]?level|intermediate|(?<=[,(–-] )mid(?=\s*\)?$)|(?:engineer|developer|scientist|analyst|swe|sde|specialist|programmer|designer|"
    r"consultant)\s*(?:2|ii)(?![\w+]))\b",
    re.I,
)
_SENIOR = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|prin|distinguished|lead|director|head of|vp|vice president|avp|fellow|chief|architect|"
    r"(?:engineer|developer|scientist|analyst|swe|sde|specialist|programmer|designer|consultant)\s*(?:[3-9]|iii|iv|v)(?![\w+])|"
    r"engineering manager|manager,? engineering|group manager|general manager)\b",
    re.I,
)
# "5+ years of experience", "3-5 years' experience", "Five years of ... experience": the stated minimum.
_SPELLED = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_YEARS = re.compile(
    r"\b(?P<low>\d{1,2}|" + "|".join(_SPELLED) + r")\b(?:\s*\(\d{1,2}\))?\s*(?:\+|(?:[-–—]|to)\s*\d{1,2}\+?)?\s*"
    r"(?:years?|yrs?)[’']?\s+(?:[-–—:]\s+)?(?:[a-z][\w/,&-]*\s+){0,8}?(?:experience|exp\b)",
    re.I,
)
# A mention that is not the job's own minimum: a preferred skill (around it), or what a higher degree substitutes (before it: "a
# Master's with 3 years" is the alternative, "a Bachelor's with 5 years or a Master's" names 5 first).
_OPTIONAL = re.compile(r"preferred|nice.to.have|bonus|ideally|a plus|desired", re.I)
_ALTERNATIVE = re.compile(r"master|ph\.?d|doctora|advanced degree", re.I)
# Only an experience claim about the candidate: a company's "60 years of experience across industries" is not one.
_MOST_YEARS = 20
_ENTRY_TEXT = re.compile(r"\bentry[ -]?level\b|\bno (?:prior )?experience (?:is )?(?:required|necessary)\b", re.I)
_BULLET_YEARS = re.compile(r"(?m)^[\W_]*(?P<low>\d{1,2})\s*\+?\s*(?:years?|yrs?)\b")


def stated_years(text):
    """The largest minimum of experience a posting's text requires, in years, or None: the headline requirement ("5+ years") outranks
    the skill-by-skill ones under it ("3+ years of Python"). A mention is read within its own line; preferred skills, a higher
    degree's alternative and absurd figures (a company's age) are left out."""
    text = text or ""
    found = []
    for match in [*_YEARS.finditer(text), *_BULLET_YEARS.finditer(text)]:
        start, end = text.rfind("\n", 0, match.start()) + 1, text.find("\n", match.end())
        end = min(end if end >= 0 else len(text), match.end() + 80)
        if _OPTIONAL.search(text[max(start, match.start() - 120) : end]) or _ALTERNATIVE.search(text[max(start, match.start() - 45) : match.start()]):
            continue
        if re.search(r"education|school|study|degree", match.group(0), re.I):  # "4 years of relevant education and/or experience"
            continue
        low = match["low"].casefold()
        years = _SPELLED.get(low) or int(low)
        if years <= _MOST_YEARS:
            found.append(years)
    return max(found) if found else None


def career_level(title, text="", stored=""):
    """A posting's level, one of LEVELS. The title decides when it says a level ("Senior", "Engineer II", "New Grad",
    "Intern"); otherwise the posting's stated years of experience (0-1 entry, 2-4 mid, 5 or more senior) or its own
    'entry level' wording; otherwise what the feed called it ('New grad'); otherwise "Unknown", never a guess."""
    title = " ".join(str(title or "").split())
    if _INTERN.search(title):
        return "Intern"
    if _NEW_GRAD.search(title):
        return "New grad"
    if _SENIOR.search(title) or re.search(_SENIOR_ASSOCIATE, title, re.I):
        return "Senior"
    if _MID.search(title):
        return "Mid"
    if _ENTRY.search(title):
        return "Entry"
    years = stated_years(text)
    if years is not None:
        return "Entry" if years <= 1 else "Mid" if years <= 4 else "Senior"
    if _ENTRY_TEXT.search((text or "")[:6000]):
        return "Entry"
    return "New grad" if str(stored).casefold() == "new grad" else "Unknown"
