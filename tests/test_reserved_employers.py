"""Reserved employers (owner, 2026-10-27: "make a different section for FAANG, MANGO, Big four consulting, and these
scrapers should not apply before my permission").

Their jobs are found, ranked and listed in their own section; queue admission, the autopilot, retries, recovery, the
repair agent and remembered Apply clicks never start one before the owner approves that job. Synthetic data only: the
employers are the owner's configurable list, every applicant and other employer is an example.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert

from jobpilot import autopilot, reserved
from jobpilot.admission import QueueBlocked
from jobpilot.db import Job, JobListing, Run, Setting
from jobpilot.jobboard import BoardSettings
from jobpilot.reserved import Matcher, parse

DEFAULTS = Matcher(parse(None))


def iso(minutes_ago=0):
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


def add_job(service, jid="amzn", company="Amazon", url="https://www.amazon.jobs/en/jobs/1", status="ready"):
    with service.db.exclusive() as s:
        s.add(Job(id=jid, identity=jid, url=url, company=company, title="Software Development Engineer",
                  ats="custom", status=status, score=90, location="Remote, United States"))
    return jid


async def nothing(*args, **kwargs):
    return None


# ----- matching -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "company, url, section, employer",
    [
        ("Amazon", "", "faang", "Amazon"),
        ("Amazon Web Services (AWS)", "", "faang", "Amazon"),
        ("Amazon.com Services LLC", "", "faang", "Amazon"),
        ("Amazon Development Center U.S., Inc.", "", "faang", "Amazon"),
        ("Meta Platforms, Inc.", "", "faang", "Meta"),
        ("Facebook", "", "faang", "Meta"),
        ("Apple Inc.", "", "faang", "Apple"),  # also MANGO: the first section that lists it wins
        ("Netflix", "", "faang", "Netflix"),
        ("Google LLC", "", "faang", "Google"),
        ("Alphabet Inc.", "", "faang", "Google"),
        ("Google DeepMind", "", "faang", "Google"),
        ("Microsoft Corporation", "", "mango", "Microsoft"),
        ("NVIDIA Corporation", "", "mango", "Nvidia"),
        ("OpenAI", "", "mango", "OpenAI"),
        ("Deloitte US", "", "big_four", "Deloitte"),
        ("Deloitte & Touche LLP", "", "big_four", "Deloitte"),
        ("PricewaterhouseCoopers (PwC)", "", "big_four", "PwC"),
        ("PwC US Advisory", "", "big_four", "PwC"),
        ("Ernst & Young LLP", "", "big_four", "EY"),
        ("EY-Parthenon", "", "big_four", "EY"),
        ("KPMG LLP", "", "big_four", "KPMG"),
        # A staffing or aggregator name on the employer's own career site.
        ("Example Staffing", "https://www.amazon.jobs/en/jobs/123", "faang", "Amazon"),
        ("Example Staffing", "https://hiring.amazon.com/job/1", "faang", "Amazon"),
        ("Example Staffing", "https://www.metacareers.com/jobs/1", "faang", "Meta"),
        ("Example Staffing", "https://jobs.apple.com/en-us/details/1", "faang", "Apple"),
        ("Example Staffing", "https://careers.google.com/jobs/results/1", "faang", "Google"),
        ("Example Staffing", "https://www.google.com/about/careers/applications/jobs/results/1", "faang", "Google"),
        ("Example Staffing", "https://boards.greenhouse.io/deepmind/jobs/1", "faang", "Google"),
        ("Example Staffing", "https://apply.careers.microsoft.com/careers/job/1", "mango", "Microsoft"),
        ("Example Staffing", "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/1", "mango", "Nvidia"),
        ("Example Staffing", "https://jobs.ashbyhq.com/openai/abc", "mango", "OpenAI"),
        ("Example Staffing", "https://openai.com/careers/research-engineer", "mango", "OpenAI"),
        ("Example Staffing", "https://deloitteus.avature.net/careers/JobDetail/1", "big_four", "Deloitte"),
        ("Example Staffing", "https://apply.deloitte.com/careers/JobDetail/1", "big_four", "Deloitte"),
        ("Example Staffing", "https://jobs-us.pwc.com/job/1", "big_four", "PwC"),
        ("Example Staffing", "https://careers.ey.com/ey/job/1", "big_four", "EY"),
        ("Example Staffing", "https://www.kpmg.com/us/en/careers.html", "big_four", "KPMG"),
    ],
)
def test_a_reserved_employer_matches_by_its_name_or_its_career_site(company, url, section, employer):
    found = DEFAULTS(company, url)
    assert found is not None and (found.section, found.employer) == (section, employer)


@pytest.mark.parametrize(
    "company, url",
    [
        ("Metaview", "https://jobs.ashbyhq.com/metaview/1"),
        ("Metabase", "https://jobs.lever.co/metabase/1"),
        ("Meta Financial Example Group", ""),
        ("Applebee's", ""),
        ("Apple Leisure Group", ""),
        ("Apple Bank for Savings", ""),
        ("Amazon Delivery Partner Example LLC", ""),
        ("Netflixer Example", ""),
        ("Eyeview", ""),
        ("Googleplex Example Cafe", ""),
        ("Acme Example", "https://careers.journey.com/1"),  # ends in "ey.com", not on ey.com
        ("Acme Example", "https://docs.google.com/forms/d/1"),  # a form on Google's domain is not a Google job
        ("Acme Example", "https://boards.greenhouse.io/deepmindx/jobs/1"),
        ("Acme Example", "https://jobs.ashbyhq.com/openai-example/1"),
        ("Acme Example", "https://acme.wd5.myworkdayjobs.com/External/job/1"),
        ("Acme Example", "not a url"),
        ("", ""),
    ],
)
def test_names_match_whole_words_and_sites_match_hosts(company, url):
    assert DEFAULTS(company, url) is None


def test_saved_rules_are_validated_and_an_unreadable_value_keeps_the_defaults():
    with pytest.raises(ValueError):
        reserved.Rules.model_validate({"sections": [{"id": "x", "label": "X", "employers": [{"name": "A", "sites": ["no site"]}]}]})
    with pytest.raises(ValueError):
        reserved.Rules.model_validate({"sections": [{"id": "x", "label": "X"}, {"id": "x", "label": "Y"}]})
    rules = reserved.Rules.model_validate(
        {"sections": [{"id": "x", "label": "X", "employers": [{"name": " Acme  Example ", "sites": ["HTTPS://Jobs.Example.Test/"]}]}]}
    )
    assert rules.sections[0].employers[0].name == "Acme Example"
    assert rules.sections[0].employers[0].sites == ["jobs.example.test"]
    assert parse({"sections": "garbage"}).model_dump() == parse(None).model_dump()  # fails closed


# ----- the admission gate ---------------------------------------------------------------------------------------------


async def test_queue_admission_holds_a_reserved_job_until_the_owner_approves_it(client, service, prepared):
    service.db.set_setting("control", {"auto_submit": True})
    add_job(service)
    for owner_pick in (False, True):  # the owner's own Apply click included
        with pytest.raises(QueueBlocked) as held:
            await service.queue("amzn", "submit", owner_pick=owner_pick)
        assert (held.value.code, held.value.outcome) == ("reserved_employer", "protected")
    assert "Amazon" in str(held.value) and "Jobs › Reserved" in str(held.value)
    with service.db.session() as s:
        assert s.get(Setting, "owner_pick:amzn") is None  # a refused click leaves nothing behind
    with pytest.raises(QueueBlocked) as dry:
        await service.queue("amzn", "dry_run")
    assert dry.value.code == "reserved_employer"

    answer = client.post("/api/jobs/amzn/approve-reserved", json={"apply": False})
    assert answer.status_code == 200, answer.text
    assert answer.json()["approved"] is True and answer.json()["label"] == "FAANG"
    try:
        await service.queue("amzn", "submit")
    except QueueBlocked as exc:
        assert exc.code != "reserved_employer"
    except ValueError:
        pass  # past the gate; this synthetic installation lacks something later admission needs

    # Withdrawing the approval holds it again.
    assert client.delete("/api/jobs/amzn/approve-reserved").json()["removed"] is True
    with pytest.raises(QueueBlocked) as again:
        await service.queue("amzn", "submit")
    assert again.value.code == "reserved_employer"


async def test_other_employers_and_demo_jobs_pass_the_gate(service):
    add_job(service, "acme", company="Acme Example", url="https://jobs.example.test/1")
    with service.db.exclusive() as s:
        s.add(Job(id="demo", identity="demo", url="https://www.amazon.jobs/en/jobs/9", company="Amazon", demo=True))
    with service.db.session() as s:
        reserved.require_approval(s, s.get(Job, "acme"))
        reserved.require_approval(s, s.get(Job, "demo"))


def test_the_owners_apply_click_is_refused_with_where_to_approve(client, service, prepared):
    service.dispatch = nothing
    service.db.set_setting("control", {"auto_submit": True})
    add_job(service)
    answer = client.post("/api/jobs/amzn/apply", json={})
    assert answer.status_code == 400 and "Jobs › Reserved" in answer.json()["detail"]


async def test_approving_one_job_applies_through_the_one_click_path(client, service, monkeypatch):
    service.dispatch = nothing
    add_job(service)
    started = []

    async def apply(service, job_id):
        started.append(job_id)
        return {"job_id": job_id, "state": "queued", "run_id": "r1", "message": "Application queued."}

    monkeypatch.setattr("jobpilot.owner_apply.apply", apply)
    answer = client.post("/api/jobs/amzn/approve-reserved", json={}).json()
    assert started == ["amzn"] and answer["state"] == "queued" and answer["employer"] == "Amazon"
    with service.db.session() as s:
        assert reserved.approved(s, "amzn") and not reserved.held(s)
    assert client.post("/api/jobs/missing/approve-reserved", json={}).status_code == 400
    add_job(service, "acme", company="Acme Example", url="https://jobs.example.test/1")
    refused = client.post("/api/jobs/acme/approve-reserved", json={})
    assert refused.status_code == 400 and "not at a reserved employer" in refused.json()["detail"]


def test_the_final_submit_reservation_checks_the_approval_again(service):
    service.db.set_setting("control", {"auto_submit": True})
    add_job(service, status="running")
    with service.db.exclusive() as s:
        s.add(Run(id="r", job_id="amzn", mode="submit", state="running", packet={}))
    with pytest.raises(QueueBlocked) as held:
        service.reserve_submission("r")
    assert held.value.code == "reserved_employer"
    with service.db.session() as s:
        assert s.get(Run, "r").state == "running"


async def test_a_run_queued_before_the_hold_is_stopped_before_it_starts(service):
    add_job(service, status="queued")
    with service.db.exclusive() as s:
        s.add(Run(id="q", job_id="amzn", mode="submit", state="queued", packet={"worker_pool": "normal"}))
    await service.execute("q")
    with service.db.session() as s:
        run = s.get(Run, "q")
        assert run.state == "cancelled" and "reserved employer" in run.reason
        assert s.get(Job, "amzn").status == "ready"


# ----- unattended paths ------------------------------------------------------------------------------------------------


async def test_the_autopilot_dispatches_a_reserved_job_only_after_approval(service, monkeypatch):
    service.dispatch = nothing
    service.db.set_setting("control", {"auto_submit": True})
    cfg = BoardSettings(auto_apply=True)
    monkeypatch.setattr(autopilot, "settings", lambda db: cfg)
    monkeypatch.setattr(autopilot, "replenish_discovery", lambda *args: None)

    async def no_retries(*args):
        return 0

    monkeypatch.setattr(autopilot, "persist_stalled", no_retries)
    add_job(service, "amzn")
    add_job(service, "acme", company="Acme Example", url="https://jobs.example.test/1")
    board = {
        "jobs": [
            {"id": jid, "status": "ready", "score": 90, "title": "Software Development Engineer", "company": company,
             "location": "US", "posted_at": "2026-10-27", "career_level": "New grad"}
            for jid, company in (("amzn", "Amazon"), ("acme", "Acme Example"))
        ]
    }
    monkeypatch.setattr(autopilot, "board_snapshot", lambda *args, **kwargs: board)
    calls = []

    async def queue(job_id, *args, **kwargs):
        calls.append(job_id)
        return {"id": "run-" + job_id}

    monkeypatch.setattr(service, "queue", queue)
    await autopilot.tick(service)
    await autopilot.tick(service)
    assert calls == ["acme"]
    with service.db.exclusive() as s:
        reserved.approve(s, "amzn")
    await autopilot.tick(service)
    assert calls == ["acme", "amzn"]


async def test_stalled_retries_and_the_repairs_hold_leave_reserved_jobs_alone(service, monkeypatch):
    resumed = []

    async def manage(service, job_id, action, **kwargs):
        resumed.append(job_id)
        return {"run_id": "r"}

    monkeypatch.setattr("jobpilot.job_control.manage", manage)
    for jid, company, url in (
        ("amzn", "Amazon", "https://www.amazon.jobs/en/jobs/1"),
        ("acme", "Acme Example", "https://jobs.example.test/1"),
    ):
        with service.db.exclusive() as s:
            s.add(Job(id=jid, identity=jid, url=url, company=company, title="Software Engineer", ats="workday",
                      status="waiting_browser"))
            s.add(Run(id="run-" + jid, job_id=jid, state="waiting_browser", mode="submit", packet={},
                      reason="did not advance", created_at=iso(10), finished_at=iso(9)))
    # A held repair would otherwise keep every new application waiting (owner rule: repairs first).
    assert autopilot.open_repairs(service.db) == 1
    assert await autopilot.persist_stalled(service, 5, [], {}) == 1 and resumed == ["acme"]


async def test_draft_recovery_stops_at_the_gate_and_does_not_spin(service):
    from jobpilot.recovery import retry_due

    service.dispatch = nothing
    service.db.set_setting("control", {"auto_submit": True})
    add_job(service, status="waiting_browser")
    with service.db.exclusive() as s:
        s.add(Run(id="src", job_id="amzn", mode="submit", state="waiting_browser", packet={"match_approved": True}))
    service.db.set_setting("draft_recovery:amzn", {"source_run_id": "src", "cause": "control_observation",
                                                   "scheduled": True, "not_before": iso(5), "attempts": 0})
    assert await retry_due(service) == []
    recovery = service.db.get_setting("draft_recovery:amzn")
    assert recovery["scheduled"] is False and recovery["queue_outcome"]["code"] == "reserved_employer"
    with service.db.session() as s:
        assert s.query(Run).count() == 1


async def test_a_remembered_apply_click_is_dropped_not_retried(service):
    from jobpilot.owner_apply import due

    add_job(service)
    service.db.set_setting("owner_apply:amzn", {"at": iso(5), "code": "worker_capacity", "reason": "All busy"})
    assert await due(service, 5) == 0
    assert service.db.get_setting("owner_apply:amzn") is None


async def test_the_repair_agent_leaves_a_held_job_alone(service):
    from jobpilot.repair_agent import repair_run

    add_job(service, status="waiting_browser")
    with service.db.exclusive() as s:
        s.add(Run(id="r", job_id="amzn", mode="submit", state="waiting_browser", packet={}, reason="did not advance"))
    assert await repair_run(service, "r") == {"skipped": "reserved_employer"}


# ----- the dashboard section ------------------------------------------------------------------------------------------


def seed_dashboard(service):
    """Fresh listings: two held jobs never applied to, a held repair, a held question, a reserved job already applying,
    a confirmed one, and two other employers (one a near-miss name)."""
    jobs = [
        ("amzn", "Amazon", "https://www.amazon.jobs/en/jobs/1", "ready"),
        ("nvda", "Example Staffing", "https://nvidia.wd5.myworkdayjobs.com/External/job/2", "ready"),
        ("pwc", "PricewaterhouseCoopers (PwC)", "https://jobs-us.pwc.com/job/3", "waiting_browser"),
        ("meta", "Meta", "https://www.metacareers.com/jobs/4", "waiting_answer"),
        ("goog", "Google", "https://careers.google.com/jobs/results/5", "queued"),
        ("kpmg", "KPMG LLP", "https://www.kpmg.com/us/en/careers/6", "confirmed"),
        ("acme", "Acme Example", "https://jobs.example.test/7", "ready"),
        ("mview", "Metaview", "https://jobs.ashbyhq.com/metaview/8", "ready"),
    ]
    with service.db.exclusive() as s:
        s.execute(insert(Job), [
            dict(id=jid, identity=jid, url=url, company=company, title=f"Software Engineer {n}", ats="greenhouse",
                 status=status, score=85, demo=False, location="Remote, United States", created_at=iso(100 - n))
            for n, (jid, company, url, status) in enumerate(jobs)
        ])
        s.execute(insert(JobListing), [
            dict(job_id=jid, source_id="src", posted_at=iso(60), date_basis="posted", location="Remote, United States",
                 country="US", career_level="New grad", match_reason="Title matches", active=True,
                 first_seen=iso(60), last_seen=iso(5))
            for jid, *_ in jobs
        ])
        s.add_all([
            Run(id="r-pwc", job_id="pwc", state="waiting_browser", mode="submit", reason="did not advance",
                created_at=iso(30), finished_at=iso(29)),
            Run(id="r-meta", job_id="meta", state="waiting_answer", mode="submit", created_at=iso(20)),
            Run(id="r-goog", job_id="goog", state="queued", mode="submit", created_at=iso(2)),
            Run(id="r-kpmg", job_id="kpmg", state="confirmed", mode="submit", created_at=iso(40),
                receipt={"website_confirmed": True}),
        ])
    service.db.set_setting("job_board_settings", {"enabled": True, "minimum_auto_fit": 70, "max_age_hours": 720})


def test_reserved_jobs_have_their_own_section_and_dashboard_group(client, service):
    seed_dashboard(service)
    snap = client.get("/api/snapshot?include_sources=false").json()
    groups = snap["counts"]["groups"]
    # amzn, nvda, pwc and meta wait for approval; Google's queued run and KPMG's confirmation read as themselves.
    assert groups["reserved"] == 4 and groups["repair"] == 0 and groups["needs"] == 0
    assert groups["active"] == 1 and groups["done"] == 1 and groups["new"] == 2
    assert snap["counts"]["pipeline"] == 2  # held jobs are outside the pipeline, like jobs never started
    # Neither Discovered nor the Batch inventory offers them.
    board = client.get("/api/job-board", params={"include_sources": "false", "page": 1}).json()
    assert {j["id"] for j in board["jobs"]} == {"acme", "mview"}
    assert snap["counts"]["discovered"] == 2

    page = client.get("/api/reserved").json()
    assert [(s["id"], s["label"], s["held"]) for s in page["sections"]] == [
        ("faang", "FAANG", 2), ("mango", "MANGO", 1), ("big_four", "Big Four", 1)
    ]
    assert page["section"] == "faang" and {r["job"]["id"] for r in page["rows"]} == {"amzn", "meta"}
    meta = next(r for r in page["rows"] if r["job"]["id"] == "meta")
    assert meta["reserved"] == {"section": "faang", "label": "FAANG", "employer": "Meta", "approved": False}
    assert meta["run"]["state"] == "waiting_answer"
    assert page["rules"]["sections"][0]["employers"][0]["name"] == "Meta"
    mango = client.get("/api/reserved", params={"section": "mango"}).json()
    assert [r["job"]["id"] for r in mango["rows"]] == ["nvda"]
    searched = client.get("/api/reserved", params={"section": "faang", "q": "engineer 3"}).json()
    # A search narrows the rows; each section still counts every job "Approve all" would approve.
    assert [r["job"]["id"] for r in searched["rows"]] == ["meta"] and searched["sections"][0]["held"] == 2
    listed = client.get("/api/jobs", params={"group": "reserved"}).json()
    assert listed["total"] == 4 and all(r["reserved"]["approved"] is False for r in listed["rows"])
    every = client.get("/api/jobs").json()
    assert {r["job"]["id"] for r in every["rows"]} == {"goog", "kpmg"}
    assert client.get("/api/jobs/pwc").json()["reserved"]["employer"] == "PwC"

    # Approving a whole section lifts the hold; nothing starts from that click.
    assert client.post("/api/reserved/approve-all", json={"section": "big_four"}).json()["approved"] == 1
    after = client.get("/api/snapshot?include_sources=false").json()["counts"]["groups"]
    assert after["reserved"] == 3 and after["repair"] == 1
    assert {s["id"]: s["held"] for s in client.get("/api/reserved").json()["sections"]}["big_four"] == 0
    with service.db.session() as s:
        assert s.query(Run).count() == 4
    # The rest, every section: the jobs waiting, not Google's run already under way or KPMG's confirmed one.
    assert client.post("/api/reserved/approve-all", json={}).json()["approved"] == 3
    assert client.get("/api/snapshot?include_sources=false").json()["counts"]["groups"]["reserved"] == 0


def test_the_owner_edits_the_reserved_employers(client, service):
    add_job(service, "amzn")
    add_job(service, "acme", company="Acme Example", url="https://jobs.example.test/1")
    rules = client.get("/api/reserved").json()["rules"]
    rules["sections"][0]["employers"] = [e for e in rules["sections"][0]["employers"] if e["name"] != "Amazon"]
    rules["sections"].append(
        {"id": "watch", "label": "Watch list", "employers": [{"name": "Acme Example", "aliases": [], "sites": []}]}
    )
    saved = client.put("/api/reserved", json=rules)
    assert saved.status_code == 200, saved.text
    assert {s["id"]: s["held"] for s in saved.json()["sections"]} == {"faang": 0, "mango": 0, "big_four": 0, "watch": 1}
    with service.db.session() as s:
        assert set(reserved.held(s)) == {"acme"}
    bad = client.put("/api/reserved", json={"sections": [
        {"id": "x", "label": "X", "employers": [{"name": "Bad", "sites": ["not a site"]}]}
    ]})
    assert bad.status_code == 422
    off = client.put("/api/reserved", json={**rules, "enabled": False}).json()
    assert off["enabled"] is False and off["sections"] == []
    with service.db.session() as s:
        assert reserved.held(s) == {}
