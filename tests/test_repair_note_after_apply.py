"""The repair agent's note for a job is dropped once the owner clicks Apply after it: Deloitte's "stop retrying sign-in"
outlived the fix for its sign-in and the next run stopped on it (2026-10-26)."""

from types import SimpleNamespace

from jobpilot.browser import BrowserEngine
from jobpilot.runtime import engine_version


def engine(service, **settings):
    for key, value in settings.items():
        service.db.set_setting(key.replace("__", ":"), value)
    value = object.__new__(BrowserEngine)
    value.db, value.job = service.db, {"id": "j1"}
    return value


def test_a_note_written_after_the_owners_apply_is_followed(service):
    e = engine(service, owner_pick__j1={"at": "2026-10-26T10:00:00+00:00"},
               repair_directive__j1={"text": "Fill the country first.", "at": "2026-10-26T11:00:00+00:00",
                                     "engine": engine_version()})
    assert e.current_repair_instruction() == "Fill the country first."


def test_a_note_older_than_the_owners_apply_is_dropped(service):
    e = engine(service, repair_directive__j1={"text": "Stop retrying sign-in.", "at": "2026-10-26T05:00:00+00:00"},
               owner_pick__j1={"at": "2026-10-26T17:50:00+00:00"})
    assert e.current_repair_instruction() == ""


def test_a_note_from_an_earlier_build_is_dropped(service):
    # Applied Materials (2026-10-26): "do not retry the Workday tenant" outlived the handoff fix.
    e = engine(service, repair_directive__j1={"text": "Stop: the tenant is another employer's.",
                                              "at": "2026-10-26T11:00:00+00:00", "engine": "execution-v2-old"})
    assert e.current_repair_instruction() == ""
