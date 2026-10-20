"""Bank of America (Workday) 2026-10-19: "How Did You Hear About Us?" offered only "Displaced (US)" and "LOA - Job Search
(US)", internal channels, but the navigator's target carried no options, so the run asked the owner three times. The
form's own observed catalog decides: an internal-only posting closes."""

from types import SimpleNamespace

from jobpilot.answers import UNOFFERED_SOURCE
from jobpilot.browser import BrowserEngine


def engine(form_options):
    e = object.__new__(BrowserEngine)
    e.form = SimpleNamespace(fields={"f1": {"id": "f1", "label": "How Did You Hear About Us?", "options": form_options}},
                             pending=[])
    e.service = SimpleNamespace(review_kind=lambda q: "missing_fact")
    return e


def verdict(e):
    target = {"id": "f1", "label": "How Did You Hear About Us?", "type": "dropdown"}
    feedback = {"failure_category": "unsupported", "detail": "Answer validation rejected: " + UNOFFERED_SOURCE}
    return e.answer_verdict(SimpleNamespace(action="choose"), target, feedback, {})


def test_an_internal_only_catalog_closes_the_job_even_when_the_target_lacks_options():
    result = verdict(engine(["Select One", "Displaced (US)", "LOA - Job Search (US)"]))
    assert result["state"] == "closed" and "Internal-only" in result["reason"]


def test_a_public_catalog_without_the_saved_source_still_asks():
    result = verdict(engine(["Select One", "Indeed", "Company Website"]))
    assert result["state"] != "closed"
