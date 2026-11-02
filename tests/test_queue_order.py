"""Queue (owner, 2026-11-01: "don't know where my queue is"): the order shown is the order the dispatcher starts. Synthetic data only."""

from datetime import UTC, datetime, timedelta

import pytest

from jobpilot import autopilot, owner_apply, queue_order, startable
from jobpilot.application_pacing import LIMIT_KEY
from jobpilot.db import Job, JobListing, Run, Setting
from jobpilot.schemas import Profile

pytestmark = pytest.mark.integration


def setup(service):
    service.db.set_setting("profile", Profile(target_roles=["Software Engineer"]).model_dump())
    service.db.set_setting("job_board_settings", {"enabled": True, "auto_apply": True, "minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": False})
    service.db.set_setting(LIMIT_KEY, {"limit": 25})
    service.db.set_setting("control", {"paused": False, "auto_submit": True})


def ready(service, jid, company, score=80, level="New grad", ats="greenhouse", url=None, title="Software Engineer", location="Austin, TX", age=0):
    seen = (datetime.now(UTC) - timedelta(hours=age)).isoformat()
    now = datetime.now(UTC).isoformat()
    slug = company.replace(" ", "-").lower()
    with service.db.exclusive() as s:
        s.add(Job(id=jid, identity=jid, url=url or f"https://boards.example.test/{slug}/jobs/{jid}", ats=ats, company=company, title=title,
                  status="ready", score=score, demo=False, location=location, created_at=seen, description="Build things. " * 40))
        s.add(JobListing(job_id=jid, source_id="src", posted_at=seen, date_basis="Employer first_published", location=location, country="US",
                         career_level=level, match_reason="x", first_seen=now, last_seen=now, active=True))
    return jid


def item(jid, employer, lane="normal"):
    return {"id": jid, "employer": employer, "lane": lane}


def test_the_replay_starts_one_per_employer_and_what_a_lane_has_room_for():
    items = [item("a1", "a"), item("a2", "a"), item("b1", "b"), item("c1", "c", "custom"), item("c2", "d", "custom"), item("e1", "e")]
    picks, stuck = queue_order.replay(items, {"normal": 2, "custom": 1}, set(), [])
    # A tick walks from the top: a2 waits for a1, the second company site for the first, e1 for a free normal worker.
    assert [i["id"] for i, _ in picks][:3] == ["a1", "b1", "c1"]
    assert {i["id"] for i, _ in picks} == {i["id"] for i in items} and not stuck
    waits = {i["id"]: why for i, why in picks}
    assert waits["a2"] == "employer" and waits["c2"] == "lane" and waits["a1"] == ""


def test_the_replay_never_promises_a_lane_with_no_worker():
    picks, stuck = queue_order.replay([item("c1", "c", "custom"), item("a1", "a")], {"normal": 1, "custom": 0}, set(), [])
    assert [i["id"] for i, _ in picks] == ["a1"]
    assert [i["id"] for i in stuck] == ["c1"]


def test_an_employer_already_applying_waits_until_that_application_ends():
    picks, _ = queue_order.replay([item("a2", "a"), item("b1", "b")], {"normal": 1}, {"a"}, [("a", "normal")])
    assert [i["id"] for i, _ in picks] == ["b1", "a2"]


def test_the_shown_order_is_the_dispatchers_sort_and_a_moved_job_goes_first(service):
    setup(service)
    best = ready(service, "best", "Alpha Example", score=90)
    other = ready(service, "other", "Beta Example", score=70, level="Experience unspecified")
    fresh = ready(service, "fresh", "Gamma Example", score=70)
    older = ready(service, "older", "Delta Example", score=70, age=48)
    value = startable.read(service)
    got = [i["id"] for i in queue_order.plan(service, value)["next"]]
    # New grad first, then fit, then the newest: the same function the tick sorts with.
    assert got == [best, fresh, older, other]
    jobs = [value["details"][j] for j in value["ids"]]
    assert [j["id"] for j in autopilot.dispatch_order(jobs, "new_grad_first")] == got
    owner_apply.enqueue(service, older, front=True)
    owner_apply.enqueue(service, other, front=True)
    value = startable.read(service)
    plan = queue_order.plan(service, value)
    # The latest click goes first, and a moved job is listed once.
    assert [i["id"] for i in plan["next"]] == [other, older, best, fresh]
    assert plan["positions"][other] == 1
    assert plan["next"][0]["kind"] == "front"


def test_the_same_posting_under_two_links_is_one_row_and_one_count(service):
    setup(service)
    first = ready(service, "direct", "Alpha Example", score=90)
    twin = ready(service, "short", "Alpha Example", score=60, url="https://go.example.test/abc")
    value = startable.read(service)
    assert value["ids"] == [first] and value["startable"] == 1
    assert value["jobs"][twin]["code"] == "twin"
    assert {h["code"]: h["count"] for h in value["held"]}["twin"] == 1


def test_a_job_with_only_a_listing_elsewhere_is_screened_with_its_own_details(service):
    """startable.read reused the name of its unlisted-jobs map for each job's extras, so every job after the first lost its details."""
    setup(service)
    a = ready(service, "a", "Alpha Example", score=90)
    b = ready(service, "b", "Beta Example", score=80)
    # No longer re-seen by the board's window: the dispatcher lists these through unlisted_ready.
    with service.db.exclusive() as s:
        for row in s.query(JobListing).all():
            row.last_seen = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    value = startable.read(service)
    assert set(value["ids"]) == {a, b}
    assert {value["details"][j]["career_level"] for j in value["ids"]} == {"New grad"}


def test_remove_keeps_the_dispatcher_from_starting_it_and_put_back_undoes_it(client, service):
    setup(service)
    a = ready(service, "a", "Alpha Example", score=90)
    b = ready(service, "b", "Beta Example", score=80)
    assert client.get("/api/startable").json()["startable"] == 2
    assert [i["id"] for i in queue_order.plans(service).now(service)["next"]] == [a, b]
    assert client.delete(f"/api/queue/{a}").json() == {"removed": True, "job_id": a}
    with service.db.session() as s:
        assert s.get(Job, a).status == "paused"
        assert s.get(Setting, "job_control:" + a).value["state"] == "paused"
    value = startable.read(service)
    assert value["ids"] == [b]  # The next tick's screening does not bring it back.
    assert client.post(f"/api/queue/{a}/back", json={}).json()["message"] == "Back in the queue."
    with service.db.session() as s:
        assert s.get(Job, a).status == "ready"
        assert s.get(Setting, "job_control:" + a) is None
    assert startable.read(service)["ids"] == [a, b]


def test_nothing_starts_when_a_job_moves(client, service):
    setup(service)
    a = ready(service, "a", "Alpha Example")
    queue_order.plans(service).now(service)
    response = client.post(f"/api/queue/{a}/front", json={})
    body = response.json()
    assert body["state"] == "waiting" and "front" in body["message"]
    with service.db.session() as s:
        assert s.query(Run).count() == 0  # Only the dispatcher starts it, in its turn: a Stop holds.


def test_requests_order_keeps_the_latest_click_first_and_retries_last():
    waiting = [
        ("retry", {"at": "2026-01-01"}),
        ("plain", {"at": "2026-01-02"}),
        ("moved-early", {"at": "2026-01-03", "front": "2026-01-05T10:00:00"}),
        ("moved-late", {"at": "2026-01-04", "front": "2026-01-05T11:00:00"}),
        ("moved-retry", {"at": "2026-01-01", "front": "2026-01-05T09:00:00"}),
    ]
    got = [job for job, _ in owner_apply.request_order(waiting, {"retry", "moved-retry"})]
    assert got == ["moved-late", "moved-early", "moved-retry", "plain", "retry"]


def test_jobs_held_by_the_platforms_own_pace_say_why_and_when_and_are_not_promised_a_worker(service):
    """Ashby takes one application at a time with a pause between: workers sat idle while the page promised each Ashby job the next
    free worker (2026-11-02, empty install, 30 jobs). Such a job stays in line with its reason and its due time, and is not an
    exact pick."""
    from jobpilot.application_pacing import gap_key

    setup(service)
    ready(service, "g1", "Alpha", ats="greenhouse")
    ready(service, "a1", "Beta", ats="ashby")
    ready(service, "a2", "Gamma", ats="ashby")
    with service.db.exclusive() as s:
        s.add(Setting(key=gap_key("ashby"), value={"run_id": "earlier", "started_at": datetime.now(UTC).timestamp() - 30}))
    value = queue_order.plan(service, startable.read(service))
    waits = {i["id"]: i["wait"] for i in value["next"]}
    assert waits["g1"] == ""
    for jid in ("a1", "a2"):
        assert "Ashby takes one application at a time" in waits[jid] and "It is due in about" in waits[jid]
    assert [i["id"] for i in value["next"]][0] == "g1"
    assert value["exact"] == 1  # only the Greenhouse job is what a free worker takes now
