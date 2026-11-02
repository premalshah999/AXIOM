"""The employer and title a posting states about itself, for jobs saved without them.

Pasted links, short links and source crawls leave jobs with no title, no company, or the career site's hostname as the
company ("ats.rippling.com", "careers.capgemini.com"): receipts never matched an employer named like that and the answer
scope had none (2026-10-31: 266 jobs). A page states its own title and employer in structured data, JSON-LD JobPosting or an
ATS's own job fields (Rippling's page props, SuccessFactors' schema.org microdata), and nothing else is read: a heading or the page title is free text, and
"Job Details | Capgemini" names the wrong company often enough. Only a blank value, or a company that is just the job's own
hostname, is replaced; a real value is never overwritten.
"""

import html as htmllib
import json
import re
from urllib.parse import urlsplit

from .schemas import employer_from_title, job_title

LD_BLOCK = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)
NEXT_DATA = re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.I | re.S)
MICRO_SCOPE = re.compile(r'itemtype=["\'][^"\']*JobPosting', re.I)
MICRO_ORG = re.compile(r'<meta[^>]*itemprop=["\']hiringOrganization["\'][^>]*>', re.I)
MICRO_CONTENT = re.compile(r'content=["\']([^"\']*)["\']', re.I)
MICRO_TITLE = re.compile(r'itemprop=["\']title["\'][^>]*>([^<]{1,500})<', re.I)
HOSTNAME = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}")
INTERNAL_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
UNTITLED = re.compile(r"untitled(?: role)?", re.I)
MAX_TITLE, MAX_COMPANY, MAX_PAGE = 500, 300, 3_000_000

# The live page's side of the same two sources (page_inputs reads a saved page's).
IDENTITY_JS = r"""() => {
  const ld = [...document.querySelectorAll('script[type="application/ld+json"]')].map((s) => s.textContent).slice(0, 12);
  let micro = null;
  const scopes = document.querySelectorAll('[itemtype*="JobPosting"]');
  if (scopes.length === 1) {
    const text = (e) => (e.getAttribute('content') || (e.querySelector('[itemprop=name]') || e).textContent || '').trim();
    const orgs = [...new Set([...document.querySelectorAll('[itemprop=hiringOrganization]')].map(text))];
    const titles = [...new Set([...document.querySelectorAll('[itemprop=title]')].map(text))];
    micro = {title: titles.length === 1 ? titles[0] : '', company: orgs.length === 1 ? orgs[0] : ''};
  }
  let post = null;
  try {
    const raw = document.getElementById('__NEXT_DATA__');
    const p = raw && JSON.parse(raw.textContent)?.props?.pageProps?.apiData?.jobPost;
    if (p && typeof p.name === 'string') post = {name: p.name, companyName: p.companyName || p.board?.companyName || ''};
  } catch (e) {}
  return {ld, post, micro};
}"""


def page_inputs(page_html):
    """The same inputs IDENTITY_JS reads, from a saved page."""
    post = None
    if match := NEXT_DATA.search(page_html):
        try:
            found = json.loads(match.group(1))["props"]["pageProps"]["apiData"]["jobPost"]
            if isinstance(found, dict) and isinstance(found.get("name"), str):
                board = found.get("board") if isinstance(found.get("board"), dict) else {}
                post = {"name": found["name"], "companyName": found.get("companyName") or board.get("companyName") or ""}
        except (ValueError, KeyError, TypeError):
            pass
    micro = None
    if len(MICRO_SCOPE.findall(page_html)) == 1:
        orgs = {m[1] for tag in MICRO_ORG.findall(page_html) if (m := MICRO_CONTENT.search(tag))}
        titles = {" ".join(t.split()) for t in MICRO_TITLE.findall(page_html)}
        micro = {"title": next(iter(titles)) if len(titles) == 1 else "", "company": next(iter(orgs)) if len(orgs) == 1 else ""}
    return {"ld": LD_BLOCK.findall(page_html)[:12], "post": post, "micro": micro}


def _text(value, limit):
    text = " ".join(htmllib.unescape(value).split()) if isinstance(value, str) else ""
    return "" if "<" in text or len(text) > limit else text


def _job_postings(node, out):
    if isinstance(node, list):
        for item in node:
            _job_postings(item, out)
    elif isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            org = node.get("hiringOrganization")
            name = org.get("name") if isinstance(org, dict) else org
            out.add((_text(node.get("title"), MAX_TITLE), _text(name, MAX_COMPANY)))
        _job_postings(node.get("@graph"), out)


def identity(inputs):
    """{"title", "company"} the page states about its one posting, else {}.

    A page that lists several postings (a search or board page) states none: taking one would give a job another's name."""
    postings = set()
    for text in (inputs or {}).get("ld") or []:
        try:
            _job_postings(json.loads(text.strip()), postings)
        except (ValueError, TypeError, AttributeError):
            continue
    inputs = inputs or {}
    if len(postings) == 1:
        title, company = next(iter(postings))
    elif postings:
        return {}
    elif inputs.get("post"):
        title, company = _text(inputs["post"].get("name"), MAX_TITLE), _text(inputs["post"].get("companyName"), MAX_COMPANY)
    elif inputs.get("micro"):
        title, company = _text(inputs["micro"].get("title"), MAX_TITLE), _text(inputs["micro"].get("company"), MAX_COMPANY)
    else:
        return {}
    return {
        k: v
        for k, v in {"title": job_title(title).strip(), "company": employer_from_title(company).strip()}.items()
        if v
    }


def _site(host):
    from .accounts.tenants import registrable

    return registrable((host or "").lower().removeprefix("www."))


def placeholder_company(value, url):
    """No company, an internal id (one Ashby organisation's own name is a UUID), the screens' words for no name, or just the job's own
    hostname (or a host of its site)."""
    value = (value or "").strip().lower()
    if not value or INTERNAL_ID.fullmatch(value) or value == "company not listed":
        return True
    return bool(HOSTNAME.fullmatch(value)) and _site(value) == _site(urlsplit(url or "").hostname)


# A page's <title> is not the job's: career sites title every page "Job Details", "Career Portal" or "<role> | <Employer>
# Careers" (Atlas Copco, Capgemini, Citi, 2026-10). Such a title names no role, so a structured-data title may replace it.
PAGE_LABEL = r"(?:jobs?|careers?|career (?:site|portal|opportunities)|job (?:details?|postings?|listings?|descriptions?)|current opportunities|open positions|search results?|apply|home)"
GENERIC_TITLE = re.compile(PAGE_LABEL, re.I)
# Inside "<part> | <part>": a part that is only a page label, or ends in one ("Job Details", "<Employer> Careers"; not "Early Career").
PAGE_TITLE_PART = re.compile(rf"{PAGE_LABEL}|.*\bjob details?|.*(?<!early\s)\bcareers", re.I)


def placeholder_title(value):
    text = (value or "").strip()
    if not text or UNTITLED.fullmatch(text) or GENERIC_TITLE.fullmatch(text):
        return True
    parts = [part.strip() for part in re.split(r"\s+[|\u2013\u2014-]\s+", text) if part.strip()]
    return len(parts) > 1 and any(PAGE_TITLE_PART.fullmatch(part) for part in parts)


def save(db, job_id, found):
    """Write what a page states into the job's blank title and placeholder company. Returns what changed."""
    from .db import Job

    changes = {}
    with db.session() as s:
        row = s.get(Job, job_id)
        if row is None or row.demo:
            return {}
        company = found.get("company", "")
        if company and placeholder_company(row.company, row.url) and not placeholder_company(company, row.url):
            row.company = changes["company"] = company
        if found.get("title") and placeholder_title(row.title):
            row.title = changes["title"] = found["title"]
    return changes


def backfill(db, runs_dir, apply=False, examples=8):
    """Fill jobs from the pages their runs saved. Reads every candidate's newest saved page that states a posting.

    Reports how many jobs have a placeholder, how many have a saved page, and how many that page can fix; writes only with
    `apply`."""
    from sqlalchemy import select

    from .db import Job, Run

    report = {"placeholders": 0, "with_runs": 0, "with_saved_pages": 0, "fixable": 0, "company": 0, "title": 0, "examples": []}
    with db.session() as s:
        jobs = [
            (jid, url, company, title)
            for jid, url, company, title in s.execute(select(Job.id, Job.url, Job.company, Job.title).where(Job.demo.is_(False)))
            if placeholder_company(company, url) or placeholder_title(title)
        ]
        runs = {}
        for run_id, job_id in s.execute(select(Run.id, Run.job_id).order_by(Run.created_at.desc())):
            runs.setdefault(job_id, []).append(run_id)
    report["placeholders"] = len(jobs)
    for jid, url, company, title in jobs:
        if not runs.get(jid):
            continue
        report["with_runs"] += 1
        found = {}
        saved = False
        for run_id in runs[jid]:
            for path in sorted((runs_dir / run_id / "steps").glob("*.html")):
                saved = True
                try:
                    if path.stat().st_size > MAX_PAGE:
                        continue
                    found = identity(page_inputs(path.read_text(errors="ignore")))
                except OSError:
                    continue
                if found:
                    break
            if found:
                break
        report["with_saved_pages"] += saved
        wants = {
            "company": found.get("company")
            if placeholder_company(company, url) and not placeholder_company(found.get("company", ""), url)
            else None,
            "title": found.get("title") if placeholder_title(title) else None,
        }
        wants = {k: v for k, v in wants.items() if v}
        if not wants:
            continue
        report["fixable"] += 1
        report["company"] += "company" in wants
        report["title"] += "title" in wants
        if len(report["examples"]) < examples:
            report["examples"].append({"job": jid[:8], **wants})
        if apply:
            save(db, jid, wants)
    return report
