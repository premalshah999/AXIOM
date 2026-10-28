"""Worker settings from the web app (owner 2026-10-28): normal workers 4, 6 or 8; difficult mode 1 or 2; one worker for
custom career sites that can be turned off. Custom-site jobs never take an ATS worker's slot."""

from types import SimpleNamespace

import pytest

from jobpilot.config import Topology
from jobpilot.connections import save, status
from jobpilot.pipeline_comparison import CUSTOM, NORMAL, SPECIALIST, assign_pool


def test_slots_are_numbered_normal_then_difficult_then_custom():
    t = Topology(4, 1, 1)
    assert (t.slot_ids(NORMAL), t.slot_ids(SPECIALIST), t.slot_ids(CUSTOM), t.total) == ([1, 2, 3, 4], [5], [6], 6)
    assert Topology(4, 2, 0).slot_ids(CUSTOM) == []


def test_a_custom_site_job_runs_only_on_the_custom_worker():
    escalated = {"packet": {"specialist_escalation": {"eligible": True}}}
    assert assign_pool(None, SimpleNamespace(ats="custom"), escalated) == CUSTOM
    assert assign_pool(None, SimpleNamespace(ats=""), None) == CUSTOM
    assert assign_pool(None, SimpleNamespace(ats="workday"), None) == NORMAL
    assert assign_pool(None, SimpleNamespace(ats="workday"), escalated) == SPECIALIST


def test_the_web_app_offers_fixed_worker_choices(service):
    with pytest.raises(ValueError, match="choose 4 or 6 or 8"):
        save(service.config, {"normal_workers": "5"})
    with pytest.raises(ValueError, match="choose 1 or 2"):
        save(service.config, {"specialist_workers": "3"})


def test_worker_counts_wait_for_the_next_start(service):
    running = service.config.normal_workers
    config = save(service.config, {"normal_workers": "8" if running != 8 else "6", "custom_ats_worker": "false"})
    assert config.normal_workers == running  # The worker queues were sized at startup.
    fields = {f["name"]: f for f in status(config)["fields"]}
    assert fields["normal_workers"]["pending"] in {"8", "6"} and fields["normal_workers"]["options"] == ["4", "6", "8"]
    assert fields["custom_ats_worker"]["pending"] == "false" and fields["custom_ats_worker"]["restart"]
