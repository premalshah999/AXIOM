"""What a friend reads before AXIOM can say anything: the disk image's text, the message to paste, and the update check when GitHub has
nothing to say. A wrong word here cannot be fixed by the app, because the person has not got that far."""

import http.client
import plistlib
import re
import urllib.error
from pathlib import Path

import pytest

from jobpilot.native import desktop

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "desktop" / "dmg-readme.txt").read_text()
FRIENDS = (ROOT / "docs" / "FRIENDS.md").read_text()
WINDOW = (ROOT / "desktop" / "AXIOM.m").read_text()
BUILD = (ROOT / "desktop" / "build.sh").read_text()


def flat(text):
    """Line breaks and the quoted message's `> ` out of the way, so a phrase is found wherever it wraps."""
    return re.sub(r"\s+", " ", re.sub(r"(?m)^> ?", "", text))


@pytest.mark.parametrize(
    "words",
    [
        "Apple could not verify AXIOM",
        "Click Done",
        "System Settings",
        "Privacy & Security",
        "Open Anyway",
        "AXIOM was blocked",
        "Open or Open Anyway",
        "macOS 15 or newer",
        "Apple silicon",
        "a Gmail",
        "openrouter.ai",
        "$5 of credit usually covers a few hundred applications",
        "Copy Diagnostics",
    ],
)
def test_the_disk_image_and_the_message_to_paste_say_the_same(words):
    assert words in flat(README), "the disk image"
    assert words in flat(FRIENDS), "the message"


def test_the_first_open_goes_in_the_same_order_in_both():
    for text in (flat(README), flat(FRIENDS.split("## Message to paste")[1].split("## What a friend agrees to")[0])):
        places = [text.index(step) for step in ("Apple could not verify", "Done", "Privacy & Security", "Open Anyway", "password", "Open or Open Anyway")]
        assert places == sorted(places)


def test_the_disk_image_is_what_the_build_puts_on_it():
    assert 'cp "$root/desktop/dmg-readme.txt" "$build/dmg/Read me first.txt"' in BUILD
    assert '"$build/AXIOM.dmg"' in BUILD and "Download AXIOM.dmg" in flat(FRIENDS)


def test_every_control_the_text_names_exists():
    account = (ROOT / "frontend" / "src" / "account.tsx").read_text()
    assert "Settings, Your account, Delete everything" in flat(README) and "Settings, Your account, Delete everything" in flat(FRIENDS)
    assert "Delete everything" in account and "Delete my data" not in README
    assert '@"Copy Diagnostics"' in WINDOW and 'addItemWithTitle:@"Check for Updates' in WINDOW
    assert 'NSMenu *help = [self addMenu:@"Help"' in WINDOW


def test_nobody_is_told_of_a_way_in_that_the_app_refuses():
    """The minimum comes from the strictest binary, and the last build said 15.0. An older Mac cannot open it by any click."""
    assert "right-click" not in README.lower()
    assert "On macOS 14 or older AXIOM will not open" in flat(README)


def test_a_copy_that_stopped_part_way_says_the_same_thing_everywhere():
    assert 'showSplash:@"AXIOM was not copied completely"' in WINDOW
    assert '"AXIOM was not copied completely"' in FRIENDS
    assert "was not copied completely" in flat(README)
    assert "drag it from the" in WINDOW and "drag it from this window to Applications again" in flat(README)
    assert "Use Finder, not Terminal" in flat(README) and "use Finder, not Terminal" in flat(FRIENDS)


def test_quit_is_described_as_it_is():
    desktop_doc = flat((ROOT / "docs" / "DESKTOP.md").read_text())
    for button in ("Stop and Quit", "Quit", "Keep Running"):
        assert f'addButtonWithTitle:@"{button}"' in WINDOW and f"**{button}**" in desktop_doc
    assert "['Quit now','quit-now']" in WINDOW and "**Quit now**" in desktop_doc
    assert "they carry on by themselves the next time you" in flat(WINDOW.replace('"\n                              @"', ""))


def installed(layout, version="1.0.0"):
    (layout.bundle / "Contents").mkdir(parents=True, exist_ok=True)
    (layout.bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": version}))
    return layout


@pytest.fixture
def layout(tmp_path):
    return installed(desktop.Layout(home=tmp_path, resources=tmp_path / "AXIOM.app" / "Contents" / "Resources"))


def answering(code):
    def fetch():
        raise urllib.error.HTTPError(desktop.RELEASES_API, code, "answer", {}, None)

    return fetch


def test_a_private_repository_is_a_quiet_no_answer(layout):
    """GitHub answers 404 for a private repository and for one with nothing published alike."""
    answer = desktop.check_update(layout, fetch=answering(404))
    assert answer == {"current": "1.0.0", "latest": "", "url": "", "newer": False, "error": ""}


@pytest.mark.parametrize("code", [403, 429, 500, 503])
def test_any_other_answer_is_noted_for_someone_who_asked_and_never_offers_anything(layout, code):
    answer = desktop.check_update(layout, fetch=answering(code))
    assert str(code) in answer["error"] and not answer["newer"] and not answer["url"]


@pytest.mark.parametrize(
    "failure",
    [OSError("no route"), TimeoutError("slow"), ValueError("not JSON"), http.client.IncompleteRead(b"x"), http.client.BadStatusLine("x")],
    ids=["offline", "timeout", "not-json", "cut-off", "garbled"],
)
def test_a_failed_check_is_only_ever_a_sentence(layout, failure):
    def fetch():
        raise failure

    answer = desktop.check_update(layout, fetch=fetch)
    assert answer["error"].startswith("Could not reach github.com") and not answer["newer"]


@pytest.mark.parametrize("body", [None, [], "text", {}, {"tag_name": ""}, {"message": "Not Found"}])
def test_an_answer_with_no_version_offers_nothing(layout, body):
    answer = desktop.check_update(layout, fetch=lambda: body)
    assert not answer["newer"] and not answer["latest"] and not answer["error"]


def test_the_daily_check_only_ever_speaks_when_there_is_something_new():
    """The window: a dialog comes from `newer`, or from someone having asked. Nothing else in this method can show one."""
    method = WINDOW[WINDOW.index("- (void)checkUpdates:(BOOL)asked {") : WINDOW.index("- (IBAction)updateAlertOpen:")]
    assert "if ([answer[@\"newer\"] boolValue]" in method and "} else if (asked) {" in method
    assert method.count("runModal") == 1 and method.index("runModal") > method.index("} else if (asked) {")
    assert "if (!asked && [[defaults stringForKey:@\"UpdateToldVersion\"] isEqual:latest]) return;" in method


def test_only_a_number_from_github_makes_this_copy_the_newest():
    method = WINDOW[WINDOW.index("} else if (asked) {") : WINDOW.index("- (IBAction)updateAlertOpen:")]
    assert '@"No update to offer"' in method and "known ?" in method
