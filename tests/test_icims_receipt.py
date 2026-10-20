"""iCIMS shows its receipt in the content frame on the job's own page (ICE 2026-10-19): the engine read only the outer
page, recorded submission_unknown, and the application was in fact submitted."""

from jobpilot.forms import icims_receipt

JOB = "https://careers-ice.icims.com/jobs/13825/java-developer/job?in_iframe=1"
SUBMITTED = "Java Developer\nYour application was submitted successfully. Thank you for applying.\nYou are currently submitted to this job."


def test_the_jobs_own_frame_carries_the_receipt():
    frame = "https://careers-ice.icims.com/jobs/13825/java-developer/job?mode=submit_apply&in_iframe=1"
    assert icims_receipt(JOB, frame, SUBMITTED) == "Your application was submitted successfully. Thank you for applying."
    assert icims_receipt(JOB, frame, "You are currently submitted to this job.") == "You are currently submitted to this job"


def test_another_job_or_host_never_counts():
    assert icims_receipt(JOB, "https://careers-ice.icims.com/jobs/13899/full-stack-developer/job", SUBMITTED) is None
    assert icims_receipt(JOB, "https://careers-other.icims.com/jobs/13825/java-developer/job", SUBMITTED) is None
    assert icims_receipt(JOB, "https://careers-ice.icims.com/jobs/13825/java-developer/job", "Apply for this job online") is None


async def test_an_optional_assessment_last_step_is_confirmed_from_the_dashboard(service, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from jobpilot.adapters.base import Adapter
    from support_icims import adapter

    NBSP = " "
    bar = "\n".join(
        f"Step {NBSP} {n} {NBSP} of {NBSP} 5. {NBSP} " + (name + "(Current Step)" if n == 5 else f"Step {n}(Completed Step)")
        for n, name in [(1, ""), (2, ""), (3, ""), (4, ""), (5, "Assessments")]
    )
    obs = {"url": "https://careers-acme.icims.com/jobs/20717/junior-data-analyst/assessment", "fields": [], "controls": [],
           "errors": [], "headings": [], "text": bar + "\nClick Here to Proceed to the Required Screening Questions"}
    value = adapter(service, obs)
    value.job["url"] = "https://careers-acme.icims.com/jobs/20717/job"
    dashboard = "Past Job Submittals\n2026-20717\tJunior Data Analyst\tReceived Submission\t10/19/2026\tContinue Application"
    frame = SimpleNamespace(url="https://careers-acme.icims.com/jobs/dashboard?in_iframe=1",
                            locator=lambda sel: SimpleNamespace(inner_text=AsyncMock(return_value=dashboard)))
    value.e.form.page.frames = [frame]
    value.e.form.page.goto = AsyncMock()
    value.e.form.page.title = AsyncMock(return_value="Dashboard")
    value.form.settle = AsyncMock()
    value.form.evidence_context = lambda: {"job_id": "j", "run_id": "r"}
    monkeypatch.setattr(Adapter, "on_unknown", AsyncMock(return_value={"fallback": True}))
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    await value.on_unknown(obs)
    value.e.form.page.goto.assert_not_awaited()  # this run walked no step: never claim a dashboard row
    value.step_submits = 2
    result = await value.on_unknown(obs)
    assert result["state"] == "confirmed" and "Received Submission" in result["receipt"]["confirmation"]
    assert result["receipt"]["type"] == "explicit_confirmation_page" and result["receipt"]["run_id"] == "r"


async def test_the_candidate_dashboard_continues_or_confirms_never_reads_as_a_receipt(service):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from jobpilot.adapters.base import Step
    from support_icims import adapter

    obs = {"url": "https://eb-acme.icims.com/jobs/dashboard", "fields": [], "controls": [], "errors": [],
           "headings": ["Welcome to your Dashboard", "Past Job Submittals"], "text": "Welcome to your Dashboard Past Job Submittals"}

    def dashboard(row):
        value = adapter(service, obs)
        value.job["url"] = "https://eb-acme.icims.com/jobs/9917/ai-engineer/job"
        text = "Past Job Submittals\n" + row + "\tContinue Application\tWithdraw"
        value.e.form.page.frames = [SimpleNamespace(url="https://eb-acme.icims.com/jobs/dashboard?in_iframe=1",
                                                    locator=lambda sel: SimpleNamespace(inner_text=AsyncMock(return_value=text)))]
        value.e.form.page.title = AsyncMock(return_value="Dashboard")
        value.e.return_to_job = AsyncMock()
        value.form.evidence_context = lambda: {"job_id": "j", "run_id": "r"}
        value.committed = False
        return value

    incomplete = dashboard("2026-9917\tAI Engineer\tIncomplete\t10/19/2026")
    assert incomplete.classify(obs) == Step.UNKNOWN
    assert await incomplete.on_unknown(obs) is None
    incomplete.e.return_to_job.assert_awaited_once()
    earlier = dashboard("2026-9917\tAI Engineer\tReceived Submission\t10/19/2026")
    assert (await earlier.on_unknown(obs))["state"] == "already_applied"  # this run walked no step
    walked = dashboard("2026-9917\tAI Engineer\tReceived Submission\t10/19/2026")
    walked.step_submits = 3
    result = await walked.on_unknown(obs)
    assert result["state"] == "confirmed" and result["receipt"]["evidence_selector"] == "icims_candidate_dashboard_row"
