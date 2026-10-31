"""Compact failed-application history; immutable job identities prevent rediscovery retries."""

import gzip
import json
import os
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import delete, select

from .db import Event, Job, Run, Setting, now, record


def archive_failed(service):
    db, root = service.db, service.config.data_dir / "archive"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    archived, retained_bytes = [], 0
    with db.session() as s:
        jobs = [
            record(j) for j in s.scalars(select(Job).where(Job.demo.is_(False), Job.status != "archived"))
        ]
    for job in jobs:
        jid = job["id"]
        with db.session() as s:
            runs = [
                record(r) for r in s.scalars(select(Run).where(Run.job_id == jid).order_by(Run.created_at))
            ]
            if not runs or any(r["state"] in {"confirmed", "queued", "running", "submitting"} for r in runs):
                continue
            latest = runs[-1]
            log = root / f"{jid}.jsonl.gz"
            temporary = root / f".{jid}.tmp"
            with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as out:
                for run in runs:
                    out.write(json.dumps({"run": {k: v for k, v in run.items() if k != "packet"}}) + "\n")
                    for e in s.scalars(select(Event).where(Event.run_id == run["id"]).order_by(Event.id)):
                        out.write(json.dumps({"event": record(e)}) + "\n")
            temporary.chmod(0o600)
            os.replace(temporary, log)
            size = log.stat().st_size
            summary = {
                "job_id": jid,
                "company": job["company"],
                "title": job["title"],
                "url": job["url"],
                "ats": job["ats"],
                "run_id": latest["id"],
                "state": latest["state"],
                "reason": latest["reason"][:800],
                "attempts": len(runs),
                "archived_at": now(),
                "cost": round(sum(r["cost"] for r in runs), 5),
                "log_bytes": size,
                "uncertain_submission": any(r["state"] == "submission_unknown" for r in runs),
            }
        with db.exclusive() as s:
            current = list(s.scalars(select(Run).where(Run.job_id == jid)))
            if any(r.state in {"confirmed", "queued", "running", "submitting"} for r in current):
                continue
            s.get(Job, jid).status = "archived"
            s.add(Setting(key="archived_application:" + jid, value=summary))
            for r in current:
                # Preserve receipt matching and duplicate protection, without a
                # repeated full profile/resume snapshot in each abandoned run.
                if r.state != "submission_unknown":
                    r.packet = {k: v for k, v in r.packet.items() if k in {"match_approved", "resume_sha"}}
                s.execute(delete(Event).where(Event.run_id == r.id))
            recovery = s.get(Setting, "draft_recovery:" + jid)
            if recovery:
                recovery.value = {**recovery.value, "scheduled": False, "status": "archived"}
            desktop = s.get(Setting, "desktop_job:" + jid)
            if desktop:
                # Keep target for the one-time tab cleanup, not any browser ledger.
                desktop.value = {"target_id": desktop.value.get("target_id"), "archived": True}
        for run in runs:
            folder = service.config.data_dir / "runs" / run["id"]
            # The archive retains logs; repeated images, copied resumes and
            # detailed step artifacts are removed from these abandoned drafts.
            if folder.exists():
                shutil.rmtree(folder)
        archived.append(jid)
        retained_bytes += size
    db.set_setting("telegram_question", {})
    db.set_setting("telegram_browser_question", {})
    return {"archived": len(archived), "log_bytes": retained_bytes}


def summaries(db):
    with db.session() as s:
        rows = list(
            row.value for row in s.scalars(select(Setting).where(Setting.key.startswith("archived_application:", autoescape=True)))
        )
        known = {row["job_id"] for row in rows}
        removed = [
            row
            for row in s.scalars(select(Setting).where(Setting.key.startswith("job_control:", autoescape=True)))
            if row.value.get("state") == "deleted"
        ]
        for control in removed:
            jid = control.key.removeprefix("job_control:")
            job = s.get(Job, jid)
            if not job or jid in known:
                continue
            # Columns only: the archive page read every removed job's whole packets (18 of 121 JSON samples, 2026-10-30).
            runs = s.execute(
                select(Run.id, Run.state, Run.reason, Run.cost).where(Run.job_id == jid).order_by(Run.created_at.desc())
            ).all()
            last = runs[0] if runs else None
            rows.append(
                {
                    "job_id": jid,
                    "company": job.company,
                    "title": job.title,
                    "url": job.url,
                    "ats": job.ats,
                    "run_id": last.id if last else "",
                    "state": last.state if last else "cancelled",
                    "reason": last.reason if last else "Removed by owner",
                    "attempts": len(runs),
                    "archived_at": control.value.get("at", job.created_at),
                    "cost": sum(r.cost for r in runs),
                    "log_bytes": control.value.get("log_bytes", 0),
                    "owner_removed": True,
                    "uncertain_submission": any(
                        r.state in {"submission_unknown", "submitting"} for r in runs
                    ),
                }
            )
        return sorted(rows, key=lambda row: row["archived_at"], reverse=True)


def log_path(service, jid):
    if not service.db.get_setting("archived_application:" + jid):
        key = "job_control:" + jid
        control = service.db.get_setting(key, {})
        if control.get("state") != "deleted":
            raise ValueError("Archived application not found")
        root = service.config.data_dir / "archive"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Owner deletion preserves the live evidence rows. Export their latest
        # history on demand; never prune a submitting worker's log or receipts.
        path = root / f"{jid}.jsonl.gz"
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as tmp:
            temporary = Path(tmp.name)
        with service.db.session() as s, gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as out:
            for run in s.scalars(select(Run).where(Run.job_id == jid).order_by(Run.created_at)):
                out.write(json.dumps({"run": {k: v for k, v in record(run).items() if k != "packet"}}) + "\n")
                for event in s.scalars(select(Event).where(Event.run_id == run.id).order_by(Event.id)):
                    out.write(json.dumps({"event": record(event)}) + "\n")
        os.replace(temporary, path)
        service.db.set_setting(key, {**control, "log_bytes": path.stat().st_size})
    return Path(service.config.data_dir) / "archive" / f"{jid}.jsonl.gz"
