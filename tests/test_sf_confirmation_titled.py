"""SuccessFactors' candidate portal names the job in its confirmation: "Your application for Software Developer Engineer
II has been submitted" read as no receipt and the run ended unconfirmed (Optimum 2026-10-26)."""

from jobpilot.forms import SF_CONFIRMATION_RE


def test_a_confirmation_naming_the_job_is_a_confirmation():
    assert SF_CONFIRMATION_RE.search("Your application for Software Developer Engineer II has been submitted successfully.")
    assert SF_CONFIRMATION_RE.search("Your application has been submitted")
    assert not SF_CONFIRMATION_RE.search("Your application for this job. Has been submitted to review? Not yet.")
