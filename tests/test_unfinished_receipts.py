"""Liberty Mutual 2026-10-21: "You've started your Liberty Mutual application!" was matched as an employer receipt
and the run was marked confirmed. A notice about an unfinished application is never a receipt."""

from jobpilot.receipts import UNFINISHED


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

