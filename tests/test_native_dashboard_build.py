"""A dependency change must reach the deployed dashboard. On 2026-10-31 a deploy built the new fonts against an old
node_modules: Vite only warned, the build succeeded, and every font was a 404 in production."""

import os

import pytest

from jobpilot.native import release
from jobpilot.native.env import NativeError


def checkout(tmp_path, *, lock_age=0, installed_age=0, built_age=0):
    frontend = tmp_path / "frontend"
    (frontend / "node_modules").mkdir(parents=True)
    (frontend / "src").mkdir()
    static = tmp_path / "jobpilot" / "static" / "assets"
    static.mkdir(parents=True)
    now = 1_800_000_000
    for path, age in (
        (frontend / "package-lock.json", lock_age),
        (frontend / "node_modules" / ".package-lock.json", installed_age),
        (tmp_path / "jobpilot" / "static" / "index.html", built_age),
    ):
        path.write_text("{}")
        os.utime(path, (now - age, now - age))
    return tmp_path


def fake_npm(monkeypatch, calls, css="body{}"):
    def run(command, cwd=None, **_):
        calls.append(command[1])
        if command[1:] == ["run", "build"]:
            (cwd.parent / "jobpilot" / "static" / "assets" / "index.css").write_text(css)

        class Done:
            returncode = 0

        return Done()

    monkeypatch.setattr(release.shutil, "which", lambda _: "npm")
    monkeypatch.setattr(release.subprocess, "run", run)


def test_a_lock_file_newer_than_the_install_reinstalls_and_rebuilds(tmp_path, monkeypatch):
    root = checkout(tmp_path, lock_age=10, installed_age=100, built_age=50)
    calls = []
    fake_npm(monkeypatch, calls)
    assert release.dashboard_stale(root)
    assert release.build_dashboard(root, say=lambda _: None)
    assert calls == ["ci", "run"]


def test_current_packages_are_not_reinstalled(tmp_path, monkeypatch):
    root = checkout(tmp_path, lock_age=100, installed_age=50, built_age=200)
    calls = []
    fake_npm(monkeypatch, calls)
    assert release.build_dashboard(root, say=lambda _: None)
    assert "ci" not in calls


def test_an_install_without_a_frontend_has_no_packages_to_refresh(tmp_path):
    assert not release.packages_stale(tmp_path / "frontend")


def test_a_lock_file_with_nothing_installed_is_stale(tmp_path):
    root = checkout(tmp_path)
    (root / "frontend" / "node_modules" / ".package-lock.json").unlink()
    assert release.packages_stale(root / "frontend")


def test_nothing_to_do_builds_nothing(tmp_path, monkeypatch):
    root = checkout(tmp_path, lock_age=100, installed_age=50, built_age=10)
    calls = []
    fake_npm(monkeypatch, calls)
    assert not release.build_dashboard(root, say=lambda _: None)
    assert calls == []


@pytest.mark.parametrize(
    "css",
    [
        "@font-face{src:url(@fontsource/example/files/a.woff2)format('woff2')}",
        '@font-face{src:url("@fontsource/example/files/a.woff2")}',
    ],
)
def test_a_build_that_left_a_package_path_in_its_css_fails(tmp_path, monkeypatch, css):
    root = checkout(tmp_path, lock_age=10, installed_age=100, built_age=50)
    fake_npm(monkeypatch, [], css)
    with pytest.raises(NativeError, match="@fontsource/example"):
        release.build_dashboard(root, say=lambda _: None)


def test_resolved_assets_and_inline_data_pass(tmp_path):
    root = checkout(tmp_path)
    assets = root / "jobpilot" / "static" / "assets"
    (assets / "index.css").write_text("a{src:url(/assets/doto-abc.woff2)}b{background:url(%23n)}c{background:url(data:image/png;base64,AA)}")
    assert release.unresolved_assets(root / "jobpilot" / "static") == []
