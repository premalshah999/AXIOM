"""Press Start, see idle workers and a full queue (owner, 2026-11-02): each case says why nothing is starting and what to do next, on
Overview and on Queue, from one answer. Synthetic data only."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jobpilot import autopilot, owner_apply, queue_order, startable, stop, waiting
from jobpilot.admission import QueueBlocked
from jobpilot.db import Budget, Job, Run, Setting
from jobpilot.schemas import Profile
from test_queue_order import ready, setup

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def has_a_key(client, service):
    """After the app is built: building it applies the saved connections to the config."""
    from jobpilot.agent_settings import PROVIDER_NAMES

    for name in PROVIDER_NAMES:
        setattr(service.config, name + "_api_key", "synthetic-key")


def fresh(service):
    startable.reading(service).reload(service)
    return queue_order.plans(service).now(service)


def said(client):
    """What Queue shows, and what the snapshot Overview reads says: the same answer."""
    queue = client.get("/api/queue").json()
    snapshot = client.get("/api/snapshot").json()
    assert (queue["why"]["reason"] or {}).get("code") == (snapshot["waiting"]["reason"] or {}).get("code")
    assert queue["why"]["in_line"] == snapshot["waiting"]["in_line"] == queue["total"]
    return queue, queue["why"]["reason"]


def day(service):
    return datetime.now(ZoneInfo(service.config.timezone)).date().isoformat()


def start_batch(service, **more):
    service.db.set_setting("job_board_autopilot", {"state": "active", "target": 25, "started_at": datetime.now(UTC).isoformat(), "run_ids": [], "considered_ids": [], "confirmed": 0, "queued": 0, "skipped": [], **more})


def test_a_line_of_paced_platforms_says_so_and_only_one_row_is_promised_a_worker(client, service):
    """40 Ashby jobs and nothing running: one can start, the rest wait on Ashby, and no row but the first says the next free worker."""
    service.dispatch = lambda *a: None
    setup(service)
    start_batch(service)
    for n in range(4):
        ready(service, f"a{n}", f"Ashby Employer {n}", ats="ashby", score=90 - n, url=f"https://a{n}.example.test/jobs/1")
    ready(service, "i0", "Icims Employer 0", ats="icims", score=80, url="https://i0.example.test/jobs/1")
    ready(service, "i1", "Icims Employer 1", ats="icims", score=79, url="https://i1.example.test/jobs/1")
    value = fresh(service)
    assert value["startable_now"] == 2 and value["paced"] == 4  # One Ashby, one iCIMS; the rest wait their platform's turn.
    waits = {i["id"]: i["wait"] for i in value["next"]}
    assert waits["a0"] == "" and "takes one application at a time" in waits["a1"] and "It is due in about" in waits["a1"]
    assert "limited number of applications at a time" in waits["i1"]
    # Later Ashby jobs are later: not all "due in about 3 minutes".
    assert waits["a1"] != waits["a3"]
    # While the one that can start has started, the whole line is the platforms' pace.
    with service.db.exclusive() as s:
        s.add(Run(id="ra", job_id="a0", mode="submit", state="running", created_at=datetime.now(UTC).isoformat(), packet={"worker_slot": 1, "worker_pool": "normal"}))
        s.add(Run(id="ri", job_id="i0", mode="submit", state="running", created_at=datetime.now(UTC).isoformat(), packet={"worker_slot": 2, "worker_pool": "normal"}))
    fresh(service)
    queue, reason = said(client)
    assert reason["code"] == "paced" and reason["blocking"] is False  # Two are running.
    assert "Ashby" in reason["says"] and "iCIMS" in reason["says"] and "workers are free because" in reason["says"]
    assert "Next start" in reason["says"]
    assert all(row["wait"] for row in queue["next"])
    assert queue["total"] == 4 and {r["job_id"] for r in queue["running"]} == {"a0", "i0"}


def test_the_owners_case_workers_free_and_a_line_of_ashby_says_why_with_live_numbers_and_what_waits_on_them(client, service):
    """Owner, 2026-11-02: "there are 70+ jobs in the queue, so why are all the workers idle?" 88 startable, 87 of them Ashby, one start
    every 150 s, 6 to 9 workers free, and the rows came back with no reason."""
    from jobpilot.application_pacing import gap_key

    service.dispatch = lambda *a: None
    setup(service)
    start_batch(service)
    for n in range(5):
        ready(service, f"a{n}", f"Ashby Employer {n}", ats="ashby", score=90 - n, url=f"https://a{n}.example.test/jobs/1")
    # Over the employer limit, and a reserved employer: the dispatcher's own reading holds both.
    for n in range(2):
        ready(service, f"o{n}", "Limited Employer", ats="greenhouse", score=70, url=f"https://limited.example.test/jobs/{n}", title=f"Software Engineer {n}")
    service.db.set_setting("application_pacing:company_limit", {"limit": 1})
    with service.db.exclusive() as s:
        job = Job(id="done", identity="done", url="https://limited.example.test/jobs/9", ats="greenhouse", company="Limited Employer", title="Engineer", status="confirmed", demo=False)
        s.add(job)
        s.add(Run(id="done-run", job_id="done", mode="submit", state="confirmed", created_at=datetime.now(UTC).isoformat(), receipt={}))
        # The gap began a minute ago: Ashby's next start is known.
        s.add(Setting(key=gap_key("ashby"), value={"run_id": "earlier", "started_at": datetime.now(UTC).timestamp() - 60}))
    service.config.normal_workers = 6
    fresh(service)
    queue, reason = said(client)
    assert reason["code"] == "paced"
    assert reason["says"].startswith("6 workers are free because the next 5 jobs are all on Ashby, which takes one start every 2.5 minutes.")
    assert "Next start in about" in reason["says"]
    # Each row its own wait, none empty.
    waits = [row["wait"] for row in queue["next"]]
    assert all(waits) and len(set(waits)) > 1 and all("2.5 minutes apart" in w for w in waits)
    assert "over the employer limit" in queue["why"]["ask"]["says"] and "approve them" in queue["why"]["ask"]["says"]
    assert {"view": "needs", "filter": "over", "label": "Over the employer limit"} in queue["why"]["ask"]["links"]


@pytest.mark.asyncio
async def test_what_the_plan_says_can_start_is_what_a_real_tick_starts(service, monkeypatch):
    """The sentence reads the dispatcher's gates, so it can't disagree: of five Ashby jobs and one Greenhouse job, two start, through the real
    tick and the real admission, and the plan said two."""
    from types import SimpleNamespace

    from jobpilot import execution_services
    from test_stop import add_resume

    async def nothing(run_id):
        return None

    async def zero(*a, **k):
        return 0

    async def prepared(service):
        return SimpleNamespace(classify=[])

    service.dispatch = nothing
    setup(service)
    service.db.set_setting("profile", Profile(name="Alex Example", email="alex@example.test", target_roles=["Software Engineer"]).model_dump())
    add_resume(service)
    start_batch(service)
    service.db.set_setting("execution_services", {"enabled": True, "version": 1})
    service.db.set_setting("application_resume_policy", {"mode": "master"})
    monkeypatch.setattr(autopilot, "replenish_discovery", lambda *a: None)
    monkeypatch.setattr(autopilot, "persist_stalled", zero)
    monkeypatch.setattr(autopilot, "resubmit_unknown", zero)
    monkeypatch.setattr(execution_services, "prepare_due", prepared)
    for n in range(5):
        ready(service, f"a{n}", f"Ashby Employer {n}", ats="ashby", score=90 - n, url=f"https://a{n}.example.test/jobs/1")
    ready(service, "g0", "Green Employer", ats="greenhouse", score=50, url="https://g0.example.test/jobs/1")
    before = fresh(service)
    assert before["startable_now"] == 2 and before["paced"] == 4
    await autopilot.tick(service)
    with service.db.session() as s:
        started = {run.job_id for run in s.query(Run).filter(Run.state == "queued")}
    assert len(started) == before["startable_now"], (started, service.db.get_setting("job_board_autopilot").get("rejections"))
    after = fresh(service)
    assert after["startable_now"] == 0 and after["paced"] == 4 and started.isdisjoint({i["id"] for i in after["next"]})


def test_accepted_ashby_jobs_the_pace_turned_away_are_said_as_the_pace_not_as_an_employer(client, service):
    """Review a batch: each accepted job the dispatcher tried holds its refusal's not_before, and the engine's words."""
    from jobpilot.application_pacing import gap_key

    service.dispatch = lambda *a: None
    setup(service)
    service.db.set_setting("job_board_settings", {"enabled": True, "auto_apply": False, "minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": False})
    for n in range(3):
        ready(service, f"a{n}", f"Ashby Employer {n}", ats="ashby", url=f"https://a{n}.example.test/jobs/1")
        owner_apply.enqueue(service, f"a{n}")
        with service.db.exclusive() as s:
            row = s.get(Setting, owner_apply.KEY + f"a{n}")
            row.value = {**row.value, "code": "ats_pacing", "reason": "Ashby application start interval has not elapsed", "not_before": (datetime.now(UTC) + timedelta(seconds=90)).isoformat()}
    with service.db.exclusive() as s:
        s.add(Setting(key=gap_key("ashby"), value={"run_id": "earlier", "started_at": datetime.now(UTC).timestamp() - 60}))
    fresh(service)
    queue, reason = said(client)
    assert reason["code"] == "paced" and "all on Ashby" in reason["says"] and "every 2.5 minutes" in reason["says"]
    assert not any("interval" in row["wait"] for row in queue["next"])  # The engine's words stay in the engine.


def test_jobs_waiting_out_a_time_are_said_as_that(client, service):
    service.dispatch = lambda *a: None
    setup(service)
    service.db.set_setting("job_board_settings", {"enabled": True, "auto_apply": False, "minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": False})
    ready(service, "g0", "Green Employer", ats="greenhouse")
    owner_apply.enqueue(service, "g0")
    with service.db.exclusive() as s:
        row = s.get(Setting, owner_apply.KEY + "g0")
        row.value = {**row.value, "code": "employer_cooldown", "reason": "This employer rejected an application as possible spam.", "not_before": (datetime.now(UTC) + timedelta(hours=6)).isoformat()}
    fresh(service)
    queue, reason = said(client)
    assert reason["code"] == "line_waits" and "waiting out a time" in reason["says"] and "Next start in about 6 hours" in reason["says"]
    assert "possible spam" in queue["next"][0]["wait"]


def test_a_paced_job_moved_to_the_front_stays_at_the_front_and_says_why_it_waits(service):
    setup(service)
    start_batch(service)
    ready(service, "g0", "Green Employer", ats="greenhouse", url="https://g0.example.test/jobs/1")
    ready(service, "i0", "Icims Employer 0", ats="icims", score=70, url="https://i0.example.test/jobs/1")
    ready(service, "i1", "Icims Employer 1", ats="icims", score=60, url="https://i1.example.test/jobs/1")
    with service.db.exclusive() as s:
        s.add(Run(id="ri", job_id="i0", mode="submit", state="running", created_at=datetime.now(UTC).isoformat(), packet={"worker_slot": 1, "worker_pool": "normal"}))
    fresh(service)
    owner_apply.enqueue(service, "i1", front=True)
    value = fresh(service)
    assert value["next"][0]["id"] == "i1" and "iCIMS" in value["next"][0]["wait"]
    assert value["next"][1]["id"] == "g0" and value["next"][1]["wait"] == ""


def test_a_job_that_started_since_the_reading_is_not_also_in_line(service):
    setup(service)
    start_batch(service)
    ready(service, "g0", "Green Employer", ats="greenhouse", url="https://g0.example.test/jobs/1")
    ready(service, "g1", "Other Employer", ats="greenhouse", url="https://g1.example.test/jobs/1")
    assert {i["id"] for i in fresh(service)["next"]} == {"g0", "g1"}
    with service.db.exclusive() as s:
        s.add(Run(id="r0", job_id="g0", mode="submit", state="running", created_at=datetime.now(UTC).isoformat(), packet={}))
    # The minute-old reading still lists it.
    assert "g0" in startable.reading(service).get(service)["ids"]
    assert [i["id"] for i in queue_order.plans(service).now(service)["next"]] == ["g1"]


def test_the_first_read_of_a_plan_nobody_has_read_for_a_while_is_current(client, service, monkeypatch):
    """After Start the Queue page opened on "Nothing is in line" over 20 jobs: a plan from before the press."""
    service.dispatch = lambda *a: None
    setup(service)
    service.db.set_setting("job_board_settings", {"enabled": True, "auto_apply": False, "minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": False})
    ready(service, "g0", "Green Employer", ats="greenhouse")
    fresh(service)
    assert client.get("/api/queue").json()["total"] == 0
    service.db.set_setting("job_board_settings", {"enabled": True, "auto_apply": True, "minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": False})
    start_batch(service)
    planner = queue_order.plans(service)
    planner.at -= 60
    assert client.get("/api/queue").json()["total"] == 1


def test_a_daily_limit_reached_says_so_on_every_row_and_nothing_can_start(client, service):
    service.dispatch = lambda *a: None
    setup(service)
    start_batch(service)
    service.config.daily_application_limit = 2
    ready(service, "g0", "Green Employer", ats="greenhouse")
    with service.db.exclusive() as s:
        s.add(Budget(day=day(service), spent=0, submissions=2))
    fresh(service)
    queue, reason = said(client)
    assert reason["code"] == "daily_limit" and reason["blocking"] is True
    assert "2 of your 2" in reason["says"] and "raise the daily limit" in reason["next"]
    assert "midnight" in queue["next"][0]["wait"]


@pytest.mark.asyncio
async def test_with_the_limit_used_a_queued_job_does_not_start_only_to_be_refused_at_the_last_step(service, monkeypatch):
    """Review a batch: the batch's own sum never ran, so each queued job opened a browser, filled a whole form, spent AI credit and was
    refused by reserve_submission."""
    setup(service)
    service.dispatch = lambda *a: None
    service.db.set_setting("job_board_settings", {"enabled": True, "auto_apply": False, "minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": False})
    service.config.daily_application_limit = 2
    ready(service, "g0", "Green Employer", ats="greenhouse")
    owner_apply.enqueue(service, "g0")
    started = []

    async def queue(job_id, *a, **k):
        started.append(job_id)
        return {"id": "r"}

    monkeypatch.setattr(service, "queue", queue)
    with service.db.exclusive() as s:
        s.add(Budget(day=day(service), spent=0, submissions=2))
    assert await autopilot.owner_requests_only(service) == 0
    assert started == []
    service.config.daily_application_limit = 3  # Raised: it goes at once, with no waiting for midnight.
    assert await autopilot.owner_requests_only(service) == 1
    assert started == ["g0"]


def test_a_click_past_the_daily_limit_waits_with_its_reason_and_writes_no_run(service):
    setup(service)
    service.config.daily_application_limit = 1
    ready(service, "g0", "Green Employer", ats="greenhouse")
    with service.db.exclusive() as s:
        s.add(Budget(day=day(service), spent=0, submissions=1))
    with pytest.raises(QueueBlocked) as refused:
        service._admit("g0", "submit", owner_pick=True)
    assert refused.value.code == "daily_limit" and refused.value.outcome == "deferred"
    with service.db.session() as s:
        assert s.query(Run).count() == 0


def test_the_ai_spending_limit_stops_new_starts_and_says_so(client, service):
    """Started anyway, each job ended at its first model call with its attempt spent, and nothing retries that by itself."""
    service.dispatch = lambda *a: None
    setup(service)
    start_batch(service)
    service.config.daily_budget_usd = 1.0
    ready(service, "g0", "Green Employer", ats="greenhouse")
    with service.db.exclusive() as s:
        s.add(Budget(day=day(service), spent=1.0, submissions=0))
    fresh(service)
    queue, reason = said(client)
    assert reason["code"] == "ai_budget" and "$1" in reason["says"] and "raise AI spending" in reason["next"]
    assert "AI spending" in queue["next"][0]["wait"]
    with pytest.raises(QueueBlocked) as refused:
        service._admit("g0", "submit", owner_pick=True)
    assert refused.value.code == "ai_budget" and refused.value.outcome == "deferred"


def test_a_goal_reached_today_says_so_and_what_to_do(client, service):
    service.dispatch = lambda *a: None
    setup(service)
    start_batch(service, state="complete", target=5, finished_day=day(service), finished_at=datetime.now(UTC).isoformat())
    ready(service, "g0", "Green Employer", ats="greenhouse")
    fresh(service)
    queue, reason = said(client)
    assert reason["code"] == "goal_reached" and "Press Start tomorrow" in reason["next"]
    assert queue["total"] == 0  # Over, so the dispatcher starts nothing by itself and the line doesn't say it will.
    assert client.get("/api/snapshot").json()["autopilot_runtime"]["state"] == "complete"


def test_yesterdays_goal_is_over_today(client, service):
    """"Daily limit reached. Press Start tomorrow" read every day after, beside "0 of 25"."""
    service.dispatch = lambda *a: None
    setup(service)
    yesterday = (datetime.now(ZoneInfo(service.config.timezone)) - timedelta(days=1)).date().isoformat()
    start_batch(service, state="complete", target=5, finished_day=yesterday, finished_at=(datetime.now(UTC) - timedelta(days=1)).isoformat())
    fresh(service)
    _, reason = said(client)
    assert reason["code"] == "nothing_in_line" and "press Start" in reason["next"]
    assert client.get("/api/snapshot").json()["autopilot_runtime"]["state"] == "idle"


def test_a_job_the_person_queued_still_starts_after_the_goal_and_the_page_does_not_say_otherwise(client, service):
    service.dispatch = lambda *a: None
    setup(service)
    start_batch(service, state="complete", target=5, finished_day=day(service), finished_at=datetime.now(UTC).isoformat())
    ready(service, "g0", "Green Employer", ats="greenhouse")
    owner_apply.enqueue(service, "g0")
    fresh(service)
    queue, reason = said(client)
    assert queue["total"] == 1 and reason is None


def test_stopped_comes_first_and_every_row_says_it(client, service):
    service.dispatch = lambda *a: None
    setup(service)
    start_batch(service)
    service.config.daily_application_limit = 1
    ready(service, "g0", "Green Employer", ats="greenhouse")
    with service.db.exclusive() as s:
        s.add(Budget(day=day(service), spent=0, submissions=1))
    stop.press(service.db)
    fresh(service)
    queue, reason = said(client)
    assert reason["code"] == "stopped" and "Resume" in reason["says"]
    assert "Resume" in queue["next"][0]["wait"]


def test_sending_off_names_the_switch(client, service):
    service.dispatch = lambda *a: None
    setup(service)
    service.db.set_setting("control", {"paused": False, "auto_submit": False})
    ready(service, "g0", "Green Employer", ats="greenhouse")
    owner_apply.enqueue(service, "g0")
    fresh(service)
    queue, reason = said(client)
    assert reason["code"] == "sending_off" and reason["action"]["tab"] == "applying"
    assert "Sending is off" in queue["next"][0]["wait"]


def test_a_new_install_with_nothing_queued_is_told_to_queue_something_not_that_sending_is_off(client, service):
    service.dispatch = lambda *a: None
    setup(service)
    service.db.set_setting("job_board_settings", {"enabled": True, "auto_apply": False, "minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": False})
    service.db.set_setting("control", {"paused": False, "auto_submit": False})
    fresh(service)
    _, reason = said(client)
    assert reason["code"] == "nothing_in_line" and "Review a batch" in reason["next"]


def test_review_chosen_in_settings_reads_as_not_running_not_as_paused(client, service):
    """With a batch active and Review a batch chosen, the screen read "Waiting to start, Paused", and its Resume turned automatic applying
    back on with no Start dialog."""
    service.dispatch = lambda *a: None
    setup(service)
    start_batch(service)
    service.db.set_setting("job_board_settings", {"enabled": True, "auto_apply": False, "minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": False})
    fresh(service)
    runtime = client.get("/api/snapshot").json()["autopilot_runtime"]
    assert runtime["state"] == "idle" and runtime["intake_reason"] != "Paused"


def test_battery_is_said_only_while_something_is_wanted(client, service, monkeypatch):
    service.dispatch = lambda *a: None
    setup(service)
    monkeypatch.setitem(waiting._power, "at", -10_000)
    monkeypatch.setattr("jobpilot.native.power.power_status", lambda: {"battery": True, "percent": 41, "remaining": "2:10"})
    fresh(service)
    assert client.get("/api/queue").json()["why"]["battery"] == ""
    ready(service, "g0", "Green Employer", ats="greenhouse")
    owner_apply.enqueue(service, "g0")
    fresh(service)
    words = client.get("/api/queue").json()["why"]["battery"]
    assert "on battery (41%)" in words and "Plug it in" in words


def test_nothing_in_review_mode_starts_a_company_site_and_the_doors_refuse_it(client, service):
    """Start, the batch, Best matches, Select all and To front: none of them puts a company's own site in line."""
    service.dispatch = lambda *a: None
    setup(service)
    start_batch(service)
    ready(service, "c0", "Own Site Employer", ats="custom", url="https://careers.ownsite.example.test/jobs/1")
    ready(service, "g0", "Green Employer", ats="greenhouse")
    value = fresh(service)
    assert [i["id"] for i in value["next"]] == ["g0"]  # The dispatcher's line.
    assert "c0" not in client.get("/api/queue/ids").json()["ids"]
    assert client.post("/api/queue/c0/add", json={}).status_code >= 400
    assert client.post("/api/queue/c0/front", json={}).status_code >= 400
    many = client.post("/api/queue/add-many", json={"job_ids": ["c0"]}).json()
    assert many["added"] == 0 and many["refused"]
    many = client.post("/api/queue/front-many", json={"job_ids": ["c0"]}).json()
    assert many["added"] == 0 and many["refused"]
    with service.db.session() as s:
        assert s.get(Setting, owner_apply.KEY + "c0") is None
        assert s.get(Job, "c0").status == "ready"
    from jobpilot import board_view

    board_view.board(service).reload(service)
    assert "c0" not in {r["id"] for r in board_view.page(service, fit="all", group=False)["rows"]}
    # Its own section's click is the one way in.
    assert client.post("/api/queue/company-sites", json={"job_ids": ["c0"]}).json()["added"] == 1
    assert "c0" in client.get("/api/queue/ids").json()["ids"]
