"""Owner 2026-10-12: "Don't protect unknown submissions, resubmit them till you get a confirmation." The autopilot
resubmits an uncertain application only when no receipt arrived, the receipt wait passed, and the run's own final page
shows no confirmation; a page that thanks the applicant is never resubmitted."""

from datetime import UTC, datetime, timedelta

from jobpilot.autopilot import resubmit_unknown
from jobpilot.db import Job, Run, Setting


def _unknown(service, n, minutes_ago, page=None):
    at = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    with service.db.exclusive() as s:
        s.add(Job(id=f"u{n}", identity=f"u{n}", url=f"https://boards.greenhouse.io/acme{n}/jobs/{n}", ats="greenhouse",
                  company=f"Acme {n}", status="submission_unknown"))
        s.add(Run(id=f"ur{n}", job_id=f"u{n}", mode="submit", state="submission_unknown", created_at=at, finished_at=at,
                  started_at=at, reason="Submission attempted, but no explicit receipt was captured"))
    if page is not None:
        folder = service.config.data_dir / "runs" / f"ur{n}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "final_outline.txt").write_text(page)


async def test_uncertain_applications_are_resubmitted_only_without_a_confirmation(service, monkeypatch):
    _unknown(service, 1, 120, "Apply for Data Scientist\nFirst Name\nSubmit application")
    _unknown(service, 2, 120, "Thank you for applying! We have received your application.")
    _unknown(service, 3, 120)  # No saved final page.
    _unknown(service, 4, 5, "Apply for Data Scientist\nSubmit application")  # The receipt may still arrive.
    _unknown(service, 5, 120, "h1: Task complete!\np: Thank you for sharing your information with us at Asana.")
    queued = []

    async def queue(job_id, mode, *args, **kwargs):
        queued.append(job_id)
        return {"id": "again"}

    monkeypatch.setattr(service, "queue", queue)
    assert await resubmit_unknown(service, 5) == 1
    assert queued == ["u1"]
    with service.db.session() as s:
        assert s.get(Setting, "unknown_triage:u2").value["verdict"] == "submitted"
        assert s.get(Setting, "unknown_triage:u5").value["verdict"] == "submitted"  # Asana's wording, 2026-10-18.
        assert s.get(Setting, "unknown_triage:u1") is None and s.get(Setting, "unknown_triage:u3") is None
    # The triaged job is not offered again.
    queued.clear()
    await resubmit_unknown(service, 5)
    assert "u2" not in queued
