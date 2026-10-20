"""Employer-hosted Greenhouse thank-you pages count as receipts only without fields or submit controls."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession, employer_confirmation_text
from jobpilot.schemas import Profile

JOB = {"id": "job-1", "ats": "greenhouse", "url": "https://careers.acme.test/jobs/123"}


def form_for(page, service, tmp_path, job=JOB):
    resolver = Resolver(Profile(), service.db, service.config, run_id="run-1", job=job)
    return FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a: None)


async def serve(page, url, body):
    async def route(request):
        await request.fulfill(status=200, content_type="text/html", body=body)

    await page.context.route("**/*", route)
    await page.goto(url)


@pytest.mark.parametrize(
    "text",
    [
        "Thank you for submitting an application to Acme",
        "Task complete! Thank you for sharing your information with us at Acme",
        "Your application has been received",
    ],
)
def test_pure_rule_accepts_employer_thank_you_text(text):
    assert employer_confirmation_text(text)
    assert employer_confirmation_text(text) in text


@pytest.mark.parametrize(
    "text",
    [
        "Phone number is too short. Thank you for submitting an application to Acme",
        "Thank you for submitting an application to Acme. Something could not be saved, try again",
        "Apply for this job. Thank you for submitting an application to Acme",
        "Application form. Your application has been received",
        "Thank you for applying",
        "",
    ],
)
def test_pure_rule_rejects_errors_forms_and_unrelated_text(text):
    assert employer_confirmation_text(text) is None


@pytest.mark.parametrize(
    "page_url",
    [
        "https://careers.acme.test/jobs/123/thanks",
        "https://job-boards.greenhouse.io/acme/jobs/123/confirmation",
    ],
)
@pytest.mark.parametrize(
    "body",
    [
        "<h1>Thank you for submitting an application to Acme</h1>",
        "<h1>Task complete!</h1><p>Thank you for sharing your information with us at Acme</p>",
        "<h1>Your application has been received</h1>",
    ],
)
async def test_static_thank_you_page_returns_receipt(page, service, tmp_path, page_url, body):
    await serve(page, page_url, f"<html><head><title>Thanks</title></head><body>{body}</body></html>")
    receipt = await form_for(page, service, tmp_path).proof()
    assert receipt is not None
    assert receipt["type"] == "explicit_confirmation_page"
    assert receipt["evidence_selector"] == "employer_greenhouse_thank_you"
    assert receipt["status_region"] == "job_application_status"
    assert receipt["url"] == page_url
    assert receipt["job_id"] == "job-1"
    assert len(receipt["confirmation"]) <= 300


async def test_thank_you_text_with_visible_input_is_not_a_receipt(page, service, tmp_path):
    body = '<h1>Thank you for submitting an application to Acme</h1><label>Phone <input type="text"></label>'
    await serve(page, "https://careers.acme.test/jobs/123/thanks", f"<body>{body}</body>")
    assert await form_for(page, service, tmp_path).proof() is None


async def test_validation_error_form_with_thank_you_footer_is_not_a_receipt(page, service, tmp_path):
    body = (
        "<h1>Apply for this job</h1>"
        "<p>Phone number is too short</p>"
        "<p>Thank you for applying</p>"
        '<label>Phone <input type="tel" value="5550100"></label>'
    )
    await serve(page, "https://careers.acme.test/jobs/123/thanks", f"<body>{body}</body>")
    assert await form_for(page, service, tmp_path).proof() is None


async def test_apply_page_with_submit_button_is_not_a_receipt(page, service, tmp_path):
    body = (
        "<h1>Apply for this job</h1>"
        "<p>Thank you for submitting an application to Acme</p>"
        "<button>Submit</button>"
    )
    await serve(page, "https://careers.acme.test/jobs/123/thanks", f"<body>{body}</body>")
    assert await form_for(page, service, tmp_path).proof() is None


async def test_unrelated_host_with_thank_you_text_is_not_a_receipt(page, service, tmp_path):
    body = "<h1>Thank you for submitting an application to Acme</h1>"
    await serve(page, "https://evil.example/thanks", f"<body>{body}</body>")
    assert await form_for(page, service, tmp_path).proof() is None


async def test_non_greenhouse_job_with_thank_you_text_is_not_a_receipt(page, service, tmp_path):
    body = "<h1>Thank you for submitting an application to Acme</h1>"
    job = {**JOB, "ats": "workday"}
    await serve(page, "https://careers.acme.test/jobs/123/thanks", f"<body>{body}</body>")
    assert await form_for(page, service, tmp_path, job=job).proof() is None


EMBED_JOB = {"id": "job-1", "ats": "greenhouse", "url": "https://job-boards.greenhouse.io/asana/jobs/7962412"}
ASANA_THANKS = (
    "<div class=confirmation><h1>Task complete!</h1><p>Thank you for sharing your information with us at Asana. "
    "We're excited to add more mission-driven and passionate people to our global team. Our Talent team will review "
    "your experience, and if there is a fit for the role you applied for, or any open role, we will be in touch soon."
    "</p><a class='btn btn--rounded' href='https://job-boards.greenhouse.io/asana'>View more jobs at Asana</a>"
    "<a class=link href='/asana/jobs/7962412'>Back to job post</a></div>"
)


async def test_embedded_greenhouse_confirmation_is_a_receipt(page, service, tmp_path):
    # The embed form assigns location to its loader's confirmationPath after a successful submit.
    url = "https://job-boards.greenhouse.io/embed/job_app/confirmation?for=asana&token=7962412"
    await serve(page, url, f"<html><head><title>Thank you</title></head><body>{ASANA_THANKS}</body></html>")
    receipt = await form_for(page, service, tmp_path, job=EMBED_JOB).proof()
    assert receipt and receipt["evidence_selector"] == "employer_greenhouse_thank_you"
    assert receipt["confirmation"].startswith("Task complete!")


@pytest.mark.parametrize(
    "url",
    [
        "https://job-boards.greenhouse.io/embed/job_app/confirmation?for=otherco&token=7962412",
        "https://job-boards.greenhouse.io/embed/job_app/confirmation?for=asana&token=1111111",
        "https://job-boards.greenhouse.io/embed/job_app/confirmation",
    ],
)
async def test_embedded_confirmation_for_another_board_or_post_is_not_a_receipt(page, service, tmp_path, url):
    await serve(page, url, f"<body>{ASANA_THANKS}</body>")
    assert await form_for(page, service, tmp_path, job=EMBED_JOB).proof() is None



async def test_pinterest_your_application_is_in_is_a_receipt(page, service, tmp_path):
    job = {"id": "job-1", "ats": "greenhouse", "url": "https://job-boards.greenhouse.io/pinterest/jobs/7011111"}
    url = "https://job-boards.greenhouse.io/embed/job_app/confirmation?for=pinterest&token=7011111"
    body = ("<div class=confirmation><p>Good news: your application is in! Thank you for your interest in a career at "
            "Pinterest.</p><a class=btn href='/pinterest'>View more jobs at Pinterest</a>"
            "<a href='/pinterest/jobs/7011111'>Back to job post</a></div>")
    await serve(page, url, f"<html><head><title>Thank you</title></head><body>{body}</body></html>")
    receipt = await form_for(page, service, tmp_path, job=job).proof()
    assert receipt and "your application is in" in receipt["confirmation"]


async def test_greenhouse_embed_confirmation_message_is_a_receipt(page, service, tmp_path):
    # Toast 2026-10-17: the employer page closed the Greenhouse iframe after its "greenhouse.confirmation" message.
    from jobpilot.browser import GREENHOUSE_RECEIPT

    job = {"id": "job-1", "ats": "greenhouse", "url": "https://careers.toasttab.test/jobs/123"}

    async def route(request):
        if "greenhouse.io" in request.request.url:
            await request.fulfill(status=200, content_type="text/html",
                                  body="<script>window.parent.postMessage('greenhouse.confirmation', '*')</script>")
        else:
            await request.fulfill(status=200, content_type="text/html",
                                  body="<h1>Software Engineer</h1><button>Apply now</button>"
                                       "<iframe src='https://job-boards.greenhouse.io/embed/job_app/confirmation'></iframe>")

    await page.add_init_script(GREENHOUSE_RECEIPT)
    await page.context.route("**/*", route)
    await page.goto("https://careers.toasttab.test/jobs/123")
    await page.wait_for_function("window.__jpGreenhouseConfirmation > 0")
    receipt = await form_for(page, service, tmp_path, job=job).proof()
    assert receipt and receipt["evidence_selector"] == "greenhouse_embed_confirmation_message"


async def test_the_message_from_another_origin_is_ignored(page, service, tmp_path):
    from jobpilot.browser import GREENHOUSE_RECEIPT

    job = {"id": "job-1", "ats": "greenhouse", "url": "https://careers.toasttab.test/jobs/123"}
    await page.add_init_script(GREENHOUSE_RECEIPT)
    await serve(page, "https://careers.toasttab.test/jobs/123",
                "<h1>Software Engineer</h1><script>window.postMessage('greenhouse.confirmation', '*')</script>")
    await page.wait_for_timeout(200)
    assert await form_for(page, service, tmp_path, job=job).proof() is None


async def test_workday_completion_route_is_read_after_its_message_renders(page, service, tmp_path):
    # Medtronic and CACI (2026-10-18) reached /jobTasks/completed/application while it showed only its header and
    # footer; the completion message rendered a few seconds later and the runs ended as unknown.
    job = {"id": "job-1", "ats": "workday",
           "url": "https://medtronic.wd1.myworkdayjobs.com/en-US/MedtronicCareers/job/Remote/Software-Engineer_R1"}
    await serve(page, "https://medtronic.wd1.myworkdayjobs.com/en-US/MedtronicCareers/jobTasks/completed/application",
                """<html><body><header>Careers Home</header><main id=m></main><script>
                setTimeout(() => document.getElementById('m').innerHTML =
                  '<h2>Application Submitted</h2><p>Your application was successfully submitted.</p>', 2500);
                </script></body></html>""")
    receipt = await form_for(page, service, tmp_path, job=job).proof()
    assert receipt and receipt["evidence_selector"] == "workday_completed_application_route"
