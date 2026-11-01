"""What an upgrade or a rollback rests on, without bundles: scripts/dev/upgrade_check.py proves the same on real builds."""

from pathlib import Path

import pytest
from sqlalchemy import text

from jobpilot.db import ADDED_COLUMNS, Base, Database, Job, Run
from jobpilot.features._store import FeatureBase
from jobpilot.native import smoke

pytestmark = pytest.mark.integration


def test_a_column_a_build_adds_lets_the_previous_build_keep_writing():
    """A rollback runs the old code on the new schema. Migrations only add, and a column the old code does not know must take its
    inserts: nothing added may be NOT NULL without a default, or the previous build could no longer save anything to that table."""
    for table, columns in ADDED_COLUMNS.items():
        for name, kind in columns.items():
            assert "not null" not in kind.lower() or "default" in kind.lower(), f"{table}.{name}"
            declared = Base.metadata.tables[table].columns[name]
            assert declared.nullable or declared.server_default is not None, f"{table}.{name}"


def test_a_migration_that_fails_half_way_is_finished_by_the_next_start(service, monkeypatch):
    """Each added column is its own transaction, and asks first, so the next start does what the last one could not."""
    db = service.db
    with db.engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE half (id TEXT)")
        connection.exec_driver_sql("INSERT INTO half VALUES ('kept')")
    monkeypatch.setitem(ADDED_COLUMNS, "half", {"first": "TEXT", "second": "NOT VALID SQL HERE", "third": "TEXT"})
    with pytest.raises(Exception):
        db.add_missing_columns()
    with db.engine.connect() as connection:
        names = [row[1] for row in connection.exec_driver_sql("PRAGMA table_info(half)")]
    assert "first" in names and "second" not in names  # Half way: one applied, one refused.
    monkeypatch.setitem(ADDED_COLUMNS, "half", {"first": "TEXT", "second": "TEXT", "third": "TEXT"})
    db.add_missing_columns()
    with db.engine.connect() as connection:
        names = [row[1] for row in connection.exec_driver_sql("PRAGMA table_info(half)")]
        assert connection.execute(text("select id from half")).scalar() == "kept"
    assert {"first", "second", "third"} <= set(names)


def test_starting_twice_on_the_same_database_changes_no_row(config):
    """Every start runs the schema step; on a database that has it all, it must be nothing."""
    db = Database(config.database_url)
    with db.exclusive() as s:
        s.add(Job(id="j", identity="j", url="https://boards.example.test/1", ats="greenhouse", title="T", company="Acme"))
        s.add(Run(id="r", job_id="j", mode="submit", state="confirmed", packet={"a": 1}))
    Database(config.database_url)
    with db.session() as s:
        assert s.get(Run, "r").packet == {"a": 1} and s.get(Job, "j").title == "T"


def test_the_smoke_check_names_what_the_database_lacks_and_ignores_what_only_it_has():
    expected = {"runs": {"id", "state"}, "jobs": {"id"}}
    assert smoke.schema_gaps(expected, {"runs": {"id", "state", "from_a_newer_build"}, "jobs": {"id"}}) == []
    assert smoke.schema_gaps(expected, {"runs": {"id"}}) == ["jobs", "runs.state"]


def test_the_smoke_check_knows_every_table_the_code_declares():
    tables = smoke.expected_schema()
    assert {"jobs", "runs", "settings"} <= set(tables) and "state" in tables["runs"]
    assert set(FeatureBase.metadata.tables) <= set(tables) and any(name.startswith("kn_") for name in tables)


def test_the_smoke_check_finds_the_dashboards_files_and_what_its_styles_ask_for():
    html = '<script type="module" src="/assets/index-a1.js"></script><link rel="stylesheet" href="/assets/index-b2.css"><a href="/x">'
    assert smoke.asset_paths(html) == ["/assets/index-a1.js", "/assets/index-b2.css"]
    assert smoke.css_urls('@font-face{src:url("/assets/f.woff2?v=1") format("woff2"),url(/assets/g.ttf#x)}') == ["/assets/f.woff2", "/assets/g.ttf"]


@pytest.mark.parametrize(
    "result, words",
    [
        ({"dry_run": {"state": "needs_review", "reason": "x"}, "submit": {"state": "confirmed", "received": 1}, "fields": True}, "dry run ended"),
        ({"dry_run": {"state": "dry_run_passed", "received": 1}, "submit": {"state": "confirmed", "received": 1}, "fields": True}, "dry run sent"),
        ({"dry_run": {"state": "dry_run_passed"}, "submit": {"state": "confirmed", "received": 2}, "fields": True}, "received 2"),
        ({"dry_run": {"state": "dry_run_passed"}, "submit": {"state": "confirmed", "received": 1}}, "name"),
        ({"dry_run": {"state": "dry_run_passed", "model_calls": 1}, "submit": {"state": "confirmed", "received": 1}, "fields": True}, "model"),
    ],
)
def test_the_dry_fill_fails_on_anything_but_one_clean_application(result, words):
    assert any(words in problem for problem in smoke.judge_fill(result))


def test_the_dry_fill_passes_one_clean_application():
    good = {"dry_run": {"state": "dry_run_passed", "received": 0}, "submit": {"state": "confirmed", "received": 1}, "fields": True}
    assert smoke.judge_fill(good) == []


def test_the_isolated_copy_inherits_nothing_of_the_running_apps(monkeypatch):
    for name in ("DATABASE_URL", "APP_TOKEN", "BROWSER_CDP_URL", "MIMO_API_KEY", "DEEPSEEK_API_KEY", "AXIOM_BUNDLE", "AXIOM_CONFIG_DIR"):
        monkeypatch.setenv(name, "from-the-running-app")
    env = smoke.child_environment(Path("/tmp/x"))
    assert not {"DATABASE_URL", "APP_TOKEN", "BROWSER_CDP_URL", "MIMO_API_KEY", "DEEPSEEK_API_KEY", "AXIOM_BUNDLE", "AXIOM_CONFIG_DIR"} & set(env)
    assert env["ENABLE_WORKERS"] == "false" and env["DATA_DIR"] == "/tmp/x/data"
