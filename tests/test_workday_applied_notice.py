"""HPE (Workday) 2026-10-15: the posting page of a submitted application says "You applied for this job
on <date>" with a View Application button; that is already applied, not an unrecognized page."""

from jobpilot.adapters.base import ALREADY, APPLY


def test_workday_applied_notice_is_already_applied():
    assert ALREADY.search(
        "System / Software engineer II You applied for this job on October 15, 2026. View Application"
    )
    assert not APPLY.search("View Application")


def test_sign_in_footer_is_not_an_application():
    assert not ALREADY.search("Already applied? Sign in to view your application status")


def test_icims_currently_submitted_notice_is_already_applied():
    # MCI (iCIMS, 2026-10-24): "Your application was submitted successfully. ... You are currently submitted to this
    # job." on the job's own page; the run asked the owner instead of recording it.
    assert ALREADY.search("You are currently submitted to this job. Check out interests related to this job")
    assert not ALREADY.search("Submit your application to this job today")


async def test_a_confirmation_this_run_did_not_produce_is_already_applied_when_the_page_says_so():
    from types import SimpleNamespace

    from jobpilot.adapters.base import Adapter

    adapter = Adapter.__new__(Adapter)
    adapter.committed = False
    adapter.e = SimpleNamespace(form=SimpleNamespace(page=SimpleNamespace(url="https://careers-acme.icims.com/jobs/1/job")))
    adapter.defer = lambda reason, *a, **k: {"state": "needs_review", "reason": reason}
    said = await adapter.on_confirmation({"text": "Your application was submitted successfully. You are currently "
                                                  "submitted to this job."})
    assert said["state"] == "already_applied"
    unclear = await adapter.on_confirmation({"text": "Thank you for your interest in Acme."})
    assert unclear["state"] == "needs_review"  # anything less explicit still goes to the owner
