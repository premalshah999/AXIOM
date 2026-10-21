"""One-time corrections of records a fixed bug wrote wrongly. Each runs once and records that it ran."""

from sqlalchemy import select

from .db import Job, Run, Setting, now
from .receipts import unfinished_receipt

UNFINISHED_MARKER = "corrected:unfinished_receipts_v1"


def revert_unfinished_receipts(db) -> list:
    """Un-confirm runs confirmed only by an employer email about an unfinished application.

    Liberty Mutual 2026-10-21: "You've started your Liberty Mutual application!" was matched as a receipt and the run
    was marked confirmed. A run the website itself confirmed is left alone. Returns the reverted run ids.
    """
    with db.exclusive() as s:
        if s.get(Setting, UNFINISHED_MARKER):
            return []
        reverted = []
        for run in s.scalars(select(Run).where(Run.state == "confirmed")):
            receipt = dict(run.receipt or {})
            if not receipt.get("email_confirmed") or receipt.get("website_confirmed"):
                continue
            if not unfinished_receipt(receipt.get("email")):
                continue
            receipt.update(email_confirmed=False, evidence_reconciliation="reverted_unfinished_notice")
            run.receipt = receipt
            run.state = "needs_review"
            run.reason = "The employer's email was about an unfinished application, not a receipt; it was never confirmed"
            job = s.get(Job, run.job_id)
            if job is not None and job.status == "confirmed":
                job.status = "needs_review"
            reverted.append(run.id)
        s.add(Setting(key=UNFINISHED_MARKER, value={"at": now(), "reverted": reverted}))
    for run_id in reverted:
        db.event(run_id, "receipt_reverted", "An unfinished-application email had been taken as a receipt", {})
    return reverted
