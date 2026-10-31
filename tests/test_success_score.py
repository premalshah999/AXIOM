"""GET /api/success: the 90% score the owner watches (2026-10-31), on synthetic rows.

The definitions are those of the push's score query: tried, closed, already applied, owner only, eligible, final and first-try.
Each case below is one kind of job, so a changed definition names the kind it broke.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert

from jobpilot.api.success import success
from jobpilot.db import Job, Run

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def at(days_ago, minutes=0):
    return (NOW - timedelta(days=days_ago) + timedelta(minutes=minutes)).isoformat()


def seed(service, jobs):
    """jobs: {name: (ats, [(state, reason, days_ago), ...], demo)}. Returns nothing; ids are the names."""
    with service.db.session() as s:
        s.execute(
            insert(Job),
            [
                dict(id=name, identity=f"example.test/{name}", url=f"https://boards.example.test/{name}", title="Engineer",
                     company="Example Co", location="Remote", ats=ats, status="ready", demo=demo, created_at=at(30))
                for name, (ats, _, demo) in jobs.items()
            ],
        )
        s.execute(
            insert(Run),
            [
                dict(id=f"{name}-{n}", job_id=name, state=state, mode="submit", reason=reason, packet={}, receipt={}, created_at=at(days))
                for name, (_, runs, _) in jobs.items()
                for n, (state, reason, days) in enumerate(runs)
            ],
        )


def row(report, ats):
    return next(p for p in report["platforms"] if p["ats"] == ats)


@pytest.fixture
def report(service):
    gh = "greenhouse"
    seed(service, {
        "first": (gh, [("confirmed", "", 1)], False),                                   # confirmed on the first run
        "second": (gh, [("failed", "", 3), ("confirmed", "", 1)], False),               # confirmed on a later run
        "lost": (gh, [("failed", "", 3), ("waiting_browser", "stuck", 1)], False),      # an engine failure
        "closed": (gh, [("closed", "The posting is closed or no longer available", 2)], False),
        "deleted": (gh, [("cancelled", "Owner deleted this application", 2)], False),
        "twice": (gh, [("already_applied", "The employer says you already applied", 2)], False),
        "refs": (gh, [("waiting_answer", "Needs a supervisor reference", 1)], False),   # only the owner can answer
        "refs_ok": (gh, [("waiting_answer", "Needs a supervisor reference", 3), ("confirmed", "", 1)], False),
        "dry": (gh, [("dry_run_passed", "", 1)], False),                                # never tried
        "old": (gh, [("confirmed", "", 12)], False),                                    # outside the window
        "demo": (gh, [("confirmed", "", 1)], True),                                     # a demo job is not an application
        "confirmed_then_closed": (gh, [("confirmed", "", 3), ("closed", "gone", 1)], False),
        "lv": ("lever", [("confirmed", "", 1)], False),
    })
    with service.db.session() as s:
        return success(s, 7, now=NOW)


def test_each_kind_of_job_lands_in_its_bucket(report):
    gh = row(report, "greenhouse")
    assert gh["tried"] == 9  # not the dry run, not the old run, not the demo
    assert gh["closed"] == 2 and gh["already_applied"] == 1 and gh["owner_only"] == 1
    assert gh["confirmed"] == 4  # first, second, refs_ok, confirmed_then_closed
    # eligible: tried 9 - closed or cancelled 3 (a confirmed one stays in) - already applied 1 - owner only 1 = 4, plus the
    # job that was confirmed before it closed.
    assert gh["eligible"] == 5


def test_final_and_first_try_use_the_same_denominator(report):
    gh = row(report, "greenhouse")
    # counted = tried 9 - closed 3 - already applied 1 - owner only 1 = 4: first, second, lost, refs_ok
    # (confirmed_then_closed is out of the denominator but its confirmation stays in the numerator, as in the push's query)
    assert gh["final_pct"] == 100.0  # 4 confirmed of 4 counted
    # Confirmed by the first run: `first` and `confirmed_then_closed` (the push's query counts the latter in the numerator too).
    assert gh["first_try_pct"] == 50.0


def test_overall_adds_the_platforms_and_flags_small_ones(report):
    assert report["overall"]["tried"] == 10 and report["overall"]["confirmed"] == 5
    assert row(report, "greenhouse")["volume"] is True and row(report, "lever")["volume"] is False
    assert report["target"] == 90 and report["days"] == 7


def test_a_failure_pulls_the_number_down_and_never_rounds_up_to_the_target(service):
    # 9 of 10 confirmed is 90.0, which meets 90; 89 of 100 is 89.0 and does not, but 897 of 1000 is 89.7 and must not read as 90.
    seed(service, {f"j{n}": ("workday", [("confirmed" if n < 897 else "failed", "", 1)], False) for n in range(1000)})
    with service.db.session() as s:
        wd = row(success(s, 7, now=NOW), "workday")
    assert wd["final_pct"] == 89.7 and wd["meets_target"] is False


def test_the_window_decides_what_counts(service):
    seed(service, {"a": ("ashby", [("confirmed", "", 1)], False), "b": ("ashby", [("confirmed", "", 20)], False)})
    with service.db.session() as s:
        assert success(s, 7, now=NOW)["overall"]["tried"] == 1
        assert success(s, 30, now=NOW)["overall"]["tried"] == 2


def test_platforms_below_target_come_first_then_the_rest(service):
    seed(service, {
        **{f"a{n}": ("ashby", [("confirmed", "", 1)], False) for n in range(8)},
        **{f"c{n}": ("custom", [("confirmed" if n < 5 else "failed", "", 1)], False) for n in range(8)},
        **{f"w{n}": ("workday", [("confirmed" if n < 7 else "failed", "", 1)], False) for n in range(8)},
        "tiny": ("rippling", [("failed", "", 1)], False),
    })
    with service.db.session() as s:
        order = [p["ats"] for p in success(s, 7, now=NOW)["platforms"]]
    assert order == ["custom", "workday", "ashby", "rippling"]  # worst with volume, then ones meeting it, then small ones


def test_an_empty_window_is_all_zeros_and_no_percentage(service):
    with service.db.session() as s:
        report = success(s, 7, now=NOW)
    assert report["platforms"] == [] and report["overall"]["tried"] == 0 and report["overall"]["final_pct"] is None


def test_the_route_is_read_only_and_bounds_the_window(client):
    ok = client.get("/api/success")
    assert ok.status_code == 200 and ok.json()["days"] == 7 and set(ok.json()) == {"days", "target", "overall", "platforms"}
    assert client.get("/api/success", params={"days": 0}).status_code == 422
    assert client.get("/api/success", params={"days": 91}).status_code == 422
    assert client.post("/api/success", json={}).status_code == 405
