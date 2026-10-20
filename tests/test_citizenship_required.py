"""Guidehouse 2026-10-14: a role that requires US citizenship is closed for a non-citizen, not retried."""

from types import SimpleNamespace

from jobpilot.browser import BrowserEngine
from jobpilot.schemas import Profile

LABEL = (
    "This position requires US Citizenship due to contractual obligations. Are you a United States Citizen?"
)


def engine(citizen, value):
    e = object.__new__(BrowserEngine)
    profile = Profile(facts={"us_citizen": citizen})
    e.form = SimpleNamespace(
        resolver=SimpleNamespace(profile=profile), fields={"f": {"label": LABEL, "value": value}}
    )
    return e


def test_required_citizenship_closes_for_a_non_citizen():
    assert engine(False, "No").citizenship_required_result()["state"] == "closed"


def test_citizens_and_plain_questions_continue():
    assert engine(True, "Yes").citizenship_required_result() is None
    e = engine(False, "No")
    e.form.fields = {"f": {"label": "Are you a US citizen?", "value": "No"}}
    assert e.citizenship_required_result() is None
