"""A restricted source catalog must not turn a saved policy into a missing fact."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.browser import BrowserEngine
from jobpilot.forms import FormSession
from jobpilot.schemas import Answer, Profile


@pytest.mark.parametrize(
    "blocker,cause",
    [
        ("control_failure", "control_observation"),
        ("validation", "control_observation"),
        ("model_output", "model_output"),
    ],
)
def test_stopped_controller_retains_specialist_recovery_cause(service, blocker, cause):
    from types import SimpleNamespace

    engine = BrowserEngine(service, "synthetic")
    engine.form = SimpleNamespace(pending=[], failed_strategies={})
    result = engine.controller_blocker("Stopped", original={"blocker_type": blocker})
    assert result["recovery_cause"] == cause
    assert result["state"] == "waiting_browser"


def field(options, *, partial=False):
    return {
        "id": "source",
        "label": "How Did You Hear About Us?",
        "type": "dropdown",
        "required": True,
        "options": options,
        "options_partial": partial,
        "value": "",
        "valid": True,
    }


@pytest.mark.parametrize("options", [["Displaced (US)", "LOA - Job Search (US)"], ["Employee Referral"]])
def test_known_source_with_unrelated_options_needs_control_recovery(service, options):
    resolver = Resolver(Profile(application_source="LinkedIn"), service.db, service.config)
    answer = resolver.local(field(options))
    assert answer.disposition == "review"
    assert answer.blocker_type == "control_failure"
    assert answer.retryable
    assert answer.supporting_facts == ["policy:source"]
    assert not answer.value
    # The same saved fact can drive a search; it never authorizes a different source.
    search = resolver.local(field(options, partial=True))
    assert search.value == "LinkedIn" and search.disposition == "answer"


def test_offered_supported_source_still_commits_without_model_interpretation(service):
    resolver = Resolver(Profile(application_source="LinkedIn"), service.db, service.config)
    answer = resolver.local(field(["LinkedIn", "Employee Referral"]))
    assert answer.disposition == "answer" and answer.value == "LinkedIn"
    assert answer.evidence_ids == ["policy:source"]


def test_absent_source_uses_profile_source_list(service):
    # A profile's own source order (answer_rules.source_order): a listed channel is a true answer.
    rules = {"source_order": ["linkedin", "company website"], "source_any_channel": True}
    resolver = Resolver(Profile(answer_rules=rules), service.db, service.config)
    assert resolver.local(field(["LinkedIn", "Employee Referral"])).value == "LinkedIn"
    assert resolver.local(field(["Employee Referral", "Agency"])) is None


async def test_navigator_help_preserves_known_source_catalog_blocker(page, service, tmp_path):
    resolver = Resolver(Profile(application_source="LinkedIn"), service.db, service.config)
    form = FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a: None)
    source = field(["Displaced (US)", "LOA - Job Search (US)"])
    form.fields = {source["id"]: source}
    engine = BrowserEngine(service, "synthetic-run")
    engine.form = form
    engine.record_help_reviews(
        [
            Answer(
                field_id=source["id"], disposition="review", reason="No truthful offered source is available"
            )
        ]
    )
    result = engine.controller_blocker("Restricted source catalog")
    assert result["state"] == "waiting_browser"
    assert result["blocker_type"] == "control_failure"
    assert result["recovery_cause"] == "control_observation"
    assert result["report"]["pending"][0]["supporting_facts"] == ["policy:source"]
