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
