"""A booked final page must acknowledge this application (fix-submit, 2026-10-31).

ASSA ABLOY (SuccessFactors) 2026-10-27: the read-only "Review" view of an application already on file was booked as the
run's confirmation. Shield AI 2026-10-29: "Your application was already submitted" is the employer's record of an earlier
one. A review, a form, a listing or an applied-application view never counts; synthetic data only."""

import json

import pytest

from jobpilot.db import Event, Job, Run
from jobpilot.final_page import acknowledges, final_page_verdict

APPLIED_VIEW = (
    "https://career2.successfactors.example/portalcareer?career_ns=job_application&navBarLevel=JOB_MGMT"
    "&subNavBarLevel=JOB_APPLIED&fbja_action=fbja_viewApp&career_job_req_id=51362"
)


@pytest.mark.parametrize(
    "evidence,outline,verdict",
    [
        ('Final page: "Your application for Software Engineer has been received!"', "", "acknowledged"),
        ('SmartRecruiters /success: "Bewerbung eingereicht! Die Bewerbung als Engineer wurde erfolgreich eingereicht."',
         "", "acknowledged"),
        ("Thank you for applying. Your application has been submitted and will be reviewed.", "", "acknowledged"),
        # The employer's record of an earlier application is never this run's receipt.
        ("Your application was already submitted. We received your previous application on October 23, 2026.", "",
         "already_applied"),
        ("Thank you for applying", "p: You have already applied to this job.", "already_applied"),
        # A hedge, a negation, a review step or a form is not an acknowledgement.
        ("After you apply, your application will be received by our team.", "", "rejected"),
        ("The page does not say your application was submitted.", "", "rejected"),
        ("The read-only 7. Review with Withdraw Application and Close", "", "rejected"),
        ("Thank you for applying", "h2: Review\n  button.x: Withdraw Application\n  button.y: Close", "rejected"),
        ("Your application was submitted", "button#a: Submit\ninput#b: Email", "rejected"),
        ("Your application was submitted", "h1: Software Engineer\na.apply: Apply now", "rejected"),
        # The posting's shell under a green banner (iCIMS): the banner names this application, "currently submitted" is
        # the portal's wording for it.
        ("Your application was submitted successfully. You are currently submitted to this job.", "h1: Analyst",
         "acknowledged"),
    ],
)
def test_a_booked_final_page_must_acknowledge_this_application(evidence, outline, verdict):
    assert final_page_verdict(evidence, outline=outline)[0] == verdict


def test_the_employers_applied_view_is_already_applied_whatever_the_quote_says():
    assert final_page_verdict("Your application was submitted", url=APPLIED_VIEW)[0] == "already_applied"
    assert not acknowledges("Your application was already submitted")


def seed(service, outline=None, url=None):
    with service.db.exclusive() as s:
        job = Job(identity="fp-1", url="https://jobs.example.test/1", company="Acme", title="Analyst", ats="custom")
        s.add(job)
        s.flush()
        run = Run(job_id=job.id, state="submission_unknown", mode="submit", packet={}, reason="Submission attempted")
        s.add(run)
        s.flush()
        s.add(Event(run_id=run.id, kind="submitting", message="Commit step started"))
        run_id, job_id = run.id, job.id
    folder = service.config.data_dir / "runs" / run_id
    (folder / "steps").mkdir(parents=True)
    if url:
        (folder / "steps" / "01-form.json").write_text(json.dumps({"url": url}))
    if outline:
        (folder / "final_outline.txt").write_text(outline)
    return run_id, job_id


def state_of(service, run_id, job_id):
    with service.db.session() as s:
        return s.get(Run, run_id).state, s.get(Job, job_id).status, s.get(Run, run_id).receipt


def test_final_page_booking_routes_an_applied_view_to_already_applied(service):
    """ASSA ABLOY 2026-10-27: the read-only "Review" view of a stored application was booked as a confirmation."""
    run_id, job_id = seed(service, url=APPLIED_VIEW)
    service.reconcile(run_id, True, "The Review view with Withdraw Application for this job", final_page=True)
    state, status, receipt = state_of(service, run_id, job_id)
    assert (state, status, receipt["type"]) == ("already_applied", "already_applied", "employer_already_applied")


def test_final_page_booking_routes_an_already_submitted_notice_to_already_applied(service):
    run_id, job_id = seed(service)
    service.reconcile(
        run_id, True, "Final page: 'Your application was already submitted. We received your previous application.'",
        final_page=True,
    )
    assert state_of(service, run_id, job_id)[0] == "already_applied"


@pytest.mark.parametrize(
    "evidence,outline",
    [
        ("The page shows step 7 Review of the wizard", ""),
        ("Your application was submitted", "button.a: Submit application\ninput#email: Email"),
        ("Your application was submitted", "h1: Software Engineer\na.apply: Apply now"),
    ],
)
def test_final_page_booking_refuses_a_review_a_form_or_a_listing(service, evidence, outline):
    run_id, job_id = seed(service, outline=outline)
    with pytest.raises(ValueError):
        service.reconcile(run_id, True, evidence, final_page=True)
    assert state_of(service, run_id, job_id)[0] == "submission_unknown"


def test_final_page_booking_keeps_a_real_acknowledgement(service):
    run_id, job_id = seed(service, outline="h1: Thank you for applying!\np: Your application has been submitted.")
    service.reconcile(run_id, True, 'Final page: "Your application has been submitted."', final_page=True)
    state, status, receipt = state_of(service, run_id, job_id)
    assert (state, status, receipt["type"]) == ("confirmed", "confirmed", "final_page_confirmation")
