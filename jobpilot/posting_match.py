"""Whether a page belongs to the posting this run was given, from what the page and its address state.

A run was given one job and applied to another (2026-11-01, saved as 13045, sent as 14102): its saved route bounced to the career
home, the guided browser opened a different role from the list, and the receipt was booked against the job it was given. Three
guards use this module: the browser may not open another posting, the commit stops on one, and a receipt naming another role is
not a confirmation of this job.

Two sides of one rule, and the second matters as much as the first. A clear difference stops a run. Anything unclear is not a
difference: a page whose identity can't be read, another site (a hand-off to an ATS numbers postings its own way), a form address
that adds an id the board address lacked, a heading that names a step rather than a role. Stopping sends nothing, but it also
loses an application, so a stop has to be something a person would call a different job.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

from .accounts.tenants import registrable

# ----- the posting an address names ---------------------------------------------------------------------------------------

POSTING_PARAMS = {
    "ghjid", "jobid", "jid", "reqid", "requisitionid", "jobreqid", "careerjobreqid", "postingid", "positionid",
    "jobnumber", "jobcode", "jobpostingid", "opportunityid", "vacancyid", "openingid", "job",
}  # fmt: skip
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
YEAR = re.compile(r"(?:19|20)\d\d")
COUNTER = re.compile(r"(?<=[0-9A-Za-z])-\d{1,2}$")  # A repost counter: R0076343-1 is R0076343.
# A whole path segment that is a posting's number or code: 13045, R0076343-1, JR-123456, REQ_0586.
CODE_SEGMENT = re.compile(r"(?:[A-Za-z]{0,5}[-_]?)?\d{3,}(?:-\d{1,2})?")
# A title that carries its code after an underscore, as Workday's do: Software-Engineer_R0076343-1, Analyst_10079702-WD-1, Backend_Q326SWEB2.
UNDERSCORE_TAIL = re.compile(r".*[A-Za-z0-9-]_([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)")
# Or after a hyphen: senior-engineer-73421, Engineer-REQ-0586.
HYPHEN_TAIL = re.compile(r".*[A-Za-z0-9][-]((?:[A-Za-z]{1,5}-?)?\d{4,})(?:-\d{1,2})?")
# Or first: 7440000-senior-engineer, e59f8d41d06d01-senior-engineer.
HEAD = re.compile(r"(\d{6,}|[0-9a-f]{10,})-[A-Za-z].*")
# What follows a posting's address on the way through its application, never another posting.
STEP_WORD = re.compile(
    r"apply(?:manually|now|withresume)?|applications?|applicationform|autofill|login|signin|sign-in|register|confirmation|thanks|"
    r"thank-?you|success|submitted|completed?|review|form|job|step-?\d*|\d",
    re.I,
)
# A hyphenated page name of the site, not a role ("create-account", "job-search").
SITE_PAGE_WORD = re.compile(
    r"\b(?:account|apply|application|applications|about|alerts?|benefits|candidate|career|careers|contact|culture|dashboard|"
    r"events?|faqs?|forgot|home|index|internships?|jobs?|locations?|log|login|openings?|opportunities|password|portal|privacy|"
    r"profile|register|reset|results?|roles?|search|settings|sign|signin|signup|students?|talent|teams?|terms|verify)\b",
    re.I,
)


def _segments(url):
    return [unquote(part) for part in urlsplit(str(url or "")).path.split("/") if part]


def _site(url):
    return registrable((urlsplit(str(url or "")).hostname or "").lower().removeprefix("www."))


def _code(text):
    return COUNTER.sub("", text).replace("-", "").replace("_", "").casefold()


def _segment_id(segment, *, last):
    """The posting number a path segment holds, else None."""
    if UUID.fullmatch(segment):
        return segment.casefold()
    if CODE_SEGMENT.fullmatch(segment):
        # A bare year in the middle of a path is part of a date.
        return None if not last and YEAR.fullmatch(segment) else _code(segment)
    for pattern in (UNDERSCORE_TAIL, HYPHEN_TAIL, HEAD):
        if (match := pattern.fullmatch(segment)) and any(c.isdigit() for c in match[1]) and len(match[1]) >= 3:
            # "2027" ends many titles ("New-Grad-2027"): it numbers no posting.
            return None if YEAR.fullmatch(match[1]) else _code(match[1])
    return None


def query_ids(url):
    """{normalized key: value} of the parameters that name a posting. A value must hold a digit: ?job=apply names none."""
    found = {}
    for key, value in parse_qsl(urlsplit(str(url or "")).query):
        name = re.sub(r"[^a-z]", "", key.casefold())
        if name in POSTING_PARAMS and len(value) >= 3 and any(c.isdigit() for c in value):
            found[name] = _code(value)
    return found


def posting_ids(url):
    """Every posting number the address states."""
    segments = _segments(url)
    return {
        *query_ids(url).values(),
        *(found for i, part in enumerate(segments) if (found := _segment_id(part, last=i == len(segments) - 1))),
    }


def _strip_steps(segments):
    out = list(segments)
    while out and STEP_WORD.fullmatch(out[-1]):
        out.pop()
    return out


def _slug(segment):
    return len(re.findall(r"[^\W\d_]{2,}", segment)) >= 2 and bool(re.search(r"[-_ ]", segment))


def _same_shape(a, b):
    """The two addresses are the same kind of page: every segment equal, apart from the posting's number and its readable slug."""
    if len(a) != len(b):
        return False
    for i, (x, y) in enumerate(zip(a, b)):
        if x.casefold() == y.casefold():
            continue
        numbered = _segment_id(x, last=i == len(a) - 1) and _segment_id(y, last=i == len(b) - 1)
        if not numbered and not (_slug(x) and _slug(y)):
            return False
    return True


def other_posting_address(own_url, url):
    """ "query", "path" or "slug" when the address is plainly another posting's: the same kind of page on the same site with another
    number (`/job/12` and `/job/34`, `?jobId=12` and `?jobId=34`), or, where a site's addresses carry no number, another role's
    slug under the same parent. None when unclear, including on another site, where a hand-off numbers the same posting its own way."""
    if not own_url or not url or _site(own_url) != _site(url):
        return None
    mine, here = query_ids(own_url), query_ids(url)
    if (shared := mine.keys() & here.keys()) and all(mine[key] != here[key] for key in shared):
        return "query"
    # One number in common: the same posting, whatever else the address says.
    if posting_ids(own_url) & posting_ids(url):
        return None
    own_parts, parts = _strip_steps(_segments(own_url)), _strip_steps(_segments(url))
    if not own_parts or not parts or not _same_shape(own_parts, parts):
        return None
    if [p.casefold() for p in own_parts] == [p.casefold() for p in parts]:
        return None
    if posting_ids("/" + "/".join(own_parts)) and posting_ids("/" + "/".join(parts)):
        return "path"
    # No numbers: two readable slugs side by side ("/careers/data-analyst", "/careers/senior-engineer"), when both read as roles.
    if (
        [p.casefold() for p in own_parts[:-1]] == [p.casefold() for p in parts[:-1]]
        and _slug(own_parts[-1])
        and _slug(parts[-1])
        and not SITE_PAGE_WORD.search(parts[-1].replace("-", " ").replace("_", " "))
        and not SITE_PAGE_WORD.search(own_parts[-1].replace("-", " ").replace("_", " "))
    ):
        return "slug"
    return None


def unrelated_posting_number(own_urls, url):
    """The address states a posting number that none of the addresses this run owns on that site states. Weaker than
    other_posting_address: an application form can number the posting its own way (a career site's `/job/900100200` opens
    `/talentcommunity/apply/45678`), so this only means something when the run did not walk there from its own posting."""
    here = posting_ids(url)
    mine = {found for own in own_urls if own and _site(own) == _site(url) for found in posting_ids(own)}
    return bool(here and mine and not mine & here)


# ----- the role a page or receipt names -------------------------------------------------------------------------------------

GENERIC_ROLE = re.compile(
    r"(?:(?:this|the|your|a|an|our|that|these|those|each|any|one|open|new|above|following|selected|applied|same|potential)\s+)*"
    r"(?:job|jobs|position|positions|role|roles|opening|openings|opportunity|opportunities|vacancy|vacancies|posting|requisition|"
    r"application|applications|employment|company|team|us|organization|organisation|career|careers|work|talent\s+community|"
    r"general\s+application|consideration|job\s+application|job\s+opening|job\s+posting)?",
    re.I,
)
# "Your application for X has been submitted", "Thank you for applying for X at Acme", "Application for X was received". Only wording
# that says the application itself is for X. "To" names the employer far more often than a role ("Thank you for applying to NCR
# Voyix!"), and "Interested in other roles like X?" names a recommendation.
ROLE_IN_RECEIPT = re.compile(
    r"(?:\b(?:your|my)\s+(?:job\s+)?application\s+for|"
    r"\bthank(?:s| you)\s+for\s+(?:your\s+(?:job\s+)?application\s+for|applying\s+for)|"
    r"\b(?:you(?:'ve| have)?\s+|successfully\s+)applied\s+for|"
    r"(?:^|[.!?]\s+)application\s+for)\s+"
    r"(?:the\s+(?:position|role|job|opening)\s+(?:of\s+)?|(?:the\s+)?(?:position|role|job|opening)\s+of\s+|the\s+)?"
    r"(?P<role>[^.!?\n]{2,140}?)"
    r"(?=\s+(?:has|have|was|were|is|are|at|with|on|in|by|we|and|position|role|job|opening|requisition|req\b)\b|\s*[.!?\n(]|\s*$)",
    re.I | re.M,
)
# A form that says which job it is for: "Apply for X", "Applying for X", "Application for X", "Apply: X".
ROLE_ON_FORM = re.compile(
    r"^\s*(?:(?:you(?:'re| are)\s+)?apply(?:ing)?\s+for|apply\s*[:–—-]|application\s+for|job\s+application\s*[:–—-]|"
    r"(?:job\s+title|position|role)\s*:)\s*(?:the\s+)?(?P<role>.{2,140}?)\s*$",
    re.I,
)
POSTING_NUMBER_TEXT = re.compile(
    r"\b(?:requisition|job\s*(?:requisition|posting)?|req|posting|position|vacancy)\s*"
    r"(?:id|number|no\.?|#|code)\s*[:#]?\s*([A-Za-z]{0,5}[-_]?\d{3,})\b",
    re.I,
)
FILLER = {"a", "an", "and", "the", "of", "for", "to", "in", "at", "on", "with", "or", "new", "open", "role", "position", "job", "remote"}  # fmt: skip
SAME_WORD = {"sr": "senior", "jr": "junior", "mgr": "manager", "eng": "engineer", "engg": "engineer", "dev": "developer", "swe": "engineer", "sde": "engineer"}  # fmt: skip
LEVEL = {"i", "ii", "iii", "iv", "v", "vi", "1", "2", "3", "4", "5", "6"}
LEVEL_SAME = {"1": "i", "2": "ii", "3": "iii", "4": "iv", "5": "v", "6": "vi"}
QUOTES = " \t\"'“”‘’:-"
NOT_A_ROLE_WORD = re.compile(r"\b(?:jobs?|careers?|employment|opportunit(?:y|ies)|positions?|openings?|vacanc(?:y|ies)|team|us|company)\b", re.I)


def _words(text):
    return [SAME_WORD.get(w, w) for w in re.findall(r"[^\W_]+", str(text or "").casefold())]


def _meaningful(text):
    return {w for w in _words(text) if w not in FILLER and w not in LEVEL and len(w) > 1}


def _levels(text):
    return {LEVEL_SAME.get(w, w) for w in _words(text) if w in LEVEL}


def clearly_other_role(own_title, stated, company=""):
    """`stated` is a role, and plainly not the job's: no word of a role in common, or the same role at another numbered level
    (Engineer I and Engineer II are different requisitions). Two titles for one job share words; "Staff Engineer" and "Senior
    Engineer" can't be told apart here, and an id decides those. Unreadable, generic, or the employer's own name: not a role."""
    from .posting_identity import placeholder_title

    own, role = _meaningful(own_title), _meaningful(stated)
    if not own or not role or GENERIC_ROLE.fullmatch(str(stated or "").strip()):
        return False
    if placeholder_title(own_title) or placeholder_title(stated):
        return False
    if company and role <= _meaningful(company) | {"inc", "llc", "ltd", "corp", "corporation", "company", "co", "group", "careers"}:
        return False  # "Thank you for applying for Acme".
    if own & role:
        one, other = _levels(own_title), _levels(stated)
        return bool(own == role and one and other and not one & other)
    if any(a in b or b in a for a in own for b in role if min(len(a), len(b)) >= 4):
        return False  # "Softwareentwickler" and "Software Developer": one word holds the other, as a translation's often does.
    return len(role) >= 2 or len(own) == 1


def role_in_receipt(text, company=""):
    """The role a receipt says the application was for, else ""."""
    for match in ROLE_IN_RECEIPT.finditer(str(text or "")[:3000]):
        role = match["role"].strip(QUOTES)
        if company:
            role = re.sub(rf"\s+(?:at|with|-|–|—|\|)\s+{re.escape(company)}.*$", "", role, flags=re.I).strip()
        # A title starts with a capital: "application for potential employment", "applied for a Social Security number" name no role.
        if role and role[0].isupper() and not GENERIC_ROLE.fullmatch(role):
            return role
    return ""


def role_on_form(headings):
    """The role a form's own heading says it is for ("Apply: Data Analyst"), else "". "Application Form" names a step."""
    for heading in headings or ():
        if match := ROLE_ON_FORM.match(str(heading or "")):
            role = match["role"].strip(QUOTES)
            # "Apply for jobs at Acme", "Application for Employment": a heading can't be taken for a role unless it reads as one.
            if role and role[0].isupper() and not GENERIC_ROLE.fullmatch(role) and not NOT_A_ROLE_WORD.search(role):
                return role
    return ""


def text_posting_numbers(text):
    return {_code(found) for found in POSTING_NUMBER_TEXT.findall(str(text or "")[:3000])}


def receipt_conflict(job, receipt):
    """The plain reason this receipt is not a confirmation of `job`, or None. A generic thank-you names nothing and confirms.
    `receipt["posting_numbers"]` holds the numbers the run walked to from its own posting: a form's own numbering is this job's."""
    job, receipt = job or {}, receipt or {}
    title, company, url = str(job.get("title") or ""), str(job.get("company") or ""), str(job.get("url") or "")
    wording = " ".join(str(receipt.get(key) or "") for key in ("confirmation", "evidence"))
    role = role_in_receipt(wording, company)
    if role and clearly_other_role(title, role, company):
        return f'The employer\'s receipt names "{role[:120]}", but this job is "{title[:120]}". Not recorded as sent for this job'
    own_numbers = posting_ids(url) | {str(n) for n in receipt.get("posting_numbers") or ()}
    receipt_url = str(receipt.get("url") or "")
    if other_posting_address(url, receipt_url) in {"query", "path"} and not posting_ids(receipt_url) & own_numbers:
        return f'The receipt\'s address carries another posting\'s number, not this job\'s ("{title[:120]}"). Not recorded as sent for this job'
    stated = text_posting_numbers(wording)
    if stated and own_numbers and not stated & own_numbers:
        number = sorted(stated)[0].upper()
        return f'The employer\'s receipt carries another posting number ({number}), not this job\'s ("{title[:120]}"). Not recorded as sent for this job'
    return None


# ----- a run's walk through the employer's site ------------------------------------------------------------------------------

LISTING_ADDRESS = re.compile(
    r"(?:^|/)(?:search(?:jobs|results?)?|job-?search(?:-results)?|jobs?|openings?|open-?(?:roles|positions|jobs)|"
    r"current-?(?:openings|opportunities|vacancies)|opportunities|vacancies|positions|all-?(?:jobs|roles|openings)|careers?|"
    r"career-?(?:home|opportunities|portal|site)|home|index(?:\.html?)?|results?|join-?us|work-?with-?us)/?$",
    re.I,
)
ENTRY_LABEL = re.compile(
    r"\s*(?:apply|apply (?:now|here|online|for this (?:job|position|role)|to (?:this )?(?:job|position|role))|"
    r"start (?:your |my )?application|begin application|i'?m interested)\s*",
    re.I,
)
ROLE_LINK = re.compile(r"(?:[\w&+/.'()-]+\s+){1,8}[\w&+/.'()-]+")
ROLE_WORD = re.compile(
    r"\b(?:engineer|developer|analyst|manager|scientist|associate|consultant|designer|director|specialist|architect|administrator|"
    r"coordinator|lead|intern|researcher|programmer|technician|representative|officer|recruiter|accountant|strategist|executive)\b",
    re.I,
)
SEARCH_FIELD = re.compile(
    r"keyword|search|location|country|region|function|category|experience level|business|department|career area|distance|radius|"
    r"posted|sort|job type|remote|^\s*(?:what|where)\b",
    re.I,
)
NOT_FOR_APPLICANT = {"search", "hidden"}
STEP_CONTROL = re.compile(r"\s*(?:next|continue|save (?:and|&) continue|review|submit)\b", re.I)


@dataclass
class Verdict:
    kind: str  # "ok", "other_posting" (its address or its own words name another job) or "listing" (a career home or a job list)
    why: str = ""


def _on_own_address(own_urls, url):
    here = urlsplit(url)
    for own in own_urls:
        there = urlsplit(own)
        if (
            (here.hostname or "").removeprefix("www.") == (there.hostname or "").removeprefix("www.")
            and here.path.rstrip("/").casefold() == there.path.rstrip("/").casefold()
            and (not query_ids(own) or query_ids(own) == query_ids(url))
        ):
            return True
    return False


def _names_own_number(own_urls, url):
    return bool({found for own in own_urls for found in posting_ids(own)} & posting_ids(url))


def links_to_other_postings(controls, own_urls, base):
    """Links on the page to another number in the shape of this job's own address, unless the link states this job's number."""
    found = []
    for control in controls or ():
        href = str(control.get("href") or "")
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        target = urljoin(base, href)
        if not _names_own_number(own_urls, target) and any(other_posting_address(own, target) for own in own_urls):
            found.append(control)
    return found


def role_links(controls):
    """Links that read as job titles, one per title: the rows of a job list."""
    seen = set()
    for control in controls or ():
        label = " ".join(str(control.get("label") or "").split())
        if control.get("href") and len(label) <= 90 and ROLE_LINK.fullmatch(label) and ROLE_WORD.search(label):
            seen.add(label.casefold())
    return seen


def application_fields(obs):
    """Fields a person is asked to answer: a search box and its filters aren't an application."""
    return [
        f
        for f in (obs or {}).get("fields") or ()
        if f.get("type") not in NOT_FOR_APPLICANT and not SEARCH_FIELD.search(str(f.get("label") or ""))
    ]


def looks_like_listing(obs, url, own_urls):
    """A career home, a job search or a list of jobs rather than this posting. Never the job's own address, one that states its
    number, or a page that asks for anything beyond a search: a sign-in, a form and a posting with its Apply control all stay."""
    if _on_own_address(own_urls, url) or _names_own_number(own_urls, url) or application_fields(obs):
        return False
    controls = (obs or {}).get("controls") or ()
    entries = [c for c in controls if ENTRY_LABEL.fullmatch(str(c.get("label") or ""))]
    # One Apply, even at the top and foot of the page, is this page's own posting, whatever its "similar jobs" list says. A list's
    # rows each go somewhere else.
    if entries and len({urlsplit(urljoin(url, str(c.get("href") or ""))).path for c in entries}) == 1:
        return False
    if entries or len(links_to_other_postings(controls, own_urls, url)) >= 2 or len(role_links(controls)) >= 3:
        return True
    # A page with no list at all is only a listing by its address, an ancestor of the job's own or named like one.
    path = urlsplit(str(url or "")).path.rstrip("/")
    if len(controls) < 3 or any(STEP_CONTROL.match(str(c.get("label") or "")) for c in controls):
        return False  # An application step at a plain address still has its Next.
    return not path or bool(LISTING_ADDRESS.search(path))


class PostingWatch:
    """What one run has seen of its posting. `trail` is True while the run has walked here from its own posting's address, through
    sign-in or an Apply: that walk is what makes a form's own numbering part of this job, and the only thing that does."""

    def __init__(self, job, *, opened_fresh=True, entry_url=""):
        self.job = job
        self.own_urls = [u for u in dict.fromkeys((str(job.get("url") or ""), str(entry_url or ""))) if u]
        self.trail = bool(opened_fresh)
        self.returns = 0
        self.just_returned = False  # The last move went back to the job's own address, and the page after it isn't read yet.

    def numbers(self):
        return sorted({found for own in self.own_urls for found in posting_ids(own)})

    def other_posting_link(self, href, base):
        """The link opens another posting's address. An unrelated number on an Apply link stays a matter for the page it opens."""
        return bool(links_to_other_postings([{"href": href}], self.own_urls, base))

    def other_posting(self, url, obs=None, stated=""):
        """Why the page belongs to another job, else "". `stated`: the title the page's own structured data gives."""
        title, company = str(self.job.get("title") or ""), str(self.job.get("company") or "")
        if stated and clearly_other_role(title, stated, company):
            return f'the page is for "{stated[:120]}", not "{title[:120]}"'
        role = role_on_form((obs or {}).get("headings"))
        if role and clearly_other_role(title, role, company):
            return f'the page is for "{role[:120]}", not "{title[:120]}"'
        if not _names_own_number(self.own_urls, url) and any(other_posting_address(own, url) for own in self.own_urls):
            return "the page's address carries another posting's number"
        if not self.trail and unrelated_posting_number(self.own_urls, url):
            return "the page's address carries a posting number this run never walked to from its own posting"
        return ""

    def judge(self, url, obs=None, *, stated=""):
        """A Verdict on the page at `url`. A page that passes while the run is on its trail becomes part of this job."""
        url = str(url or "")
        if not url.startswith("http"):
            return Verdict("ok")
        if why := self.other_posting(url, obs, stated):
            return Verdict("other_posting", why)
        if looks_like_listing(obs, url, self.own_urls):
            return Verdict("listing", "the page is a career home or job list")
        if _on_own_address(self.own_urls, url) or _names_own_number(self.own_urls, url):
            self.trail = True
        if self.trail and not _on_own_address(self.own_urls, url) and (
            posting_ids(url) or not any(_site(own) == _site(url) for own in self.own_urls)
        ):
            # Walked here from its own posting: this site's numbering (a hand-off, or a form's own id) is now this job's.
            self.own_urls.append(url)
        return Verdict("ok")
