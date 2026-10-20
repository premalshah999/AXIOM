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
# Where a profile has no target roles yet, discovery keeps its original AI/ML focus.
DEFAULT_ROLES = ("Machine Learning Engineer", "AI Engineer")


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


def search_terms(roles, limit=6):
    """Search phrases for the profile's roles: each role plus its most common alternate title."""
    terms = []
    for role in roles:
        group = forms(role)
        terms.append(str(role).strip())
        if len(group) > 1 and group[0].casefold() != " ".join(words(role)):
            terms.append(group[0].title())
    return list(dict.fromkeys(t for t in terms if t))[:limit]


# Owner 2026-10-18: postings in these families were usually good matches even when the fit score was low, so a profile
# with admit_by_title applies to them by title. Broad families ("Developer", "Graduate Engineer", "Research Engineer")
# do not qualify: they caught silicon, verification and lab roles.
TITLE_ADMIT_FAMILIES = (
    "data scientist", "data analyst", "analytics engineer", "machine learning engineer", "machine learning scientist",
    "ai engineer", "software engineer", "forward deployed engineer", "data engineer", "full stack engineer",
    "backend engineer",
)
NOT_SOFTWARE = re.compile(
    r"\b(?:embedded|firmware|hardware|silicon|soc|fpga|asic|rtl|verification|validation|semiconductor|packaging|"
    r"analog|mechanical|electrical|manufacturing|bilingual)\b",
    re.I,
)
# A numbered level from 3 up ("Full-stack Engineer 4", "Data Engineer 3") is past early career.
LEVEL = re.compile(r"\b(?:engineer|scientist|developer|analyst)\s*(?:[3-9]|v)\b", re.I)
# Requirements the applicant's facts cannot meet; a title never overrides them.
HARD_BLOCKER = re.compile(
    r"clearance|polygraph|\bcitizen(?:ship)?\b|green card|permanent resident|export.control|\bitar\b|bilingual|"
    r"fluen(?:t|cy) in (?!english)[a-z]+",
    re.I,
)


def admitted_by_title(title, reason=""):
    """Whether a posting's title alone qualifies it under the owner's admit-by-title policy."""
    if NOT_SOFTWARE.search(title or "") or LEVEL.search(title or "") or HARD_BLOCKER.search(reason or ""):
        return False
    return bool(matching_role(title, TITLE_ADMIT_FAMILIES))
