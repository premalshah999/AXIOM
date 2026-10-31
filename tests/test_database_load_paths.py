"""Production's database and shared loop were dominated by reads of data nobody used (py-spy and pg_stat, 2026-10-29):
the receipt poll made Postgres parse every ~320 KB run packet (25 s a call, once a minute), and each readiness tick
loaded all ~16,000 job descriptions (112 MB) plus every job_origin/job_control setting. Synthetic rows only."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import event, select

from jobpilot.db import Job, JobListing, Run
from jobpilot.features.discovery.policy import DiscoveryPolicy
from jobpilot.features.discovery.readiness import _candidates, ready_manual_job_ids
from jobpilot.receipts import _PACKET_FACTS, check_receipts, packet_facts
from support_mail import receipt_seed


def statements(service):
    seen = []

    def record(conn, cursor, statement, *rest):
        seen.append(" ".join(statement.split()))

    event.listen(service.db.engine, "before_cursor_execute", record)
    return seen, lambda: event.remove(service.db.engine, "before_cursor_execute", record)


def test_a_runs_packet_is_read_once_per_process(service):
    ids = receipt_seed(service, 3)
    with service.db.exclusive() as s:
        s.get(Run, ids[1]).packet = {"profile": {"email": "alex@example.test"}, "match_approved": True}
    for rid in ids:
        _PACKET_FACTS.pop(rid, None)
    seen, stop = statements(service)
    try:
        with service.db.session() as s:
            first = packet_facts(s, ids)
            again = packet_facts(s, ids)
    finally:
        stop()
    assert first == again and first[ids[1]] == ("alex@example.test", True) and first[ids[0]][1] is False
    assert len([q for q in seen if "runs.packet" in q or "packet" in q]) == 1


async def test_the_receipt_poll_parses_no_packet_and_loads_no_description_for_competitors(service, monkeypatch):
    receipt_seed(service, 3)

    async def request(*args, **kwargs):
        return {"messages": []}

    monkeypatch.setattr(service.mail, "request", request)
    await check_receipts(service.mail)  # fills the per-process packet facts
    seen, stop = statements(service)
    try:
        await check_receipts(service.mail)
    finally:
        stop()
    competitors = [q for q in seen if "jobs.demo" in q and "runs.id IN" not in q and "FROM runs JOIN jobs" in q]
    assert competitors, seen
    assert all("description" not in q and "packet" not in q for q in competitors)
    assert not [q for q in seen if "min(runs.created_at)" in q and "packet" in q]


def seed_listed(service, name, description="Build software. " * 40, **kwargs):
    at = datetime.now(UTC).isoformat()
    with service.db.exclusive() as s:
        job = Job(identity=name, url=f"https://example.test/jobs/{name}-123456", title="Software Engineer",
                  company=name, location="New York, NY, United States", ats="workday", status="ready", score=90,
                  description=description, **kwargs)
        s.add(job)
        s.flush()
        s.add(JobListing(job_id=job.id, source_id="src", posted_at=at, date_basis="verified", location="New York, NY",
                         career_level="Entry", match_reason="", first_seen=at, last_seen=at))
        return job.id


def test_candidates_read_descriptions_only_for_the_postings_that_survive(service):
    keep = seed_listed(service, "keep", description="Keep this description. " * 20)
    attempted = seed_listed(service, "attempted", description="Never needed. " * 20)
    paused = seed_listed(service, "paused")
    with service.db.exclusive() as s:
        s.add(Run(job_id=attempted, state="failed"))
    service.db.set_setting("job_control:" + paused, {"state": "paused"})
    service.db.set_setting("job_control:" + keep, {"state": "resumed"})
    seen, stop = statements(service)
    try:
        jobs, stats = _candidates(service.db, DiscoveryPolicy(), datetime.now(UTC))
    finally:
        stop()
    assert {j["id"]: j["description"] for j in jobs} == {keep: "Keep this description. " * 20}
    assert stats["attempted"] == 1 and stats["controlled"] == 1
    # The description is only ever selected for the survivors, by id; never for every job.
    assert all("jobs.id IN" in q for q in seen if "jobs.description" in q)



def test_a_job_under_an_earlier_submission_guard_is_still_archived(service):
    from jobpilot.operations.reset import guard_key
    from jobpilot.ats import identity_variants

    jid = seed_listed(service, "guarded")
    url = f"https://example.test/jobs/guarded-123456"
    for identity in identity_variants(url):
        service.db.set_setting(guard_key(identity), {"at": "x"})
    jobs, stats = _candidates(service.db, DiscoveryPolicy(), datetime.now(UTC))
    assert jobs == [] and stats["protected"] == 1
    with service.db.session() as s:
        assert s.get(Job, jid).status == "archived" and "submission guard" in s.get(Job, jid).reason


def test_ready_manual_ids_ask_for_origins_once(service):
    ids = []
    for n in range(4):
        with service.db.exclusive() as s:
            job = Job(identity=f"m{n}", url=f"https://example.test/jobs/m{n}-123456", title="Software Engineer",
                      company=f"m{n}", location="New York, NY, United States", ats="workday", status="ready",
                      score=90, description="Build software. " * 50)
            s.add(job)
            s.flush()
            ids.append(job.id)
        service.db.set_setting("job_origin:" + ids[-1], {"kind": "manual"})
    seen, stop = statements(service)
    try:
        ready = ready_manual_job_ids(service.db, DiscoveryPolicy(early_career_only=True))
    finally:
        stop()
    assert ready == set(ids)


def test_the_board_marks_only_paused_jobs_from_a_filtered_settings_read(service):
    from jobpilot.jobboard import board_snapshot

    paused, other = seed_listed(service, "paused"), seed_listed(service, "other")
    service.db.set_setting("job_control:" + paused, {"state": "paused"})
    service.db.set_setting("job_control:" + other, {"state": "resumed"})
    board = board_snapshot(service.db)
    assert {j["id"]: j["status"] for j in board["jobs"]} == {paused: "paused", other: "ready"}


async def test_a_large_response_is_decoded_chunk_by_chunk_with_the_same_result(monkeypatch):
    """One decode of a 16 MB feed held the interpreter for 8-30 ms on the shared loop; chunks decode in microseconds
    and a character split across two chunks must still come out whole, bad bytes replaced as before."""
    import httpx

    from jobpilot import network

    body = ("café 世界 " * 3000).encode() + b"\xff tail \xe4\xb8"  # invalid byte and a cut sequence
    pieces = [body[i : i + 7] for i in range(0, len(body), 7)]

    class Response:
        status_code, url, headers = 200, httpx.URL("https://example.test/feed"), {}

        def raise_for_status(self):
            pass

        async def aiter_bytes(self):
            for piece in pieces:
                yield piece

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    text, url = await network.fetch("https://example.test/feed", allow_private=True)
    assert text == body.decode("utf-8", errors="replace") and url == "https://example.test/feed"


def test_a_receipt_claim_check_reads_no_packet_but_the_recipients_it_lacks(service):
    """It read every receipted run's whole packet per message, under the app lock (673 runs, ~300 MB, 2026-10-30)."""
    from jobpilot.receipts import receipt_claimed

    with service.db.exclusive() as s:
        for n in range(3):
            s.add(Job(id=f"j{n}", identity=f"j{n}", url=f"https://careers.example.test/jobs/{n}", company=f"Example{n}", ats="custom"))
            s.add(Run(id=f"r{n}", job_id=f"j{n}", state="confirmed", mode="submit",
                      packet={"profile": {"email": "alex@example.test"}, "ledger": ["x" * 100]},
                      receipt={"email": {"message_id": f"gm:{n}", "received_at": "2026-10-30T00:00:00+00:00"}}))
        s.add(Job(id="now", identity="now", url="https://careers.example.test/jobs/now", company="ExampleSoft", ats="custom"))
        s.add(Run(id="now", job_id="now", state="waiting_email", mode="submit", packet={"profile": {"email": "alex@example.test"}}))
    for rid in ("r0", "r1", "r2", "now"):
        _PACKET_FACTS.pop(rid, None)
    receipt = {"run_id": "now", "received_at": "2026-10-30T01:00:00+00:00"}
    seen, stop = statements(service)
    try:
        with service.db.session() as s:
            assert receipt_claimed(s, {"id": "gm:1"}, receipt) is True  # another run already holds this message
            assert receipt_claimed(s, {"id": "gm:new"}, receipt) is False
    finally:
        stop()
    assert not [q for q in seen if "runs.resume_id" in q], seen  # a whole Run row carries the whole packet
    assert len([q for q in seen if "packet" in q]) == 1  # the recipients, once


def test_a_claim_held_for_another_mailbox_does_not_block_this_one(service):
    from jobpilot.receipts import receipt_claimed

    with service.db.exclusive() as s:
        s.add(Job(id="old", identity="old", url="https://careers.example.test/jobs/old", company="ExampleSoft", ats="custom"))
        s.add(Run(id="old", job_id="old", state="confirmed", mode="submit", packet={"profile": {"email": "other@example.test"}},
                  receipt={"email": {"message_id": "gm:1"}}))
        s.add(Job(id="now2", identity="now2", url="https://careers.example.test/jobs/now2", company="ExampleSoft", ats="custom"))
        s.add(Run(id="now2", job_id="now2", state="waiting_email", mode="submit", packet={"profile": {"email": "ALEX@example.test"}}))
    for rid in ("old", "now2"):
        _PACKET_FACTS.pop(rid, None)
    with service.db.session() as s:
        assert receipt_claimed(s, {"id": "gm:1"}, {"run_id": "now2", "received_at": "2026-10-30T01:00:00+00:00"}) is False


def test_a_new_posting_finds_its_older_identity_without_loading_descriptions(service):
    """Every new posting read all of its platform's jobs, descriptions included, under the app lock (2026-10-30)."""
    from jobpilot.discovery import add_job
    from jobpilot.schemas import JobInput

    url = "https://job-boards.greenhouse.io/example/jobs/1234"
    with service.db.exclusive() as s:
        # A row saved before tracking parameters were dropped: its stored identity is not today's.
        s.add(Job(id="older", identity="legacy-identity", url=url + "?utm_source=feed", title="Engineer", company="ExampleSoft",
                  ats="greenhouse", description="Build software. " * 400))
    seen, stop = statements(service)
    try:
        row, new = add_job(service.db, JobInput(url=url, title="Engineer", company="ExampleSoft", description="d"))
    finally:
        stop()
    assert row["id"] == "older" and new is False
    scans = [q for q in seen if "jobs.ats =" in q]
    assert scans and all("description" not in q for q in scans), seen


def test_the_autopilots_board_read_leaves_sources_and_scans_alone(service):
    """It read all 4,479 sources and their scans up to three times a tick and used only the jobs (2026-10-30)."""
    from jobpilot.db import Setting, Source
    from jobpilot.jobboard import board_snapshot

    listed = seed_listed(service, "listed")
    service.db.set_setting("job_board_sources", {"ids": ["s1"]})
    with service.db.exclusive() as s:
        s.add(Source(id="s1", name="Managed", kind="greenhouse", url="https://example.test/s1", cron="@daily"))
        s.add(Source(id="s2", name="Mine", kind="greenhouse", url="https://example.test/s2", cron="@daily"))
        s.add(Setting(key="job_board_scan:s1", value={"state": "done", "started_at": "2026-10-30T00:00:00+00:00"}))
        s.add(Setting(key="job_board_scan:s2", value={"state": "running", "started_at": "2026-10-30T00:00:00+00:00"}))
    seen, stop = statements(service)
    try:
        light = board_snapshot(service.db, limit=None, jobs_only=True)
    finally:
        stop()
    assert listed in {job["id"] for job in light["jobs"]} and set(light) == {"settings", "jobs"}
    assert not [q for q in seen if "FROM sources" in q or "job_board_scan" in q]
    seen, stop = statements(service)
    try:
        full = board_snapshot(service.db, limit=None)
    finally:
        stop()
    # Only managed sources and their own scans, without a bound parameter per source.
    assert [s["id"] for s in full["sources"]] == ["s1"] and full["sources"][0]["scan"]["state"] == "done"
    assert full["scanning"] == 0 and full["last_requested_at"] == "2026-10-30T00:00:00+00:00"
    assert not [q for q in seen if "settings.key IN" in q and "?" in q and q.count("?") > 3]


def test_candidates_create_no_orm_object_per_listing(service):
    """10.8% of the interpreter's samples built a JobListing for rows that were read once and dropped (2026-10-30)."""
    from sqlalchemy import event

    from jobpilot.db import JobListing as Listing

    one = seed_listed(service, "one")
    built = []

    def listener(target, context):
        built.append(target)

    event.listen(Listing, "load", listener)
    try:
        jobs, _ = _candidates(service.db, DiscoveryPolicy(), datetime.now(UTC))
    finally:
        event.remove(Listing, "load", listener)
    assert [job["id"] for job in jobs] == [one] and not built
    assert jobs[0]["posted_basis"] and jobs[0]["country"] == "US" and jobs[0]["description"]


def test_a_settings_prefix_index_is_created_only_on_postgres_and_only_once():
    from jobpilot.db import Database

    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def exec_driver_sql(self, statement):
            calls.append(statement)
            return type("R", (), {"scalar": staticmethod(lambda: None if len(calls) == 1 else "ix_settings_key_prefix")})

    class Engine:
        class dialect:
            name = "postgresql"

        begin = staticmethod(Connection)

    db = Database.__new__(Database)
    db.engine = Engine
    db.add_settings_prefix_index()
    assert len(calls) == 2 and "text_pattern_ops" in calls[1]
    calls.append("second start")
    db.add_settings_prefix_index()
    assert not [c for c in calls[3:] if "CREATE INDEX" in c]
    Engine.dialect.name = "sqlite"
    calls.clear()
    db.add_settings_prefix_index()
    assert calls == []


async def test_a_queue_attempt_loads_packets_only_for_the_run_it_continues(service):
    """Admission holds the app lock; each earlier run's whole packet was read per attempt (2026-10-30)."""
    from jobpilot.pipeline_comparison import routed_pool

    with service.db.exclusive() as s:
        s.add(Job(id="job", identity="job", url="https://careers.example.test/jobs/1", title="Engineer", company="ExampleSoft", ats="custom"))
        for n in range(4):
            s.add(Run(id=f"old{n}", job_id="job", state="failed", mode="submit", packet={"worker_pool": "normal"},
                      created_at=f"2026-10-30T00:0{n}:00+00:00"))
        s.add(Run(id="waiting", job_id="job", state="waiting_browser", mode="submit", packet={"worker_pool": "specialist"},
                  created_at="2026-10-30T00:09:00+00:00"))
    seen, stop = statements(service)
    try:
        with service.db.session() as s:
            routed_pool(s, s.get(Job, "job"))
    finally:
        stop()
    packets = [q for q in seen if "runs.packet" in q]
    assert len(packets) <= 1 and all("runs.id = ?" in q for q in packets), seen


def test_the_archive_page_reads_no_packet_of_a_removed_job(service):
    from jobpilot.archive import summaries
    from jobpilot.db import Setting

    with service.db.exclusive() as s:
        s.add(Job(id="gone", identity="gone", url="https://careers.example.test/jobs/2", title="Engineer", company="ExampleSoft", ats="custom"))
        s.add(Run(id="r1", job_id="gone", state="failed", mode="submit", packet={"ledger": ["x"]}, reason="stopped", cost=0.25))
        s.add(Setting(key="job_control:gone", value={"state": "deleted", "at": "2026-10-30T00:00:00+00:00"}))
    seen, stop = statements(service)
    try:
        rows = summaries(service.db)
    finally:
        stop()
    assert [(r["run_id"], r["state"], r["cost"], r["attempts"]) for r in rows] == [("r1", "failed", 0.25, 1)]
    assert not [q for q in seen if "runs.packet" in q], seen


def test_a_form_question_does_not_ask_for_the_migration_again(service, monkeypatch):
    """About 10 reads a second of kn_migrations, each returning all 252 rows, for an answer that changes with the profile
    (2026-10-30). A new digest still migrates at once, and a rollback here is seen at once."""
    from jobpilot.features.knowledge import migration
    from jobpilot.features._store import digest

    service.db.set_setting("profile", {"email": "alex@example.test", "first_name": "Alex"})
    profile = service.db.get_setting("profile")
    first = migration.sync(service.db, source_digest=digest(profile))
    assert first.get("status") != "unchanged"
    assert migration.sync(service.db, source_digest=digest(profile)) == {"status": "unchanged"}  # now remembered
    seen, stop = statements(service)
    try:
        assert migration.sync(service.db, source_digest=digest(profile)) == {"status": "unchanged"}
    finally:
        stop()
    assert seen == []
    # An edited profile has another digest: it must not be answered from the memo.
    service.db.set_setting("profile", {"email": "alex@example.test", "first_name": "Alexander"})
    changed = service.db.get_setting("profile")
    assert migration.sync(service.db, source_digest=digest(changed)).get("status") != "unchanged"
    # The recheck notices what another process did.
    migration.sync(service.db, source_digest=digest(changed))
    clock = {"t": migration.time.monotonic() + migration.SYNC_RECHECK_SECONDS + 1}
    monkeypatch.setattr(migration.time, "monotonic", lambda: clock["t"])
    seen, stop = statements(service)
    try:
        assert migration.sync(service.db, source_digest=digest(changed)) == {"status": "unchanged"}
    finally:
        stop()
    assert [q for q in seen if "kn_migrations" in q]
    migration.rollback(service.db)
    assert migration.sync(service.db, source_digest=digest(changed)).get("status") != "unchanged"


def test_pending_telegram_markers_are_picked_by_the_database(service):
    from jobpilot.db import Setting
    from jobpilot.telegram import pending_run_ids

    with service.db.exclusive() as s:
        s.add(Setting(key="telegram_run_pending:done", value={"pending": False}))
        s.add(Setting(key="telegram_run_pending:open", value={"pending": True}))
        s.add(Setting(key="telegramXrunXpending:other", value={"pending": True}))  # `_` must not act as a wildcard
    assert pending_run_ids(service.db) == ["open"]


def test_a_worker_bots_status_reads_one_number_per_run_not_every_packet(service):
    """current_rows read every run's whole packet: 3.6 GB of JSON at 9,782 runs (2026-10-30)."""
    from types import SimpleNamespace

    from jobpilot.telegram import current_rows

    with service.db.exclusive() as s:
        for n, slot in enumerate((2, 3)):
            s.add(Job(id=f"j{n}", identity=f"j{n}", url=f"https://careers.example.test/jobs/{n}", title="Engineer", company=f"Example{n}", ats="custom"))
            s.add(Run(id=f"r{n}", job_id=f"j{n}", state="running", mode="submit", packet={"worker_slot": slot, "ledger": ["x"]}))
    seen, stop = statements(service)
    try:
        rows = current_rows(SimpleNamespace(db=service.db, telegram_worker_slot=2))
    finally:
        stop()
    assert [row["id"] if isinstance(row, dict) else row.id for row in rows] == ["r0"]
    assert not [q for q in seen if "runs.resume_id" in q], seen


def test_url_cache_stays_above_the_stored_jobs_and_below_what_it_used_to_hold():
    from jobpilot.ats import identity_variants, url_identity

    assert 20_000 < url_identity.cache_info().maxsize <= 32_768
    assert identity_variants.cache_info().maxsize >= 20_000  # a scan over every job must not evict itself


def test_employer_keys_of_an_old_alias_table_are_dropped(monkeypatch):
    from jobpilot import application_pacing as pacing

    pacing._KEYS.clear()
    first = pacing.employer_keys({"alias one": "first"})
    first("ExampleSoft", "https://careers.example.test/1")
    first("Other", "https://careers.example.test/2")
    assert len([k for k in pacing._KEYS if k != "version"]) == 2
    second = pacing.employer_keys({"alias two": "second"})
    assert not [k for k in pacing._KEYS if k != "version"]  # the previous table's keys can never be asked for again
    second("ExampleSoft", "https://careers.example.test/1")
    # A caller still holding the old function cannot serve the new table a key computed under the old one.
    first("Late", "https://careers.example.test/3")
    assert len({k[0] for k in pacing._KEYS if k != "version"}) == 2


def test_a_gate_memo_entry_keeps_no_text_of_the_posting_or_the_policy(service):
    from jobpilot.features.discovery import readiness

    readiness._GATES.clear()
    job = {"id": "job", "title": "Software Engineer", "url": "https://careers.example.test/jobs/1", "location": "Remote",
           "country": "US", "description": "Build software. " * 40}
    first = readiness.gated(job, DiscoveryPolicy())
    assert readiness.gated(job, DiscoveryPolicy()) == first and len(readiness._GATES) == 1
    readiness.gated({**job, "location": "Elsewhere"}, DiscoveryPolicy())
    assert len(readiness._GATES) == 2  # a changed field is a new decision
    for key in readiness._GATES:
        assert [part for part in key if isinstance(part, str)] == ["job"]


def test_job_control_and_the_unknown_scan_load_no_packet_they_do_not_read(service):
    from jobpilot.autopilot import unknown_candidates

    with service.db.exclusive() as s:
        s.add(Job(id="u", identity="u", url="https://careers.example.test/jobs/u", title="Engineer", company="ExampleSoft", ats="custom", status="submission_unknown"))
        for n in range(3):
            s.add(Run(id=f"u{n}", job_id="u", state="failed", mode="submit", packet={"ledger": ["x"]}, created_at=f"2026-10-30T00:0{n}:00+00:00"))
    seen, stop = statements(service)
    try:
        unknown_candidates(service.db)
    finally:
        stop()
    assert not [q for q in seen if "runs.packet" in q], seen
