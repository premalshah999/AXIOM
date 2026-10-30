"""ADP, 2026-10-29: a navigator `choose` refused with "Choice is stale or outside this control" was retried on 7 of 30
decisions on one field (245 production runs hit it). The model alternated choose with inspect_options and clicks, so the
repeated-action refusal (reset by every other action) never fired. The refusals are counted per field."""

from types import SimpleNamespace

from jobpilot.browser import STALE_CHOICE_LIMIT, BrowserEngine
from jobpilot.forms import STALE_CHOICE

FIELD = {"id": "f1", "label": "Social Media Source", "type": "combobox", "section": "", "group": "", "slot": 0}
STALE = {"failure_category": "unavailable", "detail": f"unclassified: {STALE_CHOICE}"}


def verdict(target, feedback, rejected):
    return BrowserEngine.answer_verdict(SimpleNamespace(), SimpleNamespace(action="choose"), target, feedback, rejected)


def test_the_third_stale_choose_on_a_field_ends_the_recovery_naming_the_field():
    rejected = {}
    for _ in range(STALE_CHOICE_LIMIT - 1):
        assert verdict(FIELD, STALE, rejected) is None
    stopped = verdict(FIELD, STALE, rejected)
    assert stopped["state"] == "waiting_browser" and stopped["blocker_type"] == "control_failure"
    assert stopped["recovery_cause"] == "control_observation"
    assert "Social Media Source" in stopped["reason"] and "stale" in stopped["reason"]


def test_each_field_has_its_own_count_and_other_refusals_do_not_count():
    rejected = {}
    other = {**FIELD, "id": "f2", "label": "Candidate Source"}
    for _ in range(STALE_CHOICE_LIMIT - 1):
        assert verdict(FIELD, STALE, rejected) is None
        assert verdict(other, STALE, rejected) is None
    assert verdict(FIELD, {"failure_category": "stale", "detail": ""}, rejected) is None
    assert verdict(FIELD, {"failure_category": "unavailable", "detail": "unclassified: covered"}, rejected) is None
    assert verdict(other, STALE, rejected)["state"] == "waiting_browser"
