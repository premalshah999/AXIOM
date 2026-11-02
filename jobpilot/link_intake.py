"""What a pasted link is, before it becomes a job.

A link the owner pastes is often not the employer's posting itself: LinkedIn's "leaving LinkedIn" page around the employer
link (the one pasted on 2026-10-26 nested a click tracker around a PwC posting and was archived without a single run), a
search engine's redirect, a job board's own page, or a company's list of jobs. Each used to become a job whose run opened
the wrong page and ended in a vague browser failure. Here the link is unwrapped (no network), a board page is asked once for
the employer's apply link, and a link that cannot be applied to says so in a sentence with what to do, before any job or
run exists.
"""

import asyncio
import base64
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from bs4 import BeautifulSoup

READ_SECONDS = 10
MAX_UNWRAP = 4  # A wrapper around a wrapper.

# Redirect wrappers: (host suffix, path prefix) -> the query parameters that carry the destination.
WRAPPERS = (
    ("linkedin.com", "/safety/go", ("url",)),
    ("linkedin.com", "/redir/redirect", ("url",)),
    ("linkedin.com", "/jobs/view/externalapply/", ("url",)),
    ("google.com", "/url", ("q", "url")),
    ("facebook.com", "/l.php", ("u",)),
    ("bing.com", "/ck/a", ("u",)),
    ("reddit.com", "/out", ("url",)),
)
# Job boards whose own pages need their sign-in (or apply inside the board): the engine cannot apply there.
BOARDS = {
    "linkedin.com": "LinkedIn",
    "indeed.com": "Indeed",
    "ziprecruiter.com": "ZipRecruiter",
    "glassdoor.com": "Glassdoor",
    "joinhandshake.com": "Handshake",
}


def _host(url):
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _on(host, suffix):
    return host == suffix or host.endswith("." + suffix)


def board_name(url):
    """The board whose own page this is (LinkedIn, Indeed, ... or Google's job search), else ''."""
    host = _host(url)
    for suffix, name in BOARDS.items():
        if _on(host, suffix):
            return name
    if _on(host, "google.com"):
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        if parts.path.rstrip("/") == "/search" and (
            "htidocid" in query or "htivrt" in query or query.get("udm") == "8" or "ibp" in query
        ):
            return "Google Jobs"
    return ""


def _bing(value):
    """Bing's click redirect carries the destination as "a1" and base64url."""
    if not value.startswith("a1"):
        return ""
    body = value[2:]
    try:
        return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
    except ValueError:
        return ""


def unwrap(url):
    """The destination of a redirect wrapper (LinkedIn's outbound page, a search engine's link, an ad click tracker), else
    the link itself. Pure: nothing is fetched."""
    for _ in range(MAX_UNWRAP):
        try:
            parts = urlsplit(url.strip())
        except ValueError:
            return url
        host, inner = (parts.hostname or "").lower(), ""
        if _on(host, "doubleclick.net") and re.search(r"[?;]https?://", url):
            # ad.doubleclick.net/ddm/clk/<ids>;<ids>;m?https://employer.example/job: the destination follows the first "?".
            inner = url[url.index("?") + 1 :] if "?http" in url else ""
        else:
            for suffix, prefix, keys in WRAPPERS:
                if _on(host, suffix) and (parts.path or "").lower().startswith(prefix):
                    query = dict(parse_qsl(parts.query))
                    value = next((query[k] for k in keys if query.get(k)), "")
                    inner = _bing(value) if suffix == "bing.com" else value
                    break
        inner = unquote(inner) if inner.startswith("http%3A") or inner.startswith("https%3A") else inner
        if not re.match(r"https?://[^\s/]+", inner or ""):
            return url
        url = inner
    return url


# A bare careers address, on any site: the page lists jobs and is not one. A query may carry the posting (?gh_jid=123), so only
# a link with none reads this way.
CAREERS_HOME = re.compile(r"careers?|jobs?|join(?:-us)?|work-with-us|opportunities|openings|job-openings|open-positions|vacancies", re.I)


def listing_kind(url):
    """A sentence naming what this link is when it is a company's list of jobs or a search page rather than one posting, for
    the platforms whose posting links are told apart from their list links by shape; else ''."""
    from .ats import oracle_job_catalog

    parts = urlsplit(url)
    host, path = (parts.hostname or "").lower(), parts.path or ""
    segments = [s for s in path.split("/") if s]
    query = dict(parse_qsl(parts.query))
    if not query and not parts.fragment:
        if not segments:
            return "a careers page" if re.match(r"(?:careers?|jobs?)\.", host) else "a company's home page"
        if len(segments) == 1 and CAREERS_HOME.fullmatch(segments[0]):
            return "a careers page"
    if oracle_job_catalog(url) or (
        host.endswith(".oraclecloud.com")
        and re.fullmatch(r"/hcmUI/CandidateExperience/[^/]+/sites/[^/]+/?", path, re.I)
    ):
        return "an Oracle career site's list of jobs"
    if _on(host, "greenhouse.io"):
        if (len(segments) <= 1 or segments[:2] == ["embed", "job_board"]) and not (query.get("gh_jid") or query.get("token")):
            return "a Greenhouse job board's list of jobs"
    elif _on(host, "lever.co") and host.startswith("jobs.") and len(segments) <= 1:
        return "a Lever job board's list of jobs"
    elif host == "jobs.ashbyhq.com" and len(segments) <= 1 and not query.get("ashby_jid"):
        return "an Ashby job board's list of jobs"
    elif host == "apply.workable.com" and len(segments) <= 1:
        return "a Workable job board's list of jobs"
    elif host in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"} and len(segments) <= 1:
        return "a SmartRecruiters job board's list of jobs"
    elif _on(host, "icims.com") and re.fullmatch(r"/jobs(?:/(?:search|intro))?/?", path, re.I):
        return "an iCIMS career site's list of jobs"
    elif _on(host, "myworkdayjobs.com") and not re.search(r"/(?:job|details|apply|login|userhome|createaccount)\b", path, re.I):
        return "a Workday career site's list of jobs"
    return ""


def _is_board_or_self(candidate, source):
    host = _host(candidate)
    return not host or host == _host(source) or any(_on(host, suffix) for suffix in BOARDS) or _on(host, "google.com")


EXTERNAL_APPLY = re.compile(r"externalApply/\d+\?[^\"'<>\s]*?url=([^\"'<>\s&]+)", re.I)
APPLY_URL = re.compile(r'"applyUrl"\s*:\s*"(https?:[^"]+)"|<code[^>]+id="applyUrl"[^>]*>\s*<!--\s*"(https?:[^"]+)"', re.I)
COMPANY_SITE = re.compile(r"apply\s+(?:on|at|via|with|through)\s+(?:the\s+)?(?:company|employer)(?:'s)?\s+(?:site|website)|company\s+site", re.I)


def employer_link(page_html, source_url):
    """The employer's own apply link a board's public page carries, or ''. A board page whose apply button stays on the
    board (Easy Apply, Indeed Apply) carries none."""
    found = [unquote(m.group(1)) for m in EXTERNAL_APPLY.finditer(page_html)]
    found += [(m.group(1) or m.group(2)).encode().decode("unicode_escape", "ignore") for m in APPLY_URL.finditer(page_html)]
    soup = BeautifulSoup(page_html, "html.parser")
    found += [a["href"] for a in soup.find_all("a", href=True) if COMPANY_SITE.search(a.get_text(" ", strip=True))]
    for candidate in found:
        candidate = unwrap(candidate.replace("&amp;", "&"))
        if re.match(r"https?://", candidate) and not _is_board_or_self(candidate, source_url):
            return candidate
    return ""


def board_problem(name):
    return (
        f"{name} keeps this job behind its own sign-in and the page does not give the employer's apply link. "
        f"Open the job, choose Apply, and paste the link of the employer's page that opens."
    )


# network.public_url's refusals, which also guard source fetches and are matched by their wording there, for someone pasting a link.
URL_REFUSALS = {
    "Only public http(s) URLs are supported": "That isn't a web link. Copy the whole address from your browser, starting with https://.",
    "The URL hostname could not be resolved": "There's no website at that address. Check you copied all of the link.",
    "Private, loopback and metadata-service URLs are blocked": "That link points inside this computer or a private network, so AXIOM can't apply there.",
}


# A posting always says one of these. A page that says none, in plenty of words, is an article or an About page.
JOB_WORDS = re.compile(
    r"\b(?:apply|applying|application|applicants?|jobs?|careers?|positions?|roles?|responsibilit\w+|qualifications?|requirements?|"
    r"hiring|recruit\w*|resumes?|cv|employment|openings?|vacanc\w+|requisition|salary|compensation|experience|internship)\b",
    re.I,
)
MIN_WORDS = 40  # Fewer: a page built by its script, or a bot check, which says nothing about the job.


def not_a_posting(page_html, link):
    """True only for a page we can read that plainly isn't a job: a known platform's page is its posting whatever it says, and a page
    with little text proves nothing, so a real posting is never refused here. A run on such a page used to end in a vague failure."""
    from .ats import detect

    if detect(link).id != "custom":
        return False
    soup = BeautifulSoup(page_html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    if "JobPosting" in (page_html or ""):
        return False
    return len(text.split()) >= MIN_WORDS and not JOB_WORDS.search(text)


async def opens(link, allow_private=False):
    """A sentence when the employer's own page says the link is dead, or reads plainly as no job at all, else ''. 404 and 410 are what
    ranking already closes a posting on (discovery.posting_gone); anything else (a 403 to a plain client, a slow site, no answer) says
    nothing about the job, and the application's own browser finds out. Without this a dead link sat in the queue, then read as a failure."""
    from httpx import ConnectError, HTTPStatusError

    from .network import fetch

    try:
        page, final = await asyncio.wait_for(fetch(link, allow_private=allow_private), READ_SECONDS)
        if not_a_posting(page, final):
            return "This page doesn't look like a job posting. Open the job you want and paste the link from that job's own page. Nothing was added."
    except HTTPStatusError as exc:
        if exc.response.status_code in {404, 410}:
            return "This job's page is gone: the employer's site says it no longer exists. Nothing was added."
    except ConnectError as exc:
        # The name resolving to nothing, in a browser's words. Refused or reset connections can be a site's bot guard: not here.
        if "nodename" in str(exc) or "Name or service" in str(exc):
            return "There's no website at that address. Check you copied all of the link."
    except Exception:  # noqa: BLE001 - whatever else went wrong reading it, the link may still be fine in a browser.
        pass
    return ""


async def prepare(url, allow_private=False):
    """(link, problem) for a pasted link: the posting link to save (a wrapper unwrapped, a board page followed to the
    employer's apply page), or a plain sentence saying why it cannot be applied to and what to do."""
    link = unwrap(url)
    if name := board_name(link):
        from .network import fetch

        try:
            page, final = await asyncio.wait_for(fetch(link, allow_private=allow_private), READ_SECONDS)
            target = employer_link(page, final)
        except Exception:  # noqa: BLE001 - a board that will not answer is a board whose page gave nothing.
            target = ""
        if not target:
            return link, board_problem(name)
        link = target
    if kind := listing_kind(link):
        return link, (
            f"This link opens {kind}, not one job posting. Open the job you want and paste the link from that job's own page."
        )
    if problem := await opens(link, allow_private):
        return link, problem
    return link, ""
