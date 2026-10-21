"""Small helpers shared by the mail modules."""

from urllib.parse import urlsplit
import hashlib
import re
from ..db import Job
from ..db import Run


def run_company(session, run_id):
    """The normalized employer name of a run's job ("" when unknown never matches another)."""
    run = session.get(Run, run_id)
    job = session.get(Job, run.job_id) if run else None
    name = re.sub(r"[^a-z0-9]+", " ", str(getattr(job, "company", "") or "").casefold()).strip()
    return name or "unknown:" + str(run_id)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def origin(url):
    p = urlsplit(url)
    if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
        raise ValueError("Provide an http(s) origin without embedded credentials")
    host = p.hostname.lower()
    port = p.port
    if ":" in host:
        host = f"[{host}]"
    return f"{p.scheme}://{host}" + (
        f":{port}" if port and port != (443 if p.scheme == "https" else 80) else ""
    )
