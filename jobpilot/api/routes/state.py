"""Dashboard state: the one-poll snapshot and the ledger of confirmed applications."""

import csv
import io
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy import select

from ...db import Job, Run
from ..snapshot import SnapshotCache, build_snapshot, trusted_email


def _instant(value):
    """A stored timestamp or the page's `since` as an aware UTC datetime; None when it is not one."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _has_proof(row, kind):
    """The Applications page's evidence filter: a confirmation page, an employer email, an existing application, or none of them."""
    if kind == "page":
        return row["website_confirmed"]
    if kind == "email":
        return row["email_confirmed"]
    if kind == "existing":
        return row["existing_application"]
    if kind == "none":
        return not (row["website_confirmed"] or row["email_confirmed"] or row["existing_application"])
    return True


def _proof_label(row):
    """The page's words (frontend/src/model.ts evidenceLabel): a spreadsheet and the screen say the same thing."""
    if row["existing_application"]:
        return "Existing application"
    if row["website_confirmed"] and row["email_confirmed"]:
        return "Confirmation page and email"
    if row["website_confirmed"]:
        return "Confirmation page"
    return "Employer email" if row["email_confirmed"] else "No receipt on file"


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
                .where(Run.state == "confirmed", Job.demo.is_(False))
                .order_by(Run.finished_at.desc())
            ).all()
            seen, results = set(), []
            for row in rows:
                receipt = row.receipt or {}
                if row.job_id in seen or receipt.get("evidence_reconciliation") == "pending":
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
                        "existing_application": bool(receipt.get("existing_application_notice")),
                    }
                )
            return results

    @r.get("/api/successful-applications")
    def successes():
        return {"applications": confirmed()}

    @r.get("/api/successful-applications.csv")
    def export(evidence: str = "", ats: str = "", since: str = "", q: str = ""):
        """The ledger as a spreadsheet, for the Applications page's Export. A download the app serves itself (the page
        links here), so it saves the same way in a browser tab and in the native window, where a script-made file may not.
        The filters are the page's own: evidence, platform, a start time (applications AXIOM sent, so the employer's
        "you already applied" notices fall away) and a search (a job saved without a company or title is found by its link)."""
        start = _instant(since)
        needle = q.strip().lower()
        rows = [
            row
            for row in confirmed()
            if _has_proof(row, evidence)
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
                    _proof_label(row),
                )
            )
        return Response(
            "\ufeff" + out.getvalue(),  # the BOM makes a spreadsheet read UTF-8
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="AXIOM-confirmed-applications.csv"'},
        )

    return r
