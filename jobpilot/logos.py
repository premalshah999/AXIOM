"""A company's icon beside its name, kept on this computer, found from the employer's own pages first.

Order, stopping at the first icon that is the company's and sharp enough for a tile:
  1. the job posting's own page: the logo the applicant-tracking system's company page declares for its employer (the same
     image the page shows in its header), never the system's own brand;
  2. the company's own web address, when the job's address names it: its declared icon or /apple-touch-icon.png;
  3. Google's favicon service for that address, the only third party, asked for a web address alone.
Nothing is sent about who is asking or which job. A company that none of these can vouch for gets no icon: its page shows a
letter tile, and a wrong logo is worse than none. Only raster images that parse are kept (never SVG, which can carry script),
and an image that is small, blurry or a wide wordmark is passed over for the next source.
"""

import asyncio
import re
import struct
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx

from .network import public_url

FOLDER = "logos"
GOOGLE = "https://www.google.com/s2/favicons?domain={}&sz=64"
RETRY_MISS_SECONDS = 30 * 86_400
MAX_IMAGE_BYTES = 150_000
MAX_PAGE_BYTES = 300_000
MIN_EDGE = 48  # A page's own icon smaller than this looks blurry in a 24-40 px tile on a retina screen.
MIN_EDGE_FALLBACK = 32  # Google's copy of a small icon is better than a letter when nothing sharper exists.
MAX_ASPECT = 1.6  # The tile is square: a wider wordmark would shrink to an unreadable line.
KEY = re.compile(r"[a-z0-9]{3,60}")
EXTENSIONS = ("png", "jpg", "webp", "gif", "ico")
MEDIA = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif", "ico": "image/x-icon"}
DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
# Hosts that name a tenant in their first label or first path segment, not the employer's own site.
TENANT_IN_HOST = re.compile(
    r"\.(?:myworkdayjobs|myworkdaysite|icims|successfactors|oraclecloud|avature|paylocity|ultipro|taleo|jibeapply|eightfold)\.\w+$"
)
TENANT_IN_PATH = re.compile(
    r"(?:^|\.)(?:greenhouse\.io|ashbyhq\.com|lever\.co|smartrecruiters\.com|workable\.com|gem\.com|jobvite\.com|rippling\.com|"
    r"recruitee\.com|pinpointhq\.com|breezy\.hr|comeet\.com|teamtailor\.com)$"
)
# Every other system that hosts postings for many employers: a page on one of these never vouches for a company by its address.
OTHER_SYSTEMS = re.compile(
    r"(?:^|\.)(?:applytojob\.com|jazz\.co|dayforcehcm\.com|adp\.com|careerpuck\.com|wd\d+\.myworkdayjobs\.com|bamboohr\.com|"
    r"paycomonline\.net|paycor\.com|ceridianhcm\.com|csod\.com|brassring\.com|hirebridge\.com|applicantpro\.com|"
    r"recruiterbox\.com|icims\.com|adzuna\.com|indeed\.com|linkedin\.com|ziprecruiter\.com|glassdoor\.com)$"
)
# The company's own image, as each system names it in the page it serves for that employer. Anything else a system declares
# (its favicon, its share card, its default logo) is the system's brand, not the employer's.
EMPLOYER_ASSET = re.compile(
    r"^https://[^/]*myworkday(?:jobs|site)\.com/.*/assets/logo$|"
    r"^https://(?:s\d+-)?recruiting\.cdn\.greenhouse\.io/external_greenhouse_job_boards/logos/|"
    r"^https://lever-client-logos\.s3[\w.-]*\.amazonaws\.com/|"
    r"^https://c\.smartrecruiters\.com/sr-company-(?:logo|images)-[\w-]+/[0-9a-f]{24}/|"
    r"^https://d2y4rc9q318ytb\.cloudfront\.net/accounts/|"
    r"^https://resumator\.s3\.amazonaws\.com/customer_[^/]+/social_icons/|"
    r"^https://app\.ashbyhq\.com/api/images/org-theme-logo/"
)
SECOND_LEVEL = {"co.uk", "com.au", "co.in", "co.jp", "com.br", "co.nz", "com.sg", "co.za"}
LEGAL = re.compile(r"\b(?:the|inc|llc|ltd|limited|corp|corporation|company|co|group|holdings|technologies|technology|labs?)\b\.?", re.I)
_locks = {}


def _squash(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def key_for(company):
    """The file stem for a company: its name without punctuation. None when too short to be a name."""
    key = _squash(company)[:60]
    return key if KEY.fullmatch(key) else None


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
    if OTHER_SYSTEMS.search(host):
        return None
    own = _registered(host)
    return own if own and DOMAIN.fullmatch(own) and _same(own.split(".")[0], company) else None


def domain_in(company, text):
    """A web address or email domain the company's own posting names, when it is exactly the company's name ("goldmansachs.com"
    for Goldman Sachs): stricter than domain_for, because a posting also names partners, a parent and its job board."""
    name, whole = _squash(LEGAL.sub(" ", str(company or ""))), _squash(company)
    if len(name) < 3:
        return None
    for host in re.findall(r"(?:https?://|www\.|@)([a-z0-9][a-z0-9.-]*\.[a-z]{2,})", str(text or "").lower()):
        own = _registered(host)
        if own and DOMAIN.fullmatch(own) and not OTHER_SYSTEMS.search(own) and _squash(own.split(".")[0]) in {name, whole}:
            return own
    return None


# ---------- images: only what parses is kept ----------


def image_info(body):
    """(extension, width, height) for a PNG, GIF, JPEG, WebP or ICO that parses, else None. SVG and anything else is refused."""
    try:
        if body[:8] == b"\x89PNG\r\n\x1a\n" and body[12:16] == b"IHDR":
            return ("png",) + struct.unpack(">II", body[16:24])
        if body[:6] in (b"GIF87a", b"GIF89a"):
            return ("gif",) + struct.unpack("<HH", body[6:10])
        if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
            kind = body[12:16]
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", body[26:30])
                return "webp", w & 0x3FFF, h & 0x3FFF
            if kind == b"VP8L":
                bits = struct.unpack("<I", body[21:25])[0]
                return "webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if kind == b"VP8X":
                return "webp", 1 + int.from_bytes(body[24:27], "little"), 1 + int.from_bytes(body[27:30], "little")
            return None
        if body[:3] == b"\xff\xd8\xff":
            i = 2
            while i + 9 < len(body):
                if body[i] != 0xFF:
                    i += 1
                    continue
                marker = body[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", body[i + 5 : i + 9])
                    return "jpg", w, h
                i += 2 + struct.unpack(">H", body[i + 2 : i + 4])[0]
            return None
        if body[:4] == b"\x00\x00\x01\x00":
            best = (0, 0)
            for n in range(min(struct.unpack("<H", body[4:6])[0], 32)):
                entry = body[6 + 16 * n : 22 + 16 * n]
                if len(entry) < 16:
                    break
                size = (entry[0] or 256, entry[1] or 256)
                best = max(best, size, key=lambda s: s[0] * s[1])
            return ("ico",) + best if best[0] else None
    except (struct.error, IndexError):
        return None
    return None


def sharpness(body, minimum):
    """The extension to keep it under when this is a sharp, squarish image, else None."""
    if not body or len(body) > MAX_IMAGE_BYTES:
        return None
    found = image_info(body)
    if not found:
        return None
    ext, w, h = found
    if min(w, h) < minimum or max(w, h) / max(1, min(w, h)) > MAX_ASPECT:
        return None
    return ext


# ---------- finding the employer's image in a page ----------


def declared(html, base):
    """(employer assets, icons): addresses the page declares, best first. An asset is an image a system serves as its employer's
    own (EMPLOYER_ASSET), whichever tag names it; an icon is a link rel=icon or apple-touch-icon. A share card (og:image) is
    otherwise a banner, not a logo, and is passed over."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html[:MAX_PAGE_BYTES], "html.parser")
    found = []
    for tag in soup.find_all(["link", "meta"], limit=200):
        if tag.name == "meta":
            if (tag.get("property") or tag.get("name") or "").lower() in ("og:image", "twitter:image") and tag.get("content"):
                found.append((urljoin(base, tag["content"].strip()), "og", 0))
            continue
        rel = " ".join(tag.get("rel") or []).lower()
        if "icon" in rel and tag.get("href"):
            sizes = re.findall(r"(\d+)x\d+", str(tag.get("sizes") or ""))
            # Workable names the size in the path: .../192x192/<file>.
            guess = int(sizes[0]) if sizes else (int(m.group(1)) if (m := re.search(r"/(\d+)x\d+/", tag["href"])) else 0)
            found.append((urljoin(base, tag["href"].strip()), "apple" if "apple-touch" in rel else "icon", guess))
    by_size = sorted(found, key=lambda f: -f[2])  # Workable's employer icon comes in several sizes: take the biggest.
    assets = [u for u, _, _ in by_size if EMPLOYER_ASSET.match(u)]
    icons = [u for u, role, _ in sorted(found, key=lambda f: (f[1] != "apple", -f[2])) if role != "og"]
    return list(dict.fromkeys(assets)), list(dict.fromkeys(icons))


# ---------- the network ----------


async def _get(client, url, limit, page=False, hops=3):
    """(body, final address) of an https page or image on a public host, redirects checked one hop at a time. A page is cut at
    the limit (its head is what matters); an image over it is refused. None on anything else."""
    for _ in range(hops + 1):
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.port not in (None, 443):
            return None
        try:
            # A posting's page can declare any address: this computer's own services are not fetched on its word.
            await public_url(url)
        except ValueError:
            return None
        async with client.stream("GET", url, headers={"User-Agent": "Mozilla/5.0"}) as response:
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                url = urljoin(url, response.headers["location"])
                continue
            if response.status_code != 200:
                return None
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    return (body[:limit], url) if page else None
            return body, url
    return None


async def _image(client, url, minimum):
    got = await _get(client, url, MAX_IMAGE_BYTES)
    ext = sharpness(got[0], minimum) if got else None
    return (got[0], ext) if ext else None


async def _from_page(client, page_url):
    """The employer's image from the job's own page: the system's employer asset, else (on the employer's own site) its icon."""
    got = await _get(client, page_url, MAX_PAGE_BYTES, page=True)
    if not got:
        return None
    html, final = got[0].decode("utf-8", "replace"), got[1]
    assets, others = declared(html, final)
    for url in assets:
        if found := await _image(client, url, MIN_EDGE):
            return found
    return None


async def _from_site(client, domain):
    """The company's own declared icon, for an address the job vouched for."""
    got = await _get(client, f"https://{domain}/", MAX_PAGE_BYTES, page=True)
    candidates = []
    if got:
        assets, others = declared(got[0].decode("utf-8", "replace"), got[1])
        # Only an icon on the company's own address or its content network; a page can link anything.
        candidates = [u for u in others if _registered(urlsplit(u).hostname or "") == _registered(urlsplit(got[1]).hostname or "")]
    candidates.append(f"https://{domain}/apple-touch-icon.png")
    for url in dict.fromkeys(candidates):
        if found := await _image(client, url, MIN_EDGE):
            return found
    return None


async def _from_google(client, domain):
    got = await _get(client, GOOGLE.format(quote(domain, safe="")), MAX_IMAGE_BYTES)
    ext = sharpness(got[0], MIN_EDGE_FALLBACK) if got else None
    return (got[0], ext) if ext else None


PARKED = re.compile(r"for sale|buy this domain|domain (?:is )?(?:parked|available)|parked (?:domain|free)|godaddy|sedo\.com|hugedomains|dan\.com", re.I)


def addresses(company, urls, descriptions=()):
    """[(domain, guessed)]: the company's web addresses its postings vouch for. An ATS tenant name plus ".com" is a guess (a
    parked domain shows its registrar's icon: "alarmcom.com" gave GoDaddy's heart on 2026-11-01); an address a posting lives on,
    or names in its text, is not."""
    found = {}
    for url in urls:
        if domain := domain_for(company, url):
            host = (urlsplit(str(url)).hostname or "").lower()
            guess = bool(TENANT_IN_HOST.search(host) or TENANT_IN_PATH.search(host))
            # The posting's own text naming the same address is a second, independent witness: not a guess.
            named = re.compile(r"(?:^|[\s/@.(<\"'])" + re.escape(domain) + r"(?![a-z0-9-])")
            found.setdefault(domain, guess and not any(named.search(str(t).lower()) for t in descriptions))
    for text in descriptions if not found else ():
        if domain := domain_in(company, text):
            found.setdefault(domain, False)
    return sorted(found.items(), key=lambda item: item[1])  # Addresses a posting lives on first.


async def _verified(client, domain, company):
    """A guessed address is the company's only when its home page names the company in its title and is not a parked page."""
    got = await _get(client, f"https://{domain}/", MAX_PAGE_BYTES, page=True)
    if not got:
        return False
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(got[0][:MAX_PAGE_BYTES], "html.parser")
    names = [soup.title.get_text(" ") if soup.title else ""] + [
        m.get("content", "") for m in soup.find_all("meta") if (m.get("property") or m.get("name")) in ("og:site_name", "og:title", "application-name")
    ]
    name = _squash(LEGAL.sub(" ", company))
    # The domain's own spelling is not evidence: a for-sale page is titled with it.
    titled = any(name in _squash(t.lower().replace(domain, "")) for t in names) if len(name) >= 3 else False
    return titled and not PARKED.search(got[0][:20_000].decode("utf-8", "replace"))


async def find(company, urls, descriptions=(), third_party=True):
    """(bytes, extension) for the company, or None. `urls` are the addresses of its postings, `descriptions` their texts.
    Raises the network error when every source failed: being offline is not a verdict on the company."""
    failure, answered = None, 0
    async with httpx.AsyncClient(timeout=6, follow_redirects=False, trust_env=False) as client:

        async def attempt(step, *args):
            nonlocal failure, answered
            try:
                result = await step(client, *args)
                answered += 1
                return result
            except httpx.HTTPError as exc:
                failure = exc

        for url in urls[:3]:  # A posting that has closed answers 404 or 410: the next one may still be open.
            if found := await attempt(_from_page, url):
                return found
        owned = []
        for domain, guessed in addresses(company, urls, descriptions)[:3]:
            if not guessed or await attempt(_verified, domain, company):
                owned.append(domain)
        for domain in owned[:2]:
            if found := await attempt(_from_site, domain):
                return found
        if owned and third_party and (found := await attempt(_from_google, owned[0])):
            return found
    if failure and not answered:
        raise failure
    return None


def saved(data_dir, key):
    """(bytes, extension) already kept for this key, else None."""
    for ext in EXTENSIONS:
        path = Path(data_dir) / FOLDER / f"{key}.{ext}"
        if path.is_file():
            return path.read_bytes(), ext
    return None


async def logo(data_dir, company, urls, descriptions=(), third_party=True):
    """(bytes, extension) of the company's icon, found the first time and kept. A company with none is asked again in a month;
    being offline remembers nothing."""
    key = key_for(company)
    if not key or not urls:
        return None
    folder = Path(data_dir) / FOLDER
    if got := saved(data_dir, key):
        return got
    miss = folder / f"{key}.none"
    if miss.is_file() and time.time() - miss.stat().st_mtime < RETRY_MISS_SECONDS:
        return None
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        if got := saved(data_dir, key):
            return got
        try:
            found = await find(company, urls, descriptions, third_party)
        except (httpx.HTTPError, OSError):
            return None
        finally:
            _locks.pop(key, None)
        folder.mkdir(parents=True, exist_ok=True)
        if not found:
            miss.touch()
            return None
        body, ext = found
        partial = folder / f".{key}.tmp"
        partial.write_bytes(body)
        partial.replace(folder / f"{key}.{ext}")
        return body, ext
