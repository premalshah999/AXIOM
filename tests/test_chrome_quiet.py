"""Nothing in the app's own Chrome may wait for a person: no macOS keychain dialog, restore bubble, profile error or prompt."""

import json
import plistlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobpilot.application_budget import BROWSER_OUTAGE, nothing_sent
from jobpilot.browser import BROWSER_NOT_READY, renderer_crashed, throwaway_chrome
from jobpilot.native import supervisor
from jobpilot.vocabulary import plain_sentence

ROOT = Path(__file__).resolve().parent.parent


def argv(tmp_path, monkeypatch, home, profile):
    monkeypatch.setenv("CHROME_BIN", "/bin/true")
    monkeypatch.setenv("HOME", str(home))
    cfg = SimpleNamespace(scripts=ROOT / "scripts")
    return supervisor.chrome_argv(cfg, "chrome-worker.sh", "CHROME_WORKER_PORT", "CHROME_WORKER_PROFILE", "http://127.0.0.1:59871", profile)


def with_keychain(tmp_path):
    home = tmp_path / "home"
    (home / "Library" / "Keychains").mkdir(parents=True)
    (home / "Library" / "Keychains" / "login.keychain-db").touch()
    return home


def test_a_new_profile_stays_off_the_keychain_on_every_start(tmp_path, monkeypatch):
    profile, home = tmp_path / "p", with_keychain(tmp_path)
    assert "--use-mock-keychain" in argv(tmp_path, monkeypatch, home, profile)
    (profile / "Local State").write_text("{}")  # Chrome has opened it since: the first start's choice must hold.
    assert "--use-mock-keychain" in argv(tmp_path, monkeypatch, home, profile)


def test_a_profile_on_the_real_key_keeps_it_because_switching_would_lose_its_sign_ins(tmp_path, monkeypatch):
    profile, home = tmp_path / "p", with_keychain(tmp_path)
    profile.mkdir()
    (profile / "Local State").write_text("{}")
    assert "--use-mock-keychain" not in argv(tmp_path, monkeypatch, home, profile)


def test_a_home_with_no_keychain_never_asks_for_one(tmp_path, monkeypatch):
    if sys.platform != "darwin":
        pytest.skip("macOS only")
    profile = tmp_path / "p"
    profile.mkdir()
    (profile / "Local State").write_text("{}")
    home = tmp_path / "bare"
    home.mkdir()
    assert "--use-mock-keychain" in argv(tmp_path, monkeypatch, home, profile), "A keychain cannot be found to store Chrome"


def test_nothing_in_the_worker_waits_for_a_person(tmp_path, monkeypatch):
    flags = argv(tmp_path, monkeypatch, with_keychain(tmp_path), tmp_path / "p")
    assert {"--hide-crash-restore-bubble", "--noerrdialogs", "--deny-permission-prompts"} <= set(flags)


def test_the_throwaway_chrome_never_touches_the_keychain_even_headless():
    assert "--use-mock-keychain" in throwaway_chrome("chrome", "/tmp/p", True)
    assert "--use-mock-keychain" in throwaway_chrome("chrome", "/tmp/p", False)


def test_a_window_on_a_mac_carries_no_unsupported_flag_banner():
    assert "--no-sandbox" not in throwaway_chrome("chrome", "/tmp/p", False, platform="darwin")
    assert "--no-sandbox" in throwaway_chrome("chrome", "/tmp/p", False, platform="linux")


def test_prompts_are_turned_off_and_the_rest_of_the_preferences_stay(tmp_path):
    (tmp_path / "Default").mkdir()
    path = tmp_path / "Default" / "Preferences"
    path.write_text(json.dumps({"keep": 1, "profile": {"name": "x"}, "autofill": {"last_version_deduped": 156}}))
    supervisor.quiet_profile(str(tmp_path))
    found = json.loads(path.read_text())
    assert found["keep"] == 1 and found["profile"]["name"] == "x" and found["autofill"]["last_version_deduped"] == 156
    assert found["credentials_enable_service"] is False and found["profile"]["password_manager_enabled"] is False
    assert found["signin"]["allowed"] is False and found["translate"]["enabled"] is False


def test_damaged_preferences_are_set_aside_rather_than_stopping_chrome(tmp_path):
    (tmp_path / "Default").mkdir()
    path = tmp_path / "Default" / "Preferences"
    path.write_text("this is not valid {{{")
    supervisor.quiet_profile(str(tmp_path))
    assert json.loads(path.read_text())["credentials_enable_service"] is False
    assert (tmp_path / "Default" / "Preferences.corrupt").exists()


def test_a_crashed_renderer_is_the_browsers_failure():
    assert renderer_crashed(Exception("Page.goto: Page crashed\nCall log:"))
    assert renderer_crashed(Exception("Locator.click: Target crashed"))
    assert not renderer_crashed(Exception("Locator.click: Timeout 5000ms exceeded\n the crashed-items list"))


def test_browser_trouble_reads_as_plain_words():
    assert plain_sentence("Browser control failed at forms.py:5784; application tab retained for recovery") == "The browser stopped responding on this page."
    assert "ApplyPilot" not in plain_sentence("Open the ApplyPilot Chrome window: the browser is responding")
    assert ".py" not in BROWSER_NOT_READY and "Chrome" not in BROWSER_NOT_READY


def test_the_new_outage_wording_still_counts_as_nothing_sent():
    assert BROWSER_NOT_READY.startswith(BROWSER_OUTAGE) and nothing_sent("timed_out", BROWSER_NOT_READY)


def test_an_updated_chrome_restarts_only_with_nothing_running(tmp_path):
    binary = tmp_path / "Chrome.app" / "Contents" / "MacOS" / "Chrome"
    binary.parent.mkdir(parents=True)
    info = binary.parents[1] / "Info.plist"
    info.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "2.0"}))
    stops = []
    child = SimpleNamespace(state="up", proc=object(), stopping=False, version="1.0", binary=str(binary), name="worker", request_stop=lambda now: stops.append(now))
    owner = SimpleNamespace(active=3, stop_reason="", children=[child], say=lambda _: None)
    supervisor.Supervisor.tick_chrome_update(owner, 1.0)
    assert not stops, "a restart costs running applications their tabs"
    owner.active = None
    supervisor.Supervisor.tick_chrome_update(owner, 1.0)
    assert not stops, "unknown is not quiet"
    owner.active = 0
    supervisor.Supervisor.tick_chrome_update(owner, 1.0)
    assert stops == [1.0]
