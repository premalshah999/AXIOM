"""The employer's real name for a board, as the board itself states it.

The catalog lists most boards by the slug in their address ("quora", "generalintuition-medal"), and a scan used that as the employer of
every posting it read. A new install showed 484 of 738 jobs under a lowercase slug and 78 under a UUID, an address no person chose, while the
owner's database hid it: months of postings had taken their names from other lists. So the name comes from the board:

- Greenhouse and SmartRecruiters state it in the list a scan reads anyway (no extra request).
- Ashby's and Lever's public lists do not, but their hosted board page does: read once for a slug-named board, and kept on the source.

What a board cannot name is never invented: a slug gets its case tidied only when nothing better exists, and an id or a hostname is no name at
all: the job's employer stays blank until its posting is read, and the screens say "Company not listed".
"""

import asyncio
import html as htmllib
import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, select, update

from .db import Job, JobListing, Source
from .posting_identity import placeholder_company, placeholder_title
from .schemas import employer_from_title

NOT_LISTED = "Company not listed"  # posting_identity.placeholder_company reads it as no name, so it is never stored on a job.
TRIED = "job_board_name:"  # job_board_name:<source id>: when its hosted page was last asked for a name.
TRY_AGAIN = timedelta(days=14)
PAGE_NAMED = {"ashby", "lever"}  # Boards whose public list names no employer, and whose hosted page does.
PAGE_BYTES = 2_000_000
ASHBY_DATA = re.compile(r"window\.__appData\s*=\s*\{")
TITLE = re.compile(r"<title[^>]*>([^<]{1,200})</title>", re.I)


def slug_like(name):
    """A name as an address spells it: all lower case with no spaces ("quora", "generalintuition-medal", "datadog")."""
    text = (name or "").strip()
    return bool(text) and text == text.lower() and " " not in text and any(c.isalpha() for c in text)


def tidied(name):
    """A slug's words with their first letters raised, when nothing better is known: "general-intuition" reads "General Intuition"."""
    return " ".join(part[:1].upper() + part[1:] for part in re.split(r"[-_.]+", (name or "").strip()) if part)


def usable(stated, slug=""):
    """What a board states, if it is a name: not blank, an id, a hostname or the board's own slug again."""
    text = " ".join(str(stated or "").split())[:200]
    return "" if not text or placeholder_company(text, "") or text == slug else text


def from_ashby_page(page):
    """The organisation's name in an Ashby hosted board page's own data."""
    match = ASHBY_DATA.search(page or "")
    if not match:
        return ""
    try:
        # raw_decode: the page's script goes on after this object, so only the object is read.
        data, _ = json.JSONDecoder().raw_decode(page, match.end() - 1)
        return str((data.get("organization") or {}).get("name") or "")
    except (ValueError, AttributeError):
        return ""


def from_title(page):
    """A Lever hosted board's title is the employer's name, and nothing else."""
    match = TITLE.search(page or "")
    name = employer_from_title(htmllib.unescape(match.group(1)).strip()) if match else ""
    return "" if placeholder_title(name) else name  # A page label ("Jobs", "Careers") is no employer.


def board_slug(url):
    return urlsplit(url or "").path.strip("/").split("/")[0]


async def stated_on_page(kind, url, fetch):
    """The name a slug-named board's hosted page states, else "". One public page; any failure is simply no name."""
    slug = board_slug(url)
    if kind not in PAGE_NAMED or not slug:
        return ""
    try:
        body, _ = await fetch(url.rstrip("/"), max_bytes=PAGE_BYTES)
    except Exception:  # noqa: BLE001 - a name is an extra; the scan stands without it
        return ""
    page = body.decode("utf-8", "ignore") if isinstance(body, (bytes, bytearray)) else str(body)
    return usable(from_ashby_page(page) if kind == "ashby" else from_title(page), slug)


def rename_board_jobs(session, source_id, old, new):
    """The board's own jobs that carry its old non-name take the real one: the slug in any case, an id or no name at all. A name the
    posting itself gave stays."""
    listed = select(JobListing.job_id).where(JobListing.source_id == source_id)
    names = {"", (old or "").casefold(), tidied(old).casefold()}
    return session.execute(
        update(Job).where(Job.id.in_(listed), func.lower(Job.company).in_(names), Job.company != new).values(company=new).execution_options(synchronize_session=False)
    ).rowcount


def save_name(db, source_id, old, new):
    with db.exclusive() as s:
        row = s.get(Source, source_id)
        if row is not None:
            row.name = new
        rename_board_jobs(s, source_id, old, new)


def save_stated(db, source):
    """What the list just read states about its employer (Greenhouse, SmartRecruiters): kept, and the board's jobs take it. Nothing to do once saved."""
    stated = usable(source.get("stated_name"), board_slug(source["url"]))
    if not stated:
        return
    with db.session() as s:
        row = s.get(Source, source["id"])
        current = row.name if row is not None else ""
    # A person's own name for a board, or one a list gave, stays: only a slug, an id or no name gives way.
    if current != stated and no_name(current, source["url"]) or slug_like(current) and current != stated:
        save_name(db, source["id"], current, stated)


def no_name(name, url):
    """An id, a hostname, a blank or the screens' words for none: nothing that names an employer. A board saved under its own link too."""
    return placeholder_company(name, url) or "://" in (name or "")


async def named(source, db, fetch):
    """`source` with the employer its postings are read under. A real name costs nothing; a slug or an id asks the board once. Runs on the
    event loop the browsers share, so every database touch is on a worker thread."""
    name, url, kind = source["name"], source["url"], source["kind"]
    missing = no_name(name, url)
    if not missing and not slug_like(name):
        return source
    stated = ""
    if kind in PAGE_NAMED:
        key = TRIED + source["id"]
        at = datetime.now(UTC)
        tried = (await asyncio.to_thread(db.get_setting, key, {}) or {}).get("at", "")
        if not tried or tried < (at - TRY_AGAIN).isoformat():
            stated = await stated_on_page(kind, url, fetch)
            await asyncio.to_thread(db.set_setting, key, {"at": at.isoformat(), "found": bool(stated)})
    if stated:
        await asyncio.to_thread(save_name, db, source["id"], name, stated)
        return {**source, "name": stated}
    return {**source, "name": "" if missing else tidied(name)}
