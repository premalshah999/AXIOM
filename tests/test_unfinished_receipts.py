"""Liberty Mutual 2026-10-21: "You've started your Liberty Mutual application!" was matched as an employer receipt
and the run was marked confirmed. A notice about an unfinished application is never a receipt."""

from jobpilot.corrections import UNFINISHED_MARKER, revert_unfinished_receipts
from jobpilot.db import Job, Run, Setting
from jobpilot.receipts import UNFINISHED, unfinished_receipt


def test_started_and_incomplete_notices_are_not_receipts():
    for text in [
        "You've started your Liberty Mutual application!",
        "Your application for Data Engineer is incomplete",
        "Finish your application to Acme",
        "Your application has not been submitted yet",
        "We noticed you started an application for Software Engineer",
    ]:
        assert UNFINISHED.search(text), text
    for text in [
        "Thank you for applying to Acme",
        "We received your application",
        "Your job application has been received.",
        "Thank you for completing your application",
        "Application Update",
    ]:
        assert not UNFINISHED.search(text), text


def test_only_email_only_confirmations_from_unfinished_notices_are_reverted(service):
    db = service.db
    started = {"email_confirmed": True, "website_confirmed": False,
               "email": {"subject": "You've started your Acme application!"}}
    real = {"email_confirmed": True, "website_confirmed": False, "email": {"subject": "Thank you for applying to Acme"}}
    website = {**started, "website_confirmed": True}
    with db.exclusive() as s:
        for jid in ("j1", "j2", "j3"):
            s.add(Job(id=jid, url=f"https://acme.example/{jid}", identity="acme:" + jid, title="Engineer", company="Acme",
                      status="confirmed"))
        s.add(Run(id="r1", job_id="j1", state="confirmed", receipt=started))
        s.add(Run(id="r2", job_id="j2", state="confirmed", receipt=real))
        s.add(Run(id="r3", job_id="j3", state="confirmed", receipt=website))
        marker = s.get(Setting, UNFINISHED_MARKER)  # The service already ran it at start on an empty database.
        if marker is not None:
            s.delete(marker)
    assert revert_unfinished_receipts(db) == ["r1"]
    with db.session() as s:
        assert s.get(Run, "r1").state == "needs_review" and s.get(Job, "j1").status == "needs_review"
        assert s.get(Run, "r1").receipt["email_confirmed"] is False
        assert s.get(Run, "r2").state == "confirmed" and s.get(Run, "r3").state == "confirmed"
        assert s.get(Setting, UNFINISHED_MARKER).value["reverted"] == ["r1"]
    assert revert_unfinished_receipts(db) == []  # Runs once.
    assert unfinished_receipt(None) is False
