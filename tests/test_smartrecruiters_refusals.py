"""SmartRecruiters' refusal dialogs (REA 2026-10-18, oneclick-ui showApplicationErrorMessage + its own en strings).

Six SmartRecruiters runs ended "submission_unknown": the page showed "Cannot submit your application ...", a phrase
none of the refusal rules matched. Each dialog now maps to what happened (nothing was accepted in all of them).
"""

from types import SimpleNamespace

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

DIALOG = """<oc-app-root><spl-wrapper id=host></spl-wrapper></oc-app-root>
<script>
  const root = document.getElementById('host').attachShadow({mode: 'open'});
  root.innerHTML = `<div role="dialog"><p>{message}</p><ul>{items}</ul><button>OK</button></div>`;
</script>"""


async def rejection(page, tmp_path, message, items=""):
    await page.set_content(DIALOG.replace("{message}", message).replace("{items}", items))
    resolver = Resolver(Profile(), None, None, job={"ats": "smartrecruiters", "url": "https://jobs.smartrecruiters.com/Acme/1"})
    return await FormSession(page, resolver, tmp_path / "r.pdf", lambda *a: None).rejection()


async def test_generic_submission_error_is_temporary(page, tmp_path):
    r = await rejection(page, tmp_path, "Cannot submit your application. Please try again in a while.")
    assert r and r["category"] == "temporary"


async def test_validation_issues_list_the_refused_sections(page, tmp_path):
    r = await rejection(
        page, tmp_path,
        "Cannot submit your application due to validation issues. Please review and update parts of your "
        "application listed below, then try once again.",
        "<li>Date ranges in education section</li><li>Please provide your first name</li>",
    )
    assert r and r["category"] == "validation"
    assert "Date ranges in education section" in r["missing_fields"]


async def test_answers_validation_is_a_validation_refusal(page, tmp_path):
    r = await rejection(page, tmp_path, "Problem saving answers to preliminary question. Please verify your answers "
                                        "and try again. If problem persists please wait 5 minutes and try to start application")
    assert r and r["category"] == "validation"


async def test_expired_posting_is_closed(page, tmp_path):
    r = await rejection(page, tmp_path, "This job ad has expired")
    assert r and r["category"] == "closed"


def test_commit_write_requests_are_recorded_without_query_or_noise():
    from jobpilot.browser import BrowserEngine

    engine = object.__new__(BrowserEngine)
    engine.armed = True
    engine.job = {"url": "https://jobs.smartrecruiters.com/Acme/1"}
    engine.emit = lambda *a, **k: None

    def response(url, method="POST", status=200, kind="xhr"):
        return SimpleNamespace(url=url, status=status, request=SimpleNamespace(method=method, resource_type=kind))

    engine.submission_response(response("https://jobs.smartrecruiters.com/oneclick-ui/api/company/acme/publication/p1/application?x=secret", status=403))
    engine.submission_response(response("https://rum.smartrecruiters.com/sink/"))
    engine.submission_response(response("https://jobs.smartrecruiters.com/oneclick-ui/i18n/en", method="GET"))
    assert engine.commit_requests == [{"method": "POST", "host": "jobs.smartrecruiters.com",
                                       "path": "/oneclick-ui/api/company/acme/publication/p1/application", "status": 403}]


async def test_unanswered_required_preliminary_question_is_a_validation_refusal(page, tmp_path):
    r = await rejection(page, tmp_path, "Please make sure that you answered all questions marked as required.")
    assert r and r["category"] == "validation"
