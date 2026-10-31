"""Test connection: each way a database can be unusable is found before anything changes, in words a person can act on (2026-10-31)."""

import pytest

from database_support import scratch_pg  # noqa: F401 - the fixture
from jobpilot.storage import check, choice


@pytest.fixture
def fresh(scratch_pg):
    return scratch_pg, scratch_pg.create("check")


def by_id(report):
    return {c["id"]: c for c in report["checks"]}


def test_a_good_database_passes_every_check_and_reports_what_it_measured(fresh):
    scratch, name = fresh
    report = check.run("server", scratch.url(name))
    found = by_id(report)
    assert report["ok"], [c for c in report["checks"] if c["status"] == "fail"]
    for key in ("string", "address", "connect", "version", "session", "permissions", "exposure", "durability", "limit", "latency"):
        assert found[key]["status"] in {"ok", "warn"}, found[key]
    assert found["tls"]["status"] == "warn", "loopback without TLS is allowed and said so"
    facts = report["facts"]
    assert facts["server_version"].startswith("17") and facts["latency"]["median_ms"] >= 0 and facts["connections_allowed"] >= 6
    assert report["pools"] == choice.size_pools(facts["connections_allowed"])
    assert report["workers"]["workers"] == 9, "a round trip of well under a millisecond is fast enough for every worker"


def test_the_test_leaves_nothing_behind(fresh):
    scratch, name = fresh
    check.run("server", scratch.url(name))
    assert scratch.sql(name, "select count(*) from pg_namespace where nspname like 'axiom%'")[0][0] == 0


def test_failures_are_explained_and_never_echo_the_password(fresh):
    scratch, name = fresh
    good = scratch.url(name)
    cases = [
        ("server", good.replace("scratchpw", "wrongpw"), "connect", "rejected the user name or password"),
        ("server", good.rsplit("/", 1)[0] + "/nodatabase", "connect", "database does not exist"),
        ("server", good.replace("@127.0.0.1:", "@nohost.invalid:"), "address", "Could not find the host"),
        ("supabase", good.replace(f":{scratch.port}/", ":6543/"), "string", "transaction pooler"),
    ]
    for mode, url, id, words in cases:
        report = check.run(mode, url)
        assert not report["ok"] and words in by_id(report)["%s" % id]["detail"], (id, report["checks"])
        assert "scratchpw" not in str(report) and "wrongpw" not in str(report)


def test_nothing_listening_is_said_plainly(fresh):
    scratch, name = fresh
    report = check.run("server", scratch.url(name).replace(f":{scratch.port}/", ":1/"))
    assert "Nothing is listening at 127.0.0.1:1" in by_id(report)["connect"]["detail"]


def test_a_transaction_mode_pooler_is_recognised_by_its_behaviour():
    """Port 6543 is refused by name; a pooler on another port is found because consecutive statements hit different backends."""

    class Cursor:
        def __init__(self):
            self.pids = iter([11, 12, 11, 13, 12])

        def execute(self, sql, *args):
            self.last = sql
            return self

        def fetchone(self):
            return (next(self.pids), "axiom-probe") if "pg_backend_pid" in self.last else None

    report = check.Report()
    check.pooler(report, None, Cursor())
    assert report.checks[0].status == "fail" and "transaction-mode pooler" in report.checks[0].detail
    assert "Session pooler" in report.checks[0].detail


def test_a_database_the_public_api_could_read_is_refused(fresh):
    scratch, name = fresh
    scratch.sql(name, "create schema axiom")
    scratch.sql(name, "grant usage on schema axiom to anon")
    report = check.run("server", scratch.url(name))
    assert not report["ok"] and "anon role can use the axiom schema" in by_id(report)["exposure"]["detail"]
    scratch.sql(name, "revoke usage on schema axiom from anon")
    assert by_id(check.run("server", scratch.url(name)))["exposure"]["status"] == "ok"


def test_tables_of_ours_in_public_are_refused(fresh):
    scratch, name = fresh
    scratch.sql(name, "create table public.runs (id text)")
    report = check.run("server", scratch.url(name))
    assert not report["ok"] and "public schema" in by_id(report)["exposure"]["detail"]


def test_a_user_who_may_not_create_the_schema_is_told_so(fresh):
    scratch, name = fresh
    report = check.run("server", scratch.url(name, user="limited", password="limitedpw"))
    assert not report["ok"] and "may not create the schema" in by_id(report)["permissions"]["detail"]
    scratch.sql(name, "create schema axiom")
    scratch.sql(name, "grant all on schema axiom to limited")
    assert by_id(check.run("server", scratch.url(name, user="limited", password="limitedpw")))["permissions"]["status"] == "ok"


def test_too_few_connections_is_a_failure_with_the_count(fresh):
    scratch, name = fresh
    report = check.run("server", scratch.url(name, user="tight", password="tightpw"))
    found = by_id(report)["limit"]
    assert found["status"] == "fail" and "Only 4 connection(s)" in found["detail"] and "at least 6" in found["detail"]


def test_an_old_server_is_refused(fresh, monkeypatch):
    scratch, name = fresh
    monkeypatch.setattr(check, "MIN_VERSION", 990000)
    assert "too old" in by_id(check.run("server", scratch.url(name)))["version"]["detail"]


def test_a_slow_link_gets_an_honest_verdict_for_the_workers():
    """Measured 2026-10-31: at 30 ms an application took 3 times as long and the event loop waited on the database for a fifth of every minute."""
    assert [check.rate_workers(ms)["workers"] for ms in (2, 10, 15, 30, 100, 400)] == [9, 9, 6, 3, 1, 1]
    assert "Nine would leave the browsers waiting" in check.rate_workers(30)["text"]


@pytest.mark.parametrize(
    "message, words",
    [
        ("FATAL: Tenant or user not found", "postgres.<project-ref>"),
        ("connection failed: Network is unreachable", "IPv6-only"),
        ("connection failed: timeout expired", "No answer from"),
        ('FATAL: no pg_hba.conf entry for host "1.2.3.4"', "pg_hba.conf"),
        ("server does not support SSL, but SSL was required", "does not support encrypted"),
        ("FATAL: sorry, too many clients already", "no free connections"),
    ],
)
def test_the_usual_connection_errors_have_a_plain_explanation(message, words):
    assert words in check.explain(Exception(message), "db.example.org", 5432)
