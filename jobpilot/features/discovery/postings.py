"""Posting text from each ATS's public posting API, so jobs reach ranking with their description.

HTML job pages are often script-rendered or bot-protected (403), which left feed jobs
without text and stuck before classification. The public JSON APIs below return the
full description for one posting and need no credentials. Anything else returns ""
and the application's HTML loader (`discovery.enrich`) remains the fallback.
"""

import asyncio
import html
import json
import re
from urllib.parse import parse_qs, urlsplit
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
GREENHOUSE = re.compile(r"^https://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/([^/?#]+)/jobs/(\d+)")
LEVER = re.compile(r"^https://jobs\.(?:eu\.)?lever\.co/([^/?#]+)/([0-9a-f-]{36})")
ASHBY = re.compile(r"^https://jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})")
SMART = re.compile(r"^https://(?:jobs|careers)\.smartrecruiters\.com/([^/?#]+)/(\d+)")
WORKDAY = re.compile(
    r"^https://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)/(job/[^?#]+)"
)
ORACLE = re.compile(
    r"^https://[a-z0-9-]+\.fa(?:\.(?:us\d+|ocs))?\.oraclecloud\.com/"
    r"hcmUI/CandidateExperience/[a-z-]+/sites/(CX_\d+)/job/(\d+|CONV_\d+BR)(?:[/?#]|$)",
    re.I,
)
# Dayforce's candidate portal renders client-side; the posting page's own __NEXT_DATA__
# carries jobData with the full description (its search API refuses non-browser clients).
DAYFORCE = re.compile(r"^https://jobs\.dayforcehcm\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)/([^/?#]+)/jobs/(\d+)")
# Taleo Enterprise renders client-side; its page keeps the requisition in the hidden "initialHistory" input
# (URL-encoded, "!|!"-separated). Taleo Business Edition requisition pages are server-rendered.
TALEO_EE = re.compile(r"^https://[a-z0-9-]+\.taleo\.net/careersection/[^/?#]+/jobdetail\.ftl\?", re.I)
TALEO_BE = re.compile(
    r"^https://[a-z0-9-]+\.tbe\.taleo\.net/[a-z0-9]+/ats/careers/(?:v2/viewRequisition|requisition\.jsp)\?", re.I
)
MIN_TEXT = 200  # shorter than this is a label or a stub, not a posting


def plain(markup: str) -> str:
    text = BeautifulSoup(html.unescape(markup or ""), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


async def posting_text(client: httpx.AsyncClient, url: str, cache: dict | None = None) -> str:
    """Plain description for one posting URL, or "" when this ATS/URL is not supported."""
    cache = cache if cache is not None else {}
    if m := ORACLE.match(url):
        origin = urlsplit(url)
        r = await client.get(
            f"{origin.scheme}://{origin.netloc}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
            params={
                "expand": "all",
                "onlyData": "true",
                "finder": f'ById;Id="{m[2]}",siteNumber={m[1]}',
            },
            headers={"Ora-Irc-Language": "en", "Accept": "application/json"},
        )
        if r.status_code != 200:
            return ""
        # The public page uses this same site-scoped endpoint. An empty or
        # mismatched requisition is closed/unavailable, never a company-only JD.
        items = r.json().get("items") or []
        if not isinstance(items, list):
            return ""
        job = next((j for j in items if isinstance(j, dict) and str(j.get("Id")) == m[2]), None)
        if not job:
            return ""
        return " ".join(
            plain(job.get(key, ""))
            for key in ("ExternalDescriptionStr", "ExternalQualificationsStr")
            if job.get(key)
        ).strip()
    if m := GREENHOUSE.match(url):
        r = await client.get(
            f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}", params={"content": "true"}
        )
        return plain(r.json().get("content", "")) if r.status_code == 200 else ""
    if m := LEVER.match(url):
        r = await client.get(f"https://api.lever.co/v0/postings/{m[1]}/{m[2]}")
        if r.status_code != 200:
            return ""
        j = r.json()
        parts = [j.get("descriptionPlain") or plain(j.get("description", ""))]
        for item in j.get("lists") or []:
            parts += [item.get("text", ""), plain(item.get("content", ""))]
        parts.append(j.get("additionalPlain") or plain(j.get("additional", "")))
        return " ".join(p for p in parts if p).strip()
    if m := ASHBY.match(url):
        key = "ashby:" + m[1]
        if key not in cache:
            r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{m[1]}")
            cache[key] = {j.get("id"): j for j in r.json().get("jobs", [])} if r.status_code == 200 else {}
        j = cache[key].get(m[2]) or {}
        return j.get("descriptionPlain") or plain(j.get("descriptionHtml", ""))
    if m := SMART.match(url):
        r = await client.get(f"https://api.smartrecruiters.com/v1/companies/{m[1]}/postings/{m[2]}")
        if r.status_code != 200:
            return ""
        sections = ((r.json().get("jobAd") or {}).get("sections")) or {}
        order = ("jobDescription", "qualifications", "additionalInformation", "companyDescription")
        return " ".join(plain((sections.get(k) or {}).get("text", "")) for k in order).strip()
    if m := DAYFORCE.match(url):
        return dayforce_text(await client.get(url, headers={"Accept": "text/html"}), m[3])
    if TALEO_EE.match(url) or TALEO_BE.match(url):
        # Taleo sent a malformed compressed redirect (Cognizant 2026-10-17); ask for identity encoding.
        r = await client.get(url, headers={"Accept": "text/html", "Accept-Encoding": "identity"})
        return taleo_text(r.text) if r.status_code == 200 else ""
    if (adp := adp_requisition(url)) is not None:
        cid, job_id = adp
        r = await client.get(
            f"https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions/{job_id}",
            params={"cid": cid, "lang": "en_US", "iccFlag": "yes", "eccFlag": "yes", "locale": "en_US"},
        )
        return plain(r.json().get("requisitionDescription", "")) if r.status_code == 200 else ""
    if m := WORKDAY.match(url):
        tenant, pod, site, path = m[1], m[2], m[3], m[4]
        r = await client.get(f"https://{tenant}.{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/{path}")
        if r.status_code != 200:
            return ""  # some tenants refuse non-browser clients; the browser-backed loader remains
        return plain((r.json().get("jobPostingInfo") or {}).get("jobDescription", ""))
    return ""


def adp_requisition(url):
    """(cid, jobId) of an ADP Workforce Now career-center posting; its page renders client-side,
    while the career center's own public API returns requisitionDescription (2026-10-16)."""
    parts = urlsplit(url)
    if parts.hostname != "workforcenow.adp.com" or not parts.path.endswith("/recruitment.html"):
        return None
    query = {k.casefold(): v[0] for k, v in parse_qs(parts.query).items()}
    cid, job_id = query.get("cid", ""), query.get("jobid", "")
    if re.fullmatch(r"[0-9a-fA-F-]{36}", cid) and re.fullmatch(r"\d+", job_id):
        return cid, job_id
    return None


def taleo_text(page):
    """Description of a Taleo requisition page (Enterprise initialHistory or Business Edition HTML), or ""."""
    from urllib.parse import unquote

    if m := re.search(r'id="initialHistory"[^>]*value="([^"]*)"', page) or re.search(
        r'value="([^"]*)"[^>]*id="initialHistory"', page
    ):
        parts = unquote(html.unescape(m[1])).split("!|!")
        long = list(dict.fromkeys(p.strip("!*") for p in parts if len(p) >= MIN_TEXT))
        return " ".join(plain(p) for p in long).strip()
    lines = BeautifulSoup(page, "html.parser").get_text("\n", strip=True).split("\n")
    if "Position Description" in lines and "Apply Now" in lines:
        start = lines.index("Apply Now") + 1
        end = next((i for i in range(len(lines) - 1, start, -1) if lines[i] == "Back"), len(lines))
        return re.sub(r"\s+", " ", " ".join(lines[start:end])).strip()
    return ""


def dayforce_job(response, posting_id):
    """The posting's jobData from a Dayforce page, only when it is this posting."""
    if response.status_code != 200:
        return {}
    node = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', response.text, re.S)
    try:
        job = (json.loads(node.group(1))["props"]["pageProps"] or {}).get("jobData") or {} if node else {}
    except (ValueError, KeyError, TypeError):
        return {}
    return job if str(job.get("jobPostingId")) == str(posting_id) else {}


def dayforce_text(response, posting_id):
    content = dayforce_job(response, posting_id).get("jobPostingContent") or {}
    parts = (content.get(k) or "" for k in ("jobDescriptionHeader", "jobDescription", "jobDescriptionFooter"))
    return " ".join(plain(part) for part in parts if part).strip()


async def fill_missing_text(db, job_ids=None, limit=200, concurrency=8, timeout=20.0) -> dict:
    """Store public posting text for never-attempted jobs whose description is empty.

    Only an empty description is filled, so an existing description, a manual edit or
    an application's text is never overwritten.
    """
    from sqlalchemy import func, select

    from ...db import Job, Run

    with db.session() as s:
        q = select(Job.id, Job.url).where(
            Job.demo.is_(False),
            func.length(func.coalesce(Job.description, "")) < MIN_TEXT,
            Job.status.in_(["new", "ready", "skipped"]),
            Job.id.not_in(select(Run.job_id)),
        )
        if job_ids is not None:
            q = q.where(Job.id.in_(list(job_ids)))
        todo = list(s.execute(q.limit(limit)))
    report = {"checked": len(todo), "filled": 0, "unsupported": 0, "failed": 0}
    if not todo:
        return report
    slots, cache = asyncio.Semaphore(concurrency), {}
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers={"User-Agent": UA, "Accept": "application/json"}
    ) as client:

        async def one(job_id, url):
            async with slots:
                try:
                    text = await posting_text(client, url, cache)
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    report["failed"] += 1
                    return
            if len(text) < MIN_TEXT:
                report["unsupported" if not text else "failed"] += 1
                return
            with db.exclusive() as s:
                row = s.get(Job, job_id)
                if row and len((row.description or "").strip()) < MIN_TEXT:
                    row.description = text[:40000]
                    report["filled"] += 1

        await asyncio.gather(*(one(j, u) for j, u in todo))
    report["at"] = datetime.now(UTC).isoformat()
    return report
