"""A commit whose click sent nothing that could carry an application is not an uncertain submission.

Dallas County's Oracle form (2026-10-24, four runs): the click sent only Oracle's recruitingCEUserTrackings call and
LinkedIn pixels, the form and its Submit stayed for the whole window, and each run ended as submission_unknown, which
is never retried. Any other write (on any host: applications often post to an ATS API host), a navigation, or a
vanished Submit keeps the outcome uncertain."""

from types import SimpleNamespace

import pytest

from jobpilot.browser import BrowserEngine

FORM = "https://acme.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1/apply/section/1"
TRACKING = [
    {"method": "POST", "host": "acme.fa.ocs.oraclecloud.com", "path": "/hcmRestApi/resources/latest/recruitingCEUserTrackings", "status": 201},
    {"method": "POST", "host": "www.linkedin.com", "path": "/li/track", "status": 200},
]


def engine(url=FORM, requests=TRACKING, controls=({"id": "submit", "label": "Submit"},)):
    async def submit_controls():
        return list(controls)

    return SimpleNamespace(commit_url=FORM, page=SimpleNamespace(url=url), commit_requests=list(requests),
                           job={"url": FORM}, form=SimpleNamespace(application_submit_controls=submit_controls))


async def test_tracking_calls_alone_with_the_form_still_open_sent_nothing():
    assert await BrowserEngine.commit_sent_nothing(engine()) is True


async def test_a_write_the_employer_refused_with_a_client_error_accepted_nothing():
    refused = {"method": "POST", "host": "acme.fa.ocs.oraclecloud.com",
               "path": "/hcmRestApi/resources/latest/recruitingCECandidatePreviousExpItems", "status": 400}
    assert await BrowserEngine.commit_sent_nothing(engine(requests=[*TRACKING, refused])) is True
    assert await BrowserEngine.commit_sent_nothing(engine(requests=[*TRACKING, {**refused, "status": 500}])) is False


@pytest.mark.parametrize("variant", [
    {"requests": [*TRACKING, {"method": "POST", "host": "api.ats.example", "path": "/v1/applications", "status": 200}]},
    {"requests": [*TRACKING, {"method": "POST", "host": "acme.fa.ocs.oraclecloud.com",
                              "path": "/hcmRestApi/resources/latest/recruitingCEJobApplications", "status": 500}]},
    {"url": FORM.replace("apply/section/1", "my-profile")},
    {"controls": ()},
    {"requests": TRACKING * 20},  # a full log may have dropped a write
])
async def test_any_other_write_a_navigation_or_a_vanished_submit_stays_uncertain(variant):
    assert await BrowserEngine.commit_sent_nothing(engine(**variant)) is False


def test_tracking_calls_never_fill_the_commit_log():
    engine = SimpleNamespace(armed=True, commit_requests=[], job={"url": FORM})

    def response(host, path):
        request = SimpleNamespace(method="POST", resource_type="fetch")
        return SimpleNamespace(request=request, url=f"https://{host}{path}", status=200)

    for _ in range(60):
        BrowserEngine.submission_response(engine, response("www.linkedin.com", "/li/track"))
    BrowserEngine.submission_response(engine, response("api.ats.example", "/v1/applications"))
    assert engine.commit_tracking == 60
    assert engine.commit_requests == [{"method": "POST", "host": "api.ats.example", "path": "/v1/applications",
                                       "status": 200}]


def test_a_refused_commit_releases_its_reservation(service):
    """Mattel, Loram, Versant and four more (2026-10-25): after the employer accepted nothing, the repaired page's
    own submit was refused as "not authorized" because the run was still "submitting", and the run ended unconfirmed."""
    from jobpilot.db import Budget, Job, Run

    with service.db.exclusive() as s:
        s.add(Job(id="j", identity="j", url="https://example.test/j", ats="workday", company="Acme", status="ready"))
        s.add(Run(id="r", job_id="j", state="submitting", mode="submit", packet={}))
    assert service.release_submission("r") is True
    with service.db.session() as s:
        assert s.get(Run, "r").state == "running"
    assert service.release_submission("r") is False  # Only a run still holding its reservation is released.


async def test_a_write_still_in_flight_or_failed_without_an_answer_is_never_nothing_sent():
    """Eightfold's Submit (PayPal, Micron 2026-10-25) ended in its adapter's own watch, which never asked whether the
    employer accepted anything. Asking there too must not misread a slow application post as nothing sent."""
    inflight = engine()
    inflight.commit_inflight = {object()}
    assert await BrowserEngine.commit_sent_nothing(inflight) is False
    failed = object.__new__(BrowserEngine)
    failed.armed, failed.commit_requests, failed.commit_inflight, failed.job = True, [], set(), {"url": FORM}
    request = SimpleNamespace(method="POST", resource_type="fetch", url="https://api.ats.example/v1/applications")
    BrowserEngine.commit_request(failed, request)
    assert len(failed.commit_inflight) == 1
    BrowserEngine.commit_request_failed(failed, request)
    assert not failed.commit_inflight and failed.commit_requests[0]["status"] == 0
    assert await BrowserEngine.commit_sent_nothing(engine(requests=failed.commit_requests)) is False


@pytest.mark.parametrize("body,said", [
    ('{"error": {"code": 400, "message": "Phone number 3015550100 is invalid for a@b.example"}}',
     "Phone number ########## is invalid for <email>"),
    ('{"errors": ["Answer required", "Answer required", "Resume missing"]}', "Answer required | Resume missing"),
    ("<html><body><h1>Bad Request</h1><script>x()</script></body></html>", "Bad Request"),
])
def test_an_employer_refusal_is_recorded_without_applicant_values(body, said):
    from jobpilot.browser import refusal_text

    assert refusal_text(body) == said


def test_an_already_applied_refusal_is_an_already_applied_outcome():
    """Eightfold answered PayPal's and Micron's Submit with 400 "You have already applied to this position." and the
    runs resubmitted six times, then sat in repair (2026-10-25)."""
    engine = object.__new__(BrowserEngine)
    engine.emit, engine.page = (lambda *a, **k: None), SimpleNamespace(url=FORM)
    engine.form = SimpleNamespace()
    request = SimpleNamespace(method="POST", resource_type="fetch")
    response = SimpleNamespace(url=FORM.replace("/job/1/apply/section/1", "/api/application/v2/submit"), status=400,
                               request=request)
    BrowserEngine.refusal_event(engine, response, "You have already applied to this position.")
    result = engine.refused_as_already_applied()
    assert result["state"] == "already_applied" and "already applied" in result["receipt"]["evidence"]
    assert engine.form.refusal_notes == ["You have already applied to this position."]
    other = object.__new__(BrowserEngine)
    other.refusal_said = ["Please enter your first name using ALL CAPITAL LETTERS (Example: JOHN)."]
    assert other.refused_as_already_applied() is None
