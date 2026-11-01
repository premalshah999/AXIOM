"""A company's icon beside its name, kept on this computer, found from the employer's own pages first.

Order, stopping at the first icon that is the company's and sharp enough for a tile:
  1. the job posting's own page: the logo the applicant-tracking system's company page declares for its employer (the same
     image the page shows in its header), or the logo its structured data (JSON-LD hiringOrganization) gives under the
     company's own name; never the system's own brand;
  2. the company's own web address, when a job's address, its text or that structured data names it: its declared icon or
     /apple-touch-icon.png;
  3. Google's favicon service for that address, the only third party, asked for a web address alone;
  4. a wide wordmark from step 1, when no square symbol was found (a wordmark is the company's real logo, though small in a tile).
Nothing is sent about who is asking or which job. A company that none of these can vouch for gets no icon: its page shows a
letter tile, and a wrong logo is worse than none. Only images that parse are kept: a raster one is shrunk to the size a tile
draws (the page never loads a 1000 px image for a 24 px square), an SVG only after a script-free check.

Finding is slow and uses the network, so it never happens while a page waits: a page asks `Shelf.states` (a memory lookup) and the
ones not yet looked up are queued, two at a time, on a background worker; the page asks again a few seconds later.
"""

import asyncio
import html as htmltools
import io
import json
import re
import struct
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx

from .network import _tls_context, public_url

FOLDER = "logos"
GOOGLE = "https://www.google.com/s2/favicons?domain={}&sz=64"
RETRY_MISS_SECONDS = 30 * 86_400
RETRY_OFFLINE_SECONDS = 600  # Offline or erroring everywhere is not a verdict, but is not worth hammering either.
RULES = 2  # A "no logo" verdict written under older rules (which refused every wide wordmark) is asked again.
MAX_IMAGE_BYTES = 150_000  # What is kept without being shrunk (no image library, or an image it cannot read).
MAX_FETCH_BYTES = 1_500_000  # What is read from a site: a 1000 px logo is shrunk to MAX_EDGE before it is kept.
MAX_SVG_BYTES = 60_000
MAX_PAGE_BYTES = 300_000
MAX_EDGE = 128  # 3x the largest tile (40 px): sharp on a retina screen, and a few kilobytes.
MIN_EDGE = 48  # A page's own icon smaller than this looks blurry in a 24-40 px tile on a retina screen.
MIN_EDGE_FALLBACK = 32  # Google's copy of a small icon is better than a letter when nothing sharper exists.
MAX_ASPECT = 1.6  # The tile is square: a wider wordmark would shrink to an unreadable line.
MAX_WIDE = 4.0  # ...but a wordmark up to this wide is still the company's logo, drawn contained in the tile.
MIN_WIDE = 96  # Its width in pixels: a smaller one is a smudge once it is fitted to a tile.
KEY = re.compile(r"[a-z0-9]{3,60}")
EXTENSIONS = ("webp", "svg", "png", "jpg", "gif", "ico")
MEDIA = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "ico": "image/x-icon",
    "svg": "image/svg+xml",
}
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
    r"^https://s3\.amazonaws\.com/resumator/customer_[^/]+/logos/|"
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



def svg_aspect(body):
    """The width / height of an SVG that is safe to show, else None. Shown only through <img> (which runs no script) with a
    script-denying policy on the response; still refused here when it carries script, an event handler, an embedded page or a
    reference to another file."""
    if not body or len(body) > MAX_SVG_BYTES:
        return None
    text = body.decode("utf-8", "replace")
    head = text[:400].lstrip().lower()
    if not (head.startswith("<svg") or head.startswith("<?xml") or head.startswith("<!doctype svg")):
        return None
    if re.search(r"<script|<foreignobject|<iframe|<embed|<object|<!entity|javascript:|\bon[a-z]+\s*=|href\s*=\s*[\"']?(?!#)|@import|url\(\s*[\"']?(?!#)", text, re.I):
        return None
    tag = re.search(r"<svg\b[^>]*>", text, re.I)
    if not tag:
        return None
    attrs = dict((k.lower(), v) for k, _, v in re.findall(r"([\w:-]+)\s*=\s*([\"'])(.*?)\2", tag.group(0)))
    try:
        if "viewbox" in attrs:
            _, _, w, h = (float(x) for x in re.split(r"[\s,]+", attrs["viewbox"].strip()))
        else:
            w, h = (float(re.match(r"[\d.]+", attrs[k]).group(0)) for k in ("width", "height"))
    except (ValueError, KeyError, AttributeError):
        return None
    return w / h if w > 0 and h > 0 else None


def svg_white(body):
    """True when every colour an SVG declares is white or none, and no shape is left to the default black: a logo drawn for a dark
    page, invisible on the tile's light plate (GM Financial's header logo, 2026-11-01)."""
    text = body.decode("utf-8", "replace")
    colors = re.findall(r"(?:fill|stroke|stop-color)\s*[:=]\s*[\"']?\s*(#[0-9a-f]{3,8}\b|rgba?\([^)]*\)|[a-z]+)", text, re.I)
    colors = [c.lower().replace(" ", "") for c in colors if c.lower() not in ("none", "currentcolor", "inherit", "transparent")]
    if not colors or any(c not in ("#fff", "#ffffff", "#ffffffff", "white", "rgb(255,255,255)") for c in colors):
        return False
    root = re.search(r"<svg\b[^>]*>", text, re.I)
    if root and re.search(r"\bfill\s*[:=]", root.group(0)):
        return True  # inherited by every shape
    shapes = len(re.findall(r"<(?:path|rect|circle|ellipse|polygon|polyline)\b", text, re.I))
    return len(re.findall(r"\bfill\s*[:=]", text)) >= shapes


def judge(body, minimum, wide_ok=False):
    """(extension, shape, short edge) for an image worth a tile: shape is "square" or, when wide_ok, "wide"; else None."""
    if not body:
        return None
    if body[:400].lstrip()[:1] == b"<":
        ratio = svg_aspect(body)
        if not ratio or svg_white(body):
            return None
        ratio = max(ratio, 1 / ratio)
        return ("svg", "square" if ratio <= MAX_ASPECT else "wide", 0) if ratio <= (MAX_WIDE if wide_ok else MAX_ASPECT) else None
    if len(body) > MAX_FETCH_BYTES:
        return None
    found = image_info(body)
    if not found:
        return None
    ext, w, h = found
    if min(w, h) < minimum or max(w, h) > 4096:
        return None
    ratio = max(w, h) / max(1, min(w, h))
    if ratio <= MAX_ASPECT:
        return ext, "square", min(w, h)
    if wide_ok and ratio <= MAX_WIDE and max(w, h) >= MIN_WIDE:
        return ext, "wide", min(w, h)
    return None


def _inked(im):
    """False for an image with nothing to see on a light plate: every visible pixel is near white (a logo drawn in white for a
    dark page, 2026-11-01)."""
    seen = ink = 0
    for r, g, b, a in im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata():
        if a > 40:
            seen += 1
            ink += min(r, g, b) < 225
    return seen > 0 and ink / seen >= 0.01


def shrink(body, ext):
    """(bytes, extension) of the image as a tile draws it: no larger than MAX_EDGE, as WebP. An SVG, or an image this machine
    has no library to read, is kept as it is when it is small enough, else None."""
    if ext == "svg":
        return body, ext
    try:
        from PIL import Image

        with Image.open(io.BytesIO(body)) as im:
            im.load()
            im = im.convert("RGBA")
            if max(im.size) > MAX_EDGE:
                im.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
            if not _inked(im):
                return None
            out = io.BytesIO()
            im.save(out, "WEBP", lossless=True, quality=90, method=4)
            return out.getvalue(), "webp"
    except Exception:  # noqa: BLE001 - no library, or a file it cannot read: keep the original if it is small
        return (body, ext) if len(body) <= MAX_IMAGE_BYTES else None


# ---------- finding the employer's image in a page ----------

ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
TAG = re.compile(r"<(link|meta)\b[^>]*>", re.I)
LD = re.compile(r"<script[^>]*ld\+json[^>]*>(.*?)</script>", re.I | re.S)


def _tags(html):
    """[(tag, {attribute: value})] for the first 200 <link> and <meta> tags: regular expressions, because a page is
    read on the app's one event loop and a full HTML parse of 300 KB is tens of milliseconds the browsers would wait for."""
    out = []
    for m in TAG.finditer(html[:MAX_PAGE_BYTES]):
        attrs = {k.lower(): htmltools.unescape(a or b) for k, a, b in ATTR.findall(m.group(0))}
        out.append((m.group(1).lower(), attrs))
        if len(out) >= 200:
            break
    return out


def declared(html, base):
    """(employer assets, icons): addresses the page declares, best first. An asset is an image a system serves as its employer's
    own (EMPLOYER_ASSET), whichever tag names it; an icon is a link rel=icon or apple-touch-icon. A share card (og:image) is
    otherwise a banner, not a logo, and is passed over."""
    found = []
    for name, tag in _tags(html):
        if name == "meta":
            if (tag.get("property") or tag.get("name") or "").lower() in ("og:image", "twitter:image") and tag.get("content"):
                found.append((urljoin(base, tag["content"].strip()), "og", 0))
            continue
        rel = (tag.get("rel") or "").lower()
        if "icon" in rel and tag.get("href"):
            sizes = re.findall(r"(\d+)x\d+", str(tag.get("sizes") or ""))
            # Workable names the size in the path: .../192x192/<file>.
            guess = int(sizes[0]) if sizes else (int(m.group(1)) if (m := re.search(r"/(\d+)x\d+/", tag["href"])) else 0)
            found.append((urljoin(base, tag["href"].strip()), "apple" if "apple-touch" in rel else "icon", guess))
    by_size = sorted(found, key=lambda f: -f[2])  # Workable's employer icon comes in several sizes: take the biggest.
    assets = [u for u, _, _ in by_size if EMPLOYER_ASSET.match(u)]
    icons = [u for u, role, _ in sorted(found, key=lambda f: (f[1] != "apple", -f[2])) if role != "og"]
    return list(dict.fromkeys(assets)), list(dict.fromkeys(icons))


def organizations(html):
    """[{name, site, logo}] the page's structured data (JSON-LD) gives as the hiring organization: what a posting declares for
    Google for Jobs, so an employer's own claim about itself."""
    found = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            org = node.get("hiringOrganization")
            if isinstance(org, dict):
                logo = org.get("logo")
                logo = logo.get("url") if isinstance(logo, dict) else logo
                site = org.get("sameAs") or org.get("url")
                site = site[0] if isinstance(site, list) and site else site
                found.append({k: v for k, v in (("name", org.get("name")), ("site", site), ("logo", logo)) if isinstance(v, str) and v})
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)

    for m in LD.finditer(html[:MAX_PAGE_BYTES]):
        try:
            walk(json.loads(htmltools.unescape(m.group(1)).strip()))
        except ValueError:
            continue
        if len(found) >= 3:
            break
    return found


def _platform(domain):
    """The web address belongs to a system that hosts postings for many employers, not to one of them."""
    host = f"www.{domain}"
    return bool(TENANT_IN_HOST.search(host) or TENANT_IN_PATH.search(host) or OTHER_SYSTEMS.search(host))


def _related(domain, company):
    """The address's name is the company's, or contains it ("joinhandshake.com" for Handshake): what a page's structured data
    must show before its site is taken for the company's. A job page of a system that hosts many employers names the system's own
    address as the organization's site (breezy.hr, paylocity.com, phenompeople.com: five companies showed Breezy's icon, 2026-11-01)."""
    label, name = _squash(domain.split(".")[0]), _squash(LEGAL.sub(" ", str(company or "")))
    return not _platform(domain) and (_same(label, company) or (len(name) >= 5 and name in label) or (len(label) >= 5 and label in _squash(company)))


def employer_logos(html, base, company):
    """(logo addresses, web addresses) a page vouches for as `company`'s: its system's employer assets, the structured-data logo
    and site of an organization with the company's name, and a share image named "logo" on an address that names the company."""
    assets, _ = declared(html, base)
    sites = []
    for org in organizations(html):
        if not _same(org.get("name", ""), company):
            continue
        if org.get("logo"):
            assets.append(urljoin(base, org["logo"]))
        if site := _registered((urlsplit(urljoin(base, org.get("site", ""))).hostname or "").lower()):
            if DOMAIN.fullmatch(site) and _related(site, company):
                sites.append(site)
    for name, tag in _tags(html):
        if name == "meta" and (tag.get("property") or tag.get("name") or "").lower() in ("og:image", "twitter:image") and tag.get("content"):
            url = urljoin(base, tag["content"].strip())
            host = _registered((urlsplit(url).hostname or "").lower())
            if re.search(r"logo", urlsplit(url).path, re.I) and host and not OTHER_SYSTEMS.search(host) and _same(host.split(".")[0], company):
                assets.append(url)
    return list(dict.fromkeys(assets)), list(dict.fromkeys(sites))

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


async def _image(client, url, minimum, wide_ok=False):
    """(bytes, extension, shape, short edge) of the image at `url` as it is kept, or None: judged as it is served, then shrunk to the
    size a tile draws (in a thread: decoding a large image is milliseconds of CPU the app's event loop must not spend)."""
    got = await _get(client, url, MAX_SVG_BYTES if urlsplit(url).path.lower().endswith(".svg") else MAX_FETCH_BYTES)
    verdict = judge(got[0], minimum, wide_ok) if got else None
    if not verdict:
        return None
    kept = await asyncio.to_thread(shrink, got[0], verdict[0])
    return (kept[0], kept[1], verdict[1], verdict[2]) if kept else None


async def _page(client, page_url, company):
    """(logo addresses, web sites) the job's own page vouches for under the company's name, or None when it does not answer."""
    got = await _get(client, page_url, MAX_PAGE_BYTES, page=True)
    return employer_logos(got[0].decode("utf-8", "replace"), got[1], company) if got else None


async def _from_site(client, domain):
    """The company's own declared icon, for an address the job vouched for."""
    got = await _get(client, f"https://{domain}/", MAX_PAGE_BYTES, page=True)
    candidates = []
    if got:
        assets, others = declared(got[0].decode("utf-8", "replace"), got[1])
        # Only an icon on the company's own address or its content network; a page can link anything.
        candidates = [u for u in others if _registered(urlsplit(u).hostname or "") == _registered(urlsplit(got[1]).hostname or "")]
    candidates += [f"https://{domain}/apple-touch-icon.png", f"https://{domain}/favicon.ico"]  # a site with no <link> still has these
    for url in dict.fromkeys(candidates):
        if found := await _image(client, url, MIN_EDGE):
            return found
    return None


async def _from_google(client, domain):
    return await _image(client, GOOGLE.format(quote(domain, safe="")), MIN_EDGE_FALLBACK)


PARKED = re.compile(r"for sale|buy this domain|domain (?:is )?(?:parked|available)|parked (?:domain|free)|godaddy|sedo\.com|hugedomains|dan\.com", re.I)


def addresses(company, urls, descriptions=(), named=()):
    """[(domain, guessed)]: the company's web addresses its postings vouch for. An ATS tenant name plus ".com" is a guess (a
    parked domain shows its registrar's icon: "alarmcom.com" gave GoDaddy's heart on 2026-11-01); an address a posting lives on,
    or names in its text, is not."""
    found = {}
    for url in urls:
        if domain := domain_for(company, url):
            host = (urlsplit(str(url)).hostname or "").lower()
            guess = bool(TENANT_IN_HOST.search(host) or TENANT_IN_PATH.search(host))
            # The posting's own text naming the same address is a second, independent witness: not a guess.
            spelled = re.compile(r"(?:^|[\s/@.(<\"'])" + re.escape(domain) + r"(?![a-z0-9-])")
            found.setdefault(domain, guess and not any(spelled.search(str(t).lower()) for t in descriptions))
    for text in descriptions if not found else ():
        if domain := domain_in(company, text):
            found.setdefault(domain, False)
    for domain in named:  # The company's own posting page names its web site, under the company's name: nothing to guess.
        found[domain] = False
    return sorted(found.items(), key=lambda item: item[1])  # Addresses a posting lives on first.


async def _verified(client, domain, company):
    """A guessed address is the company's only when its home page names the company in its title and is not a parked page."""
    got = await _get(client, f"https://{domain}/", MAX_PAGE_BYTES, page=True)
    if not got:
        return False
    # A guessed address that sends the visitor to another site is that site's page (superapp.com led to a LinkedIn page titled
    # with the company's name and borrowed LinkedIn's icon): it is the company's only if it ends on the same registered
    # domain or one that itself names the company.
    final = _registered((urlsplit(got[1]).hostname or "").lower())
    if final != _registered(domain) and (OTHER_SYSTEMS.search(final) or not _same(final.split(".")[0], company)):
        return False
    page = got[0][:MAX_PAGE_BYTES].decode("utf-8", "replace")
    title = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    names = [htmltools.unescape(title.group(1)) if title else ""] + [
        t.get("content", "") for n, t in _tags(page) if n == "meta" and (t.get("property") or t.get("name")) in ("og:site_name", "og:title", "application-name")
    ]
    name = _squash(LEGAL.sub(" ", company))
    # The domain's own spelling is not evidence: a for-sale page is titled with it.
    titled = any(name in _squash(t.lower().replace(domain, "")) for t in names) if len(name) >= 3 else False
    return titled and not PARKED.search(page[:20_000])


async def find(company, urls, descriptions=(), third_party=True):
    """(bytes, extension) for the company, or None. `urls` are the addresses of its postings, `descriptions` their texts.
    Raises the network error when every source failed: being offline is not a verdict on the company."""
    failure, answered = None, 0
    async with httpx.AsyncClient(timeout=6, follow_redirects=False, trust_env=False, verify=_tls_context()) as client:

        async def attempt(step, *args):
            nonlocal failure, answered
            try:
                result = await step(client, *args)
                answered += 1
                return result
            except httpx.HTTPError as exc:
                failure = exc

        assets, sites = [], []
        for url in urls[:3]:  # A posting that has closed answers 404 or 410: the next one may still be open.
            if page := await attempt(_page, url, company):
                assets, sites = page
                if assets or sites:
                    break
        wide = None
        for url in assets[:3]:
            if found := await attempt(_image, url, MIN_EDGE, True):
                if found[2] == "square":
                    return found[:2]
                wide = wide or found
        owned = []
        for domain, guessed in addresses(company, urls, descriptions, sites)[:3]:
            if not guessed or await attempt(_verified, domain, company):
                owned.append(domain)
        for domain in owned[:2]:
            if found := await attempt(_from_site, domain):
                return found[:2]
        small = None
        if owned and third_party and (found := await attempt(_from_google, owned[0])):
            if found[3] >= MIN_EDGE:
                return found[:2]
            small = found
        if wide or small:
            return (wide or small)[:2]  # A wordmark is the company's real logo; a blurred symbol is the last resort.
    if failure and not answered:
        raise failure
    return None


class Shelf:
    """The logos kept in one data folder, as an index in memory, and the background queue that fills it.

    A page never waits on the network: `states` answers from the index and queues the companies it has not looked up. Two
    workers look up one company at a time (its postings from `lookup`, then `find`), keep the image or remember the miss, and the
    page asks again. Every company is looked up once however many rows name it (the queue holds a company once), a miss is
    remembered for a month, and being offline for ten minutes."""

    WORKERS = 2
    LIMIT = 2000  # Companies waiting: more than any page names, and a bound on what a stream of made-up names can queue.

    def __init__(self, data_dir):
        self.folder = Path(data_dir) / FOLDER
        self.ready = {}  # key -> (extension, modified time): the version in the image's address, so a replaced file is fetched anew
        self.missed = {}  # key -> when the verdict was written
        self.later = {}  # key -> when it was last skipped (no posting, or offline): asked again in ten minutes
        self.waiting = set()
        self.queue = None
        self.tasks = []
        self.scanned = False
        self.legacy = []
        self.third_party = True

    def scan(self):
        """Reads the folder once. Files kept by older versions (a full-size PNG, an ICO) are served as they are and shrunk by the
        worker; a "no logo" verdict from older rules is dropped, because those rules refused every wide wordmark."""
        self.ready.clear()
        self.missed.clear()
        legacy = []
        if self.folder.is_dir():
            for entry in self.folder.iterdir():
                stem, _, ext = entry.name.rpartition(".")
                if not KEY.fullmatch(stem):
                    continue
                if ext in MEDIA:
                    old = self.ready.get(stem)
                    if old and EXTENSIONS.index(old[0]) < EXTENSIONS.index(ext):
                        continue
                    self.ready[stem] = (ext, int(entry.stat().st_mtime))
                    if ext not in ("webp", "svg"):
                        legacy.append(stem)
                elif ext == f"miss{RULES}":
                    self.missed[stem] = entry.stat().st_mtime
                elif ext == "none":
                    entry.unlink(missing_ok=True)
        self.legacy = legacy
        self.scanned = True

    def path(self, key):
        got = self.ready.get(key)
        return (self.folder / f"{key}.{got[0]}", got[0], got[1]) if got else None

    def read(self, key):
        """(bytes, extension, version) kept for this key, else None."""
        found = self.path(key)
        if not found:
            return None
        try:
            return found[0].read_bytes(), found[1], found[2]
        except OSError:
            self.ready.pop(key, None)
            return None

    def state(self, key, now=None):
        """"ready", "none" (a recent verdict), "later" (skipped lately) or None (never looked up)."""
        now = now or time.time()
        if key in self.ready:
            return "ready"
        if key in self.missed and now - self.missed[key] < RETRY_MISS_SECONDS:
            return "none"
        if key in self.later and now - self.later[key] < RETRY_OFFLINE_SECONDS:
            return "later"
        return None

    def request(self, company, lookup):
        """Queues a company to be looked up unless it is known or already waiting. `lookup(company)` gives its postings as
        [(url, description)] and runs in a thread."""
        key = key_for(company)
        if not key or key in self.waiting or self.state(key) or len(self.waiting) >= self.LIMIT:
            return
        if self.queue is None:
            self.queue = asyncio.Queue()
        self.waiting.add(key)
        self.queue.put_nowait((key, company, lookup))
        while len(self.tasks) < self.WORKERS:
            self.tasks.append(asyncio.create_task(self._work()))

    async def _work(self):
        while True:
            if self.legacy:  # Logos kept at full size by older versions: shrunk one at a time, between lookups.
                await self.shrink_saved(self.legacy.pop())
                await asyncio.sleep(0.05)
                continue
            try:
                key, company, lookup = await asyncio.wait_for(self.queue.get(), timeout=5)
            except asyncio.TimeoutError:
                if not self.legacy and self.queue.empty():
                    self.tasks.remove(asyncio.current_task())
                    return
                continue
            try:
                postings = await asyncio.to_thread(lookup, company)
                if not postings:
                    self.later[key] = time.time()  # Not a company any job here names: not worth a verdict file.
                else:
                    await self.fill(company, [u for u, _ in postings], [d for _, d in postings], self.third_party)
            except Exception:  # noqa: BLE001 - one company's failure must not end the worker
                self.later[key] = time.time()
            finally:
                self.waiting.discard(key)
                self.queue.task_done()
            await asyncio.sleep(0.05)

    async def shrink_saved(self, key):
        found = self.read(key)
        if not found:
            return
        body, ext, _ = found
        kept = await asyncio.to_thread(shrink, body, ext)
        if not kept or kept[1] == ext:
            return
        await asyncio.to_thread(self.write, key, kept[0], kept[1])
        (self.folder / f"{key}.{ext}").unlink(missing_ok=True)

    def write(self, key, body, ext):
        self.folder.mkdir(parents=True, exist_ok=True)
        partial = self.folder / f".{key}.tmp"
        partial.write_bytes(body)
        partial.replace(self.folder / f"{key}.{ext}")
        self.ready[key] = (ext, int(time.time()))
        self.missed.pop(key, None)
        self.later.pop(key, None)

    async def fill(self, company, urls, descriptions=(), third_party=True):
        """(bytes, extension) of the company's icon, found now and kept; None, remembered as a month's verdict, when no source can
        vouch for it. Being offline remembers it for ten minutes only."""
        key = key_for(company)
        if not key or not urls:
            return None
        try:
            found = await find(company, urls, descriptions, third_party)
        except (httpx.HTTPError, OSError):
            self.later[key] = time.time()
            return None
        if not found:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / f"{key}.miss{RULES}").touch()
            self.missed[key] = time.time()
            return None
        await asyncio.to_thread(self.write, key, *found)
        return found


_shelves = {}


def shelf(data_dir):
    """The one Shelf for this data folder."""
    key = str(data_dir)
    if key not in _shelves:
        _shelves[key] = Shelf(data_dir)
    return _shelves[key]


async def logo(data_dir, company, urls, descriptions=(), third_party=True):
    """(bytes, extension) of the company's icon, found now if it is not kept (for tests and scripts: a page uses the Shelf's queue)."""
    key = key_for(company)
    if not key or not urls:
        return None
    kept = shelf(data_dir)
    if not kept.scanned:
        await asyncio.to_thread(kept.scan)
    if got := kept.read(key):
        return got[:2]
    if kept.state(key) in ("none", "later"):
        return None
    return await kept.fill(company, urls, descriptions, third_party)
