"""iCIMS employee portals (2026-10-15): employees-/internal- hosts stop outside candidates at an employee
sign-in, so links to them are rewritten to the employer's public careers portal, which posts the same
requisition. The owner saw the application flow only through the public page."""

from jobpilot.ats import canonicalize


def test_internal_portal_links_use_the_public_careers_portal():
    url, identity = canonicalize(
        "https://employees-hrblock.icims.com/jobs/76907/machine-learning-engineer/job"
    )
    assert url == "https://careers-hrblock.icims.com/jobs/76907/machine-learning-engineer/job"
    public_url, public_identity = canonicalize(
        "https://careers-hrblock.icims.com/jobs/76907/machine-learning-engineer/job"
    )
    assert (url, identity) == (public_url, public_identity)
    assert canonicalize("https://internal-vtgdefense.icims.com/jobs/1234/x/job")[0].startswith(
        "https://careers-vtgdefense.icims.com/"
    )


def test_public_and_campus_portals_are_unchanged():
    for url in (
        "https://careers-goldbeltapex.icims.com/jobs/555/ml/job",
        "https://campus-acme.icims.com/jobs/9/x/job",
    ):
        assert canonicalize(url)[0] == url
