"""Dashboard state: the one-poll snapshot and the ledger of confirmed applications."""

import csv
import io
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy import select

from ...db import Job, Run
from ..pipeline import settled_confirmation
from ..snapshot import SnapshotCache, build_snapshot, trusted_email
from ..success import first_commits
from ..success_core import fields, needs_own_commit, ours


def _instant(value):
    """A stored timestamp or the page's `since` as an aware UTC datetime; None when it is not one."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# What proves a confirmed application when neither the employer's page nor an email from the employer does: the receipt's own
# type (receipts.py, completion.py, browser.py). A type this table does not know reads as no proof on file.
_PROOF_BY_TYPE = {
    "employer_already_applied": "existing",
    "application_created": "platform",  # the platform answered the application request as created
    "user_reconciled": "applicant",
    "owner_submitted": "applicant",
    "final_page_confirmation": "final_page",
    "employer_dashboard_status": "dashboard",
}
# The page's words (frontend/src/model.ts evidenceLabel): a spreadsheet and the screen say the same thing.
PROOF_LABELS = {
    "page": "Confirmation page",
    "both": "Confirmation page and email",
    "email": "Employer email",
    "existing": "Existing application",
    "platform": "Platform response",
    "final_page": "Final page",
    "dashboard": "Employer dashboard",
    "applicant": "Recorded by you",
    "none": "No receipt on file",
}


def _proof(receipt, page, email, existing):
    """What proves one application, as an id of PROOF_LABELS."""
    if existing:
        return "existing"
    if page and email:
        return "both"
    if page:
        return "page"
    if email:
        return "email"
    return _PROOF_BY_TYPE.get(receipt.get("type"), "none")


def _has_proof(row, kind):
    """The Applications page's proof filter: a confirmation page, an employer email (each also when both are there), or one proof."""
    if kind == "page":
        return row["website_confirmed"]
    if kind == "email":
        return row["email_confirmed"]
    return not kind or kind == "all" or row["proof"] == kind


def _safe(value):
    """A cell a spreadsheet will not run as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def router(service, cache: SnapshotCache) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.get("/api/snapshot")
    def snapshot(include_sources: bool = True):
        if include_sources or config.snapshot_cache_seconds <= 0:
            return build_snapshot(service, include_sources)
        body, age = cache.body, time.monotonic() - cache.at
        if body is not None and not cache.stale and age < 30:
            if age >= config.snapshot_cache_seconds:
                cache.refresh()
            return Response(body, media_type="application/json")
        return Response(cache.build(), media_type="application/json")

    def confirmed():
        with db.session() as s:
            # Columns only: whole runs decoded every packet on each dashboard poll and starved the event loop
            # the browsers share (2026-10-21).
            rows = s.execute(
                select(
                    Run.id, Run.receipt, Run.finished_at, Run.created_at,
                    Job.id.label("job_id"), Job.title, Job.company, Job.url, Job.ats,
                )
                .join(Job, Run.job_id == Job.id)
                # CONFIRMED (pipeline.confirmed_jobs): the same test of a settled confirmed run as the Jobs chip and
                # Overview, newest first, so a job's newest settled run is its row.
                .where(Job.demo.is_(False), settled_confirmation())
                .order_by(Run.finished_at.desc())
            ).all()
            # Whether AXIOM sent it is the success number's own test (success_core.ours), asked of every settled run of the job:
            # one that proves the send stands whatever a later run found on the employer's page.
            read = {row.id: fields(row.receipt or {}) for row in rows}
            commits = first_commits(s, {row.job_id for row in rows if needs_own_commit(read[row.id])})
            sent = {row.job_id for row in rows if ours(read[row.id], commits.get(row.job_id), row.created_at)}
            seen, results = set(), []
            for row in rows:
                receipt = row.receipt or {}
                if row.job_id in seen:
                    continue
                seen.add(row.job_id)
                results.append(
                    {
                        "job_id": row.job_id,
                        "run_id": row.id,
                        "title": row.title,
                        "company": row.company,
                        "url": row.url,
                        "applied_at": row.finished_at or row.created_at,
                        "ats": row.ats,
                        "website_confirmed": bool(receipt.get("website_confirmed")),
                        "email_confirmed": trusted_email(receipt),
                        # On file with the employer, but not sent by AXIOM: no "sent" filter or daily count takes it.
                        "existing_application": row.job_id not in sent,
                        "proof": _proof(
                            receipt,
                            bool(receipt.get("website_confirmed")),
                            trusted_email(receipt),
                            bool(receipt.get("existing_application_notice")),
                        ),
                    }
                )
            return results

    @r.get("/api/successful-applications")
    def successes():
        return {"applications": confirmed()}

    @r.get("/api/successful-applications.csv")
    def export(evidence: str = "", ats: str = "", since: str = "", q: str = "", replies: bool = False):
        """The ledger as a spreadsheet, for the Applications page's Export. A download the app serves itself (the page
        links here), so it saves the same way in a browser tab and in the native window, where a script-made file may not.
        The filters are the page's own: proof (`evidence`), platform, a start time (applications AXIOM sent, so the employer's
        "you already applied" notices fall away), a search (a job saved without a company or title is found by its link) and
        only the applications an employer has answered (`replies`)."""
        from ...replies import summary

        answered = set(summary(service.db)["job_ids"]) if replies else None
        start = _instant(since)
        needle = q.strip().lower()
        rows = [
            row
            for row in confirmed()
            if _has_proof(row, evidence)
            and (answered is None or row["job_id"] in answered)
            and (not ats or row["ats"] == ats)
            and (start is None or (not row["existing_application"] and (_instant(row["applied_at"]) or start) >= start))
            and (not needle or needle in f"{row['company'] or row['url']} {row['title'] or row['url']} {row['ats']}".lower())
        ]  # fmt: skip
        out = io.StringIO(newline="")
        writer = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        writer.writerow(["Company", "Job title", "Sent (UTC)", "Posting link", "Platform", "Proof"])
        for row in rows:
            sent = _instant(row["applied_at"])
            writer.writerow(
                _safe(cell)
                for cell in (
                    row["company"],
                    row["title"],
                    sent.strftime("%Y-%m-%d %H:%M") if sent else row["applied_at"],
                    row["url"],
                    row["ats"],
                    PROOF_LABELS[row["proof"]],
                )
            )
        return Response(
            "\ufeff" + out.getvalue(),  # the BOM makes a spreadsheet read UTF-8
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="AXIOM-confirmed-applications.csv"'},
        )

    return r
