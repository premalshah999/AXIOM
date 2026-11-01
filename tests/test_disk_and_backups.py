"""A nearly full disk stops new work, and the database is copied daily without filling it."""

def test_a_nearly_full_disk_starts_nothing_new_and_says_why(service, monkeypatch):
    from jobpilot import host_health

    monkeypatch.setattr(host_health.shutil, "disk_usage", lambda path: type("U", (), {"free": 1024**3})())
    host_health.disk.watch(service.config.data_dir)
    gate = host_health.check(4, db=service.db)
    assert gate.limit == 0 and gate.code == "disk_low" and "1.0 GB" in gate.reason
    monkeypatch.setattr(host_health.shutil, "disk_usage", lambda path: type("U", (), {"free": 50 * 1024**3})())
    host_health.disk.watch(service.config.data_dir)
    assert host_health.check(4, db=service.db).code != "disk_low"


def test_daily_backups_keep_the_newest_never_a_hand_made_copy_and_skip_a_full_disk(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from jobpilot.native import backups

    monkeypatch.setattr(backups.Path, "home", lambda: tmp_path)
    where = backups.folder()
    where.mkdir()
    (where / "axiom-mine.dump").write_bytes(b"x")
    for day in range(1, 11):
        (where / f"{backups.PREFIX}202611{day:02}-000000.dump").write_bytes(b"x")
    assert len(backups.prune()) == 3
    assert (where / "axiom-mine.dump").exists() and (where / f"{backups.PREFIX}20261110-000000.dump").exists()
    assert not (where / f"{backups.PREFIX}20261101-000000.dump").exists()
    assert backups.room_for_one(free=1024**3)
    assert backups.room_for_one(free=50 * 1024**3) is None

    cfg = SimpleNamespace(home=tmp_path)
    assert backups.due(cfg, now=1_000_000.0)
    backups.record(cfg, file="x")
    at = backups.read_marker(cfg)["at"]
    assert not backups.due(cfg, now=at + 3600) and backups.due(cfg, now=at + 24 * 3600)
    backups.record(cfg, skipped="full")
    assert backups.due(cfg, now=backups.read_marker(cfg)["at"] + backups.RETRY_SECONDS)
    assert (tmp_path / "backup.json").stat().st_mode & 0o077 == 0


