"""Short links hide the posting they point to.

A new-grad feed links its postings through a redirecting short link (go.vansh.nyc/XO). The job's identity came from
that link, so `canonicalize` could not see that it was the Lever posting the engine had already applied to, and Shield
AI's "Engineer I, Software Test and Automation" was applied to twice (2026-10-23 and 2026-10-29): the employer sees a
duplicate or answers "already applied". The target is resolved here, once, and kept.

Resolution never opens the employer's page: only a short-link host is asked, with one HEAD, and the first location
that is not itself a short link is the answer. A failed lookup is remembered for an hour and never blocks anything;
the application then proceeds under the link as before.
"""

import hashlib
import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlsplit

import httpx

from .admission import SHARED_LINK_HOSTS

# Hosts that redirect to a posting. The aggregators among SHARED_LINK_HOSTS serve pages of their own, so they stay.
SHORTENER_HOSTS = frozenset(SHARED_LINK_HOSTS - {"simplify.jobs", "jobright.ai"})
CACHE_PREFIX = "shortlink_target:"
TIMEOUT = 5.0  # seconds per request
MAX_HOPS = 4  # A short link that points at another short link.
MIN_GAP = 1.0  # seconds between two requests to one host, whoever asks
RETRY_AFTER = timedelta(hours=1)  # A failed lookup is not repeated sooner.
# Test seam: an httpx transport (a mock) in place of the network.
TRANSPORT = None

_lock = threading.Lock()
_last: dict = {}  # host -> monotonic time of the latest request to it
_memo: dict = {}  # url -> final URL or None, for this process
_MEMO_LIMIT = 20000


def is_shortlink(url) -> bool:
    try:
        return (urlsplit(url or "").hostname or "").lower() in SHORTENER_HOSTS
    except ValueError:
        return False


def cache_key(url) -> str:
    return CACHE_PREFIX + hashlib.sha256(url.strip().encode()).hexdigest()[:32]


def cached(session, url):
    """(known, final): the saved answer for this link without any network. `known` is False when it was never asked, or
    a failed lookup is old enough to ask again; a failure is (True, None)."""
    from .db import Setting

    row = session.get(Setting, cache_key(url))
    if row is None:
        return False, None
    value = row.value or {}
    if value.get("final"):
        return True, value["final"]
    try:
        retry_at = datetime.fromisoformat(value["retry_at"])
    except (KeyError, TypeError, ValueError):
        return False, None
    return datetime.now(UTC) < retry_at, None


AGENT = {"User-Agent": "Mozilla/5.0 (compatible; job-link-check)"}


def _location(client, url):
    """(status, Location) of one request to a short link: a HEAD, or a body-less GET when the host refuses HEAD."""
    response = client.request("HEAD", url, headers=AGENT)
    if response.status_code in {403, 405, 501}:
        with client.stream("GET", url, headers=AGENT) as streamed:
            return streamed.status_code, streamed.headers.get("location")
    return response.status_code, response.headers.get("location")


def _target(url):
    """The first non-shortener location the short link chain ends at, or None."""
    from .ats import canonicalize

    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False, transport=TRANSPORT) as client:
        for _ in range(MAX_HOPS):
            host = (urlsplit(url).hostname or "").lower()
            with _lock:  # One request per host per MIN_GAP, however many threads ask.
                ready = max(time.monotonic(), _last.get(host, 0) + MIN_GAP)
                wait, _last[host] = ready - time.monotonic(), ready
            if wait > 0:
                time.sleep(wait)
            status, location = _location(client, url)
            if status not in {301, 302, 303, 307, 308} or not location:
                return None
            url = urljoin(url, location)
            if not is_shortlink(url):
                canonicalize(url)  # Raises ValueError for anything that is not an applicant-facing http(s) URL.
                return url
    return None


def resolve(url, db=None):
    """The posting a short link points to, or None when it cannot be told (not a short link, a failed lookup, an
    unusable answer). Cached in this process and, with `db`, durably; a blocking call: callers on the event loop use
    asyncio.to_thread."""
    if not is_shortlink(url):
        return None
    url = url.strip()
    if url in _memo:
        return _memo[url]
    if db is not None:
        with db.session() as s:
            known, final = cached(s, url)
        if known:
            _memo[url] = final
            return final
    try:
        final = _target(url)
    except Exception:  # noqa: BLE001 - whatever goes wrong is a failed lookup, and a failed lookup never blocks.
        final = None
    value = {"url": url, "final": final, "at": datetime.now(UTC).isoformat()}
    if final is None:
        value["retry_at"] = (datetime.now(UTC) + RETRY_AFTER).isoformat()
    if db is not None:
        from .db import Setting

        with db.exclusive() as s:
            row = s.get(Setting, cache_key(url))
            if row:
                row.value = value
            else:
                s.add(Setting(key=cache_key(url), value=value))
    if final is not None:  # A failure is remembered by the saved retry time, not for the life of the process.
        if len(_memo) >= _MEMO_LIMIT:
            _memo.clear()
        _memo[url] = final
    return final


def twins(session, final):
    """URLs of short links whose saved target is the posting `final`: the other side of the same duplicate."""
    from sqlalchemy import select

    from .ats import identity_variants
    from .db import Setting

    wanted = set(identity_variants(final))
    found = set()
    for row in session.scalars(select(Setting).where(Setting.key.like(CACHE_PREFIX + "%"))):
        value = row.value or {}
        try:
            if value.get("final") and wanted & set(identity_variants(value["final"])):
                found.add(value["url"])
        except (KeyError, ValueError):
            continue
    return found
