"""Review a batch, queuing and Select all (owner, 2026-11-01: "the queuing is hell of a job ... the select all button is gone"). Synthetic data only."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from jobpilot import autopilot, board_view, owner_apply, queue_order, review_batch, startable
from jobpilot.application_pacing import LIMIT_KEY
from jobpilot.db import Job, JobListing, Setting
from jobpilot.jobboard import BoardSettings, settings
from jobpilot.schemas import Profile

pytestmark = pytest.mark.integration


def setup(service, auto=False, send=True, limit=25):
    service.db.set_setting("profile", Profile(target_roles=["Software Engineer"]).model_dump())
    service.db.set_setting("job_board_settings", {"enabled": True, "auto_apply": auto, "minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": False})
    service.db.set_setting(LIMIT_KEY, {"limit": limit})
    service.db.set_setting("control", {"paused": False, "auto_submit": send})


def ready(service, jid, company=None, score=80, status="ready", title="Software Engineer", reason="Backend role matches Python and SQL."):
    company = company or "Example " + jid
    seen = datetime.now(UTC).isoformat()
    slug = company.replace(" ", "-").lower()
    with service.db.exclusive() as s:
        s.add(Job(id=jid, identity=jid, url=f"https://boards.example.test/{slug}/jobs/{jid}", ats="greenhouse", company=company, title=title, status=status,
                  score=score, demo=False, location="Austin, TX", created_at=seen, description="Build things. " * 40, reason=reason))
        s.add(JobListing(job_id=jid, source_id="src", posted_at=seen, date_basis="Employer first_published", location="Austin, TX", country="US",
                         career_level="New grad", match_reason="x", first_seen=seen, last_seen=seen, active=True))
    return jid


def fresh(service):
    """Read everything again, as a poll a moment later would."""
    startable.reading(service).reload(service)
    queue_order.plans(service).now(service)
    board_view.board(service).reload(service)


def line(service):
    """The dispatcher's real view: what the plan says will start, in order."""
    return [item["id"] for item in queue_order.plans(service).now(service)["next"]]


def requests(service):
    with service.db.session() as s:
        return sorted(k.removeprefix(owner_apply.KEY) for k in s.scalars(s.query(Setting.key).filter(Setting.key.startswith(owner_apply.KEY)).statement))


def test_a_new_install_reviews_and_an_existing_install_keeps_what_it_saved(service):
    assert BoardSettings().auto_apply is False and BoardSettings().review_size == 25
    assert review_batch.mode(service.db) == "review"
    # An upgrade: the saved row, without the new field, is read as it was and not rewritten by anything here.
    saved = {"enabled": True, "auto_apply": True, "minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": True, "batch_target": 100}
    service.db.set_setting("job_board_settings", saved)
    service.db.set_setting("profile", Profile(target_roles=["Software Engineer"]).model_dump())
    ready(service, "up1")
    fresh(service)
    assert review_batch.mode(service.db) == "auto" and settings(service.db).auto_apply is True
    review_batch.current(service)
    assert service.db.get_setting("job_board_settings") == saved


def test_with_automatic_applying_off_only_what_was_asked_for_is_in_line(service):
    setup(service)
    a, b = ready(service, "a1"), ready(service, "b1")
    fresh(service)
    assert line(service) == []  # Ready, and nothing will start them: they were read as "In queue 1" with no way to queue them.
    owner_apply.enqueue(service, a)
    assert line(service) == [a]
    fresh(service)
    rows = {r["id"]: r for r in board_view.page(service, fit="all")["rows"]}
    assert rows[a]["state"] == "queue" and rows[b]["state"] == "ready" and rows[b]["can_add"] is True


def test_with_automatic_applying_on_the_line_is_the_dispatchers_as_before(service):
    setup(service, auto=True)
    a = ready(service, "a1")
    fresh(service)
    assert line(service) == [a]
    assert review_batch.current(service)["rows"] == []  # Nothing to review: it is already in line.


def test_accepting_a_batch_of_25_with_3_unticked_puts_exactly_22_in_line(service, client):
    setup(service)
    ids = [ready(service, f"j{i:02}", score=90 - i % 30) for i in range(40)]
    fresh(service)
    batch = client.get("/api/review-batch").json()
    assert len(batch["rows"]) == 25 and batch["more"] == 15 and batch["mode"] == "review"
    assert all(r["line"] and r["score"] is not None and r["company"] and r["title"] for r in batch["rows"])
    shown = [r["id"] for r in batch["rows"]]
    assert [r["score"] for r in batch["rows"]] == sorted((r["score"] for r in batch["rows"]), reverse=True)
    out, ticked = None, shown[3:]
    out = client.post("/api/review-batch/accept", json={"batch_id": batch["id"], "job_ids": ticked}).json()
    assert out["queued"] == 22 and out["passed"] == 3 and out["refused"] == []
    assert sorted(line(service)) == sorted(ticked) and sorted(requests(service)) == sorted(ticked)
    # The next batch is ready at once, and offers neither what was queued nor what was unticked.
    following = [r["id"] for r in out["next"]["rows"]]
    assert following and not set(following) & set(shown)
    assert len(following) == len(ids) - 25
    # A second press of Accept finds the batch gone and queues nothing.
    again = client.post("/api/review-batch/accept", json={"batch_id": batch["id"], "job_ids": ticked})
    assert again.status_code == 400 and "already decided" in again.json()["detail"]
    assert sorted(line(service)) == sorted(ticked)


def test_a_batch_stays_the_same_batch_until_it_is_decided_and_takes_a_few_from_one_employer(service, client):
    setup(service)
    for i in range(12):
        ready(service, f"big{i}", company="Big Example", score=95, title=f"Software Engineer {i}")
    for i in range(6):
        ready(service, f"o{i}", score=70)
    fresh(service)
    first = client.get("/api/review-batch").json()
    assert sum(r["company"] == "Big Example" for r in first["rows"]) == review_batch.PER_COMPANY
    assert client.get("/api/review-batch").json()["id"] == first["id"]
    resized = client.post("/api/review-batch/size", json={"size": 5}).json()
    assert len(resized["rows"]) == 5 and resized["id"] != first["id"] and settings(service.db).review_size == 5


def test_a_batch_never_queues_what_it_did_not_offer(service, client):
    setup(service)
    a, other = ready(service, "a1"), ready(service, "z1", status="new", score=None)
    fresh(service)
    batch = client.get("/api/review-batch").json()
    assert [r["id"] for r in batch["rows"]] == [a]
    out = client.post("/api/review-batch/accept", json={"batch_id": batch["id"], "job_ids": [a, other]}).json()
    assert out["queued"] == 1 and requests(service) == [a]


def reserve(client, company):
    assert client.post("/api/reserved/employers", json={"text": company}).status_code == 200


def test_a_reserved_job_is_refused_in_words_and_leaves_nothing_behind(service, client):
    setup(service)
    jid = ready(service, "res1", company="Reserved Example", status="new", score=None)
    reserve(client, "Reserved Example")
    response = client.post(f"/api/queue/{jid}/add", json={})
    assert response.status_code == 400 and "Needs you" in response.json()["detail"] and "reserved" in response.json()["detail"]
    with service.db.session() as s:
        assert s.get(Job, jid).status == "new"
    assert requests(service) == []
    many = client.post("/api/queue/add-many", json={"job_ids": [jid]}).json()
    assert many["added"] == 0 and many["refused"][0]["company"] == "Reserved Example" and "Needs you" in many["refused"][0]["reason"]
    assert requests(service) == []


def over_limit(service, company, count):
    for i in range(count):
        with service.db.exclusive() as s:
            jid = f"done{i}-{company[:3]}"
            s.add(Job(id=jid, identity=jid, url=f"https://boards.example.test/x/jobs/{jid}", ats="greenhouse", company=company, title=f"Role {i}", status="confirmed", score=80, demo=False))


def test_a_job_over_the_employer_limit_keeps_its_approval(service, client):
    setup(service, limit=2)
    over_limit(service, "Busy Example", 2)
    jid = ready(service, "busy1", company="Busy Example")
    response = client.post(f"/api/queue/{jid}/add", json={})
    assert response.status_code == 400 and "your limit is 2" in response.json()["detail"] and "Needs you" in response.json()["detail"]
    fresh(service)
    many = client.post("/api/queue/add-many", json={"job_ids": [jid]}).json()
    assert many["added"] == 0 and "your limit is 2" in many["refused"][0]["reason"]
    assert requests(service) == []


def test_many_jobs_at_one_employer_stop_at_its_limit_counting_what_is_in_line(service, client):
    setup(service, limit=3)
    over_limit(service, "Room Example", 1)
    waiting = ready(service, "room0", company="Room Example")
    fresh(service)
    owner_apply.enqueue(service, waiting)
    ids = [ready(service, f"room{i}", company="Room Example", title=f"Software Engineer {i}") for i in range(1, 5)]
    fresh(service)
    many = client.post("/api/queue/add-many", json={"job_ids": ids}).json()
    # One confirmed and one waiting leave room for one more.
    assert many["added"] == 1 and len(many["refused"]) == 3
    assert all("Needs you" in r["reason"] for r in many["refused"])
    assert len(requests(service)) == 2


def test_a_job_already_applied_to_is_not_queued_twice(service, client):
    setup(service)
    done = ready(service, "done1", status="confirmed")
    many = client.post("/api/queue/add-many", json={"job_ids": [done]}).json()
    assert many["added"] == 0 and many["already"] == 1 and requests(service) == []


def test_the_posting_ruling_you_out_is_refused_in_words(service, client):
    setup(service)
    jid = ready(service, "no1", status="skipped", score=0, reason="Explicit hiring restriction | Eligibility check: citizenship")
    response = client.post(f"/api/queue/{jid}/add", json={})
    assert response.status_code == 400 and "rule you out" in response.json()["detail"]


def test_a_job_you_took_out_goes_back_when_you_ask_for_it(service, client):
    setup(service)
    jid = ready(service, "back1")
    fresh(service)
    owner_apply.enqueue(service, jid)
    assert client.delete(f"/api/queue/{jid}").status_code == 200
    assert requests(service) == [] and line(service) == []
    response = client.post(f"/api/queue/{jid}/add", json={})
    assert response.status_code == 200
    assert line(service) == [jid]
    with service.db.session() as s:
        assert s.get(Setting, "job_control:" + jid) is None and s.get(Job, jid).status == "ready"


def test_select_all_counts_the_rows_on_every_page_and_only_those_that_can_join(service, client):
    setup(service)
    for i in range(60):
        ready(service, f"s{i:02}", score=80)
    reserved = ready(service, "res1", company="Reserved Example", score=99)
    reserve(client, "Reserved Example")
    fresh(service)
    first = client.get("/api/board?size=25").json()
    assert first["total"] == 61 and first["selectable"] == 60 and len(first["rows"]) == 25
    ids = client.get("/api/board?size=25&want=ids").json()["ids"]
    assert len(ids) == first["selectable"] and reserved not in ids
    # More than the fifty one request to Apply may carry: nothing starts here.
    many = client.post("/api/queue/add-many", json={"job_ids": ids}).json()
    assert many["added"] == 60 and many["refused"] == []
    assert sorted(line(service)) == sorted(ids)
    assert sorted(client.get("/api/queue/ids").json()["ids"]) == sorted(ids)
    removed = client.post("/api/queue/remove-many", json={"job_ids": ids[:10]}).json()
    assert removed == {"removed": 10, "not_in_line": 0}
    assert len(line(service)) == 50


def test_review_needs_ranking_and_ranking_sends_nothing(service, monkeypatch):
    from jobpilot import workers

    setup(service, send=False)
    asked = []

    async def classify(ids):
        asked.append(ids)

    async def dispatch(run_id):  # A worker pool: tick returns at once without one.
        raise AssertionError("nothing may start")

    service.dispatch = dispatch
    monkeypatch.setattr(workers, "enqueue_classification", classify)
    monkeypatch.setattr(autopilot, "classification_ids", lambda due, attempted: ["ranked-one"])
    asyncio.run(autopilot.tick(service))
    assert asked == [["ranked-one"]]
    # A stop stops ranking too.
    service.db.set_setting("control", {"paused": True, "auto_submit": True})
    asyncio.run(autopilot.tick(service))
    assert len(asked) == 1


def test_a_person_who_has_not_looked_for_a_week_does_not_pay_to_rank_a_week_of_postings(service, monkeypatch):
    from jobpilot import workers

    setup(service, send=False)
    asked = []

    async def classify(ids):
        asked.append(ids)

    service.dispatch = lambda run_id: None
    monkeypatch.setattr(workers, "enqueue_classification", classify)
    monkeypatch.setattr(autopilot, "REVIEW_POOL", 2)
    ready(service, "p1"), ready(service, "p2")
    asyncio.run(autopilot.tick(service))
    assert asked == []


def test_a_new_install_with_nothing_to_review_says_what_it_is_waiting_for(service, client):
    setup(service)
    ready(service, "c1", status="new", score=None)
    fresh(service)
    batch = client.get("/api/review-batch").json()
    assert batch["rows"] == [] and batch["id"] == "" and batch["checking"] == 1
    refused = client.post("/api/review-batch/accept", json={"batch_id": "gone", "job_ids": []})
    assert refused.status_code == 400
