"""ASSA ABLOY (SuccessFactors 2026-10-27): 34 runs stalled on the read-only view of the submitted application
(My Applications -> the job, subNavBarLevel=JOB_APPLIED&fbja_action=fbja_viewApp), which offers only Close."""

from types import SimpleNamespace

from jobpilot.browser import BrowserEngine

VIEW = ("https://career2.successfactors.eu/portalcareer?career_ns=job_application&navBarLevel=JOB_MGMT"
        "&subNavBarLevel=JOB_APPLIED&fbja_action=fbja_viewApp&company=acme&career_job_req_id=51362")


class Body:
    def __init__(self, text):
        self.text = text

    async def inner_text(self, timeout=None):
        return self.text


def engine(url, text, title="AI Scientist"):
    value = BrowserEngine.__new__(BrowserEngine)
    value.job = {"title": title}
    value.page = SimpleNamespace(url=url, locator=lambda selector: Body(text))
    return value


async def test_the_applied_view_of_this_job_is_already_applied():
    text = "AI Scientist - Computer Vision (51362) View Job Description Withdraw Application 7. Review Close"
    assert (await engine(VIEW, text).portal_lists_application())["state"] == "already_applied"


async def test_another_job_or_a_draft_is_not():
    assert await engine(VIEW, "Data Analyst (1) Withdraw Application").portal_lists_application() is None
    assert await engine(VIEW, "AI Scientist Continue Application").portal_lists_application() is None
    assert await engine(VIEW.replace("JOB_APPLIED", "JOB_DRAFT"), "AI Scientist Withdraw Application"
                        ).portal_lists_application() is None
