"""A company's icon beside its name, kept on this computer.

Only a web address is ever sent, once per company: not who is asking, and not the job. Clearbit's logo service, which most apps
used, no longer resolves (2026-10-31); Google's favicon service gave 6 of 6 sampled companies an icon under 2 KB. A company whose
address cannot be tied to its name gets no request at all, and the page shows its letter tile: a wrong logo is worse than none.
"""

import asyncio
import re
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

FOLDER = "logos"
SOURCE = "https://www.google.com/s2/favicons?domain={}&sz=64"
RETRY_MISS_SECONDS = 30 * 86_400
MAX_BYTES = 200_000
PNG = b"\x89PNG\r\n\x1a\n"
DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
# Hosts that name a tenant in their first label or first path segment, not the employer's own site.
TENANT_IN_HOST = re.compile(
    r"\.(?:myworkdayjobs|myworkdaysite|icims|successfactors|oraclecloud|avature|paylocity|ultipro|taleo|jibeapply|eightfold)\.\w+$"
)
TENANT_IN_PATH = re.compile(
    r"(?:^|\.)(?:greenhouse\.io|ashbyhq\.com|lever\.co|smartrecruiters\.com|workable\.com|gem\.com|jobvite\.com|rippling\.com|"
    r"recruitee\.com|pinpointhq\.com|breezy\.hr|comeet\.com|teamtailor\.com)$"
)
SECOND_LEVEL = {"co.uk", "com.au", "co.in", "co.jp", "com.br", "co.nz", "com.sg", "co.za"}
LEGAL = re.compile(r"\b(?:the|inc|llc|ltd|limited|corp|corporation|company|co|group|holdings|technologies|technology|labs?)\b\.?", re.I)
_locks = {}


def _squash(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _same(tenant, company):
    """The address names this company, in full: "twosigma" for Two Sigma, "farmersinsurance" for Farmers Insurance."""
    name, whole = _squash(LEGAL.sub(" ", str(company or ""))), _squash(company)
    tenant = _squash(tenant)
    if len(tenant) < 3 or len(name) < 3:
        return False
    return tenant in {name, whole} or tenant.startswith(name) or name.startswith(tenant)


def _registered(host):
    labels = host.split(".")
    if len(labels) < 2:
        return ""
    return ".".join(labels[-3:]) if ".".join(labels[-2:]) in SECOND_LEVEL and len(labels) >= 3 else ".".join(labels[-2:])


def domain_for(company, url):
    """The company's web address when the job's own address vouches for it, else None."""
    try:
        parts = urlsplit(str(url or ""))
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if not host or not company:
        return None
    if TENANT_IN_HOST.search(host):
        # iCIMS names a tenant "uscareers-pepsico" or "careers-berrydunn".
        tenant = re.sub(r"^(?:[a-z]{2})?(?:careers?|jobs)-|-(?:careers?|jobs)$", "", host.split(".")[0])
        return f"{tenant}.com" if _same(tenant, company) and DOMAIN.fullmatch(f"{tenant}.com") else None
    if TENANT_IN_PATH.search(host):
        segment = next((x for x in parts.path.split("/") if x), "")
        return f"{segment.lower()}.com" if _same(segment, company) and DOMAIN.fullmatch(f"{segment.lower()}.com") else None
    own = _registered(host)
    return own if own and DOMAIN.fullmatch(own) and _same(own.split(".")[0], company) else None


async def _fetch(domain):
    async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
        response = await client.get(SOURCE.format(quote(domain, safe="")), headers={"User-Agent": "Mozilla/5.0"})
    body = response.content
    return body if response.status_code == 200 and body.startswith(PNG) and len(body) <= MAX_BYTES else None


async def logo(data_dir, domain):
    """The saved icon's bytes, fetching it the first time. A miss is remembered, so a company with none is asked once a month."""
    if not DOMAIN.fullmatch(domain or ""):
        return None
    folder = Path(data_dir) / FOLDER
    image, miss = folder / f"{domain}.png", folder / f"{domain}.none"
    if image.is_file():
        return image.read_bytes()
    if miss.is_file() and time.time() - miss.stat().st_mtime < RETRY_MISS_SECONDS:
        return None
    lock = _locks.setdefault(domain, asyncio.Lock())
    async with lock:
        if image.is_file():
            return image.read_bytes()
        try:
            body = await _fetch(domain)
        except (httpx.HTTPError, OSError):
            return None  # Offline is not a verdict on the company: nothing is remembered.
        finally:
            _locks.pop(domain, None)
        folder.mkdir(parents=True, exist_ok=True)
        if not body:
            miss.touch()
            return None
        partial = folder / f".{domain}.tmp"
        partial.write_bytes(body)
        partial.replace(image)
        return body
