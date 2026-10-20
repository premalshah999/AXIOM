"""Workday discovery (2026-10-15): a tenant row without a title is skipped, not fatal for the feed.

GE Vernova, ICF, M&T Bank and others returned such rows, and `KeyError: 'title'` failed every posting
those employers listed."""

import json
from datetime import UTC, datetime

from jobpilot import jobboard
from jobpilot.schemas import Profile

LIST = {
    "jobPostings": [
        {"externalPath": "/job/untitled_R1", "postedOn": "Posted Today"},
        {"title": "Machine Learning Engineer I", "externalPath": "/job/ml_R2", "postedOn": "Posted Today"},
    ]
}
DETAIL = {
    "jobPostingInfo": {
        "location": "Austin, TX",
        "country": "United States",
        "jobDescription": "<p>Python</p>",
    }
}


async def test_untitled_workday_row_is_skipped(monkeypatch):
    async def fetch(url, **kwargs):
        return json.dumps(DETAIL if "/job/" in url and "/jobs" not in url else LIST).encode(), None

    monkeypatch.setattr(jobboard, "fetch", fetch)
    source = {"url": "https://tenant.wd5.myworkdayjobs.com/External", "kind": "workday", "name": "Tenant"}
    profile = Profile(target_roles=["Machine Learning Engineer"], skills=["Python"])
    posts, seen, _complete, errors = await jobboard.read_feed(source, profile, None, datetime.now(UTC))
    assert [p.title for p in posts] == ["Machine Learning Engineer I"]
    assert errors >= 1  # the untitled row is counted, so the feed is not treated as complete
